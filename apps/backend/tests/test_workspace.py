from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tools import permission_for
from mato_loop_backend.models import ChatSession
from mato_loop_backend.workspace import WorkspaceError, WorkspaceService


def test_workspace_manages_directories_and_files(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)

    created = workspace.create_directory(".", "project")
    written = workspace.write_file("notes.txt", "hello", base="project")

    assert created["path"] == "project"
    assert written == "project/notes.txt"
    result = workspace.read_file("notes.txt", base="project", offset=1, limit=2_000)

    assert result.content == "1: hello"
    assert result.lines_read == 1
    assert result.has_more is False
    assert [item["name"] for item in workspace.list_entries("project")] == ["notes.txt"]

    workspace.delete("project")
    assert not (tmp_path / "project").exists()


def test_workspace_rejects_escape_and_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError):
        workspace.resolve("../outside")
    with pytest.raises(WorkspaceError):
        workspace.resolve("link/secret.txt")
    with pytest.raises(WorkspaceError) as exc_info:
        workspace.list_entries("link")

    assert exc_info.value.code == "workspace.pathOutsideHome"

    workspace.delete("link")
    assert outside.exists()


def test_workspace_lists_final_directory_symlink_within_home(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "notes.txt").write_text("hello", encoding="utf-8")
    (tmp_path / "link").symlink_to(target, target_is_directory=True)

    entries = workspace.list_entries("link")

    assert [(entry["name"], entry["path"]) for entry in entries] == [
        ("notes.txt", "link/notes.txt")
    ]


def test_workspace_resolves_read_symlink_to_canonical_target(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    project = tmp_path / "project"
    private = tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (project / "link").symlink_to(private, target_is_directory=True)

    resolved = workspace.resolve_read_target("link", base="project")

    assert workspace.relative(resolved) == "private"


def test_workspace_rejects_read_symlink_outside_home(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-read"
    outside.mkdir(exist_ok=True)
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkspaceError) as exc_info:
        workspace.resolve_read_target("link")

    assert exc_info.value.code == "workspace.pathOutsideHome"


def test_workspace_normalizes_trailing_parent_segment(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "project").mkdir()

    assert workspace.resolve("..", base="project") == tmp_path


def test_workspace_reads_paginated_numbered_lines(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")

    result = workspace.read_file("notes.txt", base=None, offset=2, limit=1)

    assert result.content == "2: two"
    assert result.lines_read == 1
    assert result.has_more is True
    assert result.next_offset == 3


def test_workspace_truncates_long_lines_without_splitting_them(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "notes.txt").write_text("abcdefghij\nnext", encoding="utf-8")

    result = workspace.read_file(
        "notes.txt", base=None, offset=1, limit=2, max_line_length=5
    )

    assert result.content == "1: abcde\n2: next"
    assert result.truncated_lines == (1,)
    assert result.has_more is False


def test_workspace_returns_stable_error_for_invalid_utf8(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "binary.txt").write_bytes(b"\xff")

    with pytest.raises(WorkspaceError) as exc_info:
        workspace.read_file("binary.txt", base=None, offset=1, limit=2_000)

    assert exc_info.value.code == "workspace.invalidEncoding"


def test_workspace_cannot_delete_home(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)

    with pytest.raises(WorkspaceError):
        workspace.delete(".")


async def test_write_mode_without_workdir_still_requires_permission(tmp_path: Path) -> None:
    workspace = WorkspaceService(tmp_path)
    permission = permission_for(
        "write_file",
        {"path": "notes.txt", "content": "hello"},
        workspace,
        None,
    )
    chat_session = ChatSession(
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory=None,
        workspace_access="read-write",
    )
    grants = MagicMock()
    grants.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = grants

    assert permission is not None
    assert await AgentRuntime._is_allowed(db, chat_session, permission, workspace) is False
