import asyncio
import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
import zxingcpp
from PIL import Image
from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.files.access as mock_files_access
import mato_loop_backend.integrations.registry as integration_registry
import mato_loop_backend.tools.access as mock_tools_access
import mato_loop_backend.tools.automations as mock_tools_automations
import mato_loop_backend.tools.catalog as mock_tools_catalog
import mato_loop_backend.tools.coding as mock_tools_coding
import mato_loop_backend.tools.computer as mock_tools_computer
import mato_loop_backend.tools.files as mock_tools_files
import mato_loop_backend.tools.identity as mock_tools_identity
import mato_loop_backend.tools.media as mock_tools_media
import mato_loop_backend.tools.memory as mock_tools_memory
import mato_loop_backend.tools.personal_integrations as mock_personal_integrations
import mato_loop_backend.tools.personal_settings as mock_personal_settings
import mato_loop_backend.tools.sessions as mock_tools_sessions
import mato_loop_backend.tools.skills as mock_tools_skills
import mato_loop_backend.tools.web as mock_tools_web
import mato_loop_backend.tools.web as tools_web
from mato_loop_backend.agent.projection import (
    _projection_messages,
    _tool_result_reference,
    message_dict,
)
from mato_loop_backend.agent.run_support import _argument_edit_payload, _tool_policy
from mato_loop_backend.files.search import MAX_MATCH_TEXT, MAX_SEARCH_RESULTS, RootFileSearch
from mato_loop_backend.files.service import FileRoot, RootFileError, RootFileService
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.integrations.youtube_transcript.client import TranscriptResult
from mato_loop_backend.media.attachments import AttachmentService
from mato_loop_backend.memory.retrieval import (
    MemoryRetrieval,
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.models import (
    AgentRun,
    Automation,
    AutomationRun,
    AutomationSchedule,
    ChatSession,
    ExecutionAgent,
    MemoryRecord,
    Message,
    MessageAttachment,
)
from mato_loop_backend.permissions.policy import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    personal_settings_permission,
)
from mato_loop_backend.permissions.resolution import tool_execution_policy
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    GENERAL_PROFILE_ID,
    AgentProfilesSettings,
    AgentProfileUnavailable,
    AiSettings,
    EffectiveAgentIdentity,
    IntegrationInstance,
    IntegrationSettings,
    MemorySettings,
    PersonaProfile,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    UserAgentIdentitySettings,
    UserIntegrationSettings,
    UserProfileSettings,
)
from mato_loop_backend.skills import SkillDraft
from mato_loop_backend.tools.actions import ApprovalEditField, ToolAction
from mato_loop_backend.tools.arguments import (
    CreateAutomationArguments,
    EditAutomationArguments,
    MemoryWriteArguments,
    ReadArguments,
    TaskArguments,
)
from mato_loop_backend.tools.catalog import ToolRegistry, tool_registry
from mato_loop_backend.tools.context import ToolContext
from mato_loop_backend.tools.contracts import (
    ToolArgumentsEdited,
    ToolAttachment,
    ToolDefinition,
    ToolResult,
)
from mato_loop_backend.tools.registry import BoundTool, EffectiveToolSet
from mato_loop_backend.web.fetch import DownloadedFile, WebFetchError, WebFetchImage, WebFetchResult


class _RequiredArguments(BaseModel):
    value: str


class _SharedArguments(BaseModel):
    integration_id: UUID
    resource_key: str
    value: str


class _EmptyArguments(BaseModel):
    pass


class _SharedConfig(BaseModel):
    endpoint: str


async def _successful_handler(_arguments, _context) -> ToolResult:
    return ToolResult.json({"ok": True})


async def test_sqlalchemy_tool_errors_reach_runtime_rollback() -> None:
    async def handler(_arguments, _context):
        raise SQLAlchemyError("database failed")

    definition = ToolDefinition("database_test", "Test", _EmptyArguments, handler)
    call = ToolCall("database-test", "database_test", {})

    with pytest.raises(SQLAlchemyError, match="database failed"):
        await ToolRegistry([definition]).execute(call, Mock())


async def test_task_tool_has_bounded_prompt_and_delegates(tmp_path) -> None:
    for tasks in ([], [{"description": "Inspect", "prompt": "x"}] * 5,
                  [{"description": "Inspect", "prompt": " "}],
                  [{"description": " ", "prompt": "Inspect"}],
                  [{"description": "Inspect", "prompt": "x" * 8_001}],
                  [{"description": "Inspect", "prompt": "x", "type": "explore"}]):
        with pytest.raises(ValidationError):
            TaskArguments(tasks=tasks)

    call = ToolCall("task-1", "task", {"tasks": [{"description": "Translate", "prompt": " Translate this text "}]})
    context = _context(tmp_path, call)
    context.task_handler = AsyncMock(return_value=ToolResult("findings", {}))

    result = await tool_registry.execute(call, context)

    assert result.content == "findings"
    context.task_handler.assert_awaited_once_with(TaskArguments(tasks=[
        {"description": "Translate", "prompt": "Translate this text"}
    ]))
    schema = next(item for item in tool_registry.schemas(IntegrationSettings()) if item["name"] == "task")
    assert set(schema["parameters"]["properties"]) == {"tasks"}
    assert schema["parameters"]["properties"]["tasks"]["maxItems"] == 4
    assert schema["parameters"]["$defs"]["DelegatedTask"]["properties"]["prompt"]["maxLength"] == 8_000


async def test_delegated_file_handler_rejects_internal_root_before_permissions(tmp_path) -> None:
    call = ToolCall("read", "read", {"root": "files", "path": "note.txt"})
    context = _context(tmp_path, call)
    context.session.parent_session_id = uuid4()

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "tool.unavailable"
    context.ask_handler.assert_not_awaited()


async def test_bound_tool_defaults_fill_only_fully_omitted_arguments(tmp_path) -> None:
    received = []

    async def handler(arguments, _context):
        received.append(arguments)
        return ToolResult.json({"ok": True})

    definition = ToolDefinition("example", "Example tool", _SharedArguments, handler)
    registry = ToolRegistry([])
    effective = EffectiveToolSet(
        registry,
        {
            "example": BoundTool(
                definition,
                default_arguments={
                    "integration_id": str(uuid4()),
                    "resource_key": "default",
                },
            )
        },
    )
    schema = effective.schemas()[0]
    assert schema["parameters"]["required"] == ["value"]
    assert schema["description"] == "Example tool"

    omitted = ToolCall("omitted", "example", {"value": "first"})
    result = await effective.execute(omitted, _context(tmp_path, omitted))
    assert result.is_error is False
    assert received[-1].resource_key == "default"

    explicit_id = uuid4()
    explicit = ToolCall(
        "explicit",
        "example",
        {
            "integration_id": str(explicit_id),
            "resource_key": "explicit",
            "value": "second",
        },
    )
    result = await effective.execute(explicit, _context(tmp_path, explicit))
    assert result.is_error is False
    assert received[-1].integration_id == explicit_id
    assert received[-1].resource_key == "explicit"

    partial = ToolCall(
        "partial", "example", {"resource_key": "explicit", "value": "third"}
    )
    result = await effective.execute(partial, _context(tmp_path, partial))
    assert result.is_error is True
    assert result.metadata["errorCode"] == "tool.invalidArguments"


async def test_argument_edit_is_persisted_before_validated_retry(tmp_path) -> None:
    events = []

    async def handler(arguments, _context):
        events.append(("execute", arguments.value))
        if arguments.value == "original":
            raise ToolArgumentsEdited(((('value',), "edited"),))
        return ToolResult.json({"value": arguments.value})

    async def persist(arguments):
        events.append(("persist", arguments["value"]))

    definition = ToolDefinition(
        "example",
        "Example tool",
        _RequiredArguments,
        handler,
        approval_edit_fields=(ApprovalEditField(("value",), "Wert", required=True),),
    )
    effective = EffectiveToolSet(ToolRegistry([]), {"example": BoundTool(definition)})
    call = ToolCall("edit", "example", {"value": "original"})
    context = _context(tmp_path, call)
    context.arguments_edited_handler = persist

    result = await effective.execute(call, context)

    assert events == [
        ("execute", "original"),
        ("persist", "edited"),
        ("execute", "edited"),
    ]
    assert call.arguments == {"value": "edited"}
    assert context.approval_resolution["argument_edit_approved"]
    assert result.metadata["userEdited"] is True


async def test_argument_edit_rejects_undeclared_path(tmp_path) -> None:
    async def handler(_arguments, _context):
        raise ToolArgumentsEdited(((('other',), "edited"),))

    definition = ToolDefinition(
        "example",
        "Example tool",
        _RequiredArguments,
        handler,
        approval_edit_fields=(ApprovalEditField(("value",), "Wert"),),
    )
    effective = EffectiveToolSet(ToolRegistry([]), {"example": BoundTool(definition)})
    call = ToolCall("edit", "example", {"value": "original"})

    result = await effective.execute(call, _context(tmp_path, call))

    assert result.metadata["errorCode"] == "approval.requestChanged"
    assert call.arguments == {"value": "original"}


def test_argument_edit_uses_date_input_for_all_day_event() -> None:
    definition = SimpleNamespace(
        name="calendar_create_event",
        approval_edit_fields=(
            ApprovalEditField(("timing", "start"), "Beginn", "temporal", True),
        )
    )

    payload = _argument_edit_payload(
        definition,
        {"timing": {"type": "allDay", "start": "2026-09-20"}},
    )

    assert payload is not None
    assert payload["fields"][0]["inputType"] == "date"
    assert payload["fields"][0]["value"] == "2026-09-20"


def _definition(name: str = "example") -> ToolDefinition:
    return ToolDefinition(name, "Example tool", _RequiredArguments, _successful_handler)


def _session() -> ChatSession:
    return ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
    )


def _context(
    tmp_path,
    call: ToolCall,
    *,
    decision=PermissionDecision.ALLOW,
    web_fetch_policy=ToolExecutionPolicy.DISABLED,
    public_addresses_only=True,
) -> ToolContext:
    session = _session()
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    tool_settings = ToolSettings()
    tool_settings.private_permissions.policies["network.fetch"] = web_fetch_policy
    tool_settings.public_permissions.policies["network.fetch"] = web_fetch_policy
    tool_settings.web_fetch.public_addresses_only = public_addresses_only
    context = ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=RootFileService(
            session.id,
            session_storage=storage,
        ),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=decision),
        tool_settings=tool_settings,
    )
    context.db.scalar.return_value = Mock(data=_settings_record(tool_settings))
    context.model_trust_level = TrustLevel.PRIVATE
    return context


def _session_read_records(context: ToolContext, messages: list[Message], runs=()) -> None:
    context.db.scalars.side_effect = lambda statement: Mock(all=Mock(
        return_value=list(runs) if "FROM agent_runs" in str(statement) else messages,
    ))


async def test_execution_agent_tools_list_and_switch_with_scoped_approval(monkeypatch, tmp_path) -> None:
    assert tool_registry.action_default("execution_agent.list", TrustLevel.PRIVATE) == ToolExecutionPolicy.AUTO
    assert tool_registry.action_default("execution_agent.list", TrustLevel.PUBLIC) == ToolExecutionPolicy.ASK
    assert all(
        tool_registry.action_default("execution_agent.select", trust) == ToolExecutionPolicy.ASK
        for trust in (TrustLevel.PRIVATE, TrustLevel.PUBLIC)
    )
    first = ExecutionAgent(id=uuid4(), user_id=uuid4(), alias="Server", platform="linux")
    listing = ToolCall("list", "list_execution_agents", {})
    context = _context(tmp_path, listing)
    first.user_id = context.session.user_id
    context.db.scalars.return_value = Mock(all=Mock(return_value=[first]))
    monkeypatch.setattr(mock_tools_computer.execution_agent_connections, "is_connected", lambda agent_id: agent_id == first.id)

    result = await tool_registry.execute(listing, context)

    assert json.loads(result.content)["items"] == [{
        "id": str(first.id), "alias": "Server", "platform": "linux",
        "connected": True, "selected": False,
    }]
    assert context.ask_handler.await_args.args[0].resource_scope == str(context.session.user_id)
    assert "execution_agents.user_id" in str(context.db.scalars.await_args.args[0])

    call = ToolCall("select", "select_execution_agent", {"agent_id": str(first.id)})
    context.tool_call = call
    context.session.session_type = "chat"
    context.session.control_center = {"terminal_auto_attach": True, "tabs": [{"app_id": "terminal"}]}
    context.db.scalar.return_value = first
    run = AgentRun(id=context.run_id, session_id=context.session.id)
    context.db.get.return_value = run
    monkeypatch.setattr(mock_tools_computer.execution_agent_connections, "snapshot", lambda _id: frozenset({"filesystem.resolve", "shell.run"}))
    monkeypatch.setattr(
        "mato_loop_backend.sessions.service.validate_execution_preferences",
        AsyncMock(return_value=("/srv/new", "ask")),
    )

    switched = await tool_registry.execute(call, context)

    assert switched.metadata["executionAgentChanged"] is True
    assert context.session.preferred_execution_agent_id == first.id
    assert context.session.control_center == {"tabs": [{"app_id": "terminal"}]}
    assert context.session.current_directory == "/srv/new"
    assert run.execution_agent_id == first.id
    assert run.current_directory_snapshot == "/srv/new"
    assert run.execution_operations == ["filesystem.resolve", "shell.run"]
    permission = context.ask_handler.await_args.args[0]
    assert permission.force_ask is True
    assert permission.available_decisions == ("allow_once", "deny")
    assert permission.resource_scope == str(first.id)


async def test_execution_agent_switch_denial_keeps_existing_binding(tmp_path) -> None:
    agent_id = uuid4()
    call = ToolCall("select", "select_execution_agent", {"agent_id": str(agent_id)})
    context = _context(tmp_path, call, decision=PermissionDecision.USER_DENIED)
    context.session.session_type = "chat"
    context.db.scalar.return_value = ExecutionAgent(id=agent_id, user_id=context.session.user_id, alias="Server")
    context.session.preferred_execution_agent_id = uuid4()
    context.session.current_directory = "/old"

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "permission.denied"
    assert context.session.current_directory == "/old"
    context.db.get.assert_not_awaited()


async def test_execution_agent_switch_rechecks_connection_after_approval(monkeypatch, tmp_path) -> None:
    agent_id = uuid4()
    call = ToolCall("select", "select_execution_agent", {"agent_id": str(agent_id)})
    context = _context(tmp_path, call)
    context.session.session_type = "chat"
    context.session.current_directory = "/old"
    context.db.scalar.return_value = ExecutionAgent(
        id=agent_id, user_id=context.session.user_id, alias="Server",
    )
    monkeypatch.setattr(
        "mato_loop_backend.sessions.service.validate_execution_preferences",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(mock_tools_computer.execution_agent_connections, "snapshot", lambda _id: None)

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "executionAgent.unavailable"
    assert context.session.preferred_execution_agent_id is None
    assert context.session.current_directory == "/old"
    context.db.get.assert_not_awaited()


def _tool_settings(data: dict) -> ToolSettings:
    values = dict(data)
    permissions = {
        key: values.pop(key)
        for key in ("policies", "integrationPolicies", "resourcePolicies")
        if key in values
    }
    if permissions:
        values["privatePermissions"] = permissions
        values["publicPermissions"] = permissions
    return ToolSettings.model_validate(values)


def _settings_record(settings: ToolSettings) -> dict:
    permissions = settings.private_permissions.model_dump(mode="json")
    return {
        **settings.model_dump(mode="json"),
        "builtin_access_overrides": {
            str(GENERAL_PROFILE_ID): {
                "private_permissions": permissions,
                "public_permissions": permissions,
            }
        },
    }


def _set_profile_settings(context: ToolContext, settings: ToolSettings) -> None:
    context.db.scalar.return_value.data = _settings_record(settings)


async def test_missing_profile_reload_keeps_private_trust(monkeypatch, tmp_path) -> None:
    call = ToolCall("fetch", "webfetch", {"url": "https://example.com"})
    context = _context(tmp_path, call)
    context.model_trust_level = TrustLevel.PRIVATE
    fallback = ToolSettings()
    monkeypatch.setattr(mock_tools_access, 'load_agent_profile_tools', AsyncMock(side_effect=AgentProfileUnavailable))
    load_fallback = AsyncMock(return_value=fallback)
    monkeypatch.setattr(mock_tools_access, 'load_effective_tools', load_fallback)

    result = await mock_tools_access.profile_tool_settings(context)

    load_fallback.assert_awaited_once_with(
        context.db, context.session.user_id, TrustLevel.PRIVATE
    )
    assert result._agent_profile_unavailable is True


async def test_tool_reload_uses_run_profile_snapshot(monkeypatch, tmp_path) -> None:
    call = ToolCall("read", "read", {"root": "files", "path": "note.md"})
    context = _context(tmp_path, call)
    context.session.agent_profile_id = uuid4()
    context.agent_profile_id = uuid4()
    load = AsyncMock(return_value=(ToolSettings(), None))
    monkeypatch.setattr(mock_tools_access, 'load_agent_profile_tools', load)

    await mock_tools_access.profile_tool_settings(context)

    assert load.await_args.args[2] == context.agent_profile_id


def _shared_integration(name: str) -> IntegrationInstance:
    return IntegrationInstance(
        name=name,
        integration_type="tasks",
        adapter_type="synthetic",
        config={"endpoint": name.casefold()},
    )


def _shared_adapter(
    monkeypatch,
    handler: AsyncMock,
    *,
    releases_private_data: bool = False,
    applicable_capabilities: frozenset[str] | None = None,
) -> ToolAction:
    action = ToolAction(
        "tasks.read",
        "Aufgaben lesen",
        ToolExecutionPolicy.ASK,
        ToolExecutionPolicy.ASK,
    )

    async def resources(_instance, _credential):
        return (
            IntegrationResource(
                "project:main",
                "Hauptprojekt",
                applicable_capabilities=applicable_capabilities,
            ),
        )

    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("tasks", "synthetic"),
        IntegrationAdapterDefinition(
            integration_type="tasks",
            adapter_type="synthetic",
            config_model=_SharedConfig,
            resource_actions=(action,),
            resource_loader=resources,
            shared_tools=(
                ToolContribution(
                    name="tasks_read",
                    description="Read tasks from one configured task service.",
                    arguments_model=_SharedArguments,
                    handler=handler,
                    actions=(action,),
                    instance_argument="integration_id",
                    resource_argument="resource_key",
                    releases_private_data=releases_private_data,
                    authorization_factory=lambda arguments, _context: (
                        PermissionRequest(
                            capability=action.capability,
                            resource_type="network_service",
                            resource_scope=arguments.resource_key,
                            action_kind="tasks_read",
                            risk_level="medium",
                            title="Aufgaben lesen?",
                            summary="Der Agent möchte Aufgaben lesen.",
                        ),
                    ),
                ),
            ),
        ),
    )
    return action


def test_tool_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate tool: example"):
        ToolRegistry([_definition(), _definition()])


async def test_dynamic_tool_unresolved_local_ref_is_controlled_invalid_arguments(
    tmp_path,
) -> None:
    definition = ToolDefinition(
        "dynamic",
        "Dynamic tool",
        None,
        _successful_handler,
        arguments_schema={"type": "object", "$ref": "#/$defs/missing"},
    )
    call = ToolCall("call", "dynamic", {})

    result = await ToolRegistry([definition]).execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.invalidArguments"}


async def test_generate_qr_code_returns_scannable_png(tmp_path) -> None:
    content = "WIFI:T:WPA;S:Gäste;P:correct-horse-battery-staple;;"
    call = ToolCall(
        id="qr-generate",
        name="generate_qr_code",
        arguments={"content": content},
    )

    result = await tool_registry.execute(call, _context(tmp_path, call))

    assert result.is_error is False
    assert result.content == "QR code generated."
    assert result.attachments[0].mime_type == "image/png"
    assert result.attachments[0].filename == "qr-code.png"
    with Image.open(BytesIO(result.attachments[0].data)) as image:
        decoded = zxingcpp.read_barcode(
            image,
            formats=zxingcpp.BarcodeFormat.QRCode,
            text_mode=zxingcpp.TextMode.Plain,
        )
    assert decoded is not None
    assert decoded.text == content


async def test_protected_wifi_qr_escapes_values_without_releasing_password(tmp_path) -> None:
    ssid_id, password_id, security_id = uuid4(), uuid4(), uuid4()
    call = ToolCall("qr-protected", "generate_qr_code", {"wifi": {
        "ssid": {"parts": [{"value_ref": f"run-secret:{ssid_id}"}]},
        "password": {"parts": [{"value_ref": f"run-secret:{password_id}"}]},
        "security": {"parts": [{"value_ref": f"run-secret:{security_id}"}]},
    }})
    context = _context(tmp_path, call)
    context.one_time_secrets.update({
        ssid_id: "Gä;ste", password_id: 'a\\b:c,d"e', security_id: "WPA/WPA2",
    })

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert result.attachments[0].metadata == {"protectedInput": True}
    assert "a\\b:c" not in result.content
    with Image.open(BytesIO(result.attachments[0].data)) as image:
        decoded = zxingcpp.read_barcode(image, formats=zxingcpp.BarcodeFormat.QRCode,
                                       text_mode=zxingcpp.TextMode.Plain)
    assert decoded.text == 'WIFI:T:WPA;S:Gä\\;ste;P:a\\\\b\\:c\\,d\\"e;;'

    other_run = await tool_registry.execute(call, _context(tmp_path, call))
    assert other_run.metadata == {"errorCode": "secret.valueRefUnavailable"}

    oversized = ToolCall("qr-oversized-protected", "generate_qr_code", {
        "content": {"parts": [{"value_ref": f"run-secret:{password_id}"}]},
    })
    context.one_time_secrets[password_id] = "x" * 2_001
    rejected = await tool_registry.execute(oversized, context)
    assert rejected.metadata == {"errorCode": "tool.invalidArguments"}
    assert "x" * 200 not in rejected.content


async def test_protected_text_can_write_file_after_authorization(tmp_path) -> None:
    secret_id = uuid4()
    call = ToolCall("write-protected", "write_file", {
        "root": "session", "path": "credentials.txt",
        "content": {"parts": ["token=", {"value_ref": f"run-secret:{secret_id}"}]},
    })
    context = _context(tmp_path, call)
    context.one_time_secrets[secret_id] = "private-value"

    denied_context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    denied = await tool_registry.execute(call, denied_context)
    assert denied.metadata == {"errorCode": "permission.denied"}

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert "private-value" not in result.content
    assert context.root_files.read_bytes("session", "credentials.txt", limit=1_000) == b"token=private-value"

    edit = ToolCall("edit-protected", "edit_file", {
        "root": "session", "path": "credentials.txt",
        "oldText": {"parts": ["token=", {"value_ref": f"run-secret:{secret_id}"}]},
        "newText": {"parts": ["token=", {"value_ref": f"run-secret:{secret_id}"}, "-updated"]},
    })
    context.tool_call = edit
    edited = await tool_registry.execute(edit, context)
    assert not edited.is_error
    assert context.root_files.read_bytes("session", "credentials.txt", limit=1_000) == b"token=private-value-updated"


async def test_generate_qr_code_rejects_oversized_utf8_content(tmp_path) -> None:
    call = ToolCall(
        id="qr-large",
        name="generate_qr_code",
        arguments={"content": "ü" * 1_001},
    )

    result = await tool_registry.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.invalidArguments"}


async def test_read_qr_code_uses_current_session_image_and_source_trust(
    tmp_path, monkeypatch
) -> None:
    content = "https://example.com/über-uns"
    generate_call = ToolCall(
        id="qr-generate",
        name="generate_qr_code",
        arguments={"content": content},
    )
    generated = await tool_registry.execute(
        generate_call, _context(tmp_path, generate_call)
    )
    attachment_id = uuid4()
    read_call = ToolCall(
        id="qr-read",
        name="read_qr_code",
        arguments={"attachment_id": str(attachment_id)},
    )
    context = _context(tmp_path, read_call)
    message = Message(
        id=uuid4(),
        session_id=context.session.id,
        role="user",
        content="Lies den QR-Code",
        trust_level="private",
    )
    attachment = MessageAttachment(
        id=attachment_id,
        message_id=message.id,
        message=message,
        position=0,
        kind="image",
        category="user_upload",
        processing_status="ready",
        mime_type="image/png",
        size_bytes=len(generated.attachments[0].data),
        sha256="0" * 64,
        file_root_id=context.session.id,
        file_id=uuid4(),
        file_revision=1,
        file_path=f"attachments/{attachment_id}/qr.png",
    )
    context.db.scalar.return_value = attachment
    read_image = AsyncMock(return_value=generated.attachments[0].data)
    monkeypatch.setattr(AttachmentService, "read_image", read_image)

    result = await tool_registry.execute(read_call, context)

    assert json.loads(result.content) == {"codes": [content]}
    assert result.trust_level == TrustLevel.PRIVATE
    read_image.assert_awaited_once_with(attachment, context.session.id, current_only=True)
    statement = context.db.scalar.await_args.args[0]
    assert context.session.id in statement.compile().params.values()

    attachment.metadata_json = {"protectedInput": True}
    blocked = await tool_registry.execute(read_call, context)
    assert blocked.metadata == {"errorCode": "permission.denied"}
    read_image.assert_awaited_once()
    assert message_dict(message)["attachments"] == []
    assert str(attachment_id) not in message_dict(message)["content"]


async def test_read_qr_code_rejects_stale_attachment_id(tmp_path, monkeypatch) -> None:
    attachment_id = uuid4()
    call = ToolCall(
        id="qr-read", name="read_qr_code", arguments={"attachment_id": str(attachment_id)}
    )
    context = _context(tmp_path, call)
    data = mock_tools_media._qr_code_png("original")
    revision = context.root_files.create_output_file("qr.png", data)
    attachment = MessageAttachment(
        id=attachment_id,
        kind="image",
        processing_status="ready",
        mime_type="image/png",
        size_bytes=revision.size_bytes,
        sha256=revision.sha256,
        file_root_id=context.session.id,
        file_id=revision.file_id,
        file_revision=revision.revision,
        file_path=revision.path,
    )
    context.db.scalar.return_value = attachment
    monkeypatch.setattr(
        AttachmentService, "_root_files", lambda *_args, **_kwargs: context.root_files
    )
    context.root_files.append_output_revision(revision.file_id, mock_tools_media._qr_code_png("new"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "attachment.notFound"}
    assert await AttachmentService().read_image(attachment, context.session.id) == data


async def test_read_qr_code_rejects_unknown_session_attachment(tmp_path) -> None:
    read_call = ToolCall(
        id="qr-read",
        name="read_qr_code",
        arguments={"attachment_id": str(uuid4())},
    )
    context = _context(tmp_path, read_call)
    context.db.scalar.return_value = None
    context.db.scalars.return_value.all = lambda: []

    result = await tool_registry.execute(read_call, context)

    assert result.metadata == {"errorCode": "attachment.notFound"}
    statement = context.db.scalar.await_args.args[0]
    assert context.session.id in statement.compile().params.values()


def test_effective_toolset_accepts_host_registered_integration_tools(monkeypatch) -> None:
    contributions = IntegrationContributions(
        tools=(
            ToolContribution(
                name="integration_example",
                description="Example integration tool.",
                arguments_model=_RequiredArguments,
                handler=_successful_handler,
            ),
        ),
        prompt_fragments=("Trusted integration instruction.",),
    )
    monkeypatch.setattr(mock_tools_catalog, 'effective_integration_contributions', Mock(return_value=contributions))

    effective = ToolRegistry([]).effective(IntegrationSettings())

    assert [item["name"] for item in effective.schemas()] == ["integration_example"]
    assert effective.prompt_fragments == ("Trusted integration instruction.",)


async def test_bound_integration_tool_rejects_changed_source(tmp_path, monkeypatch) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    contribution = ToolContribution(
        name="integration_example",
        description="Example integration tool.",
        arguments_model=_RequiredArguments,
        handler=_successful_handler,
        integration_id=str(integration.id),
    )
    monkeypatch.setattr(mock_tools_catalog, 'effective_integration_contributions', Mock(return_value=IntegrationContributions(tools=(contribution,))))
    effective = ToolRegistry([]).effective(
        IntegrationSettings(instances=[integration])
    )
    changed = integration.model_copy(deep=True)
    changed.config = {"base_url": "https://other.example.com"}
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[changed])),
    )
    call = ToolCall(
        id="integration", name="integration_example", arguments={"value": "test"}
    )

    result = await effective.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.unavailable"}


async def test_bound_integration_tool_rechecks_source_after_approval(
    tmp_path, monkeypatch
) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    changed = integration.model_copy(deep=True)
    changed.enabled = False
    request = PermissionRequest(
        capability="integration.example",
        resource_type="network_service",
        resource_scope="example",
        action_kind="integration_example",
        risk_level="medium",
        title="Integration verwenden?",
        summary="Der Agent möchte eine Integration verwenden.",
        policy_resource_key="calendar:work",
    )
    contribution = ToolContribution(
        name="integration_example",
        description="Example integration tool.",
        arguments_model=_RequiredArguments,
        handler=_successful_handler,
        actions=(
            ToolAction(
                "integration.example",
                "Integration verwenden",
                ToolExecutionPolicy.ASK,
                ToolExecutionPolicy.ASK,
            ),
        ),
        integration_id=str(integration.id),
        authorization_factory=lambda _arguments, _context: (request,),
    )
    monkeypatch.setattr(mock_tools_catalog, 'effective_integration_contributions', Mock(return_value=IntegrationContributions(tools=(contribution,))))
    effective = ToolRegistry([]).effective(
        IntegrationSettings(instances=[integration])
    )
    loads = 0

    async def load_integrations(_db, _settings_type, **_kwargs):
        nonlocal loads
        loads += 1
        return IntegrationSettings(instances=[integration if loads == 1 else changed])

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_integrations))
    call = ToolCall(
        id="integration", name="integration_example", arguments={"value": "test"}
    )

    context = _context(tmp_path, call)
    result = await effective.execute(call, context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    permission = context.ask_handler.await_args.args[0]
    assert permission.policy_integration_id == integration.id
    assert permission.policy_resource_key == "calendar:work"


async def test_shared_tool_binds_two_instances_and_targets_selected_resource(
    tmp_path, monkeypatch
) -> None:
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    _shared_adapter(monkeypatch, handler)
    first = _shared_integration("First")
    second = _shared_integration("Second")
    settings = IntegrationSettings(instances=[first, second])
    effective = ToolRegistry([]).effective(settings)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(
        side_effect=lambda _db, model, **_kwargs: settings if model is IntegrationSettings else model()
    ))
    call = ToolCall(
        id="shared",
        name="tasks_read",
        arguments={
            "integration_id": str(second.id),
            "resource_key": "project:main",
            "value": "open",
        },
    )
    context = _context(tmp_path, call)
    context.db.scalar.return_value = None

    result = await effective.execute(call, context)

    assert [schema["name"] for schema in effective.schemas()] == ["tasks_read"]
    assert len(effective.tools["tasks_read"].integration_revisions) == 2
    assert result.is_error is False
    permission = context.ask_handler.await_args.args[0]
    assert permission.policy_integration_id == second.id
    assert permission.policy_resource_key == "project:main"
    assert context.bound_options["integration_id"] == str(second.id)
    assert context.bound_options["binding_revision"] == dict(
        effective.tools["tasks_read"].integration_revisions
    )[str(second.id)]
    handler.assert_awaited_once()


async def test_shared_tool_rejects_unbound_and_stale_instance_ids(
    tmp_path, monkeypatch
) -> None:
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    _shared_adapter(monkeypatch, handler)
    integration = _shared_integration("First")
    settings = IntegrationSettings(instances=[integration])
    effective = ToolRegistry([]).effective(settings)

    unbound_call = ToolCall(
        id="unbound",
        name="tasks_read",
        arguments={
            "integration_id": str(uuid4()),
            "resource_key": "project:main",
            "value": "open",
        },
    )
    unbound = await effective.execute(unbound_call, _context(tmp_path, unbound_call))

    changed = integration.model_copy(deep=True)
    changed.config["endpoint"] = "changed"
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=IntegrationSettings(instances=[changed])),
    )
    stale_call = ToolCall(
        id="stale",
        name="tasks_read",
        arguments={
            "integration_id": str(integration.id),
            "resource_key": "project:main",
            "value": "open",
        },
    )
    stale = await effective.execute(stale_call, _context(tmp_path, stale_call))

    assert unbound.metadata == {"errorCode": "tool.unavailable"}
    assert stale.metadata == {"errorCode": "tool.unavailable"}
    handler.assert_not_awaited()


async def test_shared_tool_rejects_action_irrelevant_to_selected_resource(
    tmp_path, monkeypatch
) -> None:
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    _shared_adapter(
        monkeypatch,
        handler,
        applicable_capabilities=frozenset(),
    )
    integration = _shared_integration("First")
    settings = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(mock_tools_access, 'profile_tool_settings', AsyncMock(return_value=ToolSettings()))
    effective = ToolRegistry([]).effective(settings)
    call = ToolCall(
        id="irrelevant",
        name="tasks_read",
        arguments={
            "integration_id": str(integration.id),
            "resource_key": "project:main",
            "value": "open",
        },
    )

    result = await effective.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.unavailable"}
    handler.assert_not_awaited()


async def test_aggregate_shared_tool_binds_all_current_instances(
    tmp_path, monkeypatch
) -> None:
    action = ToolAction(
        "tasks.read", "Aufgaben lesen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK
    )

    async def handler(_arguments, context):
        return ToolResult.json({"ids": list(context.bound_options["integration_ids"])})

    monkeypatch.setitem(
        integration_registry._ADAPTERS,
        ("tasks", "synthetic"),
        IntegrationAdapterDefinition(
            integration_type="tasks",
            adapter_type="synthetic",
            config_model=_SharedConfig,
            resource_actions=(action,),
            shared_tools=(
                ToolContribution(
                    name="tasks_list_accounts",
                    description="List task accounts.",
                    arguments_model=_EmptyArguments,
                    handler=handler,
                    actions=(action,),
                    authorization_factory=lambda _arguments, _context: (
                        PermissionRequest(
                            capability=action.capability,
                            resource_type="network_service",
                            resource_scope="task_accounts",
                            action_kind="tasks_list_accounts",
                            risk_level="medium",
                            title="Aufgabenkonten lesen?",
                            summary="Der Agent möchte Aufgabenkonten lesen.",
                        ),
                    ),
                ),
            ),
        ),
    )
    first = _shared_integration("First")
    second = _shared_integration("Second")
    settings = IntegrationSettings(instances=[first, second])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(
        side_effect=lambda _db, model, **_kwargs: settings if model is IntegrationSettings else model()
    ))
    effective = ToolRegistry([]).effective(settings)
    call = ToolCall(id="aggregate", name="tasks_list_accounts", arguments={})
    context = _context(tmp_path, call)

    result = await effective.execute(call, context)

    assert json.loads(result.content) == {
        "ids": [str(first.id), str(second.id)]
    }
    request = context.ask_handler.await_args.args[0]
    assert request.policy_integration_id is None


async def test_private_integration_tool_uses_only_operation_authorization(
    tmp_path, monkeypatch
) -> None:
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    _shared_adapter(monkeypatch, handler, releases_private_data=True)
    integration = _shared_integration("Private")
    settings = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(
        side_effect=lambda _db, model, **_kwargs: settings if model is IntegrationSettings else model()
    ))
    effective = ToolRegistry([]).effective(settings)
    call = ToolCall(
        id="private",
        name="tasks_read",
        arguments={
            "integration_id": str(integration.id),
            "resource_key": "project:main",
            "value": "open",
        },
    )
    context = _context(tmp_path, call)

    result = await effective.execute(call, context)

    requests = [item.args[0] for item in context.ask_handler.await_args_list]
    assert [request.capability for request in requests] == ["tasks.read"]
    assert result.metadata["privateIntegrationData"] is True


async def test_contributed_authorization_must_cover_every_declared_action(
    tmp_path, monkeypatch
) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    request = PermissionRequest(
        capability="integration.read",
        resource_type="network_service",
        resource_scope="example",
        action_kind="integration_example",
        risk_level="medium",
        title="Integration lesen?",
        summary="Der Agent möchte eine Integration lesen.",
    )
    contribution = ToolContribution(
        name="integration_example",
        description="Example integration tool.",
        arguments_model=_RequiredArguments,
        handler=_successful_handler,
        actions=(
            ToolAction(
                "integration.read", "Lesen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK
            ),
            ToolAction(
                "integration.write", "Schreiben", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK
            ),
        ),
        integration_id=str(integration.id),
        authorization_factory=lambda _arguments, _context: (request,),
    )
    monkeypatch.setattr(mock_tools_catalog, 'effective_integration_contributions', Mock(return_value=IntegrationContributions(tools=(contribution,))))
    settings = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    effective = ToolRegistry([]).effective(settings)
    call = ToolCall(
        id="integration", name="integration_example", arguments={"value": "test"}
    )

    result = await effective.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.unavailable"}


async def test_resource_policy_disabled_blocks_contributed_tool_effect(
    tmp_path, monkeypatch
) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    action = ToolAction(
        "integration.resource.read",
        "Lesen",
        ToolExecutionPolicy.ASK,
        ToolExecutionPolicy.ASK,
    )
    request = PermissionRequest(
        capability=action.capability,
        resource_type="network_service",
        resource_scope="calendar:work",
        action_kind="integration_example",
        risk_level="medium",
        title="Ressource lesen?",
        summary="Der Agent möchte eine Integrationsressource lesen.",
        policy_resource_key="calendar:work",
    )
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    contribution = ToolContribution(
        name="integration_example",
        description="Example integration tool.",
        arguments_model=_RequiredArguments,
        handler=handler,
        actions=(action,),
        integration_id=str(integration.id),
        authorization_factory=lambda _arguments, _context: (request,),
    )
    monkeypatch.setattr(mock_tools_catalog, 'effective_integration_contributions', Mock(return_value=IntegrationContributions(tools=(contribution,))))
    integration_settings = IntegrationSettings(instances=[integration])
    effective = ToolRegistry([]).effective(integration_settings)
    settings = _tool_settings(
        {
            "resourcePolicies": {
                str(integration.id): {
                    "calendar:work": {action.capability: "disabled"}
                }
            }
        }
    )

    async def authorize(permission):
        policy = tool_execution_policy(
            settings,
            permission.capability,
            action.default_policy_for(settings.trust_level),
            integration_id=permission.policy_integration_id,
            resource_key=permission.policy_resource_key,
        )
        return (
            PermissionDecision.DENY
            if policy == ToolExecutionPolicy.DISABLED
            else PermissionDecision.ALLOW
        )

    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=integration_settings)
    )
    call = ToolCall(
        id="integration", name="integration_example", arguments={"value": "test"}
    )
    context = _context(tmp_path, call)
    context.ask_handler = AsyncMock(side_effect=authorize)

    result = await effective.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    handler.assert_not_awaited()


def test_webfetch_schema_describes_verified_image_attachments() -> None:
    schemas = tool_registry.schemas(IntegrationSettings(), webfetch_enabled=True)
    webfetch = next(item for item in schemas if item["name"] == "webfetch")

    assert "validated image attachments" in webfetch["description"]
    assert "citationEligible" in webfetch["description"]


def test_file_tool_schemas_require_explicit_roots_and_exact_paths() -> None:
    schemas = {
        item["name"]: item
        for item in tool_registry.schemas(IntegrationSettings())
    }

    for name in {
        "read",
        "glob",
        "grep",
        "provide_file",
        "write_file",
        "create_directory",
        "delete_path",
    }:
        parameters = schemas[name]["parameters"]
        assert "root" in parameters["required"]
        assert parameters["properties"]["root"]["enum"] == ["files", "computer"]
        assert "path" in parameters["properties"]
    assert schemas["read"]["parameters"]["properties"]["offset"]["default"] == 1
    assert schemas["read"]["parameters"]["properties"]["limit"]["default"] == 1000
    assert "revision" not in schemas["read"]["parameters"]["properties"]
    with pytest.raises(ValidationError):
        ReadArguments(root="session", path="notes.txt", revision=1)
    assert schemas["glob"]["parameters"]["properties"]["path"]["default"] == "."
    assert schemas["grep"]["parameters"]["properties"]["path"]["default"] == "."
    assert "pattern" in schemas["glob"]["parameters"]["required"]
    assert "pattern" in schemas["grep"]["parameters"]["required"]
    assert schemas["delete_path"]["parameters"]["properties"]["recursive"]["default"] is False
    for name in {"copy_path", "move_path"}:
        assert set(schemas[name]["parameters"]["required"]) == {
            "source_root",
            "source_path",
            "destination_root",
            "destination_path",
        }
        assert schemas[name]["parameters"]["properties"]["source_root"]["enum"] == [
            "files",
            "computer",
        ]
        assert schemas[name]["parameters"]["properties"]["destination_root"][
            "enum"
        ] == ["files", "computer"]
        assert "exact new destination path" in schemas[name]["description"]


def test_search_descriptions_explain_root_specific_exclusions_and_limits() -> None:
    schemas = {item["name"]: item for item in tool_registry.schemas(IntegrationSettings())}
    for name in ("glob", "grep"):
        description = schemas[name]["description"]
        assert "100" in description
        assert "root=computer" in description
        assert "root=files" in description
        assert "ignores" in description
        assert "truncated=true" in description
        assert "symlinks" in description
        assert not {"hidden", "ignored"} & schemas[name]["parameters"]["properties"].keys()
    assert "Skip hidden/.git/symlinks" in schemas["glob"]["description"]
    assert "root=files includes hidden" in schemas["grep"]["description"]
    assert "2000 characters" in schemas["grep"]["description"]


@pytest.mark.parametrize("extra", [False, True])
async def test_files_search_result_limit_requires_an_extra_eligible_result(tmp_path, extra) -> None:
    context = _context(tmp_path, ToolCall("search", "glob", {"root": "files", "pattern": "*.txt"}))
    root = context.root_files._files_path()
    for index in range(MAX_SEARCH_RESULTS + int(extra)):
        (root / f"{index:03}.txt").write_text("match\n")
    (root / ".git").mkdir()
    (root / ".git" / "excluded.txt").write_text("match\n")
    search = RootFileSearch(context.root_files)

    glob = await search.glob(FileRoot.SESSION, ".", "*.txt")
    grep = await search.grep(FileRoot.SESSION, ".", "match", "*.txt")

    assert len(glob["paths"]) == MAX_SEARCH_RESULTS
    assert len(grep["matches"]) == MAX_SEARCH_RESULTS
    assert glob["truncated"] is extra
    assert grep["truncated"] is extra


@pytest.mark.parametrize("length", [MAX_MATCH_TEXT, MAX_MATCH_TEXT + 1])
async def test_files_search_clips_text_without_stopping(tmp_path, length) -> None:
    context = _context(tmp_path, ToolCall("search", "grep", {"root": "files", "pattern": "x+"}))
    root = context.root_files._files_path()
    (root / "long.txt").write_bytes(b"x" * length + b"\r\nx\n")

    result = await RootFileSearch(context.root_files).grep(FileRoot.SESSION, ".", "x+", None)

    assert result == {
        "matches": [
            {"path": "long.txt", "line": 1, "text": "x" * MAX_MATCH_TEXT},
            {"path": "long.txt", "line": 2, "text": "x"},
        ],
        "truncated": length > MAX_MATCH_TEXT,
    }


async def test_files_search_preserves_hidden_ignored_binary_and_symlink_behavior(tmp_path) -> None:
    context = _context(tmp_path, ToolCall("search", "grep", {"root": "files", "pattern": "needle"}))
    root = context.root_files._files_path()
    (root / "visible.py").write_text("needle\n")
    (root / ".hidden.py").write_text("needle\n")
    (root / ".hidden").mkdir()
    (root / ".hidden" / "nested.py").write_text("needle\n")
    (root / "ignored.py").write_text("needle\n")
    (root / ".gitignore").write_text("ignored.py\n")
    (root / ".git").mkdir()
    (root / ".git" / "secret.py").write_text("needle\n")
    (root / "binary.py").write_bytes(b"\0needle\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("needle\n")
    (root / "escape").symlink_to(outside, target_is_directory=True)
    (root / "link.py").symlink_to(outside / "secret.py")
    search = RootFileSearch(context.root_files)

    glob = await search.glob(FileRoot.SESSION, ".", "*.py")
    assert glob == {"paths": ["binary.py", "ignored.py", "visible.py"], "truncated": False}
    for include in (None, "*.py"):
        grep = await search.grep(FileRoot.SESSION, ".", "needle", include)
        expected = [".hidden.py", ".hidden/nested.py", "visible.py"]
        if include:
            expected.append("ignored.py")
        assert [match["path"] for match in grep["matches"]] == sorted(expected)
        assert grep["truncated"] is False


def test_tool_catalog_exposes_types_but_model_schemas_do_not() -> None:
    catalog = tool_registry.catalog(ToolSettings(), IntegrationSettings())
    working_directory = next(
        group for group in catalog if group["id"] == "working_directory"
    )
    tools = {
        tool["name"]: tool
        for group in catalog
        for tool in group["tools"]
    }

    assert tools["web_search"]["toolType"] == "backend"
    assert tools["get_system_info"]["toolType"] == "computer"
    assert tools["read"]["toolType"] == "shared"
    assert working_directory["tools"] == []
    assert working_directory["actions"] == [{
        "capability": "working_directory.external",
        "label": "Zugriff außerhalb des Arbeitsverzeichnisses",
        "policy": "ask",
        "configurable": True,
        "allowedPolicies": ["disabled", "ask", "auto"],
        "available": True,
    }]
    assert tools["change_directory"]["actions"] == []
    change_directory_schema = next(
        schema
        for schema in tool_registry.schemas(
            IntegrationSettings(), execution_operations={"filesystem.resolve"}
        )
        if schema["name"] == "change_directory"
    )
    assert "_reason" in change_directory_schema["parameters"]["properties"]
    assert "_reason" not in change_directory_schema["parameters"]["required"]
    assert {tool["toolType"] for tool in tools.values()} == {
        "backend",
        "computer",
        "shared",
    }
    assert all(
        "toolType" not in schema and "tool_type" not in schema
        for schema in tool_registry.schemas(IntegrationSettings())
    )


def test_automation_tools_have_bounded_schemas_catalog_and_policy_defaults() -> None:
    definitions = {
        name: tool_registry._definitions[name]
        for name in {
            "list_automations",
            "get_automation",
            "create_automation",
            "edit_automation",
            "run_automation",
            "delete_automation",
        }
    }
    catalog = tool_registry.catalog(ToolSettings(), IntegrationSettings())
    group = next(item for item in catalog if item["id"] == "automations")
    catalog_tools = {item["name"]: item for item in group["tools"]}

    assert set(catalog_tools) == set(definitions)
    assert definitions["create_automation"].actions[0].mutates
    assert definitions["edit_automation"].actions[0].mutates
    assert definitions["run_automation"].actions[0].mutates
    assert definitions["delete_automation"].actions[0].mutates
    assert "Reuse an existing automation for the same task" in definitions["create_automation"].description
    assert "current session" in definitions[
        "create_automation"
    ].description
    assert "Calendar event reminders use the calendar event tools" in definitions[
        "create_automation"
    ].description
    assert "cron+timezone or offset run_at" in definitions["create_automation"].description
    assert "cron+timezone or offset run_at" in definitions["edit_automation"].description
    assert tool_registry.action_default(
        "automation.read", TrustLevel.PRIVATE
    ) == ToolExecutionPolicy.AUTO
    assert tool_registry.action_default(
        "automation.read", TrustLevel.PUBLIC
    ) == ToolExecutionPolicy.ASK
    for capability in (
        "automation.create",
        "automation.update",
        "automation.run",
        "automation.delete",
    ):
        assert tool_registry.action_default(
            capability, TrustLevel.PRIVATE
        ) == ToolExecutionPolicy.ASK
        assert tool_registry.action_default(
            capability, TrustLevel.PUBLIC
        ) == ToolExecutionPolicy.ASK
    for tool in catalog_tools.values():
        assert tool["actions"][0]["allowedPolicies"] == ["disabled", "ask", "auto"]

    create = definitions["create_automation"].schema()["parameters"]
    assert "source_session_id" not in create["properties"]
    assert "output_session_id" not in create["properties"]
    assert create["properties"]["schedules"]["maxItems"] == 50
    assert "archive_after_success" in create["properties"]
    schedule_schema = create["properties"]["schedules"]["items"]
    schedule_schema = create["$defs"][schedule_schema["$ref"].rsplit("/", 1)[-1]]
    assert "run_at" in schedule_schema["properties"]
    assert "not with run_at" in schedule_schema["properties"]["valid_until"]["description"]
    assert "One run_at only" in create["properties"]["archive_after_success"]["description"]
    edit = definitions["edit_automation"].schema()["parameters"]
    assert "output_mode" not in edit["properties"]
    assert "expected_revision" in edit["required"]
    for name in ("create_automation", "edit_automation"):
        schema = definitions[name].schema()["parameters"]
        assert "schedules" in schema["required"]
        assert schema["properties"]["schedules"].get("minItems", 0) == 0
        assert schema["properties"]["schedules"]["maxItems"] == 50
        assert "Empty for manual runs only" in schema["properties"]["schedules"]["description"]


@pytest.mark.parametrize("arguments", [CreateAutomationArguments, EditAutomationArguments])
def test_manual_automation_arguments_require_explicit_schedules_and_forbid_archiving(arguments):
    draft = {"name": "Manual", "task": "Do work"}
    if arguments is EditAutomationArguments:
        draft.update(automation_id=uuid4(), expected_revision=1)
    assert arguments(**draft, schedules=[]).schedules == []
    with pytest.raises(ValidationError):
        arguments(**draft)
    with pytest.raises(ValidationError):
        arguments(**draft, schedules=[], archive_after_success=True)
    with pytest.raises(ValidationError):
        arguments(**draft, schedules=[{"cron": "0 8 * * *", "timezone": "UTC"}] * 51)


async def test_automation_argument_validation_rejects_invalid_schedule_and_output_mode(
    tmp_path,
) -> None:
    invalid_schedule = ToolCall(
        "create-invalid",
        "create_automation",
        {
            "name": "Test",
            "task": "Do work",
            "schedules": [
                {"cron": "0 0 0 * * *", "timezone": "UTC", "enabled": True}
            ],
        },
    )
    invalid_output = ToolCall(
        "edit-invalid",
        "edit_automation",
        {
            "automation_id": str(uuid4()),
            "expected_revision": 1,
            "name": "Test",
            "task": "Do work",
            "schedules": [{"cron": "0 0 * * *", "timezone": "UTC"}],
            "output_mode": "none",
            "output_session_required": True,
        },
    )

    first = await tool_registry.execute(
        invalid_schedule, _context(tmp_path, invalid_schedule)
    )
    second = await tool_registry.execute(invalid_output, _context(tmp_path, invalid_output))

    assert first.metadata == {"errorCode": "tool.invalidArguments"}
    assert second.metadata == {"errorCode": "tool.invalidArguments"}


def test_automation_schedule_arguments_accept_one_time_schedule() -> None:
    schedule = mock_tools_automations.AutomationScheduleArguments.model_validate(
        {
            "cron": None,
            "timezone": "Europe/Berlin",
            "run_at": "2026-08-21T10:30:00+02:00",
        }
    )

    assert schedule.run_at == datetime(2026, 8, 21, 8, 30, tzinfo=UTC)
    assert schedule.timezone is None
    with pytest.raises(ValueError):
        mock_tools_automations.AutomationScheduleArguments.model_validate(
            {
                "cron": "0 8 * * *",
                "run_at": "2026-08-21T10:30:00+02:00",
            }
        )

    without_skill = CreateAutomationArguments.model_validate(
        {
            "name": "Bericht",
            "task": "Erstellen",
            "skill_id": "null",
            "schedules": [{"run_at": "2026-08-21T10:30:00+02:00"}],
        }
    )
    assert without_skill.skill_id is None
    with pytest.raises(ValueError):
        CreateAutomationArguments.model_validate(
            {
                "name": "Bericht",
                "task": "Erstellen",
                "skill_id": "none",
                "schedules": [{"run_at": "2026-08-21T10:30:00+02:00"}],
            }
        )
    with pytest.raises(ValueError):
        CreateAutomationArguments.model_validate(
            {
                "name": "Bericht",
                "task": "Erstellen",
                "archive_after_success": True,
                "schedules": [{"cron": "0 8 * * *", "timezone": "UTC"}],
            }
        )

    recurring = mock_tools_automations.AutomationScheduleArguments.model_validate(
        {
            "cron": "0 8 * * *",
            "timezone": "Europe/Berlin",
            "valid_until": "2026-08-28T08:00:00+02:00",
        }
    )
    assert recurring.valid_until == datetime(2026, 8, 28, 6, tzinfo=UTC)
    with pytest.raises(ValueError):
        mock_tools_automations.AutomationScheduleArguments.model_validate(
            {
                "run_at": "2026-08-21T10:30:00+02:00",
                "valid_until": "2026-08-28T10:30:00+02:00",
            }
        )


@pytest.mark.parametrize("manual", [False, True])
async def test_create_automation_approval_uses_current_session_boundaries(
    tmp_path, monkeypatch, manual
) -> None:
    call = ToolCall(
        "create",
        "create_automation",
        {
            "name": "Bericht",
            "task": "Erstelle den Bericht",
            "enabled": True,
            "schedules": [] if manual else [
                {
                    "cron": "0 8 * * *",
                    "timezone": "Europe/Berlin",
                    "valid_until": "2026-08-28T08:00:00+02:00",
                }
            ],
        },
    )
    context = _context(tmp_path, call)
    created = Automation(
        id=uuid4(),
        user_id=context.session.user_id,
        execution_session_id=uuid4(),
        name="Bericht",
        task="Erstelle den Bericht",
        revision=1,
        enabled=True,
    )
    monkeypatch.setattr(
        mock_tools_automations.AutomationService, "skill", AsyncMock(return_value=None)
    )
    create = AsyncMock(return_value=created)
    monkeypatch.setattr(mock_tools_automations.AutomationService, "create", create)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    permission = context.ask_handler.await_args.args[0]
    assert permission.capability == "automation.create"
    assert permission.title == "Automation erstellen?"
    assert permission.approval_details["automationPreview"]["sessionId"] == str(context.session.id)
    if manual:
        assert permission.approval_details["automationPreview"]["schedules"] == []
        assert next(item for item in permission.display_items if item.label == "Zeitpläne").value == ("Nur manuell",)
        assert create.await_args.kwargs["schedules"] == []
    else:
        assert permission.approval_details["automationPreview"]["schedules"][0][
            "valid_until"
        ] == "2026-08-28T08:00:00+02:00"
        assert create.await_args.kwargs["schedules"][0]["valid_until"] == datetime(
            2026, 8, 28, 6, tzinfo=UTC
        )
    assert create.await_args.kwargs["session_id"] == context.session.id
    assert create.await_args.kwargs["archive_after_success"] is False


async def test_edit_automation_approval_labels_removing_schedules_as_manual(tmp_path, monkeypatch):
    automation_id = uuid4()
    call = ToolCall("edit", "edit_automation", {
        "automation_id": str(automation_id), "expected_revision": 1,
        "name": "Manual", "task": "Do work", "schedules": [],
    })
    context = _context(tmp_path, call)
    automation = Automation(
        id=automation_id, user_id=context.session.user_id, revision=1, name="Manual",
        task="Do work", enabled=True, archive_after_success=False,
        schedules=[AutomationSchedule(
            cron_expression="0 8 * * *", timezone="UTC", enabled=True,
        )],
    )
    monkeypatch.setattr(mock_tools_automations.AutomationService, "owned", AsyncMock(return_value=automation))
    monkeypatch.setattr(mock_tools_automations.AutomationService, "skill", AsyncMock(return_value=None))
    replace = AsyncMock(return_value=automation)
    monkeypatch.setattr(mock_tools_automations.AutomationService, "replace", replace)
    result = await tool_registry.execute(call, context)
    assert not result.is_error
    permission = context.ask_handler.await_args.args[0]
    assert permission.capability == "automation.update"
    changes = permission.approval_details["automationChange"]["changes"]
    assert len(changes) == 1 and changes[0].endswith(" -> Nur manuell")
    assert replace.await_args.kwargs["schedules"] == []
    assert replace.await_args.kwargs["expected_revision"] == 1


async def test_run_automation_rechecks_revision_after_approval_and_queues_only(
    tmp_path, monkeypatch
) -> None:
    automation_id = uuid4()
    call = ToolCall(
        "run",
        "run_automation",
        {"automation_id": str(automation_id), "expected_revision": 3},
    )
    context = _context(tmp_path, call)
    automation = Automation(
        id=automation_id,
        user_id=context.session.user_id,
        execution_session_id=uuid4(),
        name="Bericht",
        task="Bericht",
        revision=3,
    )
    run = AutomationRun(
        id=uuid4(),
        automation_id=automation.id,
        automation_revision=3,
        trigger="manual",
        status="queued",
        definition_snapshot={},
    )
    monkeypatch.setattr(
        mock_tools_automations.AutomationService, "owned", AsyncMock(return_value=automation)
    )
    enqueue = AsyncMock(return_value=run)
    monkeypatch.setattr(mock_tools_automations.AutomationService, "enqueue_manual", enqueue)
    start = Mock()
    monkeypatch.setattr(mock_tools_automations.AutomationScheduler, "start", start)

    result = await tool_registry.execute(call, context)
    await asyncio.sleep(0)

    assert not result.is_error
    assert context.ask_handler.await_args.args[0].resource_type == "automation"
    assert enqueue.await_args.kwargs["expected_revision"] == 3
    start.assert_called_once_with()


async def test_delete_automation_approval_calls_central_revision_checked_service(
    tmp_path, monkeypatch
) -> None:
    automation_id = uuid4()
    call = ToolCall(
        "delete",
        "delete_automation",
        {"automation_id": str(automation_id), "expected_revision": 7},
    )
    context = _context(tmp_path, call)
    automation = Automation(
        id=automation_id,
        user_id=context.session.user_id,
        execution_session_id=uuid4(),
        name="Alt",
        task="Alt",
        revision=7,
    )
    monkeypatch.setattr(
        mock_tools_automations.AutomationService, "owned", AsyncMock(return_value=automation)
    )
    delete = AsyncMock()
    monkeypatch.setattr(mock_tools_automations.AutomationService, "delete", delete)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    permission = context.ask_handler.await_args.args[0]
    assert permission.capability == "automation.delete"
    assert permission.risk_level == "high"
    delete.assert_awaited_once_with(
        context.db,
        automation.id,
        context.session.user_id,
        expected_revision=7,
    )

def test_inactive_capability_catalog_includes_installed_tools_without_schemas() -> None:
    integrations = IntegrationSettings(
        adapter_availability={"caldav": "disabled"}
    )
    settings = ToolSettings()
    capabilities = mock_tools_catalog.effective_tool_capabilities(settings)
    effective = tool_registry.effective(
        integrations,
        tool_capabilities=capabilities,
        tool_settings=settings,
    )

    groups = tool_registry.inactive_capability_groups(
        integrations,
        frozenset(effective.tools),
        webfetch_enabled=False,
        web_search_enabled=True,
        memory_enabled=False,
        memory_write_enabled=False,
        tool_capabilities=capabilities,
        tool_settings=settings,
    )
    catalog = "\n".join(groups)

    assert "write_file" in effective.tools
    assert "write_file" not in catalog
    assert "generate_image" not in catalog
    assert "memory_retrieve" in catalog
    assert "webfetch" in catalog
    assert "calendar_create_event" not in catalog
    assert "configure under Integrationen or the agent profile" in catalog
    assert "question" not in catalog
    assert "parameters" not in catalog


def test_policy_disabled_integration_tool_points_to_integration_settings(monkeypatch) -> None:
    _shared_adapter(monkeypatch, AsyncMock())
    integrations = IntegrationSettings(instances=[_shared_integration("Tasks")])
    settings = _tool_settings(
        {"policies": {"tasks.read": "disabled"}}
    )
    effective = tool_registry.effective(
        integrations,
        tool_settings=settings,
    )

    groups = tool_registry.inactive_capability_groups(
        integrations,
        frozenset(effective.tools),
        webfetch_enabled=False,
        web_search_enabled=True,
        memory_enabled=False,
        memory_write_enabled=False,
        tool_capabilities=frozenset(),
        tool_settings=settings,
    )
    integration_group = next(
        group for group in groups if group.startswith("Integration tools")
    )

    assert "tasks_read" in integration_group
    assert "configure under Integrationen or the agent profile" in integration_group
    assert all(
        "tasks_read" not in group
        for group in groups
        if group.startswith("Optional tools")
    )


@pytest.mark.parametrize("trust", list(TrustLevel))
async def test_session_file_operations_only_ask_for_deletion(tmp_path, trust) -> None:
    context = _context(tmp_path, ToolCall("setup", "write_file", {}))
    context.tool_settings = ToolSettings().select_trust(trust)
    context.model_trust_level = trust
    context.db.scalar.return_value = None
    context.db.scalars.return_value = Mock(all=Mock(return_value=[]))
    decisions = []

    async def authorize(request):
        policy = _tool_policy(context.tool_settings, context.integration_settings, request)
        decision = await PermissionPolicy.evaluate(
            context.db, context.session, request, context.root_files, policy,
            trust_level=trust,
        )
        decisions.append(decision)
        return decision

    context.ask_handler = authorize
    effective = tool_registry.effective(
        context.integration_settings, tool_settings=context.tool_settings
    )
    operations = [
        ("write_file", {"root": "files", "path": "note.txt", "content": "before"}),
        ("read", {"root": "files", "path": "note.txt"}),
        ("glob", {"root": "files", "pattern": "*.txt"}),
        ("grep", {"root": "files", "pattern": "before"}),
        ("provide_file", {"root": "files", "path": "note.txt"}),
        ("edit_file", {
            "root": "files", "path": "note.txt", "oldText": "before", "newText": "after",
        }),
        ("create_directory", {"root": "files", "path": "output"}),
        ("copy_path", {
            "source_root": "files", "source_path": "note.txt",
            "destination_root": "files", "destination_path": "output/copy.txt",
        }),
        ("move_path", {
            "source_root": "files", "source_path": "output/copy.txt",
            "destination_root": "files", "destination_path": "output/moved.txt",
        }),
        ("delete_path", {"root": "files", "path": "output/moved.txt"}),
    ]
    for name, arguments in operations:
        context.tool_call = ToolCall(name, name, arguments)
        decisions.clear()
        result = await effective.execute(context.tool_call, context)
        deleting = name == "delete_path"
        assert result.is_error is deleting, (name, result.content)
        assert decisions == [
            PermissionDecision.ASK if deleting else PermissionDecision.ALLOW
        ] * (2 if name in {"copy_path", "move_path"} else 1)

    assert context.root_files.read_file("session", "output/moved.txt").content == "1: after"


@pytest.mark.parametrize("name", ["copy_path", "move_path"])
@pytest.mark.parametrize("trust", list(TrustLevel))
async def test_transfer_source_denial_skips_destination_approval(tmp_path, monkeypatch, name, trust):
    call = ToolCall(name, name, {
        "source_root": "files", "source_path": "source.txt",
        "destination_root": "files", "destination_path": "destination.txt",
    })
    context = _context(tmp_path, call)
    context.tool_settings = ToolSettings().select_trust(trust)
    context.ask_handler = AsyncMock(return_value=PermissionDecision.DENY)
    context.root_files.write_file("session", "source.txt", "unchanged")
    dispatch = Mock(side_effect=AssertionError("Denied source must not be changed"))
    monkeypatch.setattr(context.root_files, name, dispatch)
    result = await tool_registry.effective(
        context.integration_settings, tool_settings=context.tool_settings
    ).execute(call, context)
    assert result.is_error
    assert json.loads(result.content)["error"]["code"] == "permission.denied"
    context.ask_handler.assert_awaited_once()
    dispatch.assert_not_called()
    assert context.root_files.read_file("session", "source.txt").content == "1: unchanged"


def test_global_policy_removes_tools_requiring_a_disabled_action() -> None:
    settings = _tool_settings(
        {"policies": {"filesystem.delete": "disabled"}}
    )

    offered = {
        item["name"]
        for item in tool_registry.effective(
            IntegrationSettings(),
            tool_settings=settings,
        ).schemas()
    }

    assert "delete_path" not in offered
    assert "move_path" in offered
    assert "write_file" in offered


async def test_web_search_normalizes_a_removed_provider_to_no_key_search(
    tmp_path, monkeypatch
) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    settings = IntegrationSettings()
    current_tools = ToolSettings.model_validate(
        {"webSearch": {"primaryProvider": f"searxng:{integration.id}"}}
    )
    effective = tool_registry.effective(
        settings, tool_settings=current_tools
    )
    call = ToolCall(
        id="stale-search",
        name="web_search",
        arguments={"query": "OpenWrt TL-MR6400 support status 2024 2025"},
    )
    context = _context(tmp_path, call)
    context.integration_settings = settings
    async def load_settings(_db, settings_type, **_kwargs):
        if settings_type is IntegrationSettings:
            return IntegrationSettings()
        return current_tools

    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(side_effect=load_settings)
    )
    search = AsyncMock(
        return_value=json.dumps(
            [
                {
                    "sourceId": "src-test",
                    "title": "MatoLoop",
                    "url": "https://example.test/source",
                    "snippet": "Beschreibung",
                }
            ]
        )
    )
    monkeypatch.setattr(mock_tools_web, 'search_web', search)

    result = await effective.execute(call, context)

    assert result.is_error is False
    assert result.metadata == {
        "resultCount": 1,
        "evidenceUrls": ["https://example.test/source"],
        "sources": [
            {
                "sourceId": "src-test",
                "url": "https://example.test/source",
                "kind": "search",
                "title": "MatoLoop",
            }
        ],
    }
    assert search.await_args.kwargs == {
        "search_type": "web",
        "provider": "no_key",
        "fallback_provider": None,
    }
    assert search.await_args.args[1] == "OpenWrt TL-MR6400 support status 2024 2025"


async def test_web_search_rejects_changed_bound_provider_configuration(
    tmp_path, monkeypatch
) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    integrations = IntegrationSettings(instances=[integration])
    tool_settings = _tool_settings(
        {"webSearch": {"primaryProvider": f"searxng:{integration.id}"}}
    )
    effective = tool_registry.effective(
        integrations, tool_settings=tool_settings
    )
    changed = integration.model_copy(deep=True)
    changed.config = {"base_url": "https://other.example.com"}

    async def load_settings(_db, settings_type, **_kwargs):
        return (
            IntegrationSettings(instances=[changed])
            if settings_type is IntegrationSettings
            else tool_settings
        )

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    search = AsyncMock(side_effect=AssertionError("Changed provider must not be used"))
    monkeypatch.setattr(mock_tools_web, 'search_web', search)
    call = ToolCall(
        id="search", name="web_search", arguments={"query": "MatoLoop"}
    )

    result = await effective.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.unavailable"}
    search.assert_not_awaited()


async def test_web_search_rejects_an_invalid_provider_result(tmp_path, monkeypatch) -> None:
    call = ToolCall(
        id="invalid-search",
        name="web_search",
        arguments={"query": "MatoLoop"},
    )
    context = _context(tmp_path, call)

    async def load_settings(_db, settings_type, **_kwargs):
        return (
            IntegrationSettings()
            if settings_type is IntegrationSettings
            else ToolSettings()
        )

    monkeypatch.setattr(SettingsStore, "load", load_settings)
    monkeypatch.setattr(mock_tools_web, 'search_web', AsyncMock(return_value="not-json"))

    result = await tool_registry.execute(call, context)

    assert result.is_error is True
    assert result.metadata == {"errorCode": "integration.invalidResponse"}


async def test_missing_workspace_returns_structured_error_without_approval(tmp_path) -> None:
    call = ToolCall(
        id="read",
        name="read",
        arguments={"root": "computer", "path": "notes.txt"},
    )
    context = _context(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "file.workspaceNotSelected"}
    context.ask_handler.assert_not_awaited()


async def test_update_agent_identity_uses_ask_policy(monkeypatch, tmp_path) -> None:
    tool_settings = _tool_settings({})
    persona = PersonaProfile(name="Direkt", instructions="Alt")
    identity = UserAgentIdentitySettings(personas=[persona])

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            AiSettings: AiSettings(),
            UserAgentIdentitySettings: identity,
        }[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="identity",
        name="update_agent_identity",
        arguments={
            "display_name": "Ada",
            "instructions": "Antworte direkt.",
        },
    )
    context = _context(tmp_path, call)
    persona.owner_id = context.session.user_id
    _mock_value = AsyncMock(return_value=object())
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_agent_profile', _mock_value)
    _mock_value = AsyncMock(
            return_value=EffectiveAgentIdentity(
                display_name="MatoLoop",
                persona=persona,
                persona_editable=True,
            )
        )
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_effective_agent_identity', _mock_value)

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content) == {
        "displayName": "Ada",
        "personaName": "Direkt",
        "instructionsUpdated": True,
        "effectiveFrom": "next_run",
    }
    assert result.metadata["agentIdentity"]["displayName"] == "Ada"
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "settings.agent_identity.update"
    assert request.resource_type == "agent_identity"
    assert request.resource_scope == str(context.session.user_id)
    save.assert_awaited_once()
    assert identity.display_name == "Ada"
    assert identity.personas[0].instructions == "Antworte direkt."


async def test_update_agent_identity_auto_skips_approval(monkeypatch, tmp_path) -> None:
    tool_settings = _tool_settings(
        {"policies": {"settings.agent_identity.update": "auto"}}
    )
    persona = PersonaProfile(name="Freundlich", instructions="Alt")
    identity = UserAgentIdentitySettings(personas=[persona])

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            AiSettings: AiSettings(),
            UserAgentIdentitySettings: identity,
        }[settings_type]

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    call = ToolCall(
        id="identity-auto",
        name="update_agent_identity",
        arguments={"instructions": "Sei freundlich."},
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    _set_profile_settings(context, tool_settings)
    persona.owner_id = context.session.user_id
    _mock_value = AsyncMock(return_value=object())
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_agent_profile', _mock_value)
    _mock_value = AsyncMock(
            return_value=EffectiveAgentIdentity(
                display_name="MatoLoop",
                persona=persona,
                persona_editable=True,
            )
        )
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_effective_agent_identity', _mock_value)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    context.ask_handler.assert_not_awaited()


async def test_update_agent_identity_rejects_changed_approved_persona(
    monkeypatch, tmp_path
) -> None:
    tool_settings = _tool_settings({})
    first = PersonaProfile(name="Erste", instructions="Alt")
    second = PersonaProfile(name="Zweite", instructions="Alt")
    identity = UserAgentIdentitySettings(personas=[first, second])

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            AiSettings: AiSettings(),
            UserAgentIdentitySettings: identity,
        }[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    context = _context(
        tmp_path,
        ToolCall(
            id="identity-race",
            name="update_agent_identity",
            arguments={"instructions": "Neu"},
        ),
    )
    for persona in (first, second):
        persona.owner_id = context.session.user_id
    _mock_value = AsyncMock(return_value=object())
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_agent_profile', _mock_value)
    _mock_value = AsyncMock(
            side_effect=[
                EffectiveAgentIdentity(
                    display_name="MatoLoop", persona=first, persona_editable=True
                ),
                EffectiveAgentIdentity(
                    display_name="MatoLoop", persona=second, persona_editable=True
                ),
            ]
        )
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_effective_agent_identity', _mock_value)

    result = await tool_registry.execute(context.tool_call, context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    save.assert_not_awaited()


async def test_update_agent_identity_rejects_persona_changed_since_run_start(
    monkeypatch, tmp_path
) -> None:
    tool_settings = _tool_settings({})
    prompted = PersonaProfile(name="Prompt", instructions="Alt")
    current = PersonaProfile(name="Aktuell", instructions="Alt")
    context = _context(
        tmp_path,
        ToolCall(
            id="identity-frozen",
            name="update_agent_identity",
            arguments={"instructions": "Neu"},
        ),
    )
    context.agent_persona_id = prompted.id
    current.owner_id = context.session.user_id
    _mock_value = AsyncMock(return_value=object())
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_agent_profile', _mock_value)
    _mock_value = AsyncMock(
            return_value=EffectiveAgentIdentity(
                display_name="MatoLoop", persona=current, persona_editable=True
            )
        )
    for _mock_target in (mock_tools_identity, mock_tools_coding,):
        monkeypatch.setattr(_mock_target, 'load_effective_agent_identity', _mock_value)
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            side_effect=lambda _db, settings_type, **_kwargs: {
                ToolSettings: tool_settings,
                AiSettings: AiSettings(),
            }[settings_type]
        ),
    )
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    result = await tool_registry.execute(context.tool_call, context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    context.ask_handler.assert_not_awaited()
    save.assert_not_awaited()


async def test_update_user_profile_uses_ask_policy_and_preserves_other_scope(
    monkeypatch, tmp_path
) -> None:
    tool_settings = _tool_settings(
        {"policies": {"settings.user_profile.update": "ask"}}
    )
    profile = UserProfileSettings(
        full_name={"both": "Ada Lovelace"},
        preferred_name={"both": "Ada"},
        primary_residence={"both": "Wien"},
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            UserProfileSettings: profile,
        }[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="profile",
        name="update_user_profile",
        arguments={
            "full_name": "Grace Hopper",
            "preferred_name": "Grace",
            "primary_residence": None,
        },
    )
    context = _context(tmp_path, call)
    context.model_trust_level = TrustLevel.PRIVATE

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content) == {
        "updatedFields": ["full_name", "preferred_name", "primary_residence"],
        "modelScope": "private_models",
        "effectiveFrom": "next_run",
    }
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "settings.user_profile.update"
    assert request.resource_type == "user_profile"
    assert "private Modelle" in request.summary
    assert "vollständigen Namen" in request.summary
    saved = save.await_args.args[1]
    assert saved.full_name.private_only == "Grace Hopper"
    assert saved.full_name.public_only == "Ada Lovelace"
    assert saved.preferred_name.both is None
    assert saved.preferred_name.private_only == "Grace"
    assert saved.preferred_name.public_only == "Ada"
    assert saved.primary_residence.private_only is None
    assert saved.primary_residence.public_only == "Wien"


async def test_update_user_profile_both_models_auto_skips_approval(
    monkeypatch, tmp_path
) -> None:
    tool_settings = _tool_settings(
        {"policies": {"settings.user_profile.update": "auto"}}
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            UserProfileSettings: UserProfileSettings(
                preferred_name={"privateOnly": "Ada", "publicOnly": "A."}
            ),
        }[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="profile-auto",
        name="update_user_profile",
        arguments={"model_scope": "both_models", "preferred_name": "Grace"},
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    _set_profile_settings(context, tool_settings)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    context.ask_handler.assert_not_awaited()
    saved = save.await_args.args[1]
    assert saved.preferred_name.both == "Grace"
    assert saved.preferred_name.private_only is None
    assert saved.preferred_name.public_only is None


async def test_personal_settings_exposes_one_tool_and_reuses_profile_rules(monkeypatch, tmp_path) -> None:
    names = tool_registry.effective(
        IntegrationSettings(), tool_settings=ToolSettings()
    ).tools
    assert "customize_matoloop" in names
    assert "update_user_profile" not in names
    assert "update_agent_identity" not in names

    call = ToolCall("profile", "customize_matoloop", {
        "action": "update", "area": "user_profile",
        "changes": {"preferred_name": "Ada"},
    })
    context = _context(tmp_path, call)
    update = AsyncMock(return_value=ToolResult.json({"updatedFields": ["preferred_name"]}))
    monkeypatch.setattr(mock_personal_settings, "_update_user_profile", update)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert update.await_args.args[0].preferred_name == "Ada"
    context.db.commit.assert_awaited_once()


def test_secret_input_available_without_shell_and_with_shell() -> None:
    for execution_operations in (frozenset(), frozenset({"shell.run"})):
        tools = tool_registry.effective(
            IntegrationSettings(), tool_settings=ToolSettings(),
            execution_operations=execution_operations,
        ).tools
        assert "secret_input" in tools
        assert "customize_matoloop" in tools
        assert ("shell_run" in tools) == bool(execution_operations)


def test_personal_settings_rejects_ui_preferences_and_raw_credentials() -> None:
    from mato_loop_backend.tools.personal_integrations import IntegrationChanges

    with pytest.raises(ValidationError):
        tool_registry._definitions["customize_matoloop"].arguments_model.model_validate({
            "action": "update", "area": "preferences", "changes": {"dark_mode": True},
        })
    with pytest.raises(ValidationError):
        IntegrationChanges.model_validate({"secret_refs": {"password": "plaintext"}})


async def test_personal_variable_is_saved_from_protected_reference(monkeypatch, tmp_path) -> None:
    secret_id = uuid4()
    call = ToolCall("variable", "customize_matoloop", {
        "action": "create", "area": "variables",
        "changes": {"name": "MEALIE_TOKEN", "value_ref": f"run-secret:{secret_id}"},
    })
    context = _context(tmp_path, call)
    context.one_time_secrets[secret_id] = "private-token"
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    create = AsyncMock(return_value=SimpleNamespace(id=uuid4(), label="MEALIE_TOKEN"))
    monkeypatch.setattr(mock_personal_settings, "create_personal_variable", create)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert create.await_args.args[1] == context.session.user_id
    assert create.await_args.kwargs["value"] == "private-token"
    assert create.await_args.kwargs["secret"] is True
    assert "private-token" not in result.content
    assert "private-token" not in str(context.ask_handler.await_args_list)


async def test_personal_integration_resolves_owned_stored_secret_with_access_approval(monkeypatch, tmp_path) -> None:
    from mato_loop_backend.tools import personal_integrations
    from mato_loop_backend.tools import secrets as tool_secrets

    variable_id = uuid4()
    call = ToolCall("calendar-secret", "customize_matoloop", {
        "action": "create", "area": "integrations", "adapter_type": "caldav",
        "changes": {"name": "Calendar", "base_url": "https://calendar.example.com",
                    "username": "ada", "secret_refs": {"password": str(variable_id)}},
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(),
        UserIntegrationSettings: UserIntegrationSettings(),
    }[model]))
    variable = SimpleNamespace(id=variable_id, label="CALENDAR_PASSWORD")
    lookup = AsyncMock(return_value=variable)
    monkeypatch.setattr(tool_secrets, "get_personal_variable", lookup)
    monkeypatch.setattr(tool_secrets, "personal_variable_value", lambda _variable: "private-password")
    item = IntegrationInstance(
        name="Calendar", integration_type="calendar", adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "ada"},
    )
    create = AsyncMock(return_value=UserIntegrationSettings(instances=[item]))
    monkeypatch.setattr(personal_integrations.CalDavIntegrationService, "create", create)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert create.await_args.kwargs["password"] == "private-password"
    assert all(used.args[1] == context.session.user_id for used in lookup.await_args_list)
    assert any(request.args[0].capability == "secrets.access" for request in context.ask_handler.await_args_list)
    assert "private-password" not in result.content


async def test_personal_integration_accepts_protected_username(monkeypatch, tmp_path) -> None:
    from mato_loop_backend.tools import personal_integrations

    username_id, password_id = uuid4(), uuid4()
    call = ToolCall("calendar-credentials", "customize_matoloop", {
        "action": "create", "area": "integrations", "adapter_type": "caldav",
        "changes": {"name": "Calendar", "base_url": "https://calendar.example.com",
                    "secret_refs": {"username": f"run-secret:{username_id}",
                                    "password": f"run-secret:{password_id}"}},
    })
    context = _context(tmp_path, call)
    context.one_time_secrets = {username_id: "ada", password_id: "private-password"}
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(),
        UserIntegrationSettings: UserIntegrationSettings(),
    }[model]))
    item = IntegrationInstance(name="Calendar", integration_type="calendar", adapter_type="caldav",
                               config={"base_url": "https://calendar.example.com", "username": "ada"})
    create = AsyncMock(return_value=UserIntegrationSettings(instances=[item]))
    monkeypatch.setattr(personal_integrations.CalDavIntegrationService, "create", create)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert create.await_args.kwargs["username"] == "ada"
    assert create.await_args.kwargs["password"] == "private-password"


async def test_personal_profile_update_checks_generic_policy_after_approval(monkeypatch, tmp_path) -> None:
    call = ToolCall("profile", "customize_matoloop", {
        "action": "update", "area": "user_profile",
        "changes": {"preferred_name": "Ada"},
    })
    context = _context(tmp_path, call)
    allowed = ToolSettings()
    blocked = ToolSettings(private_permissions={"policies": {"settings.personal.update": "disabled"}})
    monkeypatch.setattr(
        mock_tools_access, "profile_tool_settings",
        AsyncMock(side_effect=[allowed, allowed, blocked]),
    )
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "tool.unavailable"
    context.ask_handler.assert_awaited_once()
    save.assert_not_awaited()


async def test_personal_settings_cannot_bypass_disabled_profile_tool(monkeypatch, tmp_path) -> None:
    call = ToolCall("profile", "customize_matoloop", {
        "action": "update", "area": "user_profile",
        "changes": {"preferred_name": "Ada"},
    })
    context = _context(tmp_path, call)
    settings = ToolSettings(tool_availability={"update_user_profile": False})
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=settings))
    update = AsyncMock()
    monkeypatch.setattr(mock_personal_settings, "_update_user_profile", update)

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "tool.unavailable"
    update.assert_not_awaited()


async def test_personal_settings_validates_scalar_patch_and_rechecks_policy(monkeypatch, tmp_path) -> None:
    call = ToolCall("memory", "customize_matoloop", {
        "action": "update", "area": "memory", "changes": {"enabled": False},
    })
    context = _context(tmp_path, call)
    settings = ToolSettings(private_permissions={"policies": {"settings.personal.update": "ask"}})
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=settings))
    memory = MemorySettings()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=memory))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert save.await_args.args[1].enabled is False
    assert save.await_args.kwargs["user_id"] == context.session.user_id
    assert context.ask_handler.await_args.args[0].display_items[0].value == ("enabled: False",)

    for value in ({"enabled": "false"}, {"dreaming_remove_policy": "auto"}):
        call.arguments["changes"] = value
        result = await tool_registry.execute(call, context)
        assert result.metadata["errorCode"] == "tool.invalidArguments"
    assert save.await_count == 1

    call.arguments["changes"] = {"enabled": False}
    context.ask_handler.reset_mock()
    monkeypatch.setattr(
        mock_tools_access, "profile_tool_settings",
        AsyncMock(side_effect=[settings, ToolSettings(private_permissions={"policies": {"settings.personal.update": "disabled"}})]),
    )
    result = await tool_registry.execute(call, context)
    assert result.metadata["errorCode"] == "tool.unavailable"
    assert save.await_count == 1


async def test_personal_settings_mcp_uses_owned_service_and_secret_reference(monkeypatch, tmp_path) -> None:
    secret_id = uuid4()
    call = ToolCall("mcp", "customize_matoloop", {
        "action": "create", "area": "integrations", "adapter_type": "mcp_streamable_http",
        "changes": {"name": "Haus", "endpoint": "https://example.com/mcp",
                    "secret_refs": {"bearer_token": f"run-secret:{secret_id}"}},
    })
    context = _context(tmp_path, call)
    context.one_time_secrets[secret_id] = "private-token"
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(adapter_availability={"mcp_streamable_http": "full"}),
        UserIntegrationSettings: UserIntegrationSettings(),
    }[model]))
    server = IntegrationInstance(
        name="Haus", integration_type="mcp", adapter_type="mcp_streamable_http",
        config={"endpoint": "https://example.com/mcp"},
    )
    create = AsyncMock(return_value=server)
    monkeypatch.setattr(mock_personal_integrations.McpIntegrationService, "create", create)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert create.await_args.kwargs["owner_id"] == context.session.user_id
    assert create.await_args.kwargs["token"] == "private-token"
    assert context.one_time_secrets[secret_id] == "private-token"
    assert "private-token" not in result.content
    assert "private-token" not in str(context.ask_handler.await_args.args[0])

    call.arguments["action"] = "delete"
    call.arguments.pop("changes")
    call.arguments["integration_id"] = str(uuid4())
    result = await tool_registry.execute(call, context)
    assert result.metadata["errorCode"] == "integration.notFound"


async def test_personal_settings_mcp_update_keeps_owner_and_existing_connection(monkeypatch, tmp_path) -> None:
    server = IntegrationInstance(
        name="Haus", integration_type="mcp", adapter_type="mcp_streamable_http",
        config={"endpoint": "https://example.com/mcp"}, enabled=True,
    )
    call = ToolCall("mcp-update", "customize_matoloop", {
        "action": "update", "area": "integrations", "integration_id": str(server.id),
        "changes": {"name": "Zuhause", "enabled": False},
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(adapter_availability={"mcp_streamable_http": "full"}),
        UserIntegrationSettings: UserIntegrationSettings(instances=[server]),
    }[model]))
    updated = server.model_copy(update={"name": "Zuhause", "enabled": False})
    update = AsyncMock(return_value=updated)
    monkeypatch.setattr(mock_personal_integrations.McpIntegrationService, "update", update)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert update.await_args.args[1] == server.id
    assert update.await_args.kwargs["owner_id"] == context.session.user_id
    assert update.await_args.kwargs["endpoint"] == "https://example.com/mcp"
    assert update.await_args.kwargs["token"] is None
    assert update.await_args.kwargs["enabled"] is False
    scope = json.loads(context.ask_handler.await_args.args[0].resource_scope)
    assert scope["userId"] == str(context.session.user_id)
    assert scope["integrationId"] == str(server.id)


async def test_personal_settings_refuses_disabled_mcp_before_network_access(monkeypatch, tmp_path) -> None:
    call = ToolCall("mcp", "customize_matoloop", {
        "action": "create", "area": "integrations", "adapter_type": "mcp_streamable_http",
        "changes": {"name": "Haus", "endpoint": "https://example.com/mcp"},
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kwargs: {
        IntegrationSettings: IntegrationSettings(),
        UserIntegrationSettings: UserIntegrationSettings(),
    }[model]))
    create = AsyncMock()
    monkeypatch.setattr(mock_personal_integrations.McpIntegrationService, "create", create)

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "integration.personalAdapterDisabled"
    create.assert_not_awaited()


@pytest.mark.parametrize("adapter_type,kind,config,credential", [
    ("searxng", "search", {"base_url": "https://search.example.com"}, None),
    ("caldav", "calendar", {"base_url": "https://calendar.example.com", "username": "ada"}, "password"),
    ("imap_smtp", "mail", {"imap_host": "mail.example.com", "imap_port": 993, "imap_security": "tls", "smtp_host": "mail.example.com", "smtp_port": 465, "smtp_security": "tls", "username": "ada", "from_address": "ada@example.com"}, "password"),
    ("tbs-desk", "support", {"base_url": "https://support.example.com", "username": "ada"}, "password"),
    ("bookstack", "knowledge", {"base_url": "https://books.example.com"}, "token_id"),
    ("mealie", "recipes", {"base_url": "https://recipes.example.com"}, "token"),
    ("arcane", "infrastructure", {"base_url": "https://arcane.example.com"}, "api_key"),
    ("sonobin", "music", {"endpoint": "https://music.example.com/mcp/"}, "bearer_token"),
    ("openscad", "openscad", {"execution_agent_id": str(uuid4()), "binary_path": "/usr/bin/openscad"}, None),
    ("opencode", "coding", {"base_url": "https://coding.example.com", "username": "opencode"}, None),
    ("browser", "browser", {"execution_agent_id": str(uuid4())}, None),
    ("mcp_streamable_http", "mcp", {"endpoint": "https://example.com/mcp"}, None),
])
async def test_personal_integration_create_dispatches_to_owned_service(
    monkeypatch, tmp_path, adapter_type, kind, config, credential,
) -> None:
    from mato_loop_backend.tools import personal_integrations

    secret_id = uuid4()
    secret_refs = ({credential: f"run-secret:{secret_id}"} if credential else {})
    if adapter_type == "bookstack":
        secret_refs["token_secret"] = f"run-secret:{secret_id}"
    call = ToolCall("integration-create", "customize_matoloop", {
        "action": "create", "area": "integrations", "adapter_type": adapter_type,
        "changes": {"name": "Home", **config, **({"secret_refs": secret_refs} if secret_refs else {})},
    })
    context = _context(tmp_path, call)
    context.one_time_secrets[secret_id] = "protected-value"
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(adapter_availability={adapter_type: "full"}),
        UserIntegrationSettings: UserIntegrationSettings(),
    }[model]))
    instance = IntegrationInstance(
        name="Home", integration_type=kind, adapter_type=adapter_type,
        config={key: value for key, value in config.items() if key != "execution_agent_id"},
        execution_agent_id=UUID(config["execution_agent_id"]) if "execution_agent_id" in config else None,
    )
    create = AsyncMock(return_value=(
        instance if adapter_type in {"sonobin", "mcp_streamable_http"}
        else UserIntegrationSettings(instances=[instance])
    ))
    monkeypatch.setattr(personal_integrations._SERVICES[adapter_type], "create", create)

    result = await tool_registry.execute(call, context)

    assert not result.is_error, result.content
    assert create.await_count == 1
    assert create.await_args.kwargs.get("owner_id", create.await_args.kwargs.get("user_id")) == context.session.user_id
    assert "protected-value" not in result.content


async def test_personal_integration_update_keeps_existing_credential(monkeypatch, tmp_path) -> None:
    from mato_loop_backend.tools import personal_integrations

    item = IntegrationInstance(
        name="Kalender", integration_type="calendar", adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "ada"},
        credential_id=uuid4(),
    )
    call = ToolCall("calendar-update", "customize_matoloop", {
        "action": "update", "area": "integrations", "integration_id": str(item.id),
        "changes": {"name": "Privater Kalender"},
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(),
        UserIntegrationSettings: UserIntegrationSettings(instances=[item]),
    }[model]))
    update = AsyncMock(return_value=UserIntegrationSettings(instances=[item]))
    monkeypatch.setattr(personal_integrations.CalDavIntegrationService, "update", update)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert update.await_args.kwargs["password"] is None
    assert update.await_args.kwargs["owner_id"] == context.session.user_id


async def test_personal_integration_delete_disabled_existing_entry(monkeypatch, tmp_path) -> None:
    from mato_loop_backend.tools import personal_integrations

    item = IntegrationInstance(
        name="Kalender", integration_type="calendar", adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "ada"},
    )
    call = ToolCall("calendar-delete", "customize_matoloop", {
        "action": "delete", "area": "integrations", "integration_id": str(item.id),
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(adapter_availability={"caldav": "disabled"}),
        UserIntegrationSettings: UserIntegrationSettings(instances=[item]),
    }[model]))
    delete = AsyncMock()
    monkeypatch.setattr(personal_integrations.CalDavIntegrationService, "delete", delete)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    delete.assert_awaited_once_with(context.db, item.id, owner_id=context.session.user_id)


async def test_personal_search_selection_rejects_unavailable_provider(monkeypatch, tmp_path) -> None:
    from mato_loop_backend.settings import UserToolSettings
    from mato_loop_backend.tools import personal_integrations

    call = ToolCall("search-selection", "customize_matoloop", {
        "action": "update", "area": "integrations", "adapter_type": "search_selection",
        "changes": {"primary_provider": "searxng:missing"},
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(personal_integrations, "load_effective_integrations", AsyncMock(return_value=IntegrationSettings()))
    monkeypatch.setattr(personal_integrations, "_load_tools", AsyncMock(return_value=UserToolSettings()))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "integration.providerUnavailable"
    save.assert_not_awaited()

    call.arguments["changes"] = {"primary_provider": "no_key"}
    result = await tool_registry.execute(call, context)
    assert not result.is_error
    assert save.await_args.kwargs["user_id"] == context.session.user_id


async def test_personal_calendar_default_requires_discovered_owned_resource(monkeypatch, tmp_path) -> None:
    from mato_loop_backend.integrations.caldav import service as caldav_service
    from mato_loop_backend.integrations.contracts import IntegrationResource
    from mato_loop_backend.settings import UserToolSettings
    from mato_loop_backend.tools import personal_integrations

    item = IntegrationInstance(
        name="Kalender", integration_type="calendar", adapter_type="caldav",
        config={"base_url": "https://calendar.example.com", "username": "ada"},
        credential_id=uuid4(),
    )
    call = ToolCall("default-calendar", "customize_matoloop", {
        "action": "update", "area": "integrations", "adapter_type": "caldav_defaults",
        "integration_id": str(item.id),
        "changes": {"default_resource_key": f"calendar:{'a' * 32}"},
    })
    context = _context(tmp_path, call)
    monkeypatch.setattr(mock_tools_access, "profile_tool_settings", AsyncMock(return_value=context.tool_settings))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=lambda _db, model, **_kw: {
        IntegrationSettings: IntegrationSettings(),
        UserIntegrationSettings: UserIntegrationSettings(instances=[item]),
    }[model]))
    monkeypatch.setattr(caldav_service, "load_credential", AsyncMock(return_value=SimpleNamespace(kind="caldav.password")))
    monkeypatch.setattr(caldav_service, "read_credential", lambda _record: {"password": "hidden"})
    monkeypatch.setattr(caldav_service, "integration_adapter", lambda *_args: SimpleNamespace(
        resource_loader=True,
        resources=AsyncMock(return_value=(
            IntegrationResource(key=f"calendar:{'b' * 32}", label="Anderer Kalender"),
        )),
    ))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "save", save)
    monkeypatch.setattr(personal_integrations, "_load_tools", AsyncMock(return_value=UserToolSettings()))

    result = await tool_registry.execute(call, context)

    assert result.metadata["errorCode"] == "validation.invalidChoice"
    save.assert_not_awaited()

    call.arguments["changes"] = {"default_resource_key": f"calendar:{'b' * 32}"}
    result = await tool_registry.execute(call, context)
    assert not result.is_error
    assert save.await_args.args[1].default_calendar.calendar_key == f"calendar:{'b' * 32}"
    assert save.await_args.args[1].resource_labels[str(item.id)] == {
        f"calendar:{'b' * 32}": "Anderer Kalender"
    }
    assert save.await_args.kwargs["user_id"] == context.session.user_id


async def test_personal_settings_permissions_bind_to_session_owner(tmp_path) -> None:
    call = ToolCall("settings", "customize_matoloop", {"action": "get", "area": "memory"})
    context = _context(tmp_path, call)
    for request in (
        personal_settings_permission(uuid4(), "memory", "get"),
        personal_settings_permission(context.session.user_id, "memory", "get"),
    ):
        decision = await PermissionPolicy.evaluate(
            context.db, context.session, request, context.root_files, ToolExecutionPolicy.AUTO
        )
        assert decision == (
            PermissionDecision.ALLOW
            if str(context.session.user_id) in request.resource_scope
            else PermissionDecision.DENY
        )


def test_update_user_profile_requires_a_field_and_rejects_blank_values() -> None:
    arguments_model = tool_registry._definitions["update_user_profile"].arguments_model

    with pytest.raises(ValidationError):
        arguments_model.model_validate({})
    with pytest.raises(ValidationError):
        arguments_model.model_validate({"preferred_name": "   "})
    with pytest.raises(ValidationError):
        arguments_model.model_validate({"full_name": "   "})
    assert arguments_model.model_validate({"preferred_name": None}).preferred_name is None
    assert arguments_model.model_validate({"full_name": None}).full_name is None


@pytest.mark.parametrize("project_scope", [False, True])
async def test_build_skill_uses_user_scoped_approval_and_persistence(
    monkeypatch, tmp_path, project_scope
) -> None:
    tool_settings = ToolSettings()
    skills = SkillsSettings()

    async def load_settings(_db, settings_type, **_kwargs):
        return {ToolSettings: tool_settings, SkillsSettings: skills}[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="build-skill",
        name="build_skill",
        arguments={"brief": "Create a reusable API research workflow."},
    )
    context = _context(tmp_path, call)
    storage = context.root_files.session_storage
    if project_scope:
        context.session.project_id = uuid4()
        storage.move_session(context.session.id, None, context.session.project_id)
    context.root_files = RootFileService(
        context.session.id,
        project_id=context.session.project_id,
        session_storage=storage,
        run_id=context.run_id,
        session_quota_bytes=17 * 1024 * 1024,
        max_editable_text_bytes=321 * 1024,
    )
    ui_files = RootFileService(
        context.session.id,
        project_id=context.session.project_id,
        session_storage=storage,
    )
    context.root_files.write_file("session", "agent-private.txt", "unfinished work")
    draft_services = []

    def approval_files(*args, **kwargs):
        service = RootFileService(*args, **kwargs)
        draft_services.append(service)
        return service

    _mock_value = approval_files
    for _mock_target in (mock_tools_skills, mock_files_access,):
        monkeypatch.setattr(_mock_target, 'RootFileService', _mock_value)
    context.skill_draft_handler = AsyncMock(
        return_value=SkillDraft(
            name="API research",
            description="Use when an API must be queried.",
            instructions="Call the available API tool and validate its response.",
        )
    )

    async def approve_edited_draft(request):
        draft_file = request.approval_details["draftFile"]
        assert context.root_files.run_id == context.run_id
        assert (
            json.loads(
                ui_files.read_bytes(
                    "session",
                    draft_file["path"],
                    limit=mock_tools_skills.SKILL_DRAFT_MAX_BYTES,
                )
            )["name"]
            == "API research"
        )
        assert ui_files.list_revisions("agent-private.txt") == ()
        edited = json.dumps(
            {
                "name": "API checks",
                "description": "Use when an API response must be checked.",
                "instructions": "Call the available API tool and verify its response.",
            }
        )
        context.approval_resolution["expected_revision"] = ui_files.replace_text_file(
            "session",
            draft_file["path"],
            edited,
            draft_file["initialRevision"],
        )
        return PermissionDecision.ALLOW

    context.ask_handler = AsyncMock(side_effect=approve_edited_draft)

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload["name"] == "API checks"
    assert payload["effectiveFrom"] == "next_run"
    assert len(skills.skills) == 1
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "skills.write"
    assert request.resource_type == "skill_collection"
    assert request.resource_scope == str(context.session.user_id)
    assert request.approval_details["skillPreview"]["name"] == "API research"
    draft_file = request.approval_details["draftFile"]
    assert draft_file["path"].startswith("skill-draft-")
    draft_bytes = ui_files.read_bytes(
        "session", draft_file["path"], limit=mock_tools_skills.SKILL_DRAFT_MAX_BYTES
    )
    assert (
        context.approval_resolution["expected_revision"] == hashlib.sha256(draft_bytes).hexdigest()
    )
    assert skills.skills[0].model_dump(exclude={"id", "enabled"}) == json.loads(draft_bytes)
    assert draft_file["initialRevision"] != context.approval_resolution["expected_revision"]
    assert len(draft_services) == 1
    assert draft_services[0].run_id is None
    assert draft_services[0].root_identity("session") == context.root_files.root_identity("session")
    assert draft_services[0].SESSION_QUOTA_BYTES == context.root_files.SESSION_QUOTA_BYTES
    assert draft_services[0].max_editable_text_bytes == context.root_files.max_editable_text_bytes
    assert len(ui_files.list_revisions(draft_file["path"])) == 2
    assert ui_files.list_revisions("agent-private.txt") == ()
    context.root_files.write_file("session", "after-approval.txt", "run remains open")
    save.assert_awaited_once()


async def test_build_skill_rejects_a_draft_changed_after_approval(monkeypatch, tmp_path) -> None:
    skills = SkillsSettings()

    async def load_settings(_db, settings_type, **_kwargs):
        return {ToolSettings: ToolSettings(), SkillsSettings: skills}[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="build-stale-skill",
        name="build_skill",
        arguments={"brief": "Create an API workflow."},
    )
    context = _context(tmp_path, call)
    ui_files = context.root_files
    context.root_files = RootFileService(
        context.session.id,
        session_storage=ui_files.session_storage,
        run_id=context.run_id,
    )
    context.root_files.write_file("session", "agent-private.txt", "unfinished work")
    context.skill_draft_handler = AsyncMock(
        return_value=SkillDraft(
            name="API research",
            description="Use for API research.",
            instructions="Call the available API tool.",
        )
    )

    async def approve_then_change(request):
        draft = request.approval_details["draftFile"]
        initial = ui_files.read_bytes(
            "session", draft["path"], limit=mock_tools_skills.SKILL_DRAFT_MAX_BYTES
        )
        assert hashlib.sha256(initial).hexdigest() == draft["initialRevision"]
        context.approval_resolution["expected_revision"] = draft["initialRevision"]
        ui_files.write_file(
            "session",
            draft["path"],
            '{"name":"Changed","description":"Changed","instructions":"Changed"}',
            mode="replace",
            expected_sha256=draft["initialRevision"],
        )
        return PermissionDecision.ALLOW

    context.ask_handler = AsyncMock(side_effect=approve_then_change)

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert result.metadata["errorCode"] == "file.changed"
    assert skills.skills == []
    assert ui_files.list_revisions("agent-private.txt") == ()
    context.root_files.write_file("session", "after-approval.txt", "run remains open")
    save.assert_not_awaited()


async def test_edit_skill_reuses_draft_approval_and_preserves_target(
    monkeypatch, tmp_path
) -> None:
    existing = SkillDefinition(
        name="Songfinder",
        description="Findet Songs.",
        instructions="Suche breit und prüfe Quellen.",
    )
    skills = SkillsSettings(skills=[existing])

    async def load_settings(_db, settings_type, **_kwargs):
        return {ToolSettings: ToolSettings(), SkillsSettings: skills}[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="edit-skill",
        name="edit_skill",
        arguments={
            "skill_id": str(existing.id),
            "brief": "Ändere nur die Einsatzbeschreibung.",
        },
    )
    context = _context(tmp_path, call)
    target = existing.model_copy(deep=True)
    context.skill_state.catalog[existing.id] = target
    context.skill_draft_handler = AsyncMock(
        return_value=SkillDraft(
            name=existing.name,
            description="Immer bei einer Suche anhand von Lyrics verwenden.",
            instructions=existing.instructions,
        )
    )

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload == {
        "id": str(existing.id),
        "name": "Songfinder",
        "effectiveFrom": "next_run",
        "updated": True,
    }
    assert len(skills.skills) == 1
    assert skills.skills[0].id == existing.id
    assert skills.skills[0].description.startswith("Immer bei")
    assert skills.skills[0].instructions == existing.instructions
    assert context.skill_state.edit_completed
    context.skill_draft_handler.assert_awaited_once_with(
        "Ändere nur die Einsatzbeschreibung.", None, target
    )
    request = context.ask_handler.await_args.args[0]
    assert request.action_kind == "edit_skill"
    assert request.resource_type == "skill"
    assert request.resource_scope == str(existing.id)
    assert request.approval_details["skillTarget"] == {
        "id": str(existing.id),
        "name": "Songfinder",
    }
    save.assert_awaited_once()


async def test_edit_skill_rejects_parallel_target_change(monkeypatch, tmp_path) -> None:
    existing = SkillDefinition(
        name="Songfinder",
        description="Findet Songs.",
        instructions="Suche breit.",
    )
    skills = SkillsSettings(skills=[existing])

    async def load_settings(_db, settings_type, **_kwargs):
        return {ToolSettings: ToolSettings(), SkillsSettings: skills}[settings_type]

    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    call = ToolCall(
        id="edit-stale-skill",
        name="edit_skill",
        arguments={
            "skill_id": str(existing.id),
            "brief": "Verbessere die Beschreibung.",
        },
    )
    context = _context(tmp_path, call)
    context.skill_state.catalog[existing.id] = existing.model_copy(deep=True)
    context.skill_draft_handler = AsyncMock(
        return_value=SkillDraft(
            name=existing.name,
            description="Neue Beschreibung.",
            instructions=existing.instructions,
        )
    )

    async def approve_after_parallel_change(_request):
        skills.skills[0] = existing.model_copy(
            update={"description": "Parallel geändert."}
        )
        return PermissionDecision.ALLOW

    context.ask_handler = AsyncMock(side_effect=approve_after_parallel_change)

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert result.metadata["errorCode"] == "skill.changed"
    assert skills.skills[0].description == "Parallel geändert."
    assert not context.skill_state.edit_completed
    save.assert_not_awaited()


async def test_load_skill_activates_snapshot_without_returning_instructions(
    monkeypatch, tmp_path
) -> None:
    skill = SkillDefinition(
        name="API research",
        description="Use when an API must be queried.",
        instructions="Call the available API tool.",
    )
    call = ToolCall(
        id="load-skill",
        name="load_skill",
        arguments={"skill_id": str(skill.id)},
    )
    context = _context(tmp_path, call)
    context.skill_state.catalog[skill.id] = skill
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=ToolSettings())
    )

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert context.skill_state.active == skill
    assert context.skill_state.active is not skill
    assert skill.instructions not in result.content
    context.ask_handler.assert_not_awaited()


async def test_load_skill_auto_to_ask_policy_transition_blocks(
    monkeypatch, tmp_path
) -> None:
    skill = SkillDefinition(
        name="API research",
        description="Use when an API must be queried.",
        instructions="Call the available API tool.",
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(
            side_effect=[
                _tool_settings({"policies": {"skills.load": "auto"}}),
                _tool_settings({"policies": {"skills.load": "ask"}}),
            ]
        ),
    )
    call = ToolCall(
        id="load-transition",
        name="load_skill",
        arguments={"skill_id": str(skill.id)},
    )
    context = _context(tmp_path, call)
    profile_loads = iter(
        [
            _tool_settings({"policies": {"skills.load": "auto"}}),
            _tool_settings({"policies": {"skills.load": "ask"}}),
        ]
    )

    async def load_optional(_db, settings_type, **_kwargs):
        if settings_type is not AgentProfilesSettings:
            return None
        return AgentProfilesSettings.model_validate(
            _settings_record(next(profile_loads))
        )

    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(side_effect=load_optional)
    )
    context.skill_state.catalog[skill.id] = skill

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    assert context.skill_state.active is None
    context.ask_handler.assert_not_awaited()


async def test_load_skill_ask_policy_uses_the_user_skill_collection(
    monkeypatch, tmp_path
) -> None:
    skill = SkillDefinition(
        name="API research",
        description="Use when an API must be queried.",
        instructions="Call the available API tool.",
    )
    settings = _tool_settings(
        {"policies": {"skills.load": "ask"}}
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    call = ToolCall(
        id="load-ask",
        name="load_skill",
        arguments={"skill_id": str(skill.id)},
    )
    context = _context(tmp_path, call)
    _set_profile_settings(context, settings)
    context.skill_state.catalog[skill.id] = skill

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "skills.load"
    assert request.resource_type == "skill_collection"
    assert request.resource_scope == str(context.session.user_id)


async def test_disabled_agent_identity_tool_is_not_offered_or_executed(
    monkeypatch, tmp_path
) -> None:
    tool_settings = _tool_settings(
        {"policies": {"settings.agent_identity.update": "disabled"}}
    )
    capabilities = set()
    assert "update_agent_identity" not in {
        schema["name"]
        for schema in tool_registry.schemas(
            IntegrationSettings(), tool_capabilities=capabilities
        )
    }

    async def load_settings(_db, settings_type, **_kwargs):
        return {ToolSettings: tool_settings, AiSettings: AiSettings()}[settings_type]

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    call = ToolCall(
        id="identity-disabled",
        name="update_agent_identity",
        arguments={"display_name": "Ada"},
    )
    context = _context(tmp_path, call)
    _set_profile_settings(context, tool_settings)
    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert result.metadata == {"errorCode": "tool.unavailable"}


async def test_disabled_user_profile_tool_is_not_offered_or_executed(
    monkeypatch, tmp_path
) -> None:
    tool_settings = _tool_settings(
        {"policies": {"settings.user_profile.update": "disabled"}}
    )
    effective = tool_registry.effective(
        IntegrationSettings(), tool_settings=tool_settings
    )
    assert "update_user_profile" not in effective.tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=tool_settings))
    call = ToolCall(
        id="profile-disabled",
        name="update_user_profile",
        arguments={"preferred_name": "Ada"},
    )
    context = _context(tmp_path, call)
    _set_profile_settings(context, tool_settings)
    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert result.metadata == {"errorCode": "tool.unavailable"}


@pytest.mark.parametrize(
    ("call", "expected_error"),
    [
        (
            ToolCall(id="unknown", name="missing", arguments={}),
            {
                "code": "tool.unknown",
                "message": "The tool operation could not be completed.",
            },
        ),
        (
            ToolCall(id="invalid", name="example", arguments={"unexpected": True}),
            {
                "code": "tool.invalidArguments",
                "field": "value",
                "message": (
                    "The tool arguments are invalid. Inspect error.field and retry with "
                    "arguments that match the tool schema."
                ),
            },
        ),
    ],
)
async def test_tool_registry_returns_stable_errors_for_unknown_tools_and_invalid_arguments(
    call: ToolCall, expected_error: dict[str, str]
) -> None:
    result = await ToolRegistry([_definition()]).execute(call, Mock())

    assert result.metadata == {"errorCode": expected_error["code"]}
    assert json.loads(result.content) == {"error": expected_error}


@pytest.mark.parametrize(
    ("name", "arguments", "resource_type"),
    [
        ("session_read", {}, "session"),
        ("session_browse", {}, "session_collection"),
        ("session_search", {"query": "vertraulich"}, "session_collection"),
    ],
)
async def test_session_tools_require_permission_before_reading_session_data(
    tmp_path, name: str, arguments: dict, resource_type: str
) -> None:
    call = ToolCall(id="call", name=name, arguments=arguments)
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    request = context.ask_handler.await_args.args[0]
    assert request.resource_type == resource_type
    context.db.get.assert_not_awaited()
    context.db.scalars.assert_not_awaited()
    context.db.scalar.assert_not_awaited()


@pytest.mark.parametrize("target_id", [None, uuid4(), "child"])
async def test_session_read_uses_current_or_explicit_target_session(tmp_path, target_id) -> None:
    child = target_id == "child"
    if child:
        target_id = uuid4()
    arguments = {} if target_id is None else {"session_id": str(target_id)}
    call = ToolCall(id="read", name="session_read", arguments=arguments)
    context = _context(tmp_path, call)
    expected_id = target_id or context.session.id
    target = ChatSession(
        id=expected_id,
        user_id=context.session.user_id,
        title="Ziel",
        memory_mode="disabled",
        web_search_policy="available",
        private_integration_data_used=True,
        parent_session_id=context.session.id if child else None,
    )
    messages = Mock()
    messages.all.return_value = []
    context.db.scalar.return_value = target
    _session_read_records(context, messages.all.return_value)

    result = await tool_registry.execute(call, context)

    assert result.is_error is False
    assert json.loads(result.content)["session"] == {
        "id": str(expected_id),
        "title": "Ziel",
        "selectedExecutionAgentId": None,
    }
    request = context.ask_handler.await_args.args[0]
    assert request.resource_scope == str(expected_id)
    context.db.scalar.assert_awaited_once()
    context.db.scalars.assert_awaited_once()
    statement = context.db.scalar.await_args.args[0]
    assert "chat_sessions.parent_session_id IS NULL" in str(statement)
    assert context.session.id in statement.compile().params.values()


async def test_session_read_preserves_agent_provenance_across_one_run_switch(tmp_path) -> None:
    previous, selected = uuid4(), uuid4()
    call = ToolCall("read", "session_read", {})
    context = _context(tmp_path, call)
    context.session.preferred_execution_agent_id = selected
    run = AgentRun(id=uuid4(), session_id=context.session.id, execution_agent_id=selected)
    messages = [
        Message(id=uuid4(), session_id=context.session.id, role="user", content="Start",
                provider_data={"_matoloop": {"executionAgentId": str(previous)}}),
        Message(id=uuid4(), session_id=context.session.id, role="assistant", content="Old",
                provider_data={"_matoloop": {"runId": str(run.id)}}),
        Message(id=uuid4(), session_id=context.session.id, role="tool",
                content=json.dumps({"selected": True, "id": str(selected)}),
                provider_data={"_matoloop": {"runId": str(run.id), "toolName": "select_execution_agent"}}),
        Message(id=uuid4(), session_id=context.session.id, role="assistant", content="New",
                provider_data={"_matoloop": {"runId": str(run.id)}}),
    ]
    run.input_message_id = messages[0].id
    context.db.scalar.return_value = context.session
    _session_read_records(context, messages, [run])

    result = await tool_registry.execute(call, context)
    payload = json.loads(result.content)

    assert payload["session"]["selectedExecutionAgentId"] == str(selected)
    assert [item["executionAgentId"] for item in payload["messages"]] == [
        str(previous), str(previous), str(selected), str(selected),
    ]


@pytest.mark.parametrize(
    ("source_trust", "reader_trust", "expected_title"),
    [
        (TrustLevel.PUBLIC, TrustLevel.PUBLIC, "Ziel"),
        (TrustLevel.PRIVATE, TrustLevel.PUBLIC, "Ziel"),
        (TrustLevel.PUBLIC, TrustLevel.PRIVATE, "Ziel"),
        (TrustLevel.PRIVATE, TrustLevel.PRIVATE, "Ziel"),
    ],
)
async def test_session_read_exposes_authorized_messages_regardless_of_source_trust(
    tmp_path,
    source_trust: TrustLevel,
    reader_trust: TrustLevel,
    expected_title: str | None,
) -> None:
    session_id = uuid4()
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={"session_id": str(session_id)},
    )
    context = _context(tmp_path, call)
    context.memory_trust_level = reader_trust
    context.db.scalar.return_value = ChatSession(
        id=session_id,
        user_id=context.session.user_id,
        title="Ziel",
    )
    messages = Mock()
    messages.all.return_value = [
        Message(
            id=uuid4(),
            session_id=session_id,
            role="user",
            content="Start",
            trust_level=source_trust.value,
        )
    ]
    _session_read_records(context, messages.all.return_value)

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content)["session"]["title"] == expected_title
    assert "Start" in result.content


async def test_session_read_returns_a_window_around_discovery_anchor(tmp_path) -> None:
    session_id = uuid4()
    messages = [
        Message(
            id=uuid4(),
            session_id=session_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Nachricht {index}",
            trust_level=TrustLevel.PUBLIC.value,
            created_at=datetime(2026, 7, 20, 10, index, tzinfo=UTC),
        )
        for index in range(5)
    ]
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={
            "session_id": str(session_id),
            "around_message_id": str(messages[2].id),
            "limit": 3,
        },
    )
    context = _context(tmp_path, call)
    context.db.scalar.return_value = ChatSession(
        id=session_id,
        user_id=context.session.user_id,
        title="Ziel",
    )
    rows = Mock()
    rows.all.return_value = messages
    _session_read_records(context, messages)

    result = await tool_registry.execute(call, context)
    payload = json.loads(result.content)

    assert [item["id"] for item in payload["messages"]] == [
        str(item.id) for item in messages[1:4]
    ]
    assert payload["hasEarlier"] is True
    assert payload["nextAfterMessageId"] == str(messages[3].id)


async def test_session_read_cursor_uses_last_emitted_message(tmp_path) -> None:
    session_id = uuid4()
    messages = [
        Message(
            id=uuid4(),
            session_id=session_id,
            role="user",
            content=str(index) * 12_000,
            trust_level=TrustLevel.PUBLIC.value,
        )
        for index in range(6)
    ]
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={"session_id": str(session_id), "limit": 6},
    )
    context = _context(tmp_path, call)
    context.db.scalar.return_value = ChatSession(
        id=session_id,
        user_id=context.session.user_id,
        title="Ziel",
    )
    rows = Mock()
    rows.all.return_value = messages
    _session_read_records(context, messages)

    result = await tool_registry.execute(call, context)
    payload = json.loads(result.content)

    assert len(payload["messages"]) == 5
    assert payload["nextAfterMessageId"] == str(messages[4].id)
    assert context.session_read_cursors[session_id] == messages[4].id


async def test_session_read_rejects_cursor_other_than_latest_continuation(tmp_path) -> None:
    session_id = uuid4()
    expected = uuid4()
    supplied = uuid4()
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={
            "session_id": str(session_id),
            "after_message_id": str(supplied),
            "limit": 5,
        },
    )
    context = _context(tmp_path, call)
    context.session_read_cursors[session_id] = expected

    result = await tool_registry.execute(call, context)

    assert result.is_error is True
    assert json.loads(result.content) == {
        "error": {
            "code": "tool.cursorInvalid",
            "message": (
                "The session cursor is unavailable. Retry session_read with "
                "error.params.retryArguments exactly."
            ),
            "field": "after_message_id",
            "params": {
                "retryArguments": {
                    "session_id": str(session_id),
                    "after_message_id": str(expected),
                    "limit": 5,
                }
            },
        }
    }
    context.db.scalar.assert_not_awaited()
    context.db.scalars.assert_not_awaited()


@pytest.mark.parametrize("cursor_field", ["around_message_id", "after_message_id"])
async def test_session_read_invalid_cursor_returns_safe_retry_arguments(
    tmp_path, cursor_field: str
) -> None:
    session_id = uuid4()
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={"session_id": str(session_id), cursor_field: str(uuid4()), "limit": 5},
    )
    context = _context(tmp_path, call)
    context.db.scalar.return_value = ChatSession(
        id=session_id,
        user_id=context.session.user_id,
        title="Ziel",
    )
    rows = Mock()
    rows.all.return_value = []
    context.db.scalars.return_value = rows

    result = await tool_registry.execute(call, context)

    assert result.is_error is True
    assert json.loads(result.content) == {
        "error": {
            "code": "tool.cursorInvalid",
            "message": (
                "The session cursor is unavailable. Retry session_read with "
                "error.params.retryArguments exactly."
            ),
            "field": cursor_field,
            "params": {
                "retryArguments": {"session_id": str(session_id), "limit": 5},
            },
        }
    }


@pytest.mark.parametrize(
    ("name", "result_key"),
    [("session_browse", "sessions"), ("session_search", "results")],
)
@pytest.mark.parametrize("project_session", [True, False])
async def test_session_discovery_returns_private_anchor_for_direct_read(
    tmp_path, name: str, result_key: str, project_session: bool
) -> None:
    session = ChatSession(
        id=uuid4(), user_id=uuid4(), project_id=uuid4(), title="Projekt Aurora"
    )
    message_id = uuid4()
    message_at = datetime(2026, 7, 20, 10, tzinfo=UTC)
    call = ToolCall(
        id="discover",
        name=name,
        arguments={} if name == "session_browse" else {"query": "Aurora"},
    )
    context = _context(tmp_path, call)
    context.memory_trust_level = TrustLevel.PRIVATE
    context.session.project_id = uuid4() if project_session else None
    session.user_id = context.session.user_id
    rows = Mock()
    rows.all.return_value = [
        (
            session,
            message_id,
            "user",
            "Aurora bleibt unser Hauptprojekt.",
            message_at,
        )
    ]
    context.db.execute.return_value = rows

    result = await tool_registry.execute(call, context)
    item = json.loads(result.content)[result_key][0]

    assert item["title"] == "Projekt Aurora"
    assert item["anchorMessage"] == {
        "id": str(message_id),
        "role": "user",
        "createdAt": message_at.isoformat(),
        "preview": "Aurora bleibt unser Hauptprojekt.",
    }
    assert item["sessionReadArguments"] == {
        "session_id": str(session.id),
        "around_message_id": str(message_id),
    }
    statement = context.db.execute.await_args.args[0]
    assert "chat_sessions.user_id" in str(statement)
    assert "chat_sessions.id !=" in str(statement)
    assert "chat_sessions.project_id" not in str(statement.whereclause)


async def test_session_browse_shows_public_derived_titles_to_public_models(
    tmp_path,
) -> None:
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Öffentlicher Titel")
    call = ToolCall(id="browse", name="session_browse", arguments={})
    context = _context(tmp_path, call)
    session.user_id = context.session.user_id
    rows = Mock()
    rows.all.return_value = [
        (
            session,
            uuid4(),
            "user",
            "Öffentlicher Ausschnitt",
            datetime.now(UTC),
        )
    ]
    context.db.execute.return_value = rows

    result = await tool_registry.execute(call, context)
    item = json.loads(result.content)["sessions"][0]

    assert item["title"] == "Öffentlicher Titel"
    assert item["anchorMessage"]["preview"] == "Öffentlicher Ausschnitt"
    assert "messages.trust_level" not in str(context.db.execute.await_args.args[0])


async def test_session_search_shows_private_derived_titles_to_public_models(
    tmp_path,
) -> None:
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Projekt Aurora")
    call = ToolCall(
        id="search",
        name="session_search",
        arguments={"query": "Aurora"},
    )
    context = _context(tmp_path, call)
    session.user_id = context.session.user_id
    rows = Mock()
    rows.all.return_value = [
        (
            session,
            uuid4(),
            "user",
            "Öffentlicher Ausschnitt",
            datetime.now(UTC),
        )
    ]
    context.db.execute.return_value = rows

    result = await tool_registry.execute(call, context)
    item = json.loads(result.content)["results"][0]

    assert item["title"] == "Projekt Aurora"
    statement = str(context.db.execute.await_args.args[0])
    assert "chat_sessions.title" in statement
    assert "messages.trust_level =" not in statement


def test_session_tool_result_read_schema_uses_a_fixed_host_chunk_size() -> None:
    schema = tool_registry._definitions["session_tool_result_read"].schema()

    assert schema["parameters"]["required"] == ["message_id"]
    assert schema["parameters"]["properties"]["offset"] == {
        "default": 0,
        "minimum": 0,
        "type": "integer",
    }
    assert "limit" not in schema["parameters"]["properties"]


def test_session_discovery_and_anchor_schemas_keep_tasks_separate() -> None:
    browse = tool_registry._definitions["session_browse"].schema()
    search = tool_registry._definitions["session_search"].schema()
    read = tool_registry._definitions["session_read"].schema()

    assert "_reason" in browse["parameters"]["properties"]
    assert "_reason" not in browse["parameters"].get("required", [])
    assert "query" not in browse["parameters"]["properties"]
    assert search["parameters"]["required"] == ["query"]
    assert "around_message_id" in read["parameters"]["properties"]


async def test_session_read_rejects_two_positions_before_database_access(tmp_path) -> None:
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={
            "after_message_id": str(uuid4()),
            "around_message_id": str(uuid4()),
        },
    )
    context = _context(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "tool.invalidArguments"}
    context.db.scalar.assert_not_awaited()
    context.db.scalars.assert_not_awaited()


def _mock_tool_result_history(context, message, tool_name) -> None:
    message.tool_call_id = "origin"
    origin = Message(
        id=uuid4(), session_id=context.session.id, role="assistant", content="",
        tool_calls=[{"id": message.tool_call_id, "name": tool_name}],
    )
    context.db.scalar.return_value = message
    context.db.scalars.return_value = Mock()
    context.db.scalars.return_value.all.return_value = [origin, message]


@pytest.mark.parametrize("tool_name", ["secret_input", "session_tool_result_read"])
async def test_historical_secret_input_filters_expired_references(tmp_path, tool_name) -> None:
    call = ToolCall("read-result", "session_tool_result_read", {"message_id": str(uuid4())})
    context = _context(tmp_path, call)
    expired, active = uuid4(), uuid4()
    context.one_time_secrets[active] = "hidden password"
    message = Message(
        id=UUID(call.arguments["message_id"]), session_id=context.session.id, role="tool",
        content=json.dumps({"environment": {
            "OLD": f"run-secret:{expired}", "CURRENT": f"run-secret:{active}",
        }}),
    )
    if tool_name == "session_tool_result_read":
        message.content = json.dumps({"toolName": "secret_input", "content": message.content})
    _mock_tool_result_history(context, message, tool_name)
    result = await tool_registry.execute(call, context)
    history = json.loads(json.loads(result.content)["content"])
    if tool_name == "session_tool_result_read":
        history = json.loads(history["content"])
    assert (history["environment"], history["unavailableCredentialNames"]) == (
        {"CURRENT": f"run-secret:{active}"}, ["OLD"],
    )


@pytest.mark.parametrize("tool_name", ["fetch_transcript", "webfetch"])
async def test_session_tool_result_read_pages_survive_projection(tmp_path, tool_name) -> None:
    call = ToolCall("read-result", "session_tool_result_read", {"message_id": str(uuid4())})
    context = _context(tmp_path, call)
    content = (
        json.dumps({"kind": "transcript", "text": "transcript text " * 1000})
        if tool_name == "fetch_transcript"
        else "plain web content " * 1000
    )
    origin = Message(
        id=uuid4(), session_id=context.session.id, role="assistant", content="",
        tool_calls=[{"id": "reused", "name": tool_name}],
    )
    message = Message(
        id=UUID(call.arguments["message_id"]), session_id=context.session.id,
        role="tool", tool_call_id="reused", content=content, trust_level="public",
    )
    later = Message(
        id=uuid4(), session_id=context.session.id, role="assistant", content="",
        tool_calls=[{"id": "reused", "name": "memory_retrieve"}],
    )
    context.db.scalar.return_value = message
    context.db.scalars.return_value = Mock()
    context.db.scalars.return_value.all.return_value = [origin, message, later]
    reader = Message(
        id=uuid4(), session_id=context.session.id, role="assistant", content="",
        tool_calls=[{"id": call.id, "name": call.name}], trust_level="public",
    )
    chunks = []
    for offset in (0, 12_000):
        call.arguments["offset"] = offset
        result = await tool_registry.execute(call, context)
        page = Message(
            id=uuid4(), session_id=context.session.id, role="tool",
            tool_call_id=call.id, content=result.content, trust_level="public",
        )
        projected = _projection_messages("System", None, [reader, page], TrustLevel.PUBLIC)
        payload = json.loads(next(item["content"] for item in projected if item["role"] == "tool"))
        assert payload["content"]
        assert payload["toolName"] == tool_name
        assert payload["offset"] == offset
        assert payload["hasMore"] is (offset == 0)
        assert payload["nextOffset"] == (12_000 if offset == 0 else None)
        chunks.append(payload["content"])
    expected = mock_tools_sessions._sanitize_tool_result_reference_fields(content)
    statement = context.db.scalars.await_args.args[0]
    assert "messages.session_id =" in str(statement)
    assert "ORDER BY messages.created_at, messages.id" in str(statement)
    assert context.session.id in statement.compile().params.values()
    assert len(chunks[0]) == 12_000
    assert "".join(chunks) == expected
    if tool_name == "fetch_transcript":
        for chunk in chunks:
            with pytest.raises(json.JSONDecodeError):
                json.loads(chunk)


@pytest.mark.parametrize("tool_name", ["memory_retrieve", "session_read", "session_tool_result_read"])
async def test_session_tool_result_read_filters_complete_sensitive_results_before_paging(
    tmp_path, tool_name,
) -> None:
    call = ToolCall("read-result", "session_tool_result_read", {"message_id": str(uuid4())})
    context = _context(tmp_path, call)
    content = json.dumps({"items": [
        {"content": "private secret", "audience": "private_only"},
        {"content": "public text " * 1400, "audience": "public_only"},
    ]})
    if tool_name == "session_read":
        content = json.dumps({"messages": [
            {"role": "tool", "toolName": "memory_retrieve", "content": content},
        ]})
    elif tool_name == "session_tool_result_read":
        content = json.dumps({"toolName": "memory_retrieve", "content": content})
    message = Message(
        id=UUID(call.arguments["message_id"]), session_id=context.session.id,
        role="tool", content=content, trust_level="public",
    )
    _mock_tool_result_history(context, message, tool_name)
    # A later round reuses the ID for a non-sensitive tool.
    context.db.scalars.return_value.all.return_value.append(Message(
        id=uuid4(), session_id=context.session.id, role="assistant", content="",
        tool_calls=[{"id": message.tool_call_id, "name": "webfetch"}],
    ))
    reader = Message(
        id=uuid4(), session_id=context.session.id, role="assistant", content="",
        tool_calls=[{"id": call.id, "name": call.name}], trust_level="public",
    )
    chunks = []
    for offset in (0, 12_000):
        call.arguments["offset"] = offset
        result = await tool_registry.execute(call, context)
        payload = json.loads(result.content)
        assert payload["toolName"] == tool_name
        assert "private secret" not in payload["content"]
        assert "public text" in payload["content"]
        chunks.append(payload["content"])
        page = Message(
            id=uuid4(), session_id=context.session.id, role="tool",
            tool_call_id=call.id, content=result.content, trust_level="public",
        )
        # No serialized marker authorizes partial JSON after a trust change.
        for target in (TrustLevel.PUBLIC, TrustLevel.PRIVATE):
            projected = _projection_messages("System", None, [reader, page], target)
            filtered = json.loads(next(item["content"] for item in projected if item["role"] == "tool"))
            assert filtered["content"] == ""
            assert filtered["redactedNestedToolResult"] is True
    assert "redactedPrivateItems" in "".join(chunks)
    json.loads("".join(chunks))


@pytest.mark.parametrize("content", ["plain unknown", '{"toolName":"webfetch","content":"secret"}'])
async def test_session_tool_result_read_rejects_unknown_provenance(tmp_path, content) -> None:
    call = ToolCall("read-result", "session_tool_result_read", {"message_id": str(uuid4())})
    context = _context(tmp_path, call)
    message = Message(
        id=UUID(call.arguments["message_id"]), session_id=context.session.id,
        role="tool", content=content, trust_level="public",
    )
    _mock_tool_result_history(context, message, "webfetch")
    records = context.db.scalars.return_value.all.return_value
    records.insert(1, Message(
        id=uuid4(), session_id=context.session.id, role="user", content="New turn",
    ))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "session.messageNotFound"}
    assert "secret" not in result.content


async def test_session_tool_result_read_returns_bounded_continuation_chunks(tmp_path) -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="tool",
        content="a" * 12_000 + "rest",
        trust_level=TrustLevel.PRIVATE.value,
    )
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(message.id)},
    )
    context = _context(tmp_path, call)
    message.session_id = context.session.id
    _mock_tool_result_history(context, message, "webfetch")

    first = await tool_registry.execute(call, context)
    call.arguments["offset"] = 12_000
    second = await tool_registry.execute(call, context)

    first_payload = json.loads(first.content)
    second_payload = json.loads(second.content)
    assert first_payload == {
        "messageId": str(message.id),
        "offset": 0,
        "content": "a" * 12_000,
        "totalCharacters": 12_004,
        "toolName": "webfetch",
        "hasMore": True,
        "nextOffset": 12_000,
        "contentIsUntrusted": True,
    }
    assert second_payload == {
        "messageId": str(message.id),
        "offset": 12_000,
        "content": "rest",
        "totalCharacters": 12_004,
        "toolName": "webfetch",
        "hasMore": False,
        "nextOffset": None,
        "contentIsUntrusted": True,
    }
    context.ask_handler.assert_not_awaited()


@pytest.mark.parametrize("trust_level", [TrustLevel.PRIVATE, TrustLevel.PUBLIC])
async def test_session_tool_result_read_without_reason_survives_projection(
    tmp_path, trust_level,
) -> None:
    message = Message(
        id=uuid4(), session_id=uuid4(), role="tool", content="Persisted documentation",
        trust_level=trust_level.value,
    )
    reference = _tool_result_reference(message.id, message.content)
    call = ToolCall("read-result", reference["tool"], reference["arguments"])
    context = _context(tmp_path, call)
    context.assistant_message_id = uuid4()
    message.session_id = context.session.id
    _mock_tool_result_history(context, message, "webfetch")
    reader = Message(
        id=context.assistant_message_id, session_id=context.session.id,
        role="assistant", content="", trust_level=trust_level.value,
        tool_calls=[{"id": call.id, "name": call.name, "arguments": call.arguments}],
    )

    result = await tool_registry.execute(call, context)
    context.db.scalar.assert_awaited_once()
    page = Message(
        id=uuid4(), session_id=context.session.id, role="tool",
        tool_call_id=call.id, content=result.content, trust_level=trust_level.value,
    )
    projected = _projection_messages("System", None, [reader, page], trust_level)
    payload = json.loads(next(item["content"] for item in projected if item["role"] == "tool"))
    assert payload["messageId"] == str(message.id)
    assert payload["content"] == message.content


async def test_session_tool_result_read_exposes_attachment_references(tmp_path) -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="tool",
        content="Image generated.",
        trust_level=TrustLevel.PUBLIC.value,
    )
    attachment = MessageAttachment(
        id=uuid4(),
        message_id=message.id,
        position=0,
        filename="generated.png",
        kind="image",
        category="generated",
        purpose="artifact",
        processing_status="ready",
        mime_type="image/png",
        size_bytes=123,
        sha256="a" * 64,
        file_root_id=message.session_id,
        file_id=uuid4(),
        file_revision=1,
        file_path="generated.png",
    )
    message.attachments = [attachment]
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(message.id)},
    )
    context = _context(tmp_path, call)
    message.session_id = context.session.id
    attachment.file_root_id = context.session.id
    _mock_tool_result_history(context, message, "image_generate")

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content)["attachments"] == [
        {
            "id": str(attachment.id),
            "filename": "generated.png",
            "kind": "image",
            "mimeType": "image/png",
            "category": "generated",
            "processingStatus": "ready",
            "path": attachment.file_path,
            "fileId": str(attachment.file_id),
            "fileRevision": 1,
        }
    ]


async def test_session_tool_result_read_hides_unavailable_messages(tmp_path) -> None:
    message_id = uuid4()
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(message_id)},
    )
    context = _context(tmp_path, call)
    context.db.scalar.return_value = None

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "session.messageNotFound"}
    statement = str(context.db.scalar.await_args.args[0])
    assert "messages.id" in statement
    assert "messages.session_id" in statement
    assert "messages.role" in statement
    assert "messages.error_code IS NULL" in statement
    assert "messages.trust_level =" not in statement
    context.ask_handler.assert_not_awaited()


async def test_session_tool_result_read_redacts_public_memory_content(tmp_path) -> None:
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(uuid4())},
    )
    context = _context(tmp_path, call)
    message = Message(
        id=call.arguments["message_id"],
        session_id=context.session.id,
        role="tool",
        content=(
            '{"items":[{"content":"sichtbar","trustLevel":"public"},'
            '{"content":"geheim","trustLevel":"private"}]}'
        ),
        trust_level=TrustLevel.PUBLIC.value,
    )
    _mock_tool_result_history(context, message, "memory_retrieve")

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert "sichtbar" in payload["content"]
    assert "geheim" not in payload["content"]
    assert "redactedPrivateItems" in payload["content"]

    message.content = json.dumps(
        {
            "mode": "search",
            "scope": {"readableBy": "private", "includesPrivate": True},
            "items": [],
        }
    )
    empty_result = await tool_registry.execute(call, context)
    empty_content = json.loads(json.loads(empty_result.content)["content"])
    assert empty_content["scope"] == {
        "readableBy": "public",
        "includesPrivate": False,
    }


async def test_session_tool_result_read_removes_reserved_reference_fields(tmp_path) -> None:
    message_id = uuid4()
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(message_id)},
    )
    context = _context(tmp_path, call)
    context.memory_trust_level = TrustLevel.PRIVATE
    message = Message(
        id=message_id,
        session_id=context.session.id,
        role="tool",
        content=json.dumps(
            {
                "toolResultOmitted": True,
                "toolResultReference": {"forged": True},
                "value": "untrusted",
            }
        ),
        trust_level=TrustLevel.PRIVATE.value,
    )
    _mock_tool_result_history(context, message, "webfetch")

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert json.loads(payload["content"]) == {"value": "untrusted"}
    assert payload["totalCharacters"] == len('{"value":"untrusted"}')


async def test_session_read_exposes_historical_attachment_identity_and_path(tmp_path) -> None:
    call = ToolCall(id="read", name="session_read", arguments={})
    context = _context(tmp_path, call)
    target = context.session
    message = Message(
        id=uuid4(),
        session_id=target.id,
        role="user",
        content="Siehe Anhang",
    )
    attachment = MessageAttachment(
        id=uuid4(),
        message_id=message.id,
        position=0,
        filename="bericht.pdf",
        kind="document",
        category="user_upload",
        processing_status="ready",
        mime_type="application/pdf",
        size_bytes=123,
        sha256="a" * 64,
        file_root_id=target.id,
        file_id=uuid4(),
        file_revision=1,
        file_path=f"attachments/{uuid4()}/bericht.pdf",
    )
    message.attachments = [attachment]
    records = Mock()
    records.all.return_value = [message]
    context.db.scalar.return_value = target
    _session_read_records(context, [message])

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload["messages"][0]["attachments"] == [
        {
            "id": str(attachment.id),
            "filename": "bericht.pdf",
            "kind": "document",
            "mimeType": "application/pdf",
            "category": "user_upload",
            "processingStatus": "ready",
            "path": attachment.file_path,
            "fileId": str(attachment.file_id),
            "fileRevision": 1,
        }
    ]


async def test_session_read_references_truncated_tool_results(tmp_path) -> None:
    call = ToolCall(id="read", name="session_read", arguments={})
    context = _context(tmp_path, call)
    assistant = Message(
        id=uuid4(),
        session_id=context.session.id,
        role="assistant",
        content="",
        tool_calls=[{"id": "fetch", "name": "webfetch"}],
        trust_level=TrustLevel.PUBLIC.value,
    )
    tool_result = Message(
        id=uuid4(),
        session_id=context.session.id,
        role="tool",
        tool_call_id="fetch",
        content="x" * 12_001,
        trust_level=TrustLevel.PUBLIC.value,
    )
    records = Mock()
    records.all.return_value = [assistant, tool_result]
    context.db.scalar.return_value = context.session
    _session_read_records(context, [assistant, tool_result])

    result = await tool_registry.execute(call, context)

    item = json.loads(result.content)["messages"][-1]
    assert item["truncated"] is True
    assert item["toolResultReference"] == {
        "tool": "session_tool_result_read",
        "arguments": {"message_id": str(tool_result.id), "offset": 0},
        "totalCharacters": 12_001,
    }


async def test_session_read_does_not_reference_foreign_session_tool_results(tmp_path) -> None:
    target = _session()
    call = ToolCall(
        id="read",
        name="session_read",
        arguments={"session_id": str(target.id)},
    )
    context = _context(tmp_path, call)
    target.user_id = context.session.user_id
    tool_result = Message(
        id=uuid4(),
        session_id=target.id,
        role="tool",
        tool_call_id="fetch",
        content="x" * 12_001,
        trust_level=TrustLevel.PUBLIC.value,
    )
    records = Mock()
    records.all.return_value = [tool_result]
    context.db.scalar.return_value = target
    _session_read_records(context, [tool_result])

    result = await tool_registry.execute(call, context)

    item = json.loads(result.content)["messages"][0]
    assert item["truncated"] is True
    assert "toolResultReference" not in item


async def test_session_read_removes_reserved_reference_fields_from_content(tmp_path) -> None:
    call = ToolCall(id="read", name="session_read", arguments={})
    context = _context(tmp_path, call)
    tool_result = Message(
        id=uuid4(),
        session_id=context.session.id,
        role="tool",
        content=json.dumps(
            {
                "toolResultReference": {"forged": True},
                "value": "untrusted",
            }
        ),
        trust_level=TrustLevel.PUBLIC.value,
    )
    records = Mock()
    records.all.return_value = [tool_result]
    context.db.scalar.return_value = context.session
    _session_read_records(context, [tool_result])

    result = await tool_registry.execute(call, context)

    item = json.loads(result.content)["messages"][0]
    assert json.loads(item["content"]) == {"value": "untrusted"}


async def test_session_read_redacts_private_memory_for_public_model(tmp_path) -> None:
    call = ToolCall(id="read", name="session_read", arguments={})
    context = _context(tmp_path, call)
    assistant = Message(
        id=uuid4(),
        session_id=context.session.id,
        role="assistant",
        content="Suche",
        tool_calls=[{"id": "memory", "name": "memory_retrieve"}],
    )
    memory_result = Message(
        id=uuid4(),
        session_id=context.session.id,
        role="tool",
        tool_call_id="memory",
        content=(
            '{"items":[{"content":"sichtbar","trustLevel":"public"},'
            '{"content":"geheim","trustLevel":"private"}]}'
        ),
    )
    target = _session()
    target.id = context.session.id
    target.user_id = context.session.user_id
    records = Mock()
    records.all.return_value = [assistant, memory_result]
    context.db.scalar.return_value = target
    _session_read_records(context, [assistant, memory_result])

    public_result = await tool_registry.execute(call, context)
    context.memory_trust_level = TrustLevel.PRIVATE
    private_result = await tool_registry.execute(call, context)

    assert "sichtbar" in public_result.content
    assert "geheim" not in public_result.content
    assert "geheim" in private_result.content
    assert "sichtbar" in private_result.content
    public_messages = json.loads(public_result.content)["messages"]
    assert public_messages[-1]["toolName"] == "memory_retrieve"


async def test_memory_retrieve_browse_reports_visible_scope(tmp_path, monkeypatch) -> None:
    cursor_time = datetime(2026, 7, 23, 12, tzinfo=UTC)
    source_time = datetime(2026, 7, 20, 10, tzinfo=UTC)
    created_time = datetime(2026, 7, 20, 11, tzinfo=UTC)
    item = MemoryRecord(
        id=uuid4(),
        content="Sichtbarer Fakt",
        category="identity",
        pinned=True,
        audience="public_only",
        revision=2,
        created_at=created_time,
        updated_at=cursor_time,
    )
    item.source_message = Message(created_at=source_time)
    retrieve = AsyncMock(
        return_value=MemoryRetrieval(
            items=[item],
            mode=MemoryRetrieveMode.BROWSE,
            strategy=MemoryRetrievalStrategy.BROWSE,
            readable_by=TrustLevel.PUBLIC,
            offset=0,
            limit=10,
            has_more=True,
            next_cursor_updated_at=cursor_time,
            next_cursor_id=item.id,
        )
    )
    monkeypatch.setattr(MemoryRetrievalService, "retrieve", retrieve)
    call = ToolCall(id="memory", name="memory_retrieve", arguments={"mode": "browse"})
    context = _context(tmp_path, call)
    context.memory_read_enabled = True

    result = await tool_registry.execute(call, context)
    payload = json.loads(result.content)

    assert result.is_error is False
    assert payload["strategy"] == "browse"
    assert payload["purpose"] == "context"
    assert payload["scope"] == {
        "readableBy": "public",
        "includesPrivate": False,
    }
    assert payload["items"][0]["revision"] == 2
    assert payload["items"][0]["audience"] == "public_only"
    assert "pinKind" not in payload["items"][0]
    assert {
        key: payload["items"][0][key]
        for key in ("sourceMessageAt", "memoryCreatedAt", "memoryUpdatedAt")
    } == {
        "sourceMessageAt": source_time.isoformat(),
        "memoryCreatedAt": created_time.isoformat(),
        "memoryUpdatedAt": cursor_time.isoformat(),
    }
    assert payload["total"] is None
    assert payload["nextCursor"] == {
        "cursor_updated_at": cursor_time.isoformat(),
        "cursor_id": str(item.id),
    }
    retrieve.assert_awaited_once()
    assert retrieve.await_args.kwargs["owner_id"] == context.session.user_id
    assert "retrieval_level" not in retrieve.await_args.kwargs
    assert result.metadata["memoryWritePending"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        {"mode": "search"},
        {"mode": "browse", "query": "nicht erlaubt"},
        {"mode": "search", "query": "Fakt", "offset": 1},
        {"mode": "browse", "cursor_id": str(uuid4())},
    ],
)
async def test_memory_retrieve_rejects_mode_argument_mismatches(
    tmp_path, arguments
) -> None:
    call = ToolCall(id="memory", name="memory_retrieve", arguments=arguments)
    context = _context(tmp_path, call)
    context.memory_read_enabled = True

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "tool.invalidArguments"}


def test_memory_retrieve_schema_requires_explicit_mode() -> None:
    schema = tool_registry._definitions["memory_retrieve"].schema()

    assert "mode" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["purpose"]["enum"] == [
        "context",
        "write",
    ]
    assert "prepares a memory_write" in schema["parameters"]["properties"]["purpose"][
        "description"
    ]
    assert "browse visible memories" in schema["description"]
    assert "nextCursor" in schema["description"]


async def test_memory_write_reports_invalid_identifier_field(tmp_path) -> None:
    call = ToolCall(
        id="memory",
        name="memory_write",
        arguments={
            "action": "update",
            "memory_id": "not-a-uuid",
            "expected_revision": 1,
            "content": "Aktualisierter Fakt",
            "category": "identity",
            "pin": False,
        },
    )
    context = _context(tmp_path, call)
    context.memory_read_enabled = True
    context.memory_write_enabled = True

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "tool.invalidArguments"}
    assert json.loads(result.content) == {
        "error": {
            "code": "tool.invalidArguments",
            "message": (
                "The tool arguments are invalid. Inspect error.field and retry with arguments "
                "that match the tool schema."
            ),
            "field": "memory_id",
        }
    }


def test_memory_write_schema_keeps_update_arguments_explicit() -> None:
    schema = tool_registry._definitions["memory_write"].schema()

    assert "action" in schema["parameters"]["required"]
    assert schema["parameters"]["properties"]["action"]["enum"] == [
        "create",
        "update",
        "forget",
    ]
    assert "latest user message explicitly" in schema["description"]
    assert "Create without memory_id or expected_revision" in schema["description"]
    pin = schema["parameters"]["properties"]["pin"]
    assert pin["default"] is False
    assert "latest user explicitly" in pin["description"]
    assert "meal suggestions" in pin["description"]
    assert "False does not unpin" in pin["description"]


async def test_memory_write_forget_checks_visibility_revision_and_hard_deletes(
    monkeypatch, tmp_path
) -> None:
    memory_id = uuid4()
    item = SimpleNamespace(
        id=memory_id,
        user_id=uuid4(),
        audience="private_only",
        revision=4,
        content="Eine veraltete Erinnerung",
    )
    get_memory = AsyncMock(side_effect=[item, item])
    delete_memory = AsyncMock()
    monkeypatch.setattr(mock_tools_memory.MemoryService, "get", get_memory)
    monkeypatch.setattr(mock_tools_memory.MemoryService, "delete", delete_memory)
    call = ToolCall(
        id="forget",
        name="memory_write",
        arguments={
            "action": "forget",
            "memory_id": str(memory_id),
            "expected_revision": 4,
            "pin": False,
        },
    )
    context = _context(tmp_path, call)
    context.session.user_id = item.user_id
    context.assistant_message_id = uuid4()
    context.memory_trust_level = TrustLevel.PRIVATE
    arguments = MemoryWriteArguments.model_validate(call.arguments)

    result = await mock_tools_memory._memory_write(arguments, context)

    assert json.loads(result.content) == {"id": str(memory_id), "forgotten": True}
    assert result.metadata == {"memoryWriteOutcome": "forgotten"}
    assert get_memory.await_args_list[0].kwargs == {
        "owner_id": item.user_id,
        "memory_id": memory_id,
    }
    assert get_memory.await_args_list[1].kwargs == {
        "owner_id": item.user_id,
        "memory_id": memory_id,
        "for_update": True,
    }
    delete_memory.assert_awaited_once_with(
        context.db, item, owner_id=item.user_id
    )
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "memory.write"
    assert request.action_kind == "memory_write"
    assert request.display_items[0].value == item.content


@pytest.mark.parametrize(
    ("audience", "revision", "error_code"),
    [
        ("public_only", 1, "memory.notFound"),
        ("private_only", 2, "memory.changed"),
    ],
)
@pytest.mark.parametrize("action", ["forget", "update"])
async def test_memory_write_rejects_invisible_or_changed_target(
    monkeypatch, tmp_path, audience, revision, error_code, action
) -> None:
    item = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        audience=audience,
        revision=revision,
    )
    monkeypatch.setattr(mock_tools_memory.MemoryService, "get", AsyncMock(return_value=item))
    delete_memory = AsyncMock()
    monkeypatch.setattr(mock_tools_memory.MemoryService, "delete", delete_memory)
    call = ToolCall(
        id="forget",
        name="memory_write",
        arguments={
            "action": action,
            "memory_id": str(item.id),
            "expected_revision": 1,
            "content": "Neuer Inhalt" if action == "update" else None,
            "category": "context" if action == "update" else None,
            "pin": False,
        },
    )
    context = _context(tmp_path, call)
    context.session.user_id = item.user_id
    context.assistant_message_id = uuid4()
    context.memory_trust_level = TrustLevel.PRIVATE
    arguments = MemoryWriteArguments.model_validate(call.arguments)

    result = await mock_tools_memory._memory_write(arguments, context)

    assert result.metadata == {"errorCode": error_code}
    context.ask_handler.assert_not_awaited()
    delete_memory.assert_not_awaited()


def test_video_analysis_schema_uses_semantic_modes() -> None:
    definition = tool_registry._definitions["analyze_video"]
    schema = definition.schema()
    mode = schema["parameters"]["properties"]["analysis_mode"]

    assert mode["enum"] == ["speech", "overview", "fine_grained"]
    assert mode["default"] == "overview"
    assert "visual summaries" in mode["description"]
    assert "spoken content" in mode["description"]
    assert "focus" not in schema["parameters"]["required"]
    assert "Do not repeat a successful analysis" in schema["description"]
    assert "timeout does not indicate video length" in schema["description"].lower()
    legacy = definition.arguments_model.model_validate(
        {
            "root": "session",
            "path": "attachments/video.mp4",
            "detail": "detailed",
            "focus": "Reconstruct the setup steps",
        }
    )
    assert legacy.analysis_mode == "fine_grained"


async def test_workspace_video_uses_execution_client_bytes(tmp_path) -> None:
    call = ToolCall(
        id="video",
        name="analyze_video",
        arguments={"root": "computer", "path": "video.mp4"},
    )
    context = _context(tmp_path, call)
    context.session.current_directory = "/home/user/project"
    context.execution_client = SimpleNamespace(
        agent_id=uuid4(),
        current_directory="/home/user/project",
        read_bytes=AsyncMock(return_value=b"video"),
    )
    context.video_analysis_target = Mock(return_value="video-model")
    context.video_analysis_handler = AsyncMock(return_value={"summary": "ok"})
    context.tool_settings.permissions.policies["media.analyze.video"] = (
        ToolExecutionPolicy.AUTO
    )
    context.root_files.open_file = Mock(side_effect=AssertionError("must not read locally"))
    arguments = tool_registry._definitions["analyze_video"].validate_arguments(call.arguments)

    result = await mock_tools_media._analyze_video(arguments, context)

    assert not result.is_error
    context.execution_client.read_bytes.assert_awaited_once_with(
        "video.mp4", limit=mock_tools_media.MAX_VIDEO_BYTES
    )
    context.root_files.open_file.assert_not_called()


def test_model_facing_fields_preserve_search_scope_and_persona_text() -> None:
    web_search = tool_registry._definitions["web_search"].schema()
    identity = tool_registry._definitions["update_agent_identity"].schema()

    query = web_search["parameters"]["properties"]["query"]["description"]
    instructions = identity["parameters"]["properties"]["instructions"]["description"]
    assert "Preserve user-supplied dates" in query
    assert "versions, model numbers, and exact names" in query
    assert "never add a date" in query
    assert "Copy explicit user-supplied persona instructions exactly" in instructions


async def test_memory_write_requires_session_permission_before_mutation(
    monkeypatch, tmp_path
) -> None:
    create_memory = AsyncMock()
    monkeypatch.setattr(mock_tools_memory.MemoryService, "create_tool", create_memory)
    call = ToolCall(
        id="memory",
        name="memory_write",
        arguments={
            "_reason": "Die Erinnerung soll dauerhaft gespeichert werden.",
            "action": "create",
            "content": "Der Nutzer heißt Max.",
            "category": "identity",
            "pin": True,
        },
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    context.assistant_message_id = uuid4()
    context.memory_read_enabled = True
    context.memory_write_enabled = True

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "memory.write"
    assert request.resource_type == "session"
    assert request.resource_scope == str(context.session.id)
    assert request.action_kind == "memory_write"
    assert [(item.label, item.value) for item in request.display_items] == [
        ("Berücksichtigung", "Immer")
    ]
    create_memory.assert_not_awaited()


@pytest.mark.parametrize("name,arguments,operation", [
    ("write_file", {"root": "files", "path": "new.txt", "content": "new"}, "create"),
    ("edit_file", {"root": "files", "path": "a.txt", "oldText": "old", "newText": "new"}, "edit"),
    ("create_directory", {"root": "files", "path": "folder"}, "mkdir"),
    ("delete_path", {"root": "files", "path": "a.txt"}, "delete"),
    ("copy_path", {"source_root": "files", "source_path": "a.txt", "destination_root": "files", "destination_path": "b.txt"}, "copy"),
    ("move_path", {"source_root": "files", "source_path": "a.txt", "destination_root": "files", "destination_path": "b.txt"}, "move"),
])
async def test_file_operations_report_confirmed_actions(tmp_path, name, arguments, operation):
    call = ToolCall("mutation", name, arguments)
    context = _context(tmp_path, call)
    context.root_files.write_file("session", "a.txt", "old")
    result = await tool_registry.execute(call, context)
    assert [action.operation for action in result.file_actions] == [operation]


async def test_provide_file_does_not_open_after_denial(tmp_path) -> None:
    call = ToolCall(
        id="provide",
        name="provide_file",
        arguments={"root": "files", "path": "report.pdf"},
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    context.root_files.open_file = Mock(side_effect=AssertionError("must not open"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    context.root_files.open_file.assert_not_called()


async def test_provide_file_supports_session_files(tmp_path) -> None:
    call = ToolCall(
        id="provide",
        name="provide_file",
        arguments={"root": "files", "path": "report.pdf"},
    )
    context = _context(tmp_path, call)
    context.root_files.write_file("session", "report.pdf", "report")

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload["filename"] == "report.pdf"
    assert payload["root"] == "files"
    assert payload["sizeBytes"] == 6
    assert not {"revision", "downloadUrl", "fileId", "runId"} & payload.keys()
    assert payload["path"] == "report.pdf"
    assert not result.attachments
    assert result.file_actions[0].file_id is not None
    assert result.file_actions[0].file_revision == 1
    assert result.file_actions[0].path == "report.pdf"


async def test_provide_file_rejects_empty_managed_file(tmp_path) -> None:
    call = ToolCall(
        id="provide",
        name="provide_file",
        arguments={"root": "files", "path": "empty.txt"},
    )
    context = _context(tmp_path, call)
    context.root_files.write_file("session", "empty.txt", "")

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "attachment.empty"}


async def test_read_does_not_access_path_after_denial(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("secret", encoding="utf-8")
    call = ToolCall(
        id="read", name="read", arguments={"root": "session", "path": "notes.txt"}
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    context.root_files.path_kind = Mock(side_effect=AssertionError("must not inspect"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    context.root_files.path_kind.assert_not_called()
    context.db.scalar.assert_not_awaited()


async def test_public_model_can_select_same_project_session_files(tmp_path) -> None:
    target_id = uuid4()
    call = ToolCall(
        id="read-project-file",
        name="read",
        arguments={"root": "session", "path": "notes.txt", "session_id": str(target_id)},
    )
    context = _context(tmp_path, call)
    project_id = uuid4()
    context.session.project_id = project_id
    context.model_trust_level = TrustLevel.PUBLIC
    context.db.scalar.return_value = ChatSession(
        id=target_id,
        user_id=context.session.user_id,
        project_id=project_id,
        session_type="chat",
    )
    arguments = tool_registry._definitions["read"].validate_arguments(call.arguments)

    selected = await mock_files_access.project_read_files(arguments, context)

    assert selected is not None
    assert selected[1] == target_id
    statement = str(context.db.scalar.await_args.args[0])
    assert "chat_sessions.user_id" in statement
    assert "chat_sessions.project_id" in statement


async def test_read_exposes_managed_session_images(tmp_path) -> None:
    attachment_id = uuid4()
    call = ToolCall(
        id="read-image",
        name="read",
        arguments={
            "root": "session",
            "path": f"attachments/{attachment_id}/diagram.png",
        },
    )
    context = _context(tmp_path, call)
    context.root_files.create_directory(FileRoot.SESSION, "attachments")
    context.root_files.create_directory(
        FileRoot.SESSION, f"attachments/{attachment_id}"
    )
    file_path = f"attachments/{attachment_id}/diagram.png"
    context.root_files.create_file(
        FileRoot.SESSION, file_path, BytesIO(b"\x89PNG\r\n\x1a\n"), max_bytes=100
    )
    revision = context.root_files.list_revisions(file_path)[0]
    attachment = MessageAttachment(
        id=attachment_id,
        message_id=uuid4(),
        position=0,
        filename="diagram.png",
        kind="image",
        category="user_upload",
        processing_status="ready",
        mime_type="image/png",
        size_bytes=8,
        sha256="a" * 64,
        file_root_id=context.session.id,
        file_id=revision.file_id,
        file_revision=revision.revision,
        file_path=file_path,
    )
    context.db.scalar.return_value = attachment
    context.root_files.read_file = Mock(side_effect=AssertionError("must not decode image"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {}
    context.root_files.read_file.assert_not_called()
    context.db.scalar.assert_awaited_once()
    statement = str(context.db.scalar.await_args.args[0])
    assert "messages.trust_level" not in statement
    assert "message_attachments.file_path" not in statement.partition("WHERE")[2]


async def test_read_reports_invalid_encoding_for_managed_binary_file(tmp_path) -> None:
    attachment_id = uuid4()
    call = ToolCall(
        id="read-binary",
        name="read",
        arguments={
            "root": "session",
            "path": f"attachments/{attachment_id}/unknown.bin",
        },
    )
    context = _context(tmp_path, call)
    context.root_files.create_directory(FileRoot.SESSION, "attachments")
    context.root_files.create_directory(
        FileRoot.SESSION, f"attachments/{attachment_id}"
    )
    context.root_files.create_file(
        FileRoot.SESSION,
        f"attachments/{attachment_id}/unknown.bin",
        BytesIO(b"\xff"),
        max_bytes=100,
    )
    context.db.scalar.return_value = None

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "file.invalidEncoding"}


async def test_read_generated_session_image_without_attachment_record(tmp_path) -> None:
    image = BytesIO()
    Image.new("RGB", (2, 2), "white").save(image, format="PNG")
    call = ToolCall("image", "read", {"root": "session", "path": "scanrand_probe.png"})
    context = _context(tmp_path, call)
    context.root_files.create_file(
        FileRoot.SESSION, "scanrand_probe.png", BytesIO(image.getvalue()), max_bytes=1000,
    )
    context.db.scalar.return_value = None

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    assert result.attachments[0].ephemeral is True
    assert result.attachments[0].data == image.getvalue()
    assert json.loads(result.content)["contentIsUntrusted"] is True


def test_webfetch_schema_has_the_expected_contract() -> None:
    schema = next(
        item.schema()
        for item in tool_registry.available(
            IntegrationSettings(), webfetch_enabled=True
        )
        if item.name == "webfetch"
    )

    assert schema["name"] == "webfetch"
    assert schema["parameters"]["properties"]["url"]["maxLength"] == 1200
    assert schema["parameters"]["properties"]["format"]["default"] == "markdown"
    assert schema["parameters"]["properties"]["format"]["enum"] == ["markdown", "text", "html"]
    assert "verbatim text" in schema["description"]
    assert all(name in schema["description"] for name in ("JSON", "CSV", "XML", "NDJSON"))
    assert schema["parameters"]["properties"]["timeout"] == {
        "default": 30,
        "maximum": 120,
        "minimum": 1,
        "type": "integer",
    }


def test_fetch_transcript_schema_has_the_expected_contract() -> None:
    schema = next(
        item.schema()
        for item in tool_registry.available(
            IntegrationSettings(), webfetch_enabled=True
        )
        if item.name == "fetch_transcript"
    )

    assert set(schema["parameters"]["properties"]) == {
        "_reason",
        "url",
        "language",
    }
    assert schema["parameters"]["required"] == ["url"]
    assert schema["parameters"]["properties"]["language"]["default"] is None


def test_skill_write_schemas_delegate_compact_briefs() -> None:
    build_schema = next(
        item.schema()
        for item in tool_registry.available(IntegrationSettings())
        if item.name == "build_skill"
    )["parameters"]
    edit_schema = next(
        item.schema()
        for item in tool_registry.available(IntegrationSettings())
        if item.name == "edit_skill"
    )["parameters"]

    assert set(build_schema["properties"]) == {
        "_reason",
        "brief",
        "name_hint",
    }
    assert build_schema["required"] == ["brief"]
    assert set(edit_schema["properties"]) == {
        "_reason",
        "skill_id",
        "brief",
    }
    assert edit_schema["required"] == ["skill_id", "brief"]


def test_tool_schemas_remove_annotative_titles_recursively() -> None:
    schemas = [
        item.schema()
        for item in tool_registry.available(
            IntegrationSettings(),
            webfetch_enabled=True,
            memory_enabled=True,
            memory_write_enabled=True,
        )
    ]

    def contains_annotative_title(value) -> bool:
        if isinstance(value, dict):
            return isinstance(value.get("title"), str) or any(
                contains_annotative_title(item) for item in value.values()
            )
        if isinstance(value, list):
            return any(contains_annotative_title(item) for item in value)
        return False

    class TitledArguments(BaseModel):
        title: str

    titled_schema = ToolDefinition(
        "titled",
        "Use a title.",
        TitledArguments,
        AsyncMock(),
    ).schema()

    assert "title" in titled_schema["parameters"]["properties"]
    assert "title" in titled_schema["parameters"]["required"]
    assert not contains_annotative_title([*schemas, titled_schema])
    assert all(schema["parameters"]["additionalProperties"] is False for schema in schemas)


@pytest.mark.parametrize(
    ("policy", "public_addresses_only", "expected_asks"),
    [
        (ToolExecutionPolicy.ASK, True, 1),
        (ToolExecutionPolicy.AUTO, True, 0),
        (ToolExecutionPolicy.AUTO, False, 0),
    ],
)
async def test_webfetch_applies_the_effective_policy(
    tmp_path, monkeypatch, policy, public_addresses_only, expected_asks
) -> None:
    fetch_kwargs = {}

    async def fake_fetch(url, **kwargs):
        fetch_kwargs.update(kwargs)
        await kwargs["authorize_url"]("https://example.com/page?token=secret")
        return WebFetchResult(
            final_url=url,
            content_type="text/plain",
            content="Inhalt",
            status_code=200,
            output_format="markdown",
            title="  Example   page  ",
        )

    monkeypatch.setattr(mock_tools_web, 'fetch_web', fake_fetch)
    call = ToolCall(
        id="fetch",
        name="webfetch",
        arguments={"url": "https://example.com/page?token=secret"},
    )
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=policy,
        public_addresses_only=public_addresses_only,
    )
    context.tool_settings.web_fetch.user_agent = "ConfiguredBrowser/1.0"
    context.db.scalar.return_value.data = _settings_record(context.tool_settings)

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload == {
        "sourceId": payload["sourceId"],
        "url": "https://example.com/page?token=secret",
        "kind": "webfetch",
        "citationEligible": True,
        "title": "Example page",
        "statusCode": 200,
        "contentType": "text/plain",
        "outputFormat": "markdown",
        "content": "Inhalt",
        "attachments": [],
    }
    assert payload["sourceId"].startswith("src-")
    assert result.metadata == {
        "sources": [
            {
                "sourceId": payload["sourceId"],
                "url": "https://example.com/page?token=secret",
                "kind": "webfetch",
                "citationEligible": True,
                "title": "Example page",
            }
        ]
    }
    assert context.ask_handler.await_count == expected_asks
    assert fetch_kwargs["max_response_bytes"] == 64 * 1024 * 1024
    assert fetch_kwargs["pdf_vision_handler"] is None
    assert fetch_kwargs["pdf_pages"] == []
    assert fetch_kwargs["user_agent"] == "ConfiguredBrowser/1.0"
    assert fetch_kwargs["public_addresses_only"] is public_addresses_only
    if expected_asks:
        request = context.ask_handler.await_args.args[0]
        assert request.capability == "network.fetch"
        assert request.resource_type == "url"
        assert "https://example.com/page?token=secret" in request.summary


@pytest.mark.parametrize("path", [None, "manual.pdf"])
async def test_download_file_saves_original_pdf_without_computer(
    tmp_path, monkeypatch, path
) -> None:
    url = "https://example.com/manual.pdf"
    pdf = b"%PDF-1.4\n\x80\xff\n%%EOF\n"
    call = ToolCall("download", "download_file", {"url": url, **({"path": path} if path else {})})
    context = _context(tmp_path, call, web_fetch_policy=ToolExecutionPolicy.AUTO)
    context.root_files = RootFileService(
        context.session.id,
        run_id=context.run_id,
        session_storage=context.root_files.session_storage,
    )
    assert context.execution_client is None

    async def fake_download(_url, **kwargs):
        assert _url == url
        assert kwargs["max_response_bytes"] == 64 * 1024 * 1024
        await kwargs["authorize_url"](url)
        return DownloadedFile(url, "application/pdf", "manual.pdf", pdf)

    monkeypatch.setattr(tools_web, "download_file", fake_download)
    result = await tool_registry.execute(call, context)

    assert json.loads(result.content) == {"root": "files", "path": "manual.pdf", "sizeBytes": len(pdf)}
    assert context.root_files.read_bytes("session", "manual.pdf", limit=100) == pdf
    assert result.file_actions[0].path == "manual.pdf"
    assert result.file_actions[0].size_bytes == len(pdf)
    assert [call.args[0].capability for call in context.ask_handler.await_args_list] == [
        "session.files.write"
    ]

    repeated = await tool_registry.execute(call, context)
    assert repeated.metadata == {"errorCode": "file.pathAlreadyExists"}
    assert context.root_files.read_bytes("session", "manual.pdf", limit=100) == pdf
    revisions = context.root_files.finalize_run(context.run_id)
    assert len(revisions) == 1 and revisions[0].sha256 == hashlib.sha256(pdf).hexdigest()


async def test_download_file_denied_write_does_not_fetch(tmp_path, monkeypatch) -> None:
    call = ToolCall("download", "download_file", {
        "url": "https://example.com/manual.pdf", "path": "manual.pdf",
    })
    context = _context(tmp_path, call, decision=PermissionDecision.DENY,
                       web_fetch_policy=ToolExecutionPolicy.AUTO)
    fetch = AsyncMock(side_effect=AssertionError("Denied download must not fetch"))
    monkeypatch.setattr(tools_web, "download_file", fetch)

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    fetch.assert_not_awaited()
    with pytest.raises(RootFileError):
        context.root_files.open_file("session", "manual.pdf")


@pytest.mark.parametrize(
    ("policy", "expected_asks"),
    [(ToolExecutionPolicy.ASK, 1), (ToolExecutionPolicy.AUTO, 0)],
)
async def test_fetch_transcript_uses_one_video_approval_and_returns_evidence(
    tmp_path, monkeypatch, policy, expected_asks
) -> None:
    calls = []

    async def fake_fetch(url, **kwargs):
        calls.append((url, kwargs))
        await kwargs["before_request"]("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        return TranscriptResult(
            source_url="https://youtu.be/dQw4w9WgXcQ",
            title="Example video",
            language="de",
            language_fallback=False,
            track_type="manual",
            content="[00:00:01] Hallo",
        )

    monkeypatch.setattr(mock_tools_web, 'fetch_youtube_transcript', fake_fetch)
    call = ToolCall(
        id="transcript",
        name="fetch_transcript",
        arguments={
            "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12",
            "language": "de",
        },
    )
    context = _context(tmp_path, call, web_fetch_policy=policy)
    context.db.scalar.return_value.data = _settings_record(context.tool_settings)

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload == {
        "sourceId": payload["sourceId"],
        "url": "https://youtu.be/dQw4w9WgXcQ",
        "kind": "transcript",
        "citationEligible": True,
        "title": "Example video",
        "sourceType": "youtube",
        "sourceUrl": "https://youtu.be/dQw4w9WgXcQ",
        "language": "de",
        "languageFallback": False,
        "trackType": "manual",
        "outputFormat": "text",
        "content": "[00:00:01] Hallo",
    }
    assert result.metadata == {"sources": [{key: payload[key] for key in (
        "sourceId", "url", "kind", "citationEligible", "title"
    )}]}
    assert context.ask_handler.await_count == expected_asks
    if expected_asks:
        request = context.ask_handler.await_args.args[0]
        assert request.action_kind == "fetch_transcript"
        assert request.resource_display == "https://youtu.be/dQw4w9WgXcQ"
    assert calls[0][0] == "https://youtu.be/dQw4w9WgXcQ"


async def test_fetch_transcript_rechecks_availability_before_each_request(
    tmp_path, monkeypatch
) -> None:
    continued = False

    async def fake_fetch(url, **kwargs):
        nonlocal continued
        await kwargs["before_request"](url)
        continued = True
        raise AssertionError("Disabled transcript fetch must not continue")

    enabled = ToolSettings()
    disabled = ToolSettings(tool_availability={"webfetch": False})
    monkeypatch.setattr(mock_tools_web, 'fetch_youtube_transcript', fake_fetch)
    monkeypatch.setattr(mock_tools_access, 'profile_tool_settings', AsyncMock(side_effect=[enabled, disabled]))
    call = ToolCall(
        id="transcript",
        name="fetch_transcript",
        arguments={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )

    result = await tool_registry.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.unavailable"}
    assert continued is False


async def test_fetch_transcript_requests_approval_when_policy_becomes_ask(
    tmp_path, monkeypatch
) -> None:
    async def fake_fetch(url, **kwargs):
        await kwargs["before_request"](url)
        return TranscriptResult(
            source_url=url,
            title=None,
            language="de",
            language_fallback=False,
            track_type="manual",
            content="Hallo",
        )

    automatic = ToolSettings(
        private_permissions={"policies": {"network.fetch": "auto"}}
    )
    ask = ToolSettings(
        private_permissions={"policies": {"network.fetch": "ask"}}
    )
    monkeypatch.setattr(mock_tools_web, 'fetch_youtube_transcript', fake_fetch)
    monkeypatch.setattr(mock_tools_access, 'profile_tool_settings', AsyncMock(side_effect=[automatic, ask, ask]))
    call = ToolCall(
        id="transcript",
        name="fetch_transcript",
        arguments={"url": "https://youtu.be/dQw4w9WgXcQ"},
    )
    context = _context(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.is_error is False
    context.ask_handler.assert_awaited_once()


@pytest.mark.parametrize("tool_name,capability", [
    ("webfetch", "network.fetch"),
    ("customize_matoloop", "settings.personal.update"),
    ("load_skill", "skills.load"),
])
@pytest.mark.parametrize("ceiling_policy", [ToolExecutionPolicy.ASK, ToolExecutionPolicy.DISABLED])
async def test_effective_dispatch_preserves_ceiling_across_live_settings_reload(
    tmp_path, monkeypatch, tool_name, capability, ceiling_policy,
):
    from mato_loop_backend.agent.subagents import permission_snapshot

    skill = SkillDefinition(name="Review", description="Review", instructions="Review the text.")
    arguments = {
        "webfetch": {"url": "https://example.test"},
        "customize_matoloop": {"action": "update", "area": "memory", "changes": {"enabled": False}},
        "load_skill": {"skill_id": str(skill.id)},
    }[tool_name]
    call = ToolCall("child-call", tool_name, arguments)
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    context.session.parent_session_id = uuid4()
    context.skill_state.catalog[skill.id] = skill
    before = ToolSettings(private_permissions={"policies": {capability: ceiling_policy}})
    snapshot = permission_snapshot(before, {tool_name})
    live = ToolSettings(private_permissions={"policies": {capability: "auto"}})
    context.db.scalar.return_value.data = _settings_record(live)
    context.tool_settings = live.model_copy(deep=True)
    context.tool_settings._delegation_ceiling = snapshot
    network_reads = []

    async def fetch(url, **kwargs):
        await kwargs["authorize_url"](url)
        network_reads.append(url)
        return WebFetchResult(final_url=url, content_type="text/plain", content="Fetched",
                              status_code=200, output_format="text")

    monkeypatch.setattr(mock_tools_web, 'fetch_web', fetch)
    # Use a previously offered live toolset; dispatch must still retain the child's ceiling.
    effective = tool_registry.effective(IntegrationSettings(), webfetch_enabled=True, tool_settings=live)
    assert tool_name in effective.tools
    result = await effective.execute(call, context)
    assert result.is_error
    assert result.metadata["errorCode"] == (
        "permission.denied" if ceiling_policy == ToolExecutionPolicy.ASK else "tool.unavailable"
    )
    assert context.tool_settings._delegation_ceiling == snapshot
    assert network_reads == [] and context.skill_state.active is None
    if ceiling_policy == ToolExecutionPolicy.ASK:
        context.ask_handler.assert_awaited_once()
        assert context.ask_handler.await_args.args[0].capability == capability
    else:
        context.ask_handler.assert_not_awaited()
        offered = tool_registry.effective(
            IntegrationSettings(), webfetch_enabled=True, tool_settings=context.tool_settings
        ).tools
        assert (tool_name in offered) == (tool_name == "customize_matoloop")
    assert "delegation" not in str(context.tool_settings.model_dump(mode="json"))


async def test_webfetch_denial_stops_before_the_fetch_continues(tmp_path, monkeypatch) -> None:
    continued = False

    async def fake_fetch(url, **kwargs):
        nonlocal continued
        await kwargs["authorize_url"](url)
        continued = True
        raise AssertionError("The request must not continue after denial")

    monkeypatch.setattr(mock_tools_web, 'fetch_web', fake_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com"})
    context = _context(
        tmp_path,
        call,
        decision=PermissionDecision.DENY,
        web_fetch_policy=ToolExecutionPolicy.ASK,
    )

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    assert continued is False


async def test_webfetch_rejects_changed_bound_settings(tmp_path, monkeypatch) -> None:
    settings = ToolSettings()
    effective = tool_registry.effective(
        IntegrationSettings(),
        webfetch_enabled=True,
        tool_settings=settings,
    )
    disabled = ToolSettings(tool_availability={"webfetch": False})
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=disabled))
    monkeypatch.setattr(
        SettingsStore, "load_optional", AsyncMock(return_value=None)
    )
    fetch = AsyncMock(side_effect=AssertionError("Disabled webfetch must not run"))
    monkeypatch.setattr(mock_tools_web, 'fetch_web', fetch)
    call = ToolCall(
        id="fetch", name="webfetch", arguments={"url": "https://example.com"}
    )
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=ToolExecutionPolicy.AUTO,
    )

    result = await effective.execute(call, context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    fetch.assert_not_awaited()


@pytest.mark.parametrize(
    ("content_type", "content", "output_format"),
    [
        ("text/plain", "Inhalt", "markdown"),
        ("application/json", '{"title":"A & B <value>","items":[1,2]}\n', "text"),
    ],
)
async def test_webfetch_sources_preserve_distinct_query_values(
    tmp_path, monkeypatch, content_type, content, output_format,
) -> None:
    async def fake_fetch(url, **_kwargs):
        return WebFetchResult(
            final_url=url,
            content_type=content_type,
            content=content,
            status_code=200,
            output_format=output_format,
        )

    monkeypatch.setattr(mock_tools_web, 'fetch_web', fake_fetch)
    results = []
    for value in ("first", "second"):
        call = ToolCall(
            id=value,
            name="webfetch",
            arguments={"url": f"https://example.com/product?variant={value}"},
        )
        results.append(
            await tool_registry.execute(
                call,
                _context(
                    tmp_path,
                    call,
                    web_fetch_policy=ToolExecutionPolicy.AUTO,
                ),
            )
        )

    payloads = [json.loads(result.content) for result in results]
    assert payloads[0]["sourceId"] != payloads[1]["sourceId"]
    assert payloads[0]["citationEligible"] is True
    assert payloads[1]["citationEligible"] is True
    assert payloads[0]["url"] == "https://example.com/product?variant=first"
    assert payloads[1]["url"] == "https://example.com/product?variant=second"
    for payload in payloads:
        assert payload["content"] == content
        assert payload["contentType"] == content_type
        assert payload["outputFormat"] == output_format
        assert payload["attachments"] == []
        assert "notice" not in payload


@pytest.mark.parametrize("content", ["", " \t\r\n"])
async def test_webfetch_rejects_empty_content(
    tmp_path, monkeypatch, content,
) -> None:
    monkeypatch.setattr(mock_tools_web, 'fetch_web', AsyncMock(return_value=WebFetchResult(
        final_url="https://example.com/data", content_type="application/json",
        content=content, status_code=200, output_format="text",
    )))
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com/data"})

    result = await tool_registry.execute(
        call, _context(tmp_path, call, web_fetch_policy=ToolExecutionPolicy.AUTO),
    )

    assert result.metadata == {"errorCode": "webfetch.emptyResponse"}
    assert json.loads(result.content)["error"] == {
        "code": "webfetch.emptyResponse",
        "message": "The web server returned no usable content.",
        "params": {"statusCode": 200},
    }


async def test_webfetch_returns_images_as_tool_attachments(tmp_path, monkeypatch) -> None:
    image = b"\x89PNG\r\n\x1a\ncontent"

    async def fake_fetch(url, **kwargs):
        await kwargs["authorize_url"](url)
        return WebFetchResult(
            final_url=url,
            content_type="image/png",
            content="",
            status_code=200,
            output_format="image",
            images=(WebFetchImage(image, "image/png", "diagram.png"),),
        )

    monkeypatch.setattr(mock_tools_web, 'fetch_web', fake_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com/a"})
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=ToolExecutionPolicy.AUTO,
    )

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload["url"] == "https://example.com/a"
    assert payload["citationEligible"] is True
    assert payload["statusCode"] == 200
    assert payload["contentType"] == "image/png"
    assert payload["outputFormat"] == "image"
    assert payload["content"] == ""
    assert payload["attachments"] == [{"filename": "diagram.png"}]
    assert "notice" not in payload
    assert result.metadata["sources"][0]["sourceId"] == payload["sourceId"]
    assert result.attachments == (
        ToolAttachment(
            data=image,
            mime_type="image/png",
            filename="diagram.png",
            category="downloaded",
            presentation="internal",
        ),
    )


async def test_webfetch_returns_pdf_text_and_selected_page_images(
    tmp_path, monkeypatch
) -> None:
    images = (
        WebFetchImage(b"page-1", "image/jpeg", "drawing-page-1.jpg", 1),
        WebFetchImage(b"page-2", "image/jpeg", "drawing-page-2.jpg", 2),
    )
    fetch_kwargs = {}

    async def fake_fetch(url, **kwargs):
        fetch_kwargs.update(kwargs)
        return WebFetchResult(
            final_url=url,
            content_type="application/pdf",
            content="--- Page 1 ---\nDimensions",
            status_code=200,
            output_format="text",
            images=images,
            page_count=3,
            unavailable_pages=(4,),
        )

    monkeypatch.setattr(mock_tools_web, 'fetch_web', fake_fetch)
    call = ToolCall(
        id="fetch",
        name="webfetch",
        arguments={
            "url": "https://example.com/drawing.pdf",
            "pdf_pages": [2, 1, 2, 4],
        },
    )

    result = await tool_registry.execute(
        call,
        _context(tmp_path, call, web_fetch_policy=ToolExecutionPolicy.AUTO),
    )

    payload = json.loads(result.content)
    assert fetch_kwargs["pdf_pages"] == [1, 2, 4]
    assert payload["content"] == "--- Page 1 ---\nDimensions"
    assert payload["pageCount"] == 3
    assert payload["renderedPages"] == [1, 2]
    assert payload["unavailablePages"] == [4]
    assert payload["attachments"] == [
        {"filename": "drawing-page-1.jpg", "pageNumber": 1},
        {"filename": "drawing-page-2.jpg", "pageNumber": 2},
    ]
    assert result.attachments == tuple(
        ToolAttachment(
            image.data,
            image.mime_type,
            image.filename,
            category="downloaded",
            presentation="internal",
            metadata={"pageNumber": image.page_number},
        )
        for image in images
    )


@pytest.mark.parametrize(
    ("code", "params", "message"),
    [
        ("webfetch.requestFailed", {"statusCode": 404}, "The web server rejected the request."),
        ("webfetch.contentTypeUnsupported", {"statusCode": 200, "contentType": "application/zip"},
         "The web server sent an unsupported content type."),
    ],
)
async def test_webfetch_returns_stable_integration_errors(
    tmp_path, monkeypatch, code, params, message,
) -> None:
    async def failing_fetch(*args, **kwargs):
        raise WebFetchError(code, params=params)

    monkeypatch.setattr(mock_tools_web, 'fetch_web', failing_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com"})
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=ToolExecutionPolicy.AUTO,
    )

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": code}
    assert json.loads(result.content)["error"] == {
        "code": code, "params": params, "message": message,
    }
