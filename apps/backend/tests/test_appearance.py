import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image

from mato_loop_backend import appearance
from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.request_body_limit import MediaRequestBodyLimitMiddleware
from mato_loop_backend.api.routes.appearance import router
from mato_loop_backend.auth.sessions import require_user
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.media.attachments import AttachmentError
from mato_loop_backend.settings import SettingsStore


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    root = tmp_path / "themes"
    root.mkdir()
    for theme_id in ("matoloop", "test-theme"):
        folder = root / theme_id
        folder.mkdir()
        (folder / "theme.json").write_text(json.dumps({"id": theme_id, "name": theme_id}))
        (folder / "theme.css").write_text(":root { --q-primary: #123456; }")
    monkeypatch.setattr(appearance, "themes_directory", lambda: root)
    return root


@pytest.fixture
async def client(catalog, monkeypatch):
    settings = appearance.AppearanceSettings()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock()
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_middleware(MediaRequestBodyLimitMiddleware)
    register_error_handlers(app)
    app.dependency_overrides[get_db_session] = lambda: db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, app, settings


def png():
    output = BytesIO()
    Image.new("RGBA", (80, 40), "red").save(output, format="PNG")
    return output.getvalue()


async def test_public_bootstrap_and_admin_configuration(client):
    client, app, settings = client
    assert (await client.get("/api/appearance")).json()["appName"] == "MatoLoop"
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=True)
    response = await client.put("/api/appearance/branding", json={"appName": " Teamchat ", "description": "Unser Team"})
    assert response.json()["appName"] == "Teamchat"
    response = await client.put("/api/appearance/themes", json={"defaultThemeId": "test-theme", "disabledThemeIds": ["matoloop"]})
    assert response.json()["effectiveThemeId"] == "test-theme"
    assert settings.app_name == "Teamchat"
    assert (await client.get("/api/appearance/manifest.webmanifest")).json()["name"] == "Teamchat"


@pytest.mark.parametrize("path,method", [("branding", "PUT"), ("themes", "PUT"), ("logo", "PUT"), ("logo", "DELETE")])
async def test_mutations_require_admin(client, path, method):
    client, app, _settings = client
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=False)
    response = await client.request(method, f"/api/appearance/{path}")
    assert response.status_code == 403


@pytest.mark.parametrize("payload,code", [
    ({"defaultThemeId": "unknown"}, "appearance.themeUnavailable"),
    ({"defaultThemeId": "matoloop", "disabledThemeIds": ["unknown"]}, "appearance.themeUnavailable"),
    ({"defaultThemeId": "matoloop", "disabledThemeIds": ["matoloop"]}, "appearance.defaultThemeDisabled"),
    ({"defaultThemeId": "../secret"}, "validation.invalidFormat"),
])
async def test_rejects_invalid_theme_selection(client, payload, code):
    client, app, settings = client
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=True)
    response = await client.put("/api/appearance/themes", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert settings.default_theme_id == "matoloop"


async def test_logo_round_trip_and_reset(client):
    client, app, _settings = client
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=True)
    uploaded = await client.put("/api/appearance/logo", files={"file": ("logo.png", png(), "image/png")})
    assert uploaded.json()["customLogo"] is True
    assert "logo_png" not in uploaded.json()
    response = await client.get(uploaded.json()["logoUrl"])
    with Image.open(BytesIO(response.content)) as image:
        assert image.size == (512, 512)
    manifest = (await client.get("/api/appearance/manifest.webmanifest")).json()
    assert manifest["icons"][0]["src"] == uploaded.json()["logoUrl"]
    assert (await client.delete("/api/appearance/logo")).json()["customLogo"] is False


async def test_upload_limits_and_unsupported_images(client):
    client, app, _settings = client
    app.dependency_overrides[require_user] = lambda: SimpleNamespace(is_admin=True)
    response = await client.put("/api/appearance/logo", files={"file": ("logo.svg", b"<svg/>", "image/svg+xml")})
    assert response.json()["error"]["code"] == "attachment.unsupportedType"
    response = await client.put("/api/appearance/logo", content=b"x" * (2 * 1024 * 1024 + 1))
    assert response.status_code == 413
    with pytest.raises(AttachmentError):
        appearance.normalize_logo(b"\x89PNG\r\n\x1a\nnot-an-image")


def test_catalog_fallback_and_boundaries(catalog, tmp_path):
    external = tmp_path / "outside.json"
    external.write_text('{"id":"linked","name":"External"}')
    linked = catalog / "linked"
    linked.mkdir()
    (linked / "theme.json").symlink_to(external)
    (linked / "theme.css").write_text("")
    (catalog / "test-theme" / "theme.css").unlink()
    snapshot = appearance.appearance_payload(appearance.AppearanceSettings(default_theme_id="test-theme"))
    assert snapshot.effective_theme_id == "matoloop"
    assert snapshot.default_theme_id == "test-theme"
    assert [theme.id for theme in snapshot.themes] == ["matoloop"]


def test_logo_is_a_normalized_png():
    with Image.open(BytesIO(base64.b64decode(appearance.normalize_logo(png())))) as image:
        assert (image.format, image.getpixel((0, 0)), image.getpixel((256, 256))) == ("PNG", (0, 0, 0, 0), (255, 0, 0, 255))


def test_square_logo_drops_embedded_metadata():
    source = BytesIO()
    metadata = Image.Exif()
    metadata[315] = "Private image author"
    Image.new("RGB", (512, 512), "red").save(source, format="PNG", exif=metadata)
    with Image.open(BytesIO(base64.b64decode(appearance.normalize_logo(source.getvalue())))) as image:
        assert image.info == {}
