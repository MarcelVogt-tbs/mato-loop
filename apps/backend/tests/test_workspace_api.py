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
from mato_loop_backend.workspace import FileRoot, RootFileService, WorkspaceService


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


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
        ExecutionClientError(
            code,
            {"diagnostics": "private diagnostic", "diagnosticsTruncated": False},
        )
    )
    assert error.status_code == status_code
    assert error.params is None


async def test_file_context_hides_foreign_sessions() -> None:
    user = _user()
    foreign = ChatSession(id=uuid4(), user_id=uuid4(), title="Foreign")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = foreign

    with pytest.raises(ApiError) as exc_info:
        await workspace_route._file_context(FileScope.SESSION, foreign.id, user, db)

    assert exc_info.value.status_code == 404
    assert exc_info.value.code == "session.notFound"


async def test_download_file_streams_the_bound_workspace_file(
    tmp_path: Path, monkeypatch
) -> None:
    session_id = uuid4()
    project = tmp_path / "original"
    project.mkdir()
    (project / "report.bin").write_bytes(b"streamed report")
    context = workspace_route._FileContext(
        root_files=RootFileService(
            session_id,
            workspace_home=tmp_path,
            working_directory="original",
        ),
        root=FileRoot.WORKSPACE,
        working_directory="original",
    )

    async def file_context(*_args, **kwargs):
        assert kwargs["root_identity"] == "original"
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)

    response = await workspace_route.download_file(
        _user(),
        AsyncMock(spec=AsyncSession),
        "report.bin",
        scope=FileScope.FILESYSTEM,
        session_id=session_id,
        root_identity="original",
    )
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"streamed report"
    assert response.media_type == "application/octet-stream"
    assert "filename*=UTF-8''report.bin" in response.headers["content-disposition"]


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

    root_identities = []

    async def file_context(*_args, **kwargs):
        root_identities.append(kwargs.get("root_identity"))
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    entries = await workspace_route.list_entries(
        user, db, path=".", offset=0, root_identity="original"
    )
    preview = await workspace_route.preview_file(
        user, db, "readme.md", root_identity="original"
    )
    image = await workspace_route.preview_image(
        user, db, "image.png", root_identity="original"
    )

    assert {entry["path"] for entry in entries["entries"]} == {"image.png", "readme.md"}
    assert root_identities == ["original", "original", "original"]
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


async def test_preview_classifies_editable_scad_and_serves_validated_stl(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = WorkspaceService(tmp_path)
    scad = b"cube([1, 2, 3]);\n"
    stl = bytes(80) + struct.pack("<I", 1) + struct.pack("<12fH", *(0.0,) * 12, 0)
    ascii_stl = b"solid model\nendsolid model\n"
    (tmp_path / "model.scad").write_bytes(scad)
    (tmp_path / "model.stl").write_bytes(stl)
    (tmp_path / "ascii.stl").write_bytes(ascii_stl)
    context = workspace_route._FileContext(workspace=workspace)

    async def file_context(*_args, **_kwargs):
        return context

    monkeypatch.setattr(workspace_route, "_file_context", file_context)
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    scad_preview = await workspace_route.preview_file(user, db, "model.scad")
    stl_preview = await workspace_route.preview_file(user, db, "model.stl")
    ascii_preview = await workspace_route.preview_file(user, db, "ascii.stl")
    content = await workspace_route.preview_image(user, db, "model.stl")

    assert scad_preview == {
        "kind": "openscad",
        "content": scad.decode(),
        "encoding": "utf-8",
        "revision": hashlib.sha256(scad).hexdigest(),
    }
    assert stl_preview == {"kind": "stl"}
    assert ascii_preview == {"kind": "none", "reason": "unsupported"}
    assert content.media_type == "model/stl"
    assert content.body == stl
    assert content.headers["cache-control"] == "private, no-store"


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
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>path { animation-name: pulse; }</style></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>@-webkit-keyframes pulse { from { opacity: 0; } }</style></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg"><style>path { transition-duration: 1s; }</style></svg>',
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
