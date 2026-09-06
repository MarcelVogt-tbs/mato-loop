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


async def test_usage_reports_project_shared_root_and_effective_quota(
    tmp_path: Path, monkeypatch
) -> None:
    storage = SessionStorage(tmp_path / "sessions")
    project_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    storage.provision(first_id, project_id)
    storage.provision(second_id, project_id)
    first = RootFileService(first_id, session_storage=storage, project_id=project_id)
    second = RootFileService(
        second_id,
        session_storage=storage,
        project_id=project_id,
        session_quota_bytes=8 * 1024 * 1024,
    )
    first.write_file(FileRoot.SESSION, "shared.txt", "shared project data")
    expected_usage = second.session_usage()
    context = workspace_route._FileContext(root_files=second, root=FileRoot.SESSION)
    monkeypatch.setattr(workspace_route, "_file_context", AsyncMock(return_value=context))
    to_thread = AsyncMock(side_effect=lambda function, *args: function(*args))
    monkeypatch.setattr(workspace_route.asyncio, "to_thread", to_thread)

    response = await workspace_route.get_workspace_usage(
        _user(), AsyncMock(spec=AsyncSession), second_id
    )

    assert response.model_dump(by_alias=True) == {
        "scope": "project",
        "usedBytes": expected_usage,
        "quotaBytes": 8 * 1024 * 1024,
    }
    to_thread.assert_awaited_once_with(second.session_usage)


async def test_usage_hides_foreign_session(monkeypatch) -> None:
    user = _user()
    foreign = ChatSession(id=uuid4(), user_id=uuid4(), title="Foreign")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = foreign
    monkeypatch.setattr(
        "mato_loop_backend.api.workspace_service.SettingsStore.load",
        AsyncMock(return_value=InstanceLimits()),
    )

    with pytest.raises(ApiError) as exc_info:
        await workspace_route.get_workspace_usage(user, db, foreign.id)

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
        "report.bin",
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
        path=".",
        offset=0,
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    preview = await workspace_route.preview_file(
        user,
        db,
        "readme.md",
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    image = await workspace_route.preview_image(
        user,
        db,
        "image.png",
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )
    updated = await workspace_route.update_file(
        UpdateFileRequest(
            path="readme.md",
            content="updated",
            expected_revision=hashlib.sha256(markdown).hexdigest(),
        ),
        user,
        db,
        scope=FileScope.SESSION,
        session_id=context.root_files.session_id,
    )

    assert {entry["path"] for entry in entries["entries"]} == {
        "image.png",
        "readme.md",
    }
    assert preview["kind"] == "markdown"
    assert preview["revision"] == hashlib.sha256(markdown).hexdigest()
    assert image.media_type == "image/png"
    assert image.headers["cache-control"] == "private, no-store"
    assert updated["revision"] == hashlib.sha256(b"updated").hexdigest()
    revisions = context.root_files.list_revisions("readme.md")
    assert [item.revision for item in revisions] == [2, 1]
    with context.root_files.open_revision("readme.md", 1) as original:
        assert original.read() == markdown
    assert (root / "files" / "readme.md").read_text(encoding="utf-8") == "updated"


async def test_revision_api_restores_and_rejects_stale_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    context, root = _session_context(tmp_path)
    service = context.root_files
    service.write_file(FileRoot.SESSION, "notes.txt", "one")
    service.write_file(
        FileRoot.SESSION,
        "notes.txt",
        "two",
        mode="replace",
        expected_sha256=hashlib.sha256(b"one").hexdigest(),
    )
    service.write_file(
        FileRoot.SESSION,
        "notes.txt",
        "three",
        mode="replace",
        expected_sha256=hashlib.sha256(b"two").hexdigest(),
    )

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    clear_grants = AsyncMock()
    monkeypatch.setattr(workspace_route, "_clear_file_grants", clear_grants)
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    listed = await workspace_route.list_file_revisions(
        user, db, "notes.txt", service.session_id
    )
    preview = await workspace_route.preview_file_revision(
        1, user, db, "notes.txt", service.session_id
    )

    assert [item["revision"] for item in listed["items"]] == [3, 2, 1]
    assert preview["content"] == "one"
    current = listed["items"][0]
    payload = workspace_route.RevisionMutationRequest(
        path="notes.txt",
        fileId=listed["fileId"],
        expectedCurrentRevision=current["revision"],
    )
    restored = await workspace_route.restore_file_revision(
        1,
        payload,
        user,
        db,
        service.session_id,
    )

    assert restored.file_deleted is False
    assert restored.current_revision == 1
    assert restored.current_sha256 == hashlib.sha256(b"one").hexdigest()
    assert (root / "files" / "notes.txt").read_text(encoding="utf-8") == "one"
    assert [item.revision for item in service.list_revisions("notes.txt")] == [1]

    with pytest.raises(ApiError) as stale:
        await workspace_route.restore_file_revision(
            1, payload, user, db, service.session_id
        )
    assert stale.value.code == "file.changed"
    assert clear_grants.await_count == 1
    assert db.commit.await_count == 1

    service.write_file(
        FileRoot.SESSION, "notes.txt", "four", mode="replace",
        expected_sha256=hashlib.sha256(b"one").hexdigest(),
    )
    assert [item.revision for item in service.list_revisions("notes.txt")] == [4, 1]


@pytest.mark.parametrize("filename,kind", [
    ("notes.txt", "text"), ("notes.md", "markdown"), ("model.scad", "openscad"),
    ("image.png", "image"), ("image.svg", "image"), ("model.stl", "stl"),
])
async def test_historical_preview_and_content_use_exact_revision_bytes(
    tmp_path: Path, monkeypatch, filename: str, kind: str,
) -> None:
    context, _root = _session_context(tmp_path)
    service = context.root_files
    data = b"first\r\n"
    if filename.endswith(".png"):
        image = BytesIO()
        Image.new("RGB", (2, 2)).save(image, format="PNG")
        data = image.getvalue()
    elif filename.endswith(".svg"):
        data = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'
    elif filename.endswith(".stl"):
        data = bytes(80) + struct.pack("<I", 1) + struct.pack("<12fH", *(0.0,) * 12, 0)
    original = service.create_output_file(filename, data)
    service.append_output_revision(original.file_id, b"current bytes")
    get_context = AsyncMock(return_value=context)
    monkeypatch.setattr(workspace_route, "_file_context", get_context)
    user, db = _user(), AsyncMock(spec=AsyncSession)
    args = (1, user, db, original.path, service.session_id)
    kwargs = {"root_identity": str(service.session_id), "file_id": original.file_id}
    preview = await workspace_route.preview_file_revision(*args, **kwargs)
    assert preview["kind"] == kind
    get_context.assert_awaited_with(
        FileScope.SESSION, service.session_id, user, db, root_identity=str(service.session_id)
    )
    if kind in {"image", "stl"}:
        response = await workspace_route.content_file_revision(*args, **kwargs)
        assert response.body == data
        assert response.headers["x-content-type-options"] == "nosniff"
    else:
        assert preview["content"] == data.decode()
        assert preview["revision"] == hashlib.sha256(data).hexdigest()
    response = await workspace_route.download_file_revision(*args, **kwargs)
    assert b"".join([chunk async for chunk in response.body_iterator]) == data
    with pytest.raises(ApiError) as missing:
        await workspace_route.preview_file_revision(*args, **{**kwargs, "file_id": uuid4()})
    assert missing.value.code == "file.revisionNotFound"


async def test_historical_content_rejects_unsafe_svg(tmp_path: Path, monkeypatch) -> None:
    context, _root = _session_context(tmp_path)
    service = context.root_files
    revision = service.create_output_file(
        "unsafe.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    )
    monkeypatch.setattr(workspace_route, "_file_context", AsyncMock(return_value=context))
    args = (1, _user(), AsyncMock(spec=AsyncSession), revision.path, service.session_id)
    assert await workspace_route.preview_file_revision(*args) == {"kind": "none", "reason": "unsafeSvg"}
    with pytest.raises(ApiError) as unsafe:
        await workspace_route.content_file_revision(*args)
    assert unsafe.value.code == "file.previewUnavailable"


@pytest.mark.parametrize("endpoint", [
    workspace_route.preview_file_revision,
    workspace_route.content_file_revision,
    workspace_route.download_file_revision,
])
async def test_historical_endpoints_reject_foreign_owners_and_roots(monkeypatch, endpoint) -> None:
    user = _user()
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Foreign")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    monkeypatch.setattr(
        "mato_loop_backend.api.workspace_service.SettingsStore.load",
        AsyncMock(return_value=InstanceLimits()),
    )
    with pytest.raises(ApiError) as foreign:
        await endpoint(1, user, db, "notes.md", session.id, file_id=uuid4())
    assert foreign.value.code == "session.notFound"
    session.user_id = user.id
    with pytest.raises(ApiError) as wrong_root:
        await endpoint(1, user, db, "notes.md", session.id, root_identity=str(uuid4()), file_id=uuid4())
    assert wrong_root.value.code == "file.fileNotFound"


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

    stl_preview = await workspace_route.preview_file(user, db, "model.stl")
    stl_content = await workspace_route.preview_image(user, db, "model.stl")
    svg_preview = await workspace_route.preview_file(user, db, "image.svg")
    svg_content = await workspace_route.preview_image(user, db, "image.svg")

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
        for path in ("old.txt", "renamed.txt", "other.txt")
    ]
    rows = MagicMock()
    rows.all.return_value = grants
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = rows

    response = await workspace_route.rename_entry(
        RenameEntryRequest(path="old.txt", name="renamed.txt"),
        _user(),
        db,
        scope=FileScope.SESSION,
        session_id=session_id,
    )

    assert response["entry"]["path"] == "renamed.txt"
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
