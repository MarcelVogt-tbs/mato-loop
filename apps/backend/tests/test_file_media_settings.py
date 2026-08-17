from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes import file_media_settings as routes
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import FileMediaSettings


def _admin() -> UserAccount:
    return UserAccount(
        id=uuid4(),
        username="admin",
        password_hash="hash",
        is_admin=True,
        is_active=True,
    )


async def test_admin_updates_global_file_media_settings(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    current = FileMediaSettings()
    load = AsyncMock(return_value=current)
    save = AsyncMock()
    monkeypatch.setattr(routes.SettingsStore, "load", load)
    monkeypatch.setattr(routes.SettingsStore, "save", save)
    payload = FileMediaSettings(max_upload_mib=32)

    result = await routes.update_file_media_settings(payload, _admin(), db)

    assert result["maxUploadMib"] == 32
    load.assert_awaited_once_with(db, FileMediaSettings, for_update=True)
    save.assert_awaited_once()
    db.commit.assert_awaited_once()


def test_file_media_routes_split_read_and_admin_authorization() -> None:
    get_route = next(route for route in routes.router.routes if route.path.endswith("file-media-settings") and "GET" in route.methods)
    put_route = next(route for route in routes.router.routes if route.path.endswith("file-media-settings") and "PUT" in route.methods)

    assert {dependency.call.__name__ for dependency in get_route.dependant.dependencies} >= {
        "require_user",
        "get_db_session",
    }
    assert {dependency.call.__name__ for dependency in put_route.dependant.dependencies} >= {
        "require_admin",
        "get_db_session",
    }
