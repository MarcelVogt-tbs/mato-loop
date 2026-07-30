from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    agent_identity_permission,
    media_permission,
    memory_write_permission,
    path_permission,
    session_permission,
    session_search_permission,
    webfetch_permission,
)
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.integrations import canonicalize_url
from mato_loop_backend.models import AgentRun, ChatSession, PermissionGrant
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AgentRuntimeSettings,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
)
from mato_loop_backend.workspace import RootFileService


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


def _root_files(tmp_path, session: ChatSession) -> RootFileService:
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    if session.working_directory:
        (tmp_path / session.working_directory).mkdir(parents=True, exist_ok=True)
    return RootFileService(
        session.id,
        session_storage=storage,
        workspace_home=tmp_path,
        working_directory=session.working_directory,
    )


def _path_request(
    root_files: RootFileService,
    path: str,
    *,
    root: str = "session",
    capability: str = "filesystem.read",
):
    return path_permission(
        root_files=root_files,
        root=root,
        capability=capability,
        relative_path=path,
        action_kind="read_file",
        action_label="Datei lesen",
    )


def test_root_path_grant_matches_only_same_root_identity_and_subtree(tmp_path) -> None:
    session = _session(working_directory="project")
    root_files = _root_files(tmp_path, session)
    granted = _path_request(root_files, "docs", root="workspace")
    grant = PermissionGrant(
        session_id=session.id,
        capability="filesystem.read",
        resource_type=granted.resource_type,
        resource_scope=granted.resource_scope,
    )

    assert PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "docs/readme.md", root="workspace"), root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "docs-other/readme.md", root="workspace"), root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "docs/readme.md", root="session"), root_files
    )


@pytest.mark.parametrize(
    ("root", "access", "capability", "expected"),
    [
        ("session", "read-only", "filesystem.read", PermissionDecision.ALLOW),
        ("workspace", "read-only", "filesystem.read", PermissionDecision.ALLOW),
        ("session", "read-only", "filesystem.write", PermissionDecision.DENY),
        ("workspace", "read-only", "filesystem.delete", PermissionDecision.DENY),
        ("session", "read-write", "filesystem.write", PermissionDecision.ALLOW),
        ("workspace", "read-write", "filesystem.delete", PermissionDecision.ALLOW),
    ],
)
async def test_file_access_is_decided_only_for_current_host_bound_roots(
    tmp_path, root: str, access: str, capability: str, expected: PermissionDecision
) -> None:
    session = _session(working_directory="project", workspace_access=access)
    root_files = _root_files(tmp_path, session)
    db = _db_with_grants()

    assert await PermissionPolicy.evaluate(
        db,
        session,
        _path_request(root_files, "files/notes.txt", root=root, capability=capability),
        root_files,
    ) == expected
    db.scalars.assert_not_awaited()


async def test_global_policy_can_require_or_deny_a_host_bound_file_action(tmp_path) -> None:
    session = _session(working_directory="project", workspace_access="read-write")
    root_files = _root_files(tmp_path, session)
    request = _path_request(
        root_files,
        "notes.txt",
        root="workspace",
        capability="filesystem.delete",
    )

    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, request, root_files, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, request, root_files, ToolExecutionPolicy.DISABLED
    ) == PermissionDecision.DENY


async def test_policy_revocation_while_waiting_for_approval_stops_action(
    tmp_path, monkeypatch
) -> None:
    session = _session(working_directory="project", workspace_access="read-write")
    root_files = _root_files(tmp_path, session)
    request = _path_request(
        root_files,
        "notes.txt",
        root="workspace",
        capability="filesystem.delete",
    )
    settings_loads = 0

    async def load_settings(_db, settings_type, **_kwargs):
        nonlocal settings_loads
        if settings_type is IntegrationSettings:
            return IntegrationSettings()
        settings_loads += 1
        return ToolSettings.model_validate(
            {
                "policies": {
                    "filesystem.delete": "ask" if settings_loads == 1 else "disabled"
                }
            }
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        AgentRuntime, "_request_approval", AsyncMock(return_value="allow_once")
    )
    db = _db_with_grants()

    decision = await AgentRuntime._authorize(
        db,
        AgentRun(id=uuid4(), session_id=session.id),
        session,
        ToolCall(id="delete", name="delete_path", arguments={}),
        request,
        root_files,
        AgentRuntimeSettings(),
    )

    assert decision == PermissionDecision.DENY


async def test_stale_or_missing_workspace_cannot_be_approved(tmp_path) -> None:
    session = _session(working_directory="project")
    selected = _root_files(tmp_path, session)
    request = _path_request(selected, "notes.txt", root="workspace")
    session.working_directory = None
    current = RootFileService(
        session.id,
        session_storage=selected.session_storage,
        workspace_home=tmp_path,
        working_directory=None,
    )

    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, request, current
    ) == PermissionDecision.DENY


async def test_root_scope_from_another_session_is_denied(tmp_path) -> None:
    session = _session()
    current = _root_files(tmp_path, session)
    other = _session()
    other_files = _root_files(tmp_path, other)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(),
        session,
        _path_request(other_files, "files/notes.txt"),
        current,
    ) == PermissionDecision.DENY


async def test_legacy_global_path_permission_is_hard_denied(tmp_path) -> None:
    session = _session(workspace_access="read-write")
    root_files = _root_files(tmp_path, session)
    request = PermissionRequest(
        capability="filesystem.read",
        resource_type="path",
        resource_scope="notes.txt",
        action_kind="read_file",
        risk_level="medium",
        title="Datei lesen?",
        summary="Legacy-Pfad",
    )

    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, request, root_files
    ) == PermissionDecision.DENY


async def test_current_session_read_is_allowed_without_querying_grants(tmp_path) -> None:
    session = _session()
    db = _db_with_grants()

    decision = await PermissionPolicy.evaluate(
        db,
        session,
        session_permission(session.id),
        _root_files(tmp_path, session),
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
        _root_files(tmp_path, session),
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
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        db, session, session_permission(granted_target), root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db, session, session_permission(uuid4()), root_files
    ) == PermissionDecision.ASK


async def test_media_session_grant_matches_capability_and_current_session(tmp_path) -> None:
    session = _session()
    request = media_permission(session.id, "media.generate.audio", "generate_audio")
    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type="session",
        resource_scope=str(session.id),
    )

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, _root_files(tmp_path, session)
    ) == PermissionDecision.ALLOW
    other_capability = media_permission(
        session.id, "media.generate.speech", "generate_speech"
    )
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, other_capability, _root_files(tmp_path, session)
    ) == PermissionDecision.ASK


async def test_memory_write_uses_current_session_grant(tmp_path) -> None:
    session = _session()
    request = memory_write_permission(session.id, "create")
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, request, root_files
    ) == PermissionDecision.ASK

    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type=request.resource_type,
        resource_scope=request.resource_scope,
    )
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        memory_write_permission(session.id, "update"),
        root_files,
    ) == PermissionDecision.ALLOW
    assert request.capability == "memory.write"
    assert request.action_kind == "memory_write"


async def test_agent_identity_session_grant_matches_exact_user(tmp_path) -> None:
    session = _session(user_id=uuid4())
    request = agent_identity_permission(
        session.user_id,
        changes_name=True,
        changes_instructions=False,
    )
    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type=request.resource_type,
        resource_scope=str(session.user_id),
    )
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, root_files
    ) == PermissionDecision.ALLOW
    other_user = agent_identity_permission(
        uuid4(),
        changes_name=True,
        changes_instructions=False,
    )
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, other_user, root_files
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
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(read_grant), session, session_search_permission(), root_files
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(search_grant), session, session_permission(target_id), root_files
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(search_grant), session, session_search_permission(), root_files
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
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, granted, root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        webfetch_permission("https://example.com/page?lang=en"),
        root_files,
    ) == PermissionDecision.ASK
