import hashlib
import json
import struct
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
    OpenScadFileRenderRequest,
    RenameEntryRequest,
    UpdateFileRequest,
)
from mato_loop_backend.execution.client import ExecutionClientError
from mato_loop_backend.models import ChatSession, PermissionGrant, UserAccount
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import InstanceLimits
from mato_loop_backend.workspace import FileRoot, RootFileService


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def _session_context(tmp_path: Path) -> tuple[workspace_route._FileContext, Path]:
    session_id = uuid4()
    storage = SessionStorage(tmp_path / "sessions")
    root = storage.provision(session_id)
    return (
        workspace_route._FileContext(
            root_files=RootFileService(session_id, session_storage=storage),
            root=FileRoot.SESSION,
        ),
        root,
    )


def test_openscad_render_request_accepts_frontend_revision_alias() -> None:
    request = OpenScadFileRenderRequest.model_validate(
        {"path": "model.scad", "expectedRevision": "a" * 64}
    )
    assert request.expected_revision == "a" * 64
    assert request.output == "preview"


@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        ("file.changed", 409),
        ("file.pathNotFound", 404),
        ("file.operationTooLarge", 413),
        ("openscad.renderFailed", 422),
        ("executionAgent.timeout", 504),
        ("executionAgent.protocolError", 502),
        ("executionAgent.unavailable", 503),
    ],
)
def test_openscad_render_maps_status_without_exposing_diagnostics(
    code: str, status_code: int
) -> None:
    error = workspace_route._openscad_render_api_error(
        ExecutionClientError(code, {"diagnostics": "private"})
    )
    assert error.status_code == status_code
    assert error.params is None


async def test_file_context_hides_foreign_sessions() -> None:
    user = _user()
    foreign = ChatSession(id=uuid4(), user_id=uuid4(), title="Foreign")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = foreign

    with pytest.raises(ApiError) as exc_info:
        await workspace_route._file_context(
            FileScope.SESSION,
            foreign.id,
            user,
            db,
            instance_limits=InstanceLimits(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "session.notFound"


async def test_filesystem_scope_without_agent_context_is_rejected() -> None:
    with pytest.raises(ApiError) as exc_info:
        await workspace_route._file_context(
            FileScope.FILESYSTEM,
            None,
            _user(),
            AsyncMock(spec=AsyncSession),
            instance_limits=InstanceLimits(),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.code == "file.workspaceNotSelected"


async def test_download_streams_the_bound_session_file(tmp_path: Path, monkeypatch) -> None:
    context, root = _session_context(tmp_path)
    (root / "files" / "report.bin").write_bytes(b"streamed report")

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    response = await workspace_route.download_file(
        _user(),
        AsyncMock(spec=AsyncSession),
        "files/report.bin",
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"streamed report"
    assert response.media_type == "application/octet-stream"
    assert "filename*=UTF-8''report.bin" in response.headers["content-disposition"]


async def test_session_preview_edit_and_image_validation(tmp_path: Path, monkeypatch) -> None:
    context, root = _session_context(tmp_path)
    markdown = b"# Title\n![Remote](https://example.com/x.png)"
    (root / "files" / "readme.md").write_bytes(markdown)
    image_data = BytesIO()
    Image.new("RGB", (2, 3), "red").save(image_data, format="PNG")
    (root / "files" / "image.png").write_bytes(image_data.getvalue())

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    db = AsyncMock(spec=AsyncSession)
    rows = MagicMock()
    rows.all.return_value = []
    db.scalars.return_value = rows
    user = _user()

    entries = await workspace_route.list_entries(
        user,
        db,
        path="files",
        offset=0,
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    preview = await workspace_route.preview_file(
        user,
        db,
        "files/readme.md",
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    image = await workspace_route.preview_image(
        user,
        db,
        "files/image.png",
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    updated = await workspace_route.update_file(
        UpdateFileRequest(
            path="files/readme.md",
            content="updated",
            expected_revision=hashlib.sha256(markdown).hexdigest(),
        ),
        user,
        db,
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )

    assert {entry["path"] for entry in entries["entries"]} == {
        "files/image.png",
        "files/readme.md",
    }
    assert preview["kind"] == "markdown"
    assert preview["revision"] == hashlib.sha256(markdown).hexdigest()
    assert image.media_type == "image/png"
    assert image.headers["cache-control"] == "private, no-store"
    assert updated["revision"] == hashlib.sha256(b"updated").hexdigest()
    revisions = context.root_files.list_revisions("files/readme.md")
    assert [item.revision for item in revisions] == [2, 1]
    with context.root_files.open_revision("files/readme.md", 1) as original:
        assert original.read() == markdown
    assert (root / "files" / "readme.md").read_text(encoding="utf-8") == "updated"


async def test_revision_api_deletes_old_versions_and_restores_history(
    tmp_path: Path, monkeypatch
) -> None:
    context, root = _session_context(tmp_path)
    service = context.root_files
    service.write_file(FileRoot.SESSION, "files/notes.txt", "one")
    service.write_file(
        FileRoot.SESSION,
        "files/notes.txt",
        "two",
        mode="replace",
        expected_sha256=hashlib.sha256(b"one").hexdigest(),
    )
    service.write_file(
        FileRoot.SESSION,
        "files/notes.txt",
        "three",
        mode="replace",
        expected_sha256=hashlib.sha256(b"two").hexdigest(),
    )

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    listed = await workspace_route.list_file_revisions(
        user, db, "files/notes.txt", service.session_id
    )
    preview = await workspace_route.preview_file_revision(
        1, user, db, "files/notes.txt", service.session_id
    )

    assert [item["revision"] for item in listed["items"]] == [3, 2, 1]
    assert preview["content"] == "one"
    await workspace_route.delete_file_revision(
        2, user, db, "files/notes.txt", service.session_id
    )
    with pytest.raises(ApiError) as current:
        await workspace_route.delete_file_revision(
            3, user, db, "files/notes.txt", service.session_id
        )
    restored = await workspace_route.restore_file_revision(
        1,
        workspace_route.RestoreRevisionRequest(path="files/notes.txt"),
        user,
        db,
        service.session_id,
    )

    assert current.value.code == "file.currentRevision"
    assert restored == {"revision": hashlib.sha256(b"one").hexdigest()}
    assert (root / "files" / "notes.txt").read_text(encoding="utf-8") == "one"
    assert [item.revision for item in service.list_revisions("files/notes.txt")] == [1]


async def test_preview_validates_stl_and_static_svg(tmp_path: Path, monkeypatch) -> None:
    context, root = _session_context(tmp_path)
    stl = bytes(80) + struct.pack("<I", 1) + struct.pack("<12fH", *(0.0,) * 12, 0)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    (root / "files" / "model.stl").write_bytes(stl)
    (root / "files" / "image.svg").write_bytes(svg)

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    stl_preview = await workspace_route.preview_file(user, db, "files/model.stl")
    stl_content = await workspace_route.preview_image(user, db, "files/model.stl")
    svg_preview = await workspace_route.preview_file(user, db, "files/image.svg")
    svg_content = await workspace_route.preview_image(user, db, "files/image.svg")

    assert stl_preview == {"kind": "stl"}
    assert stl_content.media_type == "model/stl"
    assert stl_content.body == stl
    assert svg_preview == {"kind": "image", "mimeType": "image/svg+xml"}
    assert svg_content.body == svg
    assert "default-src 'none'" in svg_content.headers["content-security-policy"]


async def test_rename_session_entry_clears_exact_grants(tmp_path: Path, monkeypatch) -> None:
    context, root = _session_context(tmp_path)
    session_id = context.root_files.session_id
    (root / "files" / "old.txt").write_text("old", encoding="utf-8")

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
    rows = MagicMock()
    rows.all.return_value = grants
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = rows

    response = await workspace_route.rename_entry(
        RenameEntryRequest(path="files/old.txt", name="renamed.txt"),
        _user(),
        db,
        scope=FileScope.SESSION,
        session_id=session_id,
    )

    assert response["entry"]["path"] == "files/renamed.txt"
    assert [call.args[0] for call in db.delete.await_args_list] == grants[:2]
    assert (root / "files" / "renamed.txt").read_text(encoding="utf-8") == "old"


@pytest.mark.parametrize(
    "svg",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>',
    ],
)
def test_svg_preview_rejects_active_and_external_content(svg: bytes) -> None:
    with pytest.raises(ValueError, match="unsafeSvg"):
        workspace_route._validate_svg(svg)
