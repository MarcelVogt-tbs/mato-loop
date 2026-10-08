import json
from dataclasses import replace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

import mato_loop_backend.agent.loop as mock_agent_loop
import mato_loop_backend.agent.run_support as mock_agent_run_support
import mato_loop_backend.agent.runtime as mock_agent_runtime
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.subagents import permission_snapshot
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    ChatSession,
    ExecutionAgent,
    Message,
    PermissionGrant,
)
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    SessionGrantScope,
    administrator_permission,
    agent_identity_permission,
    automation_collection_scope,
    automation_scope,
    execution_agent_select_permission,
    execution_agents_list_permission,
    execution_directory_permission,
    execution_directory_scope,
    integration_operation_permission,
    media_permission,
    memory_write_permission,
    path_permission,
    project_session_path_permission,
    session_browse_permission,
    session_permission,
    session_search_permission,
    shell_command_scope,
    skill_edit_permission,
    skill_load_permission,
    skill_write_permission,
    transcript_permission,
    user_profile_permission,
    webfetch_permission,
)
from mato_loop_backend.permissions.resolution import resolve_tool_execution_policy
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    GENERAL_PROFILE_ID,
    AgentProfilesSettings,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.settings.models import AgentProfilePermissions, sysadmin_profile
from mato_loop_backend.tools.actions import ToolAction
from mato_loop_backend.tools.catalog import tool_registry


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
    db.get.return_value = _session()
    return db


def _grant(request: PermissionRequest, session_id=None, **values) -> PermissionGrant:
    scope = request.effective_session_grants[0]
    return PermissionGrant(
        session_id=session_id or uuid4(),
        capability=scope.capability,
        resource_type=scope.resource_type,
        resource_scope=scope.resource_scope,
        **values,
    )


def _tool_settings(data: dict) -> ToolSettings:
    values = dict(data)
    permissions = {
        key: values.pop(key)
        for key in ("policies", "integrationPolicies", "resourcePolicies")
        if key in values
    }
    values["publicPermissions"] = permissions
    return ToolSettings.model_validate(values).select_trust(TrustLevel.PUBLIC)


def _profile_settings(data: dict) -> AgentProfilesSettings:
    permissions = {
        key: data[key]
        for key in ("policies", "integrationPolicies", "resourcePolicies")
        if key in data
    }
    return AgentProfilesSettings.model_validate(
        {
            "builtin_access_overrides": {
                str(GENERAL_PROFILE_ID): {
                    "privatePermissions": permissions,
                    "publicPermissions": permissions,
                }
            }
        }
    )


def _root_files(tmp_path, session: ChatSession) -> RootFileService:
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    return RootFileService(
        session.id,
        session_storage=storage,
    )


def test_integration_write_grants_do_not_cross_capability_families(tmp_path) -> None:
    integration_id = uuid4()
    regular = integration_operation_permission(
        capability="browser.write",
        integration_id=integration_id,
        binding_revision="{}",
        operation_key="click",
        grant_resource_key="browser:one:attachment:1",
        title="Browser verwenden?",
        summary="Browseraktion",
    )
    code = integration_operation_permission(
        capability="browser.code",
        integration_id=integration_id,
        binding_revision="{}",
        operation_key="run-code",
        grant_resource_key="browser:one:attachment:1",
        title="Browser-Code ausführen?",
        summary="Codeausführung",
    )
    session = _session()
    grant = _grant(regular, session.id)
    root_files = _root_files(tmp_path, session)

    assert PermissionPolicy.grant_matches(grant, regular, root_files)
    assert not PermissionPolicy.grant_matches(grant, code, root_files)


@pytest.mark.parametrize(
    ("capability", "access"),
    [("mcp.call", "write"), ("sonobin.call", "write"), ("support.read", "read")],
)
def test_integration_session_grants_match_only_the_bound_resource(
    tmp_path, capability, access
) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    integration_id = uuid4()

    def request(*, resource_key="tool:one", selected_id=integration_id, revision="{}"):
        return integration_operation_permission(
            capability=capability,
            integration_id=selected_id,
            binding_revision=revision,
            operation_key="call",
            resource_key=resource_key,
            title="Tool aufrufen?",
            summary="Tool aufrufen.",
        )

    granted = request()
    assert granted.effective_session_grants[0].access == access
    grant = _grant(granted, session.id)
    assert PermissionPolicy.grant_matches(grant, granted, root_files)
    assert not PermissionPolicy.grant_matches(grant, request(resource_key="tool:two"), root_files)
    assert not PermissionPolicy.grant_matches(grant, request(selected_id=uuid4()), root_files)
    assert not PermissionPolicy.grant_matches(grant, request(revision='{"changed":true}'), root_files)


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
    grant = _grant(granted, session.id)

    assert PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "files/docs/readme.md"), root_files
    )
    assert not PermissionPolicy.grant_matches(
        grant, _path_request(root_files, "files/docs-other/readme.md"), root_files
    )


async def test_managed_read_reuses_saved_filesystem_grant(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    request = _path_request(root_files, "notes.txt")
    assert request.capability == "session.files.read"
    grant = _grant(request, session.id)
    db = _db_with_grants(grant)
    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ALLOW
    assert ["resource.read"] in db.scalars.await_args.args[0].compile().params.values()
    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.DISABLED
    ) == PermissionDecision.DENY


async def test_profile_session_write_paths_restrict_actual_permission(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    db = _db_with_grants()
    expected = _path_request(
        root_files,
        "plan.md",
        capability="session.files.write",
    )
    other = _path_request(
        root_files,
        "notes.md",
        capability="session.files.write",
    )

    assert await PermissionPolicy.evaluate(
        db,
        session,
        expected,
        root_files,
        ToolExecutionPolicy.AUTO,
        session_write_paths=["plan.md"],
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db,
        session,
        other,
        root_files,
        ToolExecutionPolicy.AUTO,
        session_write_paths=["plan.md"],
    ) == PermissionDecision.DENY
    assert await PermissionPolicy.evaluate(
        db, session, expected, root_files, ToolExecutionPolicy.AUTO,
        session_write_paths=[],
    ) == PermissionDecision.DENY
    assert await PermissionPolicy.evaluate(
        db, session, other, root_files, ToolExecutionPolicy.AUTO,
        session_write_paths=None,
    ) == PermissionDecision.ALLOW
    delete_other = _path_request(
        root_files, "notes.md", capability="filesystem.delete"
    )
    create_plan_directory = replace(expected, action_kind="create_directory")
    copy_to_plan = replace(expected, action_kind="copy_path")
    assert await PermissionPolicy.evaluate(
        db, session, delete_other, root_files, ToolExecutionPolicy.AUTO,
        session_write_paths=["plan.md"],
    ) == PermissionDecision.DENY
    for request in (create_plan_directory, copy_to_plan):
        assert await PermissionPolicy.evaluate(
            db, session, request, root_files, ToolExecutionPolicy.AUTO,
            session_write_paths=["plan.md"],
        ) == PermissionDecision.DENY


@pytest.mark.parametrize("trust_level", list(TrustLevel))
@pytest.mark.parametrize("policy", [None, *ToolExecutionPolicy])
async def test_managed_deletion_asks_but_honors_grants_and_disabled(
    tmp_path, trust_level, policy
) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    request = _path_request(root_files, "output.txt", capability="filesystem.delete")
    assert not request.force_ask
    assert "allow_session" in request.available_decisions
    disabled = policy == ToolExecutionPolicy.DISABLED
    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, request, root_files, policy, trust_level=trust_level
    ) == (PermissionDecision.DENY if disabled else PermissionDecision.ASK)
    grant = _grant(request, session.id, trust_level=trust_level.value)
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, root_files, policy, trust_level=trust_level
    ) == (PermissionDecision.DENY if disabled else PermissionDecision.ALLOW)


def test_execution_directory_grant_matches_only_same_agent_subtree(tmp_path) -> None:
    session = _session()
    root_files = _root_files(tmp_path, session)
    agent_id = uuid4()
    granted = execution_directory_permission(
        agent_id, "/home/user/project"
    )
    assert granted.available_decisions == ("allow_once", "deny")
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


async def test_shell_paths_require_complete_coverage_from_directory_grants(tmp_path) -> None:
    session = _session(user_id=uuid4(), current_directory="/work")
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(), user_id=session.user_id, home_directory="/home/user",
        home_filesystem_policy="ask", external_filesystem_policy="ask",
        home_shell_enabled=True, external_shell_enabled=True,
    )
    requested = (
        SessionGrantScope("read", "execution_directory", execution_directory_scope(agent.id, "/opt/tool")),
        SessionGrantScope("read", "execution_directory", execution_directory_scope(agent.id, "/srv/data")),
    )
    request = PermissionRequest(
        capability="shell.read", resource_type="shell_command",
        resource_scope=shell_command_scope(
            agent.id, "/work", "read", [["cat"]], ["/opt/tool", "/srv/data"], True,
            ["read", "read"],
        ),
        action_kind="shell_run", risk_level="low", title="Lesen?", summary="Test",
        session_grants=requested,
    )
    grants = [
        PermissionGrant(
            session_id=session.id, capability="resource.read",
            resource_type="execution_directory",
            resource_scope=execution_directory_scope(agent.id, path),
        )
        for path in ("/opt", "/srv")
    ]
    db = _db_with_grants(*grants)
    db.scalar.return_value = agent

    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ALLOW
    db.scalars.return_value.all.return_value = grants[:1]
    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ASK
    outside = replace(
        requested[0], resource_scope=execution_directory_scope(agent.id, "/optional/tool")
    )
    assert not PermissionPolicy.scope_matches(grants[0], outside, root_files)


@pytest.mark.parametrize(
    ("path", "home_policy", "computer_external_policy", "profile_external_policy", "expected"),
    [
        ("/home/user/project", "auto", "deny", ToolExecutionPolicy.ASK, PermissionDecision.ASK),
        ("/home/user-other", "auto", "deny", ToolExecutionPolicy.AUTO, PermissionDecision.DENY),
        ("/srv/project", "auto", "ask", ToolExecutionPolicy.ASK, PermissionDecision.ASK),
        ("/srv/project", "auto", "ask", ToolExecutionPolicy.AUTO, PermissionDecision.ALLOW),
        ("/srv/project", "auto", "auto", ToolExecutionPolicy.DISABLED, PermissionDecision.DENY),
    ],
)
async def test_execution_filesystem_policy_uses_home_boundary(
    tmp_path, path, home_policy, computer_external_policy, profile_external_policy, expected
) -> None:
    session = _session(user_id=uuid4())
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=session.user_id,
        alias="Desktop",
        home_directory="/home/user",
        home_filesystem_policy=home_policy,
        external_filesystem_policy=computer_external_policy,
    )
    db = _db_with_grants()
    db.scalar.return_value = agent
    request = execution_directory_permission(
        agent.id, path, capability="filesystem.read"
    )

    assert await PermissionPolicy.evaluate(
        db,
        session,
        request,
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=profile_external_policy,
    ) == expected


async def test_current_directory_is_the_auto_workspace_boundary(tmp_path) -> None:
    session = _session(user_id=uuid4(), current_directory="/srv/project")
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=session.user_id,
        alias="Desktop",
        home_directory="/home/user",
        home_filesystem_policy="auto",
        external_filesystem_policy="ask",
    )
    db = _db_with_grants()
    db.scalar.return_value = agent

    inside = execution_directory_permission(
        agent.id, "/srv/project/src/app.py", capability="filesystem.write"
    )
    outside = execution_directory_permission(
        agent.id, "/home/user/notes.txt", capability="filesystem.write"
    )

    assert await PermissionPolicy.evaluate(
        db, session, inside, root_files, ToolExecutionPolicy.AUTO
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db, session, outside, root_files, ToolExecutionPolicy.AUTO
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        db,
        session,
        outside,
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db,
        session,
        outside,
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=ToolExecutionPolicy.DISABLED,
    ) == PermissionDecision.DENY


async def test_shell_uses_external_working_directory_policy_for_known_targets(
    tmp_path,
) -> None:
    session = _session(user_id=uuid4(), current_directory="/work")
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=session.user_id,
        home_directory="/home/user",
        home_filesystem_policy="auto",
        external_filesystem_policy="ask",
        home_shell_enabled=True,
        external_shell_enabled=True,
    )
    db = _db_with_grants()
    db.scalar.return_value = agent
    request = PermissionRequest(
        capability="shell.read",
        resource_type="shell_command",
        resource_scope=shell_command_scope(
            agent.id,
            "/work",
            "read",
            [["cat"]],
            ["/srv/config.yaml"],
            True,
            ["read"],
        ),
        action_kind="shell_run",
        risk_level="low",
        title="Lesen?",
        summary="Test",
    )

    assert await PermissionPolicy.evaluate(
        db,
        session,
        request,
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=ToolExecutionPolicy.ASK,
    ) == PermissionDecision.ASK
    assert await PermissionPolicy.evaluate(
        db,
        session,
        request,
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db,
        session,
        replace(request, force_ask=True),
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=ToolExecutionPolicy.DISABLED,
    ) == PermissionDecision.DENY


async def test_confidential_workspace_paths_always_require_one_time_approval(
    tmp_path,
) -> None:
    session = _session(user_id=uuid4(), current_directory="/home/user/project")
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=session.user_id,
        alias="Desktop",
        home_directory="/home/user",
        home_filesystem_policy="auto",
        external_filesystem_policy="ask",
    )
    request = execution_directory_permission(
        agent.id, "/home/user/project/.env", capability="filesystem.read"
    )
    grant = _grant(request, session.id)
    db = _db_with_grants(grant)
    db.scalar.return_value = agent

    assert request.available_decisions == ("allow_once", "deny")
    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.AUTO
    ) == PermissionDecision.ASK
    outside = execution_directory_permission(
        agent.id, "/home/user/.env", capability="filesystem.read"
    )
    assert await PermissionPolicy.evaluate(
        db,
        session,
        outside,
        root_files,
        ToolExecutionPolicy.AUTO,
        external_working_directory_policy=ToolExecutionPolicy.DISABLED,
    ) == PermissionDecision.DENY


@pytest.mark.parametrize("trust_level", list(TrustLevel))
async def test_file_provide_is_separate_auto_capability_for_confidential_workspace(
    tmp_path, trust_level
) -> None:
    session = _session(user_id=uuid4(), current_directory="/home/user/project")
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(), user_id=session.user_id, alias="Desktop",
        home_directory="/home/user", home_filesystem_policy="auto",
        external_filesystem_policy="deny",
    )
    db = _db_with_grants()
    db.scalar.return_value = agent
    request = execution_directory_permission(
        agent.id, "/home/user/project/.env", capability="file.provide"
    )

    assert request.available_decisions == ("allow_once", "allow_session", "deny")
    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.AUTO,
        trust_level=trust_level,
    ) == PermissionDecision.ALLOW
    agent.home_filesystem_policy = "deny"
    assert await PermissionPolicy.evaluate(
        db, session, request, root_files, ToolExecutionPolicy.AUTO,
        trust_level=trust_level,
    ) == PermissionDecision.DENY


def test_file_provide_defaults_and_saved_overrides_ignore_read_policy() -> None:
    settings = ToolSettings(trust_level=TrustLevel.PUBLIC)
    settings.permissions.policies["filesystem.read"] = ToolExecutionPolicy.DISABLED

    assert resolve_tool_execution_policy(
        settings, "file.provide", ToolExecutionPolicy.AUTO
    ).policy == ToolExecutionPolicy.AUTO
    settings.permissions.policies["file.provide"] = ToolExecutionPolicy.ASK
    assert resolve_tool_execution_policy(
        settings, "file.provide", ToolExecutionPolicy.AUTO
    ).policy == ToolExecutionPolicy.ASK
    settings.permissions.policies["file.provide"] = ToolExecutionPolicy.DISABLED
    assert resolve_tool_execution_policy(
        settings, "file.provide", ToolExecutionPolicy.AUTO
    ).policy == ToolExecutionPolicy.DISABLED


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
    grant = _grant(request, session.id)

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


@pytest.mark.parametrize("capability", ["automation.read", "automation.update"])
@pytest.mark.parametrize("scope, policy, expected", [
    ("current", ToolExecutionPolicy.ASK, PermissionDecision.ALLOW),
    ("other", ToolExecutionPolicy.ASK, PermissionDecision.ASK),
    ("foreign", ToolExecutionPolicy.AUTO, PermissionDecision.DENY),
    ("stale", ToolExecutionPolicy.AUTO, PermissionDecision.DENY),
    ("current", ToolExecutionPolicy.DISABLED, PermissionDecision.DENY),
])
async def test_session_automation_access_uses_persisted_owner_session_and_revision(tmp_path, capability, scope, policy, expected):
    session = _session(user_id=uuid4())
    automation = Automation(
        id=uuid4(), user_id=uuid4() if scope == "foreign" else session.user_id,
        execution_session_id=uuid4() if scope == "other" else session.id,
        revision=2 if scope == "stale" else 1,
    )
    db = _db_with_grants()
    db.get.return_value = automation
    request = PermissionRequest(
        capability, "automation", automation_scope(automation.id, 1, session.user_id, session.id),
        "get_automation" if capability == "automation.read" else "edit_automation", "medium", "Test", "Test",
    )
    assert await PermissionPolicy.evaluate(db, session, request, _root_files(tmp_path, session), policy) == expected


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
        return ToolSettings()

    async def load_optional(_db, settings_type, **_kwargs):
        nonlocal settings_loads
        if settings_type is not AgentProfilesSettings:
            return None
        settings_loads += 1
        return _profile_settings(
            {
                "policies": {
                    "filesystem.delete": (
                        "ask" if settings_loads == 1 else "disabled"
                    )
                }
            }
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(side_effect=load_optional)
    )
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


async def test_administrator_once_approval_is_reused_for_revalidation(tmp_path, monkeypatch):
    from mato_loop_backend.agent.run_contracts import ApprovalResolution

    agent = ExecutionAgent(id=uuid4(), user_id=uuid4(), home_directory="/home/user", home_filesystem_policy="auto", external_filesystem_policy="ask")
    session = _session(user_id=agent.user_id, preferred_execution_agent_id=agent.id, current_directory="/work")
    db = _db_with_grants()
    db.scalar.return_value = agent
    settings = _tool_settings({"policies": {"filesystem.write": "ask", "working_directory.external": "auto"}})
    monkeypatch.setattr(mock_agent_runtime, "permission_origin", AsyncMock(return_value=session))
    monkeypatch.setattr(mock_agent_runtime, "delegation_snapshot", AsyncMock(return_value=None))
    monkeypatch.setattr(mock_agent_runtime, "load_agent_profile_tools", AsyncMock(return_value=(settings, sysadmin_profile())))
    monkeypatch.setattr(mock_agent_runtime, "load_effective_integrations", AsyncMock(return_value=IntegrationSettings()))
    approve = AsyncMock(return_value=ApprovalResolution("allow_once"))
    monkeypatch.setattr(AgentRuntime, "_request_approval", approve)
    request = administrator_permission(agent.id, "/work", capability="filesystem.write", path="/etc/demo.conf")
    run = AgentRun(id=uuid4(), session_id=session.id)
    call = ToolCall("edit", "edit_file", {"root": "computer", "path": "/etc/demo.conf", "oldText": "old", "newText": "new", "elevated": True})
    resolution = {}
    roots = _root_files(tmp_path, session)
    assert await AgentRuntime._authorize(db, run, session, call, request, roots, resolution) == PermissionDecision.ALLOW
    assert await AgentRuntime._authorize(db, run, session, call, request, roots, resolution, check_only=True) == PermissionDecision.ALLOW
    assert approve.await_count == 1
    resolution.clear()
    resolution["argument_edit_approved"] = json.dumps({
        "arguments": call.arguments, "permission": mock_agent_run_support._argument_edit_permission_binding(request),
    })
    assert await AgentRuntime._authorize(db, run, session, call, request, roots, resolution) == PermissionDecision.ALLOW
    assert await AgentRuntime._authorize(db, run, session, call, request, roots, resolution, check_only=True) == PermissionDecision.ALLOW
    assert approve.await_count == 1
    settings.permissions.policies["filesystem.write"] = ToolExecutionPolicy.DISABLED
    assert await AgentRuntime._authorize(db, run, session, call, request, roots, resolution, check_only=True) == PermissionDecision.DENY


@pytest.mark.parametrize("lifetime", ["run", "session", "exact"])
async def test_runtime_grant_lifetime_and_boundaries(tmp_path, monkeypatch, lifetime):
    session = _session(user_id=uuid4(), agent_profile_id=uuid4())
    run = AgentRun(id=uuid4(), session_id=session.id, trust_level="public")
    roots = _root_files(tmp_path, session)
    request = replace(_path_request(roots, "notes.txt"), force_ask=lifetime == "exact")
    call = ToolCall("first", "read", {"path": "notes.txt"})
    grant = _grant(request, session.id, run_id=None if lifetime == "session" else run.id,
                   agent_profile_id=session.agent_profile_id, trust_level="public")
    if lifetime == "exact":
        grant.capability = request.capability
        grant.resource_type = "run_action"
        grant.resource_scope = mock_agent_run_support._run_action_scope(request, call)
    settings = _tool_settings({"policies": {request.capability: "ask"}})
    monkeypatch.setattr(mock_agent_runtime, "load_agent_profile_tools", AsyncMock(return_value=(settings, sysadmin_profile())))
    monkeypatch.setattr(mock_agent_runtime, "load_effective_integrations", AsyncMock(return_value=IntegrationSettings()))
    monkeypatch.setattr(AgentRuntime, "_request_approval", AsyncMock(return_value="deny"))
    engine = create_engine("sqlite://")
    PermissionGrant.__table__.create(engine)
    try:
        with Session(engine) as stored:
            stored.add(grant)
            stored.commit()
            db = AsyncMock(spec=AsyncSession)
            db.scalar.side_effect = stored.scalar
            db.scalars.side_effect = stored.scalars

            async def authorize(current_run=run, permission=request, tool_call=call):
                return await AgentRuntime._authorize(db, current_run, session, tool_call, permission, roots)

            outcomes = [
                await authorize(),
                await authorize(tool_call=ToolCall("next", "read", {"path": "notes.txt", "_reason": "Read again"})),
                await authorize(AgentRun(id=uuid4(), session_id=session.id, trust_level="public")),
                await authorize(permission=replace(request, resource_scope=_path_request(roots, "other.txt").resource_scope)),
                await authorize(AgentRun(id=run.id, session_id=session.id, trust_level="private")),
                await authorize(AgentRun(id=run.id, session_id=session.id, trust_level="public", agent_profile_id=uuid4())),
            ]
            if lifetime == "exact":
                outcomes.extend([
                    await authorize(tool_call=ToolCall("changed", "read", {"path": "notes.txt", "offset": 10})),
                    await authorize(permission=replace(request, approval_details={"revision": "changed"})),
                ])
            settings.permissions.policies[request.capability] = ToolExecutionPolicy.DISABLED
            outcomes.append(await authorize())
            assert outcomes == [
                PermissionDecision.ALLOW, PermissionDecision.ALLOW,
                PermissionDecision.ALLOW if lifetime == "session" else PermissionDecision.USER_DENIED,
                PermissionDecision.USER_DENIED, PermissionDecision.USER_DENIED, PermissionDecision.USER_DENIED,
                *([PermissionDecision.USER_DENIED] * 2 if lifetime == "exact" else []),
                PermissionDecision.DENY,
            ]
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["file", "shell"])
@pytest.mark.parametrize(
    ("policy_after_approval", "expected"),
    [
        ("ask", PermissionDecision.ALLOW),
        ("disabled", PermissionDecision.DENY),
    ],
)
async def test_external_working_directory_policy_is_revalidated_after_approval(
    tmp_path, monkeypatch, kind, policy_after_approval, expected
) -> None:
    session = _session(user_id=uuid4(), current_directory="/work")
    root_files = _root_files(tmp_path, session)
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=session.user_id,
        home_directory="/home/user",
        home_filesystem_policy="auto",
        external_filesystem_policy="ask",
        home_shell_enabled=True,
        external_shell_enabled=True,
    )
    if kind == "file":
        request = execution_directory_permission(
            agent.id, "/srv/data.txt", capability="filesystem.read"
        )
        call = ToolCall(id="read", name="read", arguments={})
    else:
        request = PermissionRequest(
            capability="shell.read",
            resource_type="shell_command",
            resource_scope=shell_command_scope(
                agent.id,
                "/work",
                "read",
                [["cat"]],
                ["/srv/data.txt"],
                True,
                ["read"],
            ),
            action_kind="shell_run",
            risk_level="low",
            title="Lesen?",
            summary="Test",
        )
        call = ToolCall(id="shell", name="shell_run", arguments={})
    settings_loads = 0

    async def load_settings(_db, settings_type, **_kwargs):
        return IntegrationSettings() if settings_type is IntegrationSettings else ToolSettings()

    async def load_optional(_db, settings_type, **_kwargs):
        nonlocal settings_loads
        if settings_type is not AgentProfilesSettings:
            return None
        settings_loads += 1
        return _profile_settings({
            "policies": {
                request.capability: "auto",
                "working_directory.external": (
                    "ask" if settings_loads == 1 else policy_after_approval
                ),
            }
        })

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(side_effect=load_optional)
    )
    approve = AsyncMock(return_value="allow_once")
    monkeypatch.setattr(AgentRuntime, "_request_approval", approve)
    db = _db_with_grants()
    db.scalar.return_value = agent

    decision = await AgentRuntime._authorize(
        db,
        AgentRun(id=uuid4(), session_id=session.id, trust_level="public"),
        session,
        call,
        request,
        root_files,
    )

    assert decision == expected
    approve.assert_awaited_once()


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
        return ToolSettings()

    async def load_optional(_db, settings_type, **_kwargs):
        nonlocal settings_loads
        if settings_type is not AgentProfilesSettings:
            return None
        settings_loads += 1
        return _profile_settings(
            {
                "policies": {
                    "skills.load": "ask" if settings_loads == 1 else "disabled"
                }
            }
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(side_effect=load_optional)
    )
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


@pytest.mark.usefixtures("mock_db_settings")
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
                ToolAction(
                    "network.search",
                    "Suchen",
                    ToolExecutionPolicy.AUTO,
                    ToolExecutionPolicy.AUTO,
                ),
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
async def test_runtime_discovery_uses_integration_baseline_not_resource_policy(
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
    tools = ToolSettings()
    integration.permissions.public_permissions.resource_policies = {"calendar:work": {"calendar.read": "disabled"}}
    if global_policy is not None:
        integration.permissions.public_permissions.policies["calendar.read"] = global_policy

    async def load_settings(_db, settings_type, **_kwargs):
        return (
            IntegrationSettings(instances=[integration])
            if settings_type is IntegrationSettings
            else tools
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(
        SettingsStore,
        "load_optional",
        AsyncMock(
            side_effect=lambda _db, settings_type, **_kwargs: (
                _profile_settings(settings_data)
                if settings_type is AgentProfilesSettings
                else None
            )
        ),
    )
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

    assert request.policy_integration_id == integration.id
    assert decision == expected
    assert request_approval.await_count == approval_count


@pytest.mark.usefixtures("mock_db_settings")
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


def test_integration_policy_precedence_and_hard_limits() -> None:
    integration_id = uuid4()
    action = ToolAction(
        "tasks.read",
        "Aufgaben lesen",
        ToolExecutionPolicy.AUTO,
        ToolExecutionPolicy.ASK,
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
        action.default_policy_for(settings.trust_level),
        integration_id=integration_id,
    )
    resource = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy_for(settings.trust_level),
        integration_id=integration_id,
        resource_key="project:main",
    )
    hard_limit = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy_for(settings.trust_level),
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
        action.default_policy_for(settings.trust_level),
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
        ToolExecutionPolicy.ASK,
    )
    settings.permissions.policies[restricted.capability] = ToolExecutionPolicy.AUTO
    automatic = resolve_tool_execution_policy(
        settings,
        restricted.capability,
        restricted.default_policy_for(settings.trust_level),
    )
    assert (automatic.policy, automatic.reason) == (
        ToolExecutionPolicy.AUTO,
        "configured",
    )

    settings.permissions.policies[action.capability] = ToolExecutionPolicy.ASK
    settings.permissions.integration_policies[str(integration_id)] = {
        action.capability: "invalid"  # type: ignore[dict-item]
    }
    invalid = resolve_tool_execution_policy(
        settings,
        action.capability,
        action.default_policy_for(settings.trust_level),
        integration_id=integration_id,
        resource_key="project:main",
    )
    assert (invalid.policy, invalid.reason) == (
        ToolExecutionPolicy.DISABLED,
        "invalid_policy",
    )


def test_integration_grants_match_stable_resource_not_operation(tmp_path) -> None:
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
    grant = _grant(granted, session.id)

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
    assert PermissionPolicy.grant_matches(
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


async def test_current_session_read_honors_automatic_policy(tmp_path) -> None:
    session = _session()
    db = _db_with_grants()

    decision = await PermissionPolicy.evaluate(
        db,
        session,
        session_permission(session.id),
        _root_files(tmp_path, session),
        ToolExecutionPolicy.AUTO,
    )

    assert decision == PermissionDecision.ALLOW
    db.scalars.assert_not_awaited()


async def test_computer_list_and_select_permissions_are_owner_scoped(tmp_path) -> None:
    session = _session(user_id=uuid4())
    roots = _root_files(tmp_path, session)
    agent = ExecutionAgent(id=uuid4(), user_id=session.user_id, alias="Server")
    db = _db_with_grants()
    db.scalar.return_value = agent

    assert await PermissionPolicy.evaluate(
        db, session, execution_agents_list_permission(session.user_id), roots,
        ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db, session, execution_agents_list_permission(uuid4()), roots,
        ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.DENY
    request = execution_agent_select_permission(agent.id, agent.alias)
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ASK
    agent.user_id = uuid4()
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.ASK,
    ) == PermissionDecision.DENY


@pytest.mark.parametrize("kind", ["url", "root", "execution", "shell"])
async def test_child_uses_parent_grants_and_revocation_stops_execution(tmp_path, kind):
    parent = _session(user_id=uuid4())
    child = _session(user_id=parent.user_id, parent_session_id=parent.id, current_directory="/work")
    roots = _root_files(tmp_path, child)
    agent = ExecutionAgent(id=uuid4(), user_id=parent.user_id, home_directory="/home",
                           home_filesystem_policy="ask", external_filesystem_policy="ask",
                           home_shell_enabled=True, external_shell_enabled=True)
    request = {
        "url": webfetch_permission("https://example.org"),
        "root": _path_request(roots, "note.txt"),
        "execution": execution_directory_permission(agent.id, "/work", capability="filesystem.read"),
        "shell": PermissionRequest(capability="shell.read", resource_type="shell_command",
            resource_scope=shell_command_scope(agent.id, "/work", "read", [["pwd"]], [], True),
            action_kind="shell", risk_level="low", title="Read", summary="Read"),
    }[kind]
    grant = _grant(request, parent.id)
    db = _db_with_grants(grant)
    db.get.return_value = parent
    db.scalar.return_value = agent
    assert await PermissionPolicy.evaluate(
        db, child, request, roots, ToolExecutionPolicy.ASK, permission_session_id=parent.id
    ) == PermissionDecision.ALLOW
    assert parent.id in db.scalars.await_args.args[0].compile().params.values()
    db.scalars.return_value.all.return_value = []
    assert await PermissionPolicy.evaluate(
        db, child, request, roots, ToolExecutionPolicy.ASK, permission_session_id=parent.id
    ) == PermissionDecision.ASK
    if kind == "root":
        assert await PermissionPolicy.evaluate(
            db, child, request, _root_files(tmp_path, parent), ToolExecutionPolicy.AUTO,
            permission_session_id=parent.id,
        ) == PermissionDecision.DENY


@pytest.mark.parametrize("invalid", ["unrelated", "owner", "missing"])
async def test_child_cannot_choose_another_permission_namespace(tmp_path, invalid):
    parent = _session(user_id=uuid4())
    child = _session(user_id=parent.user_id, parent_session_id=parent.id)
    if invalid == "unrelated":
        child.parent_session_id = uuid4()
    elif invalid == "owner":
        parent.user_id = uuid4()
    db = _db_with_grants()
    db.get.return_value = None if invalid == "missing" else parent
    assert await PermissionPolicy.evaluate(
        db, child, webfetch_permission("https://example.org"), _root_files(tmp_path, child),
        ToolExecutionPolicy.AUTO, permission_session_id=parent.id,
    ) == PermissionDecision.DENY
    db.scalars.assert_not_awaited()


async def test_parent_can_read_only_its_own_child_sessions(tmp_path):
    parent = _session(user_id=uuid4())
    child = _session(user_id=parent.user_id, parent_session_id=parent.id)
    db = _db_with_grants()
    db.get.return_value = child
    roots = _root_files(tmp_path, parent)
    assert await PermissionPolicy.evaluate(db, parent, session_permission(child.id), roots, ToolExecutionPolicy.AUTO) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(db, parent, session_permission(child.id), roots, ToolExecutionPolicy.DISABLED) == PermissionDecision.DENY
    child.parent_session_id = uuid4()
    assert await PermissionPolicy.evaluate(db, parent, session_permission(child.id), roots, ToolExecutionPolicy.AUTO) == PermissionDecision.DENY
    child.parent_session_id = parent.id
    child.user_id = uuid4()
    assert await PermissionPolicy.evaluate(db, parent, session_permission(child.id), roots, ToolExecutionPolicy.AUTO) == PermissionDecision.DENY


@pytest.mark.parametrize("layer", ["default", "global", "integration", "resource", "profile"])
async def test_child_integration_ceiling_cannot_widen_ask_to_auto(tmp_path, monkeypatch, layer):
    integration = IntegrationInstance(name="Calendar", integration_type="calendar", adapter_type="caldav",
                                      config={"base_url": "https://example.org", "username": "private-user"},
                                      credential_id=uuid4())
    integrations = IntegrationSettings(instances=[integration])
    settings = ToolSettings().select_trust(TrustLevel.PUBLIC)
    capability = "calendar.read"
    if layer == "global":
        settings.permissions.policies[capability] = ToolExecutionPolicy.ASK
    elif layer == "integration":
        settings.permissions.integration_policies[str(integration.id)] = {capability: ToolExecutionPolicy.ASK}
    elif layer == "resource":
        settings.permissions.resource_policies[str(integration.id)] = {"calendar:test": {capability: ToolExecutionPolicy.ASK}}
    elif layer == "profile":
        settings._agent_permissions = AgentProfilePermissions(integration_policies={str(integration.id): {capability: "ask"}})
    default = (
        ToolExecutionPolicy.ASK
        if layer == "default"
        else ToolExecutionPolicy.AUTO
    )
    actions = Mock(
        return_value=(ToolAction(capability, "Read", default, default),)
    )
    monkeypatch.setattr(tool_registry, "integration_resource_actions", actions)
    snapshot = permission_snapshot(settings, {"calendar_query_events"}, integrations)
    if layer == "global":
        snapshot.pop("permissionVersion")  # Legacy children retain their former global ceiling.
    assert "private-user" not in str(snapshot) and str(integration.credential_id) not in str(snapshot)
    parent = _session(user_id=uuid4())
    child = _session(user_id=parent.user_id, parent_session_id=parent.id)
    run = AgentRun(id=uuid4(), session_id=child.id, input_message_id=uuid4(), trust_level="public")
    message = Message(id=run.input_message_id, provider_data={"_matoloop": {"delegationCeiling": snapshot}})
    db = _db_with_grants()
    db.get.side_effect = lambda model, key, **kw: message if model is Message else parent
    actions.return_value = (
        ToolAction(
            capability,
            "Read",
            ToolExecutionPolicy.AUTO,
            ToolExecutionPolicy.AUTO,
        ),
    )
    _mock_value = AsyncMock(return_value=(ToolSettings().select_trust(TrustLevel.PUBLIC), None))
    for _mock_target in (mock_agent_runtime, mock_agent_loop,):
        monkeypatch.setattr(_mock_target, 'load_agent_profile_tools', _mock_value)
    _mock_value = AsyncMock(return_value=integrations)
    for _mock_target in (mock_agent_runtime, mock_agent_loop,):
        monkeypatch.setattr(_mock_target, 'load_effective_integrations', _mock_value)
    approve = AsyncMock(return_value="deny")
    monkeypatch.setattr(AgentRuntime, "_request_approval", approve)
    request = PermissionRequest(capability=capability, resource_type="network_service", resource_scope="calendar:test",
                                action_kind="read", risk_level="low", title="Read", summary="Read",
                                policy_integration_id=integration.id, policy_resource_key="calendar:test")
    decision = await AgentRuntime._authorize(db, run, child, ToolCall("read", "calendar_query_events", {}), request, _root_files(tmp_path, child))
    assert decision == PermissionDecision.USER_DENIED
    approve.assert_awaited_once()


@pytest.mark.parametrize(
    ("current", "offered"),
    [
        (current, offered)
        for current in ToolExecutionPolicy
        for offered in ToolExecutionPolicy
    ],
)
def test_external_working_directory_delegation_ceiling(current, offered) -> None:
    settings = ToolSettings(
        public_permissions={"policies": {"working_directory.external": current}}
    ).select_trust(TrustLevel.PUBLIC)
    offered_settings = ToolSettings(
        public_permissions={"policies": {"working_directory.external": offered}}
    ).select_trust(TrustLevel.PUBLIC)
    snapshot = permission_snapshot(settings, {"change_directory"})
    snapshot["offeredCeiling"] = permission_snapshot(
        offered_settings, {"change_directory"}
    )

    assert mock_agent_run_support._external_working_directory_policy(
        settings, snapshot=snapshot
    ) == max(
        (current, offered),
        key=(
            ToolExecutionPolicy.AUTO,
            ToolExecutionPolicy.ASK,
            ToolExecutionPolicy.DISABLED,
        ).index,
    )


def test_legacy_delegation_ceiling_disables_external_working_directory() -> None:
    settings = ToolSettings().select_trust(TrustLevel.PUBLIC)
    snapshot = permission_snapshot(settings, {"change_directory"})
    snapshot["actionDefaults"].pop("working_directory.external")

    assert mock_agent_run_support._external_working_directory_policy(
        settings, snapshot=snapshot
    ) == ToolExecutionPolicy.DISABLED


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


async def test_same_project_session_reads_honor_policy(tmp_path) -> None:
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
        db,
        session,
        session_permission(target.id),
        root_files,
        ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db, session, path_request, root_files, ToolExecutionPolicy.AUTO
    ) == PermissionDecision.ALLOW
    assert await PermissionPolicy.evaluate(
        db,
        session,
        path_request,
        root_files,
        ToolExecutionPolicy.DISABLED,
    ) == PermissionDecision.DENY


@pytest.mark.parametrize("trust", list(TrustLevel))
async def test_project_file_grant_reuse_is_exact_and_rechecks_eligibility(tmp_path, trust):
    session = _session(user_id=uuid4(), project_id=uuid4(), session_type="chat")
    target = _session(user_id=session.user_id, project_id=session.project_id, session_type="chat")
    request = project_session_path_permission(
        project_id=session.project_id, session_id=target.id, relative_path="notes.txt",
        action_kind="read", action_label="Datei lesen",
    )
    profile_id = uuid4()
    grant = _grant(
        request, session.id, agent_profile_id=profile_id, trust_level=trust.value
    )
    roots = _root_files(tmp_path, session)
    db = _db_with_grants(grant)
    db.get.return_value = target
    for _ in range(2):
        assert await PermissionPolicy.evaluate(
            db, session, request, roots, ToolExecutionPolicy.ASK,
            agent_profile_id=profile_id, trust_level=trust,
        ) == PermissionDecision.ALLOW
    parameters = db.scalars.await_args.args[0].compile().params.values()
    assert session.id in parameters and profile_id in parameters and trust.value in parameters

    for changed in (
        {"relative_path": "notes.txt/child"}, {"relative_path": "other.txt"},
        {"project_id": uuid4()}, {"session_id": uuid4()},
    ):
        other = project_session_path_permission(**{
            "project_id": session.project_id, "session_id": target.id,
            "relative_path": "notes.txt", "action_kind": "read", "action_label": "Datei lesen",
            **changed,
        })
        assert not PermissionPolicy.grant_matches(grant, other, roots)
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.DISABLED
    ) == PermissionDecision.DENY
    for field, changed in (("user_id", uuid4()), ("project_id", uuid4()),
                           ("parent_session_id", uuid4()), ("session_type", "automation")):
        previous = getattr(target, field)
        setattr(target, field, changed)
        assert await PermissionPolicy.evaluate(
            db, session, request, roots, ToolExecutionPolicy.ASK
        ) == PermissionDecision.DENY
        setattr(target, field, previous)
    db.scalars.return_value.all.return_value = []
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ASK


@pytest.mark.parametrize("trust", list(TrustLevel))
@pytest.mark.parametrize("audience", ["private_models", "public_models", "both_models"])
async def test_profile_grants_bind_owner_and_data_audience(tmp_path, trust, audience):
    session = _session(user_id=uuid4())
    roots = _root_files(tmp_path, session)
    request = user_profile_permission(
        session.user_id, field_labels=["Name"], model_scope=audience
    )
    profile_id = uuid4()
    grant = _grant(
        request, session.id, agent_profile_id=profile_id, trust_level=trust.value
    )
    db = _db_with_grants(grant)
    same_audience = user_profile_permission(
        session.user_id, field_labels=["Sprache"], model_scope=audience
    )
    assert await PermissionPolicy.evaluate(
        db, session, same_audience, roots, ToolExecutionPolicy.ASK,
        agent_profile_id=profile_id, trust_level=trust,
    ) == PermissionDecision.ALLOW
    parameters = db.scalars.await_args.args[0].compile().params.values()
    assert session.id in parameters and profile_id in parameters and trust.value in parameters
    for other_audience in {"private_models", "public_models", "both_models"} - {audience}:
        other = user_profile_permission(
            session.user_id, field_labels=["Name"], model_scope=other_audience
        )
        assert await PermissionPolicy.evaluate(
            db, session, other, roots, ToolExecutionPolicy.ASK
        ) == PermissionDecision.ASK
    foreign = user_profile_permission(uuid4(), field_labels=["Name"], model_scope=audience)
    assert await PermissionPolicy.evaluate(
        db, session, foreign, roots, ToolExecutionPolicy.AUTO
    ) == PermissionDecision.DENY
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.DISABLED
    ) == PermissionDecision.DENY
    grant.resource_scope = str(session.user_id)
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ASK
    db.scalars.return_value.all.return_value = []
    assert await PermissionPolicy.evaluate(
        db, session, request, roots, ToolExecutionPolicy.ASK
    ) == PermissionDecision.ASK


@pytest.mark.parametrize("scope", [
    "null", "[]", "{}", "not-json",
    '{"modelScope":"both_models","userId":123}',
    '{"modelScope":[],"userId":"00000000-0000-4000-8000-000000000001"}',
    '{"modelScope":"current_model","userId":"00000000-0000-4000-8000-000000000001"}',
])
async def test_invalid_profile_scopes_are_denied(tmp_path, scope):
    session = _session(user_id=uuid4())
    request = user_profile_permission(session.user_id, field_labels=["Name"], model_scope="both_models")
    assert await PermissionPolicy.evaluate(
        _db_with_grants(), session, replace(request, resource_scope=scope),
        _root_files(tmp_path, session), ToolExecutionPolicy.AUTO,
    ) == PermissionDecision.DENY


async def test_session_grant_only_allows_its_exact_target(tmp_path) -> None:
    session = _session()
    granted_target = uuid4()
    grant = _grant(session_permission(granted_target), session.id)
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
    grant = _grant(request, session.id)

    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, request, _root_files(tmp_path, session)
    ) == PermissionDecision.ALLOW
    other_capability = media_permission(
        session.id, "media.generate.speech", "generate_speech"
    )
    assert await PermissionPolicy.evaluate(
        _db_with_grants(grant), session, other_capability, _root_files(tmp_path, session)
    ) == PermissionDecision.ALLOW


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
    grant = _grant(request, session.id)
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

    grant = _grant(request, session.id)
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
    grant = _grant(request, session.id)
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
    grant = _grant(request, session.id)
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
    grant = _grant(request, session.id)
    collection_grant = _grant(
        skill_write_permission(session.user_id, "Recherche"), session.id
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
    read_grant = _grant(session_permission(target_id), session.id)
    search_grant = _grant(session_search_permission(), session.id)
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


def test_webfetch_permission_hashes_and_displays_the_full_canonical_url() -> None:
    first = webfetch_permission("HTTPS://Example.com/path?token=secret#fragment")
    equivalent = webfetch_permission("https://example.com/path?token=secret")
    other_query = webfetch_permission("https://example.com/path?token=other")

    assert first.capability == "network.fetch"
    assert first.resource_type == "url"
    assert first.resource_scope == equivalent.resource_scope
    assert first.resource_scope != other_query.resource_scope
    assert len(first.resource_scope) == 64
    assert "https://example.com/path?token=secret" in first.summary
    assert first.resource_display == "https://example.com/path?token=secret"


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
    grant = _grant(granted, session.id)
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
