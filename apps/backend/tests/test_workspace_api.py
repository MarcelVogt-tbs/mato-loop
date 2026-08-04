import hashlib
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes import workspace as workspace_route
from mato_loop_backend.api.routes.workspace import (
    FileScope,
    RenameEntryRequest,
    UpdateFileRequest,
)
from mato_loop_backend.models import ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.workspace import FileRoot, RootFileService, WorkspaceService


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


async def test_file_context_hides_foreign_sessions() -> None:
    user = _user()
    foreign = ChatSession(id=uuid4(), user_id=uuid4(), title="Foreign")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = foreign

    with pytest.raises(ApiError) as exc_info:
        await workspace_route._file_context(FileScope.SESSION, foreign.id, user, db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "session.notFound"


async def test_rename_session_entry_clears_source_and_destination_grants(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    session_root = storage.provision(session_id)
    (session_root / "files" / "old.txt").write_text("old", encoding="utf-8")
    root_files = RootFileService(session_id, session_storage=storage)
    context = workspace_route._FileContext(
        root_files=root_files,
        root=FileRoot.SESSION,
    )

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    grants = [
        PermissionGrant(
            session_id=session_id,
            capability="filesystem.write",
            resource_type="root_path",
            resource_scope=json.dumps(
                {"identity": str(session_id), "path": path, "root": "session"}
            ),
        )
        for path in ("files/old.txt", "files/renamed.txt", "files/other.txt")
    ]
    result = MagicMock()
    result.all.return_value = grants
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = result

    response = await workspace_route.rename_entry(
        RenameEntryRequest(path="files/old.txt", name="renamed.txt"),
        _user(),
        db,
        scope=FileScope.SESSION,
        session_id=session_id,
    )

    assert response["entry"]["path"] == "files/renamed.txt"
    assert (session_root / "files" / "renamed.txt").read_text(encoding="utf-8") == "old"
    assert [call.args[0] for call in db.delete.await_args_list] == grants[:2]
    db.commit.assert_awaited_once()


async def test_preview_returns_markdown_and_only_validated_image_content(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "readme.md").write_text("# Title\n![Remote](https://example.com/x.png)", encoding="utf-8")
    image_data = BytesIO()
    Image.new("RGB", (2, 3), "red").save(image_data, format="PNG")
    (tmp_path / "image.png").write_bytes(image_data.getvalue())
    context = workspace_route._FileContext(workspace=workspace)

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    preview = await workspace_route.preview_file(user, db, "readme.md")
    image = await workspace_route.preview_image(user, db, "image.png")

    assert preview == {
        "kind": "markdown",
        "content": "# Title\n![Remote](https://example.com/x.png)",
        "encoding": "utf-8",
        "revision": hashlib.sha256(
            b"# Title\n![Remote](https://example.com/x.png)"
        ).hexdigest(),
    }
    assert image.media_type == "image/png"
    assert image.headers["x-content-type-options"] == "nosniff"
    assert image.headers["cache-control"] == "private, no-store"


async def test_preview_serves_static_svg_with_restrictive_headers(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceService(tmp_path)
    svg = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">
    <style>.shape { fill: url(#gradient); }</style>
    <defs><linearGradient id="gradient"><stop stop-color="red"/></linearGradient></defs>
    <rect class="shape" width="10" height="10"/>
    </svg>"""
    (tmp_path / "image.svg").write_bytes(svg)
    context = workspace_route._FileContext(workspace=workspace)

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    preview = await workspace_route.preview_file(user, db, "image.svg")
    image = await workspace_route.preview_image(user, db, "image.svg")

    assert preview == {"kind": "image", "mimeType": "image/svg+xml"}
    assert image.media_type == "image/svg+xml"
    assert image.body == svg
    assert "default-src 'none'" in image.headers["content-security-policy"]
    assert "style-src 'unsafe-inline'" in image.headers["content-security-policy"]


@pytest.mark.parametrize(
    "svg",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><rect style="fill:url(data:image/png,x)"/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject/></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><animate attributeName="x"/></svg>',
        b'<?xml-stylesheet href="https://example.com/x.css"?><svg xmlns="http://www.w3.org/2000/svg"/>',
        b'<!DOCTYPE svg [<!ENTITY x "boom">]><svg xmlns="http://www.w3.org/2000/svg">&x;</svg>',
    ],
)
def test_svg_preview_rejects_active_and_external_content(svg: bytes) -> None:
    with pytest.raises(ValueError, match="unsafeSvg"):
        workspace_route._validate_svg(svg)


def test_svg_preview_rejects_invalid_or_oversized_xml() -> None:
    with pytest.raises(ValueError, match="invalidImage"):
        workspace_route._validate_svg(b"<svg>")
    with pytest.raises(ValueError, match="tooLarge"):
        workspace_route._validate_svg(b"<svg/>" + b" " * (5 * 1024 * 1024))


async def test_update_session_file_clears_exact_grant_revalidates_and_returns_revision(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    session_root = storage.provision(session_id)
    target = session_root / "files" / "notes.txt"
    target.write_text("old", encoding="utf-8")
    context = workspace_route._FileContext(
        root_files=RootFileService(session_id, session_storage=storage),
        root=FileRoot.SESSION,
    )
    contexts = 0

    async def file_context(*_args, **_kwargs):
        nonlocal contexts
        contexts += 1
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    grants = [
        PermissionGrant(
            session_id=session_id,
            capability="filesystem.write",
            resource_type="root_path",
            resource_scope=json.dumps(
                {"identity": str(session_id), "path": path, "root": "session"}
            ),
        )
        for path in ("files/notes.txt", "files/other.txt")
    ]
    result = MagicMock()
    result.all.return_value = grants
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = result

    response = await workspace_route.update_file(
        UpdateFileRequest(
            path="files/notes.txt",
            content="new",
            expected_revision=hashlib.sha256(b"old").hexdigest(),
        ),
        _user(),
        db,
        scope=FileScope.SESSION,
        session_id=session_id,
    )

    assert response == {"revision": hashlib.sha256(b"new").hexdigest()}
    assert target.read_text(encoding="utf-8") == "new"
    assert contexts == 2
    db.delete.assert_awaited_once_with(grants[0])
    assert db.commit.await_count == 2


async def test_update_file_maps_stale_revision_to_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "notes.txt").write_text("current", encoding="utf-8")
    context = workspace_route._FileContext(workspace=workspace)

    async def file_context(*_args, **_kwargs):
        return context

    async def clear_grants(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    monkeypatch.setattr(workspace_route, "_clear_file_grants", clear_grants)
    monkeypatch.setattr(workspace_route, "WorkspaceService", lambda: workspace)
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ApiError) as exc_info:
        await workspace_route.update_file(
            UpdateFileRequest(
                path="notes.txt",
                content="new",
                expected_revision=hashlib.sha256(b"old").hexdigest(),
            ),
            _user(),
            db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "file.changed"
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "current"
