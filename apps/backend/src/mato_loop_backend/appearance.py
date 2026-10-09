from __future__ import annotations

import base64
import hashlib
import logging
from io import BytesIO
from pathlib import Path
from typing import Annotated

from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from mato_loop_backend.config import PROJECT_ROOT, get_runtime_config
from mato_loop_backend.media.attachments import AttachmentError, image_mime_type, validate_image
from mato_loop_backend.settings.models import SettingsDocument

LOGO_MAX_BYTES = 1024 * 1024
ThemeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")]
logger = logging.getLogger(__name__)


class AppearanceSettings(SettingsDocument):
    settings_key = "appearance"

    app_name: str = "MatoLoop"
    description: str = "Deine selbst gehostete AI-Chat- und Agent-Harness"
    default_theme_id: str = "matoloop"
    disabled_theme_ids: set[str] = Field(default_factory=set)
    # ponytail: bounded logo in settings; split assets out if branding reads become hot.
    logo_png: str | None = None


class BrandingUpdate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", str_strip_whitespace=True)

    app_name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=500)


class ThemesUpdate(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    default_theme_id: ThemeId
    disabled_theme_ids: set[ThemeId] = Field(default_factory=set, max_length=100)


class ThemeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: ThemeId
    name: str = Field(min_length=1, max_length=80)


class ThemeOption(ThemeDefinition):
    enabled: bool


class AppearanceResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    app_name: str
    description: str
    logo_url: str
    custom_logo: bool
    default_theme_id: str
    effective_theme_id: str
    stylesheet_url: str
    themes: list[ThemeOption]


def themes_directory() -> Path:
    static_dir = get_runtime_config().static_dir
    if static_dir and (static_dir / "themes").is_dir():
        return static_dir / "themes"
    return PROJECT_ROOT / "apps" / "frontend" / "public" / "themes"


def load_themes() -> dict[str, ThemeDefinition]:
    root = themes_directory().resolve()
    themes = {}
    for manifest in sorted(root.glob("*/theme.json")):
        try:
            if not manifest.resolve().is_relative_to(root):
                raise ValueError("Invalid theme directory")
            theme = ThemeDefinition.model_validate_json(manifest.read_text(encoding="utf-8"))
            stylesheet = manifest.parent / "theme.css"
            if (theme.id != manifest.parent.name or not stylesheet.is_file()
                    or not stylesheet.resolve().is_relative_to(root)):
                raise ValueError("Invalid theme directory")
        except (OSError, ValueError):
            logger.warning("Ignoring invalid bundled theme: %s", manifest.parent.name)
            continue
        themes[theme.id] = theme
    if "matoloop" not in themes:
        raise RuntimeError("The bundled MatoLoop fallback theme is missing")
    return themes


def appearance_payload(settings: AppearanceSettings) -> AppearanceResponse:
    themes = load_themes()
    enabled = [theme_id for theme_id in themes if theme_id not in settings.disabled_theme_ids]
    effective = settings.default_theme_id
    if effective not in enabled:
        effective = "matoloop" if "matoloop" in enabled else next(iter(enabled), "matoloop")
    css = (themes_directory() / effective / "theme.css").read_bytes()
    revision = hashlib.sha256(css).hexdigest()[:16]
    logo_revision = hashlib.sha256(settings.logo_png.encode()).hexdigest()[:16] if settings.logo_png else None
    return AppearanceResponse(
        app_name=settings.app_name,
        description=settings.description,
        custom_logo=bool(settings.logo_png),
        logo_url=f"/api/appearance/logo?v={logo_revision}" if logo_revision else "/icons/matoloop.svg",
        default_theme_id=settings.default_theme_id,
        effective_theme_id=effective,
        stylesheet_url=f"/themes/{effective}/theme.css?v={revision}",
        themes=[ThemeOption(**theme.model_dump(), enabled=theme.id in enabled) for theme in themes.values()],
    )


def normalize_logo(data: bytes) -> str:
    mime = image_mime_type(data)
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise AttachmentError("attachment.unsupportedType")
    validate_image(data, mime, max_bytes=LOGO_MAX_BYTES)
    with Image.open(BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image = ImageOps.pad(image, (512, 512), method=Image.Resampling.LANCZOS, color=(0, 0, 0, 0))
        image.info.clear()
        output = BytesIO()
        image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")
