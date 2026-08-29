from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    agent_identity_permission,
    automation_collection_scope,
    automation_scope,
    execution_directory_permission,
    integration_operation_permission,
    media_permission,
    memory_write_permission,
    path_permission,
    project_session_path_permission,
    session_browse_permission,
    session_permission,
    session_search_permission,
    skill_edit_permission,
    skill_load_permission,
    skill_write_permission,
    transcript_permission,
    webfetch_permission,
)
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tools import (
    ToolAction,
    resolve_tool_execution_policy,
    tool_registry,
)
from mato_loop_backend.integrations import canonicalize_url
from mato_loop_backend.models import AgentRun, ChatSession, ExecutionAgent, PermissionGrant
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    IntegrationInstance,
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
    }
    return ChatSession(**(defaults | values))


def _db_with_grants(*grants: PermissionGrant) -> AsyncMock:
    result = Mock()
    result.all.return_value = list(grants)
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = result
    return db


def _tool_settings(data: dict) -> ToolSettings:
    values = dict(data)
    permissions = {
        key: values.pop(key)
        for key in ("policies", "integrationPolicies", "resourcePolicies")
        if key in values
    }
    values["publicPermissions"] = permissions
    return ToolSettings.model_validate(values)


def _root_files(tmp_path, session: ChatSession) -> RootFileService:
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    return RootFileService(
        session.id,
        session_storage=storage,
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


async def test_coding_permission_is_scoped_to_current_project(tmp_path) -> None:
    project_id = uuid4()
    session = _session(user_id=uuid4(), project_id=project_id)
    root_files = _root_files(tmp_path, session)
    db = _db_with_grants()
    request = PermissionRequest(
        capability="coding.analyze",
        resource_type="project",
        resource_scope=str(project_id),
        action_kind="coding.analyze",
        risk_level="medium",
        title="Analyze?",
        summary="Analyze project.",
    )

    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.AUTO
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db,
        session,
        replace(request, resource_scope=str(uuid4())),
        root_files,
        ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.DENY


def test_root_path_grant_matches_only_same_root_identity_and_subtree(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    granted = _path_request(root_files, "files/docs")
    grant = PermissionGrant(
        session_id=session.id,
        capability="filesystem.read",
        resource_type=granted.resource_type,
        resource_scope=granted.resource_scope,
    )

    assert PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "files/docs/readme.md"), root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "files/docs-other/readme.md"), root_files
    )


def test_execution_directory_grant_matches_only_same_agent_subtree(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    agent_id = uuid4()
    granted = execution_directory_permission(
        agent_id, "/home/user/project"
    )
    grant = PermissionGrant(
        session_id=session.id,
        capability=granted.capability,
        resource_type=granted.resource_type,
        resource_scope=granted.resource_scope,
    )

    assert PermissionPolicy.grant_matches(
        grant,
        execution_directory_permission(
            agent_id, "/home/user/project/src"
        ),
        root_files,
    )
    assert not PermissionPolicy.grant_matches(
        grant,
        execution_directory_permission(
            agent_id, "/home/user/project-other"
        ),
        root_files,
    )
    assert not PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "docs/readme.md", root="session"), root_files
    )


@pytest.mark.parametrize(
    ("path", "home_policy", "external_policy", "expected"),
    [
        ("/home/user/project", "auto", "deny", PermissionDecision.ALLOW),
        ("/home/user-other", "auto", "deny", PermissionDecision.DENY),
        ("/srv/project", "auto", "ask", PermissionDecision.ASK),
    ],
)
async def test_execution_filesystem_policy_uses_home_boundary(
    tmp_path, path, home_policy, external_policy, expected
) -> None:
    session = _session(user_id=uuid4())
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=session.user_id,
        alias="Desktop",
        home_directory="/home/user",
        home_filesystem_policy=home_policy,
        external_filesystem_policy=external_policy,
    )
    db = _db_with_grants()
    db.scalar.return_value = agent
    request = execution_directory_permission(
        agent.id, path, capability="filesystem.read"
    )

    assert await PermissionPolicy.evaluate(db, session, request, root_files) == expected


async def test_automation_scopes_are_canonical_current_session_and_revision_exact(
    tmp_path,
) -> None:
    session = _session(user_id=uuid4())
    root_files = _root_files(tmp_path, session)
    automation_id = uuid4()
    request = PermissionRequest(
        capability="automation.update",
        resource_type="automation",
        resource_scope=automation_scope(
            automation_id, 4, session.user_id, session.id
        ),
        action_kind="edit_automation",
        risk_level="medium",
        title="Automation ändern?",
        summary="Test",
    )
    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type=request.resource_type,
        resource_scope=request.resource_scope,
    )

    assert PermissionPolicy.grant_matches(grant, request, root_files)
    changed = replace(
        request,
        resource_scope=automation_scope(
            automation_id, 5, session.user_id, session.id
        ),
    )
    assert not PermissionPolicy.grant_matches(grant, changed, root_files)
    wrong_session = replace(
        request,
        resource_scope=automation_scope(
            automation_id, 4, session.user_id, uuid4()
        ),
    )
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        wrong_session,
        root_files,
        ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.DENY
    malformed = replace(request, resource_scope='{"revision":4}')
    assert await PermissionPolicy.evaluate(
        _db_with_grants(),
        session,
        malformed,
        root_files,
        ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.DENY
    collection = automation_collection_scope(session.user_id, session.id)
    assert collection == (
        f'{{"sessionId":"{session.id}","userId":"{session.user_id}"}}'
    )


@pytest.mark.parametrize(
    ("root", "capability"),
    [
        ("session", "filesystem.read"),
        ("session", "filesystem.write"),
    ],
)
async def test_file_access_is_decided_only_for_current_host_bound_roots(
    tmp_path, root: str, capability: str
) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    db = _db_with_grants()

    assert await PermissionPolicy.evaluate(
        db,
        session,
        _path_request(root_files, "files/notes.txt", root=root, capability=capability),
        root_files,
    ) == PermissionDecision.ALLOW
    db.scalars.assert_not_awaited()


async def test_global_policy_can_require_or_deny_a_host_bound_file_action(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    request = _path_request(
        root_files,
        "files/notes.txt",
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
    session = _session()
    root_files = _root_files(tmp_path, session)
    request = _path_request(
        root_files,
        "files/notes.txt",
        capability="filesystem.delete",
    )
    settings_loads = 0

    async def load_settings(_db, settings_type, **_kwargs):
        nonlocal settings_loads
        if settings_type is IntegrationSettings:
            return IntegrationSettings()
        settings_loads += 1
        return _tool_settings(
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
    )

    assert decision == PermissionDecision.DENY


async def test_skill_policy_revocation_while_waiting_stops_loading(
    tmp_path, monkeypatch
) -> None:
    session = _session(user_id=uuid4())
    request = skill_load_permission(session.user_id, "Recherche")
    settings_loads = 0

    async def load_settings(_db, settings_type, **_kwargs):
        nonlocal settings_loads
        if settings_type is IntegrationSettings:
            return IntegrationSettings()
        settings_loads += 1
        return _tool_settings(
            {
                "policies": {
                    "skills.load": "ask" if settings_loads == 1 else "disabled"
                }
            }
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        AgentRuntime, "_request_approval", AsyncMock(return_value="allow_once")
    )

    decision = await AgentRuntime._authorize(
        _db_with_grants(),
        AgentRun(id=uuid4(), session_id=session.id),
        session,
        ToolCall(id="load", name="load_skill", arguments={}),
        request,
        _root_files(tmp_path, session),
    )

    assert decision == PermissionDecision.DENY


async def test_runtime_uses_bound_integration_resource_policy(
    tmp_path, monkeypatch
) -> None:
    session = _session()
    integration_id = uuid4()
    integration = IntegrationInstance(
        id=integration_id,
        name="Kalender",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    request = PermissionRequest(
        capability="network.search",
        resource_type="network_service",
        resource_scope="calendar-search",
        action_kind="calendar_search",
        risk_level="medium",
        title="Kalender durchsuchen?",
        summary="Der Agent möchte einen Kalender durchsuchen.",
        policy_integration_id=integration_id,
        policy_resource_key="calendar:work",
    )
    tools = _tool_settings(
        {
            "policies": {"network.search": "ask"},
            "resourcePolicies": {
                str(integration_id): {
                    "calendar:work": {"network.search": "auto"}
                }
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return (
            IntegrationSettings(instances=[integration])
            if settings_type is IntegrationSettings
            else tools
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        tool_registry,
        "integration_resource_actions",
        Mock(
            return_value=(
                ToolAction("network.search", "Suchen", ToolExecutionPolicy.AUTO),
            )
        ),
    )
    request_approval = AsyncMock(side_effect=AssertionError("Approval must not be requested"))
    monkeypatch.setattr(AgentRuntime, "_request_approval", request_approval)

    decision = await AgentRuntime._authorize(
        _db_with_grants(),
        AgentRun(id=uuid4(), session_id=session.id),
        session,
        ToolCall(id="search", name="calendar_search", arguments={}),
        request,
        _root_files(tmp_path, session),
    )

    assert decision == PermissionDecision.ALLOW
    request_approval.assert_not_awaited()


@pytest.mark.parametrize(
    ("global_policy", "expected", "approval_count"),
    (
        (None, PermissionDecision.ALLOW, 1),
        ("disabled", PermissionDecision.DENY, 0),
    ),
)
async def test_runtime_discovery_uses_global_not_integration_policy(
    tmp_path, monkeypatch, global_policy, expected, approval_count
) -> None:
    session = _session()
    integration = IntegrationInstance(
        name="Kalender",
        integration_type="calendar",
        adapter_type="caldav",
        config={
            "base_url": "https://calendar.example.com",
            "username": "user",
        },
    )
    request = integration_operation_permission(
        capability="calendar.read",
        integration_id=integration.id,
        binding_revision="{}",
        operation_key="list-calendars",
        title="Kalender auflisten?",
        summary="Der Agent möchte Kalender auflisten.",
        use_integration_policy=False,
    )
    settings_data = {
        "integrationPolicies": {
            str(integration.id): {"calendar.read": "disabled"}
        }
    }
    if global_policy is not None:
        settings_data["policies"] = {"calendar.read": global_policy}
    tools = _tool_settings(settings_data)

    async def load_settings(_db, settings_type, **_kwargs):
        return (
            IntegrationSettings(instances=[integration])
            if settings_type is IntegrationSettings
            else tools
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    request_approval = AsyncMock(return_value="allow_once")
    monkeypatch.setattr(AgentRuntime, "_request_approval", request_approval)

    decision = await AgentRuntime._authorize(
        _db_with_grants(),
        AgentRun(id=uuid4(), session_id=session.id),
        session,
        ToolCall(id="list", name="calendar_list_calendars", arguments={}),
        request,
        _root_files(tmp_path, session),
    )

    assert request.policy_integration_id is None
    assert decision == expected
    assert request_approval.await_count == approval_count


async def test_runtime_ignores_override_after_resource_contract_removal(
    tmp_path, monkeypatch
) -> None:
    session = _session()
    integration = IntegrationInstance(
        name="Kalender",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    request = PermissionRequest(
        capability="network.search",
        resource_type="network_service",
        resource_scope="calendar-search",
        action_kind="calendar_search",
        risk_level="medium",
        title="Kalender durchsuchen?",
        summary="Der Agent möchte einen Kalender durchsuchen.",
        policy_integration_id=integration.id,
        policy_resource_key="calendar:work",
    )
    tools = _tool_settings(
        {
            "policies": {"network.search": "ask"},
            "resourcePolicies": {
                str(integration.id): {
                    "calendar:work": {"network.search": "auto"}
                }
            },
        }
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return (
            IntegrationSettings(instances=[integration])
            if settings_type is IntegrationSettings
            else tools
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        AgentRuntime, "_request_approval", AsyncMock(return_value="deny")
    )

    decision = await AgentRuntime._authorize(
        _db_with_grants(),
        AgentRun(id=uuid4(), session_id=session.id),
        session,
        ToolCall(id="search", name="calendar_search", arguments={}),
        request,
        _root_files(tmp_path, session),
    )

    assert decision == PermissionDecision.DENY
    AgentRuntime._request_approval.assert_not_awaited()


def test_integration_policy_precedence_allowed_modes_and_hard_limits() -> None:
    integration_id = uuid4()
    action = ToolAction(
        "tasks.read",
        "Aufgaben lesen",
        ToolExecutionPolicy.ASK,
        allowed_policies=(
            ToolExecutionPolicy.DISABLED,
            ToolExecutionPolicy.ASK,
            ToolExecutionPolicy.AUTO,
        ),
    )
    settings = _tool_settings(
        {
            "policies": {action.capability: "ask"},
            "integrationPolicies": {
                str(integration_id): {action.capability: "auto"}
            },
            "resourcePolicies": {
                str(integration_id): {
                    "project:main": {action.capability: "ask"}
                }
            },
        }
    )

    integration = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy,
        allowed_policies=action.allowed_policies,
        integration_id=integration_id,
    )
    resource = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy,
        allowed_policies=action.allowed_policies,
        integration_id=integration_id,
        resource_key="project:main",
    )
    hard_limit = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy,
        allowed_policies=action.allowed_policies,
        integration_id=integration_id,
        resource_key="project:main",
        hard_unavailable_capabilities=frozenset({action.capability}),
    )

    assert (integration.policy, integration.source) == (
        ToolExecutionPolicy.AUTO,
        "integration",
    )
    assert (resource.policy, resource.source) == (
        ToolExecutionPolicy.ASK,
        "resource",
    )
    assert (hard_limit.policy, hard_limit.reason) == (
        ToolExecutionPolicy.DISABLED,
        "hard_unavailable",
    )

    settings.permissions.policies[action.capability] = ToolExecutionPolicy.DISABLED
    overridden_disabled = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy,
        integration_id=integration_id,
        resource_key="project:main",
    )
    assert (overridden_disabled.policy, overridden_disabled.source) == (
        ToolExecutionPolicy.ASK,
        "resource",
    )

    restricted = ToolAction(
        "tasks.delete",
        "Aufgaben löschen",
        ToolExecutionPolicy.ASK,
        allowed_policies=(ToolExecutionPolicy.DISABLED, ToolExecutionPolicy.ASK),
    )
    settings.permissions.policies[restricted.capability] = ToolExecutionPolicy.AUTO
    failed_closed = resolve_tool_execution_policy(
        settings,
        restricted.capability,
        restricted.default_policy,
        allowed_policies=restricted.allowed_policies,
    )
    assert (failed_closed.policy, failed_closed.reason) == (
        ToolExecutionPolicy.DISABLED,
        "policy_not_allowed",
    )

    settings.permissions.policies[action.capability] = ToolExecutionPolicy.ASK
    settings.permissions.integration_policies[str(integration_id)] = {
        action.capability: "invalid"  # type: ignore[dict-item]
    }
    invalid = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy,
        integration_id=integration_id,
        resource_key="project:main",
    )
    assert (invalid.policy, invalid.reason) == (
        ToolExecutionPolicy.DISABLED,
        "invalid_policy",
    )


def test_integration_operation_grants_match_only_exact_canonical_scope(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    integration_id = uuid4()
    revision = '{"adapter_type":"synthetic","enabled":true}'

    def request(
        *,
        selected_id=integration_id,
        selected_revision=revision,
        resource_key="project:main",
        operation_key="read:8d7f",
    ):
        return integration_operation_permission(
            capability="tasks.read",
            integration_id=selected_id,
            binding_revision=selected_revision,
            resource_key=resource_key,
            operation_key=operation_key,
            title="Aufgaben lesen?",
            summary="Der Agent möchte Aufgaben lesen.",
        )

    granted = request()
    grant = PermissionGrant(
        session_id=session.id,
        capability=granted.capability,
        resource_type=granted.resource_type,
        resource_scope=granted.resource_scope,
    )

    assert PermissionPolicy.grant_matches(grant, granted, root_files)
    assert not PermissionPolicy.grant_matches(
        grant, request(selected_id=uuid4()), root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant,
        request(selected_revision='{"adapter_type":"other","enabled":true}'),
        root_files,
    )
    assert not PermissionPolicy.grant_matches(
        grant, request(resource_key="project:other"), root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant, request(operation_key="read:other"), root_files
    )


async def test_stale_or_missing_workspace_cannot_be_approved(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)

    with pytest.raises(ValueError, match="No working directory is selected"):
        _path_request(root_files, "notes.txt", root="workspace")


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
    session = _session()
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


async def test_same_project_session_reads_are_automatic_but_can_be_disabled(tmp_path) -> None:
    project_id = uuid4()
    session = _session(user_id=uuid4(), project_id=project_id, session_type="chat")
    target = _session(user_id=session.user_id, project_id=project_id, session_type="chat")
    db = _db_with_grants()
    db.get.return_value = target
    root_files = _root_files(tmp_path, session)
    path_request = project_session_path_permission(
        project_id=project_id,
        session_id=target.id,
        relative_path="files/notes.txt",
        action_kind="read",
        action_label="Datei lesen",
    )

    assert await PermissionPolicy.evaluate(
        db, session, session_permission(target.id), root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db, session, path_request, root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db,
        session,
        path_request,
        root_files,
        ToolExecutionPolicy.DISABLED,
    ) == PermissionDecision.DENY


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


async def test_media_model_grant_matches_only_exact_target(tmp_path) -> None:
    session = _session()
    provider_id = uuid4()
    model_id = uuid4()
    request = media_permission(
        session.id,
        "media.generate.image",
        "generate_image",
        provider_id=provider_id,
        model_id=model_id,
        model_name="Image",
        trust_level="public",
    )
    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type=request.resource_type,
        resource_scope=request.resource_scope,
    )
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        media_permission(
            session.id,
            "media.generate.image",
            "generate_image",
            provider_id=provider_id,
            model_id=uuid4(),
            model_name="Other image",
            trust_level="public",
        ),
        root_files,
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

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        memory_write_permission(session.id, "forget"),
        root_files,
    ) == PermissionDecision.ASK


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


async def test_skill_collection_grant_matches_exact_user_and_capability(tmp_path) -> None:
    session = _session(user_id=uuid4())
    request = skill_load_permission(session.user_id, "Recherche")
    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type=request.resource_type,
        resource_scope=request.resource_scope,
    )
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        skill_load_permission(uuid4(), "Fremd"),
        root_files,
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        skill_write_permission(session.user_id, "Recherche"),
        root_files,
    ) == PermissionDecision.ASK


async def test_skill_edit_grant_matches_only_exact_skill(tmp_path) -> None:
    session = _session(user_id=uuid4())
    skill_id = uuid4()
    request = skill_edit_permission(skill_id, "Songfinder")
    grant = PermissionGrant(
        session_id=session.id,
        capability=request.capability,
        resource_type=request.resource_type,
        resource_scope=request.resource_scope,
    )
    collection_grant = PermissionGrant(
        session_id=session.id,
        capability="skills.write",
        resource_type="skill_collection",
        resource_scope=str(session.user_id),
    )
    root_files = _root_files(tmp_path, session)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, root_files
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant),
        session,
        skill_edit_permission(uuid4(), "Anderer Skill"),
        root_files,
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        _db_with_grants(collection_grant), session, request, root_files
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
    assert await PermissionPolicy.evaluate(
        _db_with_grants(search_grant), session, session_browse_permission(), root_files
    ) == PermissionDecision.ALLOW


def test_webfetch_permission_hashes_the_full_canonical_url_and_redacts_summary() -> None:
    first = webfetch_permission("HTTPS://Example.com/path?token=secret#fragment")
    equivalent = webfetch_permission("https://example.com/path?token=secret")
    other_query = webfetch_permission("https://example.com/path?token=other")

    assert first.capability == "network.fetch.public"
    assert first.resource_type == "url"
    assert first.resource_scope == equivalent.resource_scope
    assert first.resource_scope != other_query.resource_scope
    assert len(first.resource_scope) == 64
    assert "?REDACTED" in first.summary
    assert "secret" not in first.summary
    assert first.resource_display == "https://example.com/path?REDACTED"
    assert canonicalize_url("https://example.com/path?token=secret") not in first.summary


def test_transcript_permission_normalizes_equivalent_video_urls() -> None:
    watch = transcript_permission(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12"
    )
    short = transcript_permission("https://youtu.be/dQw4w9WgXcQ")
    other = transcript_permission("https://youtu.be/abcdefghijk")

    assert watch.resource_scope == short.resource_scope
    assert watch.resource_scope != other.resource_scope
    assert watch.resource_display == "https://youtu.be/dQw4w9WgXcQ"
    assert watch.action_kind == "fetch_transcript"


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
