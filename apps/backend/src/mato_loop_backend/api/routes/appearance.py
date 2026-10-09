import asyncio
import base64
from typing import Annotated

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.appearance import (
    LOGO_MAX_BYTES,
    AppearanceResponse,
    AppearanceSettings,
    BrandingUpdate,
    ThemesUpdate,
    appearance_payload,
    load_themes,
    normalize_logo,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.media.attachments import AttachmentError
from mato_loop_backend.security import require_admin
from mato_loop_backend.settings import SettingsStore

router = APIRouter(prefix="/appearance")
admin_router = APIRouter(dependencies=[Depends(require_admin)])
Database = Annotated[AsyncSession, Depends(get_db_session)]
NO_CACHE = {"Cache-Control": "no-store"}


@router.get("", response_model=AppearanceResponse)
async def get_appearance(db: Database, response: Response):
    response.headers.update(NO_CACHE)
    return appearance_payload(await SettingsStore.load(db, AppearanceSettings))


@router.get("/logo")
async def get_logo(db: Database):
    settings = await SettingsStore.load(db, AppearanceSettings)
    if not settings.logo_png:
        return RedirectResponse("/icons/matoloop.svg", headers=NO_CACHE)
    return Response(base64.b64decode(settings.logo_png), media_type="image/png", headers=NO_CACHE)


@router.get("/manifest.webmanifest")
async def get_manifest(db: Database):
    appearance = appearance_payload(await SettingsStore.load(db, AppearanceSettings))
    icons = [{"src": appearance.logo_url, "sizes": "512x512", "type": "image/png", "purpose": "any"}] if appearance.custom_logo else [
        {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "/icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ]
    return JSONResponse({
        "id": "/", "name": appearance.app_name, "short_name": appearance.app_name,
        "description": appearance.description, "lang": "de", "start_url": "/", "scope": "/",
        "display": "standalone", "background_color": "#f8fafc", "theme_color": "#2563eb", "icons": icons,
    }, media_type="application/manifest+json", headers=NO_CACHE)


@admin_router.put("/branding", response_model=AppearanceResponse)
async def update_branding(payload: BrandingUpdate, db: Database):
    settings = await SettingsStore.load(db, AppearanceSettings, for_update=True)
    settings.app_name = payload.app_name
    settings.description = payload.description
    await SettingsStore.save(db, settings)
    await db.commit()
    return appearance_payload(settings)


@admin_router.put("/themes", response_model=AppearanceResponse)
async def update_themes(payload: ThemesUpdate, db: Database):
    available = load_themes()
    if payload.default_theme_id not in available or payload.disabled_theme_ids - available.keys():
        raise ApiError(422, "appearance.themeUnavailable")
    if payload.default_theme_id in payload.disabled_theme_ids:
        raise ApiError(422, "appearance.defaultThemeDisabled", field="defaultThemeId")
    settings = await SettingsStore.load(db, AppearanceSettings, for_update=True)
    settings.default_theme_id = payload.default_theme_id
    settings.disabled_theme_ids = payload.disabled_theme_ids
    await SettingsStore.save(db, settings)
    await db.commit()
    return appearance_payload(settings)


@admin_router.put("/logo", response_model=AppearanceResponse)
async def upload_logo(file: UploadFile, db: Database):
    data = await file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        raise ApiError(413, "attachment.tooLarge")
    try:
        logo = await asyncio.to_thread(normalize_logo, data)
    except AttachmentError as exc:
        raise ApiError(422, exc.code) from exc
    settings = await SettingsStore.load(db, AppearanceSettings, for_update=True)
    settings.logo_png = logo
    await SettingsStore.save(db, settings)
    await db.commit()
    return appearance_payload(settings)


@admin_router.delete("/logo", response_model=AppearanceResponse)
async def reset_logo(db: Database):
    settings = await SettingsStore.load(db, AppearanceSettings, for_update=True)
    settings.logo_png = None
    await SettingsStore.save(db, settings)
    await db.commit()
    return appearance_payload(settings)


router.include_router(admin_router)
