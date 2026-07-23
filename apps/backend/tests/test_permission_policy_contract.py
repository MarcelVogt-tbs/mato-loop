from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionPolicy,
    path_permission,
    session_permission,
    session_search_permission,
    webfetch_permission,
)
from mato_loop_backend.integrations import canonicalize_url
from mato_loop_backend.models import ChatSession, PermissionGrant
from mato_loop_backend.workspace import WorkspaceService


def _session(**values) -> ChatSession:
    defaults = {
        "id": uuid4(),
        "title": "Test",
        "memory_mode": "disabled",
        "web_search_policy": "available",
        "working_directory": None,
        "workspace_access": "read-only",
    }
    return ChatSession(**(defaults | values))


def _db_with_grants(*grants: PermissionGrant) -> AsyncMock:
    result = Mock()
    result.all.return_value = list(grants)
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = result
    return db


def _read_path(path: str):
    return path_permission(
        capability="filesystem.read",
        relative_path=path,
        action_kind="read_file",
        action_label="Datei lesen",
    )


@pytest.mark.parametrize(
    ("resource_scope", "expected"),
    [
        ("project", PermissionDecision.ALLOW),
        ("project/docs/readme.md", PermissionDecision.ALLOW),
        ("project-other/readme.md", PermissionDecision.ASK),
    ],
)
async def test_path_grant_matches_exact_scope_and_subtree_only(
    tmp_path, resource_scope: str, expected: PermissionDecision
) -> None:
    session = _session()
    workspace = WorkspaceService(tmp_path)
    grant = PermissionGrant(
        session_id=session.id,
        capability="filesystem.read",
        resource_type="path",
        resource_scope="project",
    )
    db = _db_with_grants(grant)

    assert await PermissionPolicy.evaluate(
        db, session, _read_path(resource_scope), workspace
    ) == expected


@pytest.mark.parametrize(
    ("working_directory", "resource_scope", "expected"),
    [
        ("project", "project/notes.txt", PermissionDecision.ALLOW),
        ("project", "project/docs/readme.md", PermissionDecision.ALLOW),
        ("project", "other/notes.txt", PermissionDecision.ASK),
        ("project", "project-other/notes.txt", PermissionDecision.ASK),
        (None, "notes.txt", PermissionDecision.ASK),
    ],
)
async def test_read_access_is_automatic_only_inside_workdir(
    tmp_path, working_directory: str | None, resource_scope: str, expected: PermissionDecision
) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "project").mkdir()
    session = _session(working_directory=working_directory)
    db = _db_with_grants()

    assert await PermissionPolicy.evaluate(
        db, session, _read_path(resource_scope), workspace
    ) == expected


async def test_trailing_parent_segment_is_not_treated_as_inside_workdir(tmp_path) -> None:
    workspace = WorkspaceService(tmp_path)
    (tmp_path / "project").mkdir()
    session = _session(working_directory="project")

    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, _read_path("project/.."), workspace
    ) == PermissionDecision.ASK


async def test_current_session_read_is_allowed_without_querying_grants(tmp_path) -> None:
    session = _session()
    db = _db_with_grants()

    decision = await PermissionPolicy.evaluate(
        db,
        session,
        session_permission(session.id),
        WorkspaceService(tmp_path),
    )

    assert decision == PermissionDecision.ALLOW
    db.scalars.assert_not_awaited()


async def test_other_session_read_requires_approval_without_matching_grant(tmp_path) -> None:
    session = _session()
    db = _db_with_grants()

    decision = await PermissionPolicy.evaluate(
        db,
        session,
        session_permission(uuid4()),
        WorkspaceService(tmp_path),
    )

    assert decision == PermissionDecision.ASK
    db.scalars.assert_awaited_once()


async def test_session_grant_only_allows_its_exact_target(tmp_path) -> None:
    session = _session()
    granted_target = uuid4()
    grant = PermissionGrant(
        session_id=session.id,
        capability="session.read",
        resource_type="session",
        resource_scope=str(granted_target),
    )
    db = _db_with_grants(grant)
    workspace = WorkspaceService(tmp_path)

    assert await PermissionPolicy.evaluate(
        db, session, session_permission(granted_target), workspace
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db, session, session_permission(uuid4()), workspace
    ) == PermissionDecision.ASK


async def test_session_search_and_session_read_grants_remain_separate(tmp_path) -> None:
    session = _session()
    target_id = uuid4()
    read_grant = PermissionGrant(
        session_id=session.id,
        capability="session.read",
        resource_type="session",
        resource_scope=str(target_id),
    )
    search_grant = PermissionGrant(
        session_id=session.id,
        capability="session.search",
        resource_type="session_collection",
        resource_scope="all",
    )
    workspace = WorkspaceService(tmp_path)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(read_grant), session, session_search_permission(), workspace
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(search_grant), session, session_permission(target_id), workspace
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(search_grant), session, session_search_permission(), workspace
    ) == PermissionDecision.ALLOW


def test_webfetch_permission_hashes_the_full_canonical_url_and_redacts_summary() -> None:
    first = webfetch_permission("HTTPS://Example.com/path?token=secret#fragment")
    equivalent = webfetch_permission("https://example.com/path?token=secret")
    other_query = webfetch_permission("https://example.com/path?token=other")

    assert first.capability == "network.fetch"
    assert first.resource_type == "url"
    assert first.resource_scope == equivalent.resource_scope
    assert first.resource_scope != other_query.resource_scope
    assert len(first.resource_scope) == 64
    assert "?REDACTED" in first.summary
    assert "secret" not in first.summary
    assert first.resource_display == "https://example.com/path?REDACTED"
    assert canonicalize_url("https://example.com/path?token=secret") not in first.summary


async def test_webfetch_grant_matches_only_the_exact_url_hash(tmp_path) -> None:
    session = _session()
    granted = webfetch_permission("https://example.com/page?lang=de")
    grant = PermissionGrant(
        session_id=session.id,
        capability=granted.capability,
        resource_type=granted.resource_type,
        resource_scope=granted.resource_scope,
    )
    workspace = WorkspaceService(tmp_path)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, granted, workspace
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        webfetch_permission("https://example.com/page?lang=en"),
        workspace,
    ) == PermissionDecision.ASK
