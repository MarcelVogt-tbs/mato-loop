import hashlib
import json
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, Mock
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import zxingcpp
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.agent.tools as tools_module
import mato_loop_backend.integrations.registry as integration_registry
from mato_loop_backend.agent.permissions import PermissionDecision, PermissionRequest
from mato_loop_backend.agent.tools import (
    BoundTool,
    EffectiveToolSet,
    ToolAction,
    ToolAttachment,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    permission_for,
    tool_execution_policy,
    tool_registry,
)
from mato_loop_backend.attachments import AttachmentService
from mato_loop_backend.integrations import WebFetchError, WebFetchResult
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.memory.retrieval import (
    MemoryRetrieval,
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.models import ChatSession, MemoryRecord, Message, MessageAttachment
from mato_loop_backend.providers import ToolCall
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    AiSettings,
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    WebFetchPolicy,
)
from mato_loop_backend.skills import SkillDraft
from mato_loop_backend.workspace import RootFileService


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
    assert "configured default" in schema["description"]

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


def _definition(name: str = "example") -> ToolDefinition:
    return ToolDefinition(name, "Example tool", _RequiredArguments, _successful_handler)


def _session() -> ChatSession:
    return ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
        working_directory=None,
        workspace_access="read-only",
    )


def _context(
    tmp_path,
    call: ToolCall,
    *,
    decision=PermissionDecision.ALLOW,
    web_fetch_policy=WebFetchPolicy.DISABLED,
    allowed_private_origins=(),
) -> ToolContext:
    session = _session()
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    tool_settings = ToolSettings()
    tool_settings.web_fetch.allowed_private_origins = list(allowed_private_origins)
    context = ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=RootFileService(
            session.id,
            session_storage=storage,
            workspace_home=tmp_path,
            working_directory=session.working_directory,
        ),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=decision),
        tool_settings=tool_settings,
        web_fetch_policy=web_fetch_policy,
        allowed_private_origins=allowed_private_origins,
    )
    if web_fetch_policy != WebFetchPolicy.DISABLED:
        context.db.scalar.return_value = Mock(
            data=tool_settings.model_dump(mode="json")
        )
    return context


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
        storage_path=f"attachments/{attachment_id}/qr.png",
    )
    context.db.scalar.return_value = attachment
    read_image = AsyncMock(return_value=generated.attachments[0].data)
    monkeypatch.setattr(AttachmentService, "read_image", read_image)

    result = await tool_registry.execute(read_call, context)

    assert json.loads(result.content) == {"codes": [content]}
    assert result.trust_level == TrustLevel.PRIVATE
    read_image.assert_awaited_once_with(attachment, context.session.id)
    statement = context.db.scalar.await_args.args[0]
    assert context.session.id in statement.compile().params.values()


async def test_read_qr_code_rejects_unknown_session_attachment(tmp_path) -> None:
    read_call = ToolCall(
        id="qr-read",
        name="read_qr_code",
        arguments={"attachment_id": str(uuid4())},
    )
    context = _context(tmp_path, read_call)
    context.db.scalar.return_value = None

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
    monkeypatch.setattr(
        tools_module,
        "effective_integration_contributions",
        Mock(return_value=contributions),
    )

    effective = ToolRegistry([]).effective(IntegrationSettings(), "read-only")

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
    monkeypatch.setattr(
        tools_module,
        "effective_integration_contributions",
        Mock(return_value=IntegrationContributions(tools=(contribution,))),
    )
    effective = ToolRegistry([]).effective(
        IntegrationSettings(instances=[integration]), "read-only"
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
                "integration.example", "Integration verwenden", ToolExecutionPolicy.ASK
            ),
        ),
        integration_id=str(integration.id),
        authorization_factory=lambda _arguments, _context: (request,),
    )
    monkeypatch.setattr(
        tools_module,
        "effective_integration_contributions",
        Mock(return_value=IntegrationContributions(tools=(contribution,))),
    )
    effective = ToolRegistry([]).effective(
        IntegrationSettings(instances=[integration]), "read-only"
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
    effective = ToolRegistry([]).effective(settings, "read-only")
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
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
    effective = ToolRegistry([]).effective(settings, "read-only")

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
    effective = ToolRegistry([]).effective(settings, "read-only")
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
    action = ToolAction("tasks.read", "Aufgaben lesen", ToolExecutionPolicy.ASK)

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
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    effective = ToolRegistry([]).effective(settings, "read-only")
    call = ToolCall(id="aggregate", name="tasks_list_accounts", arguments={})
    context = _context(tmp_path, call)

    result = await effective.execute(call, context)

    assert json.loads(result.content) == {
        "ids": [str(first.id), str(second.id)]
    }
    request = context.ask_handler.await_args.args[0]
    assert request.policy_integration_id is None


async def test_private_integration_tool_requires_release_before_action(
    tmp_path, monkeypatch
) -> None:
    handler = AsyncMock(return_value=ToolResult.json({"ok": True}))
    _shared_adapter(monkeypatch, handler, releases_private_data=True)
    integration = _shared_integration("Private")
    settings = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    effective = ToolRegistry([]).effective(settings, "read-only")
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
    assert [request.capability for request in requests] == [
        "integration.private_data.release",
        "tasks.read",
    ]
    assert requests[0].available_decisions == ("allow_session", "deny")
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
            ToolAction("integration.read", "Lesen", ToolExecutionPolicy.ASK),
            ToolAction("integration.write", "Schreiben", ToolExecutionPolicy.ASK),
        ),
        integration_id=str(integration.id),
        authorization_factory=lambda _arguments, _context: (request,),
    )
    monkeypatch.setattr(
        tools_module,
        "effective_integration_contributions",
        Mock(return_value=IntegrationContributions(tools=(contribution,))),
    )
    settings = IntegrationSettings(instances=[integration])
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    effective = ToolRegistry([]).effective(settings, "read-only")
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
        "integration.resource.read", "Lesen", ToolExecutionPolicy.ASK
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
    monkeypatch.setattr(
        tools_module,
        "effective_integration_contributions",
        Mock(return_value=IntegrationContributions(tools=(contribution,))),
    )
    integration_settings = IntegrationSettings(instances=[integration])
    effective = ToolRegistry([]).effective(integration_settings, "read-only")
    settings = ToolSettings.model_validate(
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
            action.default_policy,
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
    schemas = tool_registry.schemas(IntegrationSettings(), "read-only", webfetch_enabled=True)
    webfetch = next(item for item in schemas if item["name"] == "webfetch")

    assert "Images are validated and attached" in webfetch["description"]
    assert "citationEligible" in webfetch["description"]
    assert "[title](URL)" in webfetch["description"]


def test_file_tool_schemas_require_explicit_roots_and_exact_paths() -> None:
    schemas = {
        item["name"]: item
        for item in tool_registry.schemas(IntegrationSettings(), "read-write")
    }

    for name in {
        "list_directory",
        "read_file",
        "provide_file",
        "write_file",
        "create_directory",
        "delete_path",
    }:
        parameters = schemas[name]["parameters"]
        assert "root" in parameters["required"]
        assert parameters["properties"]["root"]["enum"] == ["session", "workspace"]
        assert "path" in parameters["properties"]
    assert schemas["list_directory"]["parameters"]["properties"]["path"]["default"] == "."
    assert schemas["list_directory"]["parameters"]["properties"]["offset"]["default"] == 0
    assert schemas["list_directory"]["parameters"]["properties"]["limit"]["default"] == 100
    assert schemas["read_file"]["parameters"]["properties"]["offset"]["default"] == 1
    assert schemas["read_file"]["parameters"]["properties"]["limit"]["default"] == 2000
    assert schemas["delete_path"]["parameters"]["properties"]["recursive"]["default"] is False
    for name in {"copy_path", "move_path"}:
        assert set(schemas[name]["parameters"]["required"]) == {
            "source_root",
            "source_path",
            "destination_root",
            "destination_path",
        }
        assert "exact new destination path" in schemas[name]["description"]


async def test_read_only_mode_offers_only_read_file_tools_and_blocks_stale_mutations(
    tmp_path,
) -> None:
    file_tools = {
        "list_directory",
        "read_file",
        "provide_file",
        "write_file",
        "create_directory",
        "delete_path",
        "copy_path",
        "move_path",
    }
    offered = {
        item["name"] for item in tool_registry.schemas(IntegrationSettings(), "read-only")
    }
    assert offered & file_tools == {"list_directory", "read_file", "provide_file"}

    call = ToolCall(
        id="stale",
        name="write_file",
        arguments={"root": "session", "path": "files/note.txt", "content": "secret"},
    )
    context = _context(tmp_path, call)
    context.root_files.write_file = Mock(side_effect=AssertionError("must not write"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "tool.writeDisabled"}
    context.root_files.write_file.assert_not_called()


async def test_effective_toolset_rejects_a_call_that_was_not_offered(tmp_path) -> None:
    effective = tool_registry.effective(IntegrationSettings(), "read-only")
    call = ToolCall(
        id="not-offered",
        name="write_file",
        arguments={"root": "session", "path": "files/note.txt", "content": "secret"},
    )

    result = await effective.execute(call, _context(tmp_path, call))

    assert result.metadata == {"errorCode": "tool.unavailable"}


def test_inactive_capability_catalog_includes_installed_tools_without_schemas() -> None:
    integrations = IntegrationSettings()
    settings = ToolSettings()
    capabilities = tools_module.effective_tool_capabilities(settings)
    effective = tool_registry.effective(
        integrations,
        "read-only",
        tool_capabilities=capabilities,
        tool_settings=settings,
    )

    groups = tool_registry.inactive_capability_groups(
        integrations,
        frozenset(effective.tools),
        workspace_access="read-only",
        webfetch_enabled=False,
        web_search_enabled=True,
        memory_enabled=False,
        memory_write_enabled=False,
        tool_capabilities=capabilities,
        tool_settings=settings,
    )
    catalog = "\n".join(groups)

    assert "write_file" in catalog
    assert 'switch "Agent-Modus" from "Lesen" to "Schreiben"' in catalog
    assert "generate_image" in catalog
    assert "memory_retrieve" in catalog
    assert "webfetch" in catalog
    assert "calendar_create_event" in catalog
    assert '"Einstellungen > Integrationen"' in catalog
    assert "question" not in catalog
    assert "parameters" not in catalog


def test_policy_disabled_integration_tool_points_to_tool_settings(monkeypatch) -> None:
    _shared_adapter(monkeypatch, AsyncMock())
    integrations = IntegrationSettings(instances=[_shared_integration("Tasks")])
    settings = ToolSettings.model_validate(
        {"policies": {"tasks.read": "disabled"}}
    )
    effective = tool_registry.effective(
        integrations,
        "read-only",
        tool_settings=settings,
    )

    groups = tool_registry.inactive_capability_groups(
        integrations,
        frozenset(effective.tools),
        workspace_access="read-only",
        webfetch_enabled=False,
        web_search_enabled=True,
        memory_enabled=False,
        memory_write_enabled=False,
        tool_capabilities=frozenset(),
        tool_settings=settings,
    )
    optional = next(group for group in groups if group.startswith("Optional tools"))

    assert "tasks_read" in optional
    assert 'enable them under "Einstellungen > Tools"' in optional
    assert all(
        "tasks_read" not in group
        for group in groups
        if group.startswith("Integration tools")
    )


def test_global_policy_removes_tools_requiring_a_disabled_action() -> None:
    settings = ToolSettings.model_validate(
        {"policies": {"filesystem.delete": "disabled"}}
    )

    offered = {
        item["name"]
        for item in tool_registry.effective(
            IntegrationSettings(),
            "read-write",
            tool_settings=settings,
        ).schemas()
    }

    assert "delete_path" not in offered
    assert "move_path" not in offered
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
        settings, "read-only", tool_settings=current_tools
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
    monkeypatch.setattr(tools_module, "search_web", search)

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
    tool_settings = ToolSettings.model_validate(
        {"webSearch": {"primaryProvider": f"searxng:{integration.id}"}}
    )
    effective = tool_registry.effective(
        integrations, "read-only", tool_settings=tool_settings
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
    monkeypatch.setattr(tools_module, "search_web", search)
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
    monkeypatch.setattr(tools_module, "search_web", AsyncMock(return_value="not-json"))

    result = await tool_registry.execute(call, context)

    assert result.is_error is True
    assert result.metadata == {"errorCode": "integration.invalidResponse"}


async def test_missing_workspace_returns_structured_error_without_approval(tmp_path) -> None:
    call = ToolCall(
        id="read",
        name="read_file",
        arguments={"root": "workspace", "path": "notes.txt"},
    )
    context = _context(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "file.workspaceNotSelected"}
    context.ask_handler.assert_not_awaited()


async def test_update_agent_identity_uses_ask_policy(monkeypatch, tmp_path) -> None:
    tool_settings = ToolSettings.model_validate(
        {"updateAgentIdentity": {"policy": "ask"}}
    )
    identity = AgentIdentitySettings()

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            AiSettings: AiSettings(),
            AgentIdentitySettings: identity,
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

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content) == {
        "displayName": "Ada",
        "instructionsUpdated": True,
        "effectiveFrom": "next_run",
    }
    assert result.metadata["agentIdentity"]["displayName"] == "Ada"
    request = context.ask_handler.await_args.args[0]
    assert request.capability == "settings.agent_identity.update"
    assert request.resource_type == "agent_identity"
    assert request.resource_scope == str(context.session.user_id)
    save.assert_awaited_once()


async def test_update_agent_identity_auto_skips_approval(monkeypatch, tmp_path) -> None:
    tool_settings = ToolSettings.model_validate(
        {"updateAgentIdentity": {"policy": "auto"}}
    )

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            ToolSettings: tool_settings,
            AiSettings: AiSettings(),
            AgentIdentitySettings: AgentIdentitySettings(),
        }[settings_type]

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load_settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    call = ToolCall(
        id="identity-auto",
        name="update_agent_identity",
        arguments={"instructions": "Sei freundlich."},
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)

    result = await tool_registry.execute(call, context)

    assert not result.is_error
    context.ask_handler.assert_not_awaited()


async def test_build_skill_uses_user_scoped_approval_and_persistence(
    monkeypatch, tmp_path
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
    context.skill_creation_handler = AsyncMock(
        return_value=SkillDraft(
            name="API research",
            description="Use when an API must be queried.",
            instructions="Call the available API tool and validate its response.",
        )
    )

    async def approve_edited_draft(request):
        draft_file = request.approval_details["draftFile"]
        edited = json.dumps(
            {
                "name": "API checks",
                "description": "Use when an API response must be checked.",
                "instructions": "Call the available API tool and verify its response.",
            }
        )
        context.approval_resolution["expected_revision"] = (
            context.root_files.replace_text_file(
                "session",
                draft_file["path"],
                edited,
                draft_file["initialRevision"],
            )
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
    assert draft_file["path"].startswith("files/skill-draft-")
    draft_bytes = context.root_files.read_bytes(
        "session", draft_file["path"], limit=tools_module.SKILL_DRAFT_MAX_BYTES
    )
    assert context.approval_resolution["expected_revision"] == hashlib.sha256(
        draft_bytes
    ).hexdigest()
    assert draft_file["initialRevision"] != context.approval_resolution["expected_revision"]
    save.assert_awaited_once()


async def test_build_skill_rejects_a_draft_changed_after_approval(
    monkeypatch, tmp_path
) -> None:
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
    context.skill_creation_handler = AsyncMock(
        return_value=SkillDraft(
            name="API research",
            description="Use for API research.",
            instructions="Call the available API tool.",
        )
    )

    async def approve_then_change(request):
        draft = request.approval_details["draftFile"]
        context.approval_resolution["expected_revision"] = draft["initialRevision"]
        context.root_files.write_file(
            "session",
            draft["path"],
            '{"name":"Changed","description":"Changed","instructions":"Changed"}',
        )
        return PermissionDecision.ALLOW

    context.ask_handler = AsyncMock(side_effect=approve_then_change)

    result = await tool_registry.execute(call, context)

    assert result.is_error
    assert result.metadata["errorCode"] == "file.changed"
    assert skills.skills == []
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
                ToolSettings.model_validate({"policies": {"skills.load": "auto"}}),
                ToolSettings.model_validate({"policies": {"skills.load": "ask"}}),
            ]
        ),
    )
    call = ToolCall(
        id="load-transition",
        name="load_skill",
        arguments={"skill_id": str(skill.id)},
    )
    context = _context(tmp_path, call)
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
    settings = ToolSettings.model_validate(
        {"policies": {"skills.load": "ask"}}
    )
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    call = ToolCall(
        id="load-ask",
        name="load_skill",
        arguments={"skill_id": str(skill.id)},
    )
    context = _context(tmp_path, call)
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
    tool_settings = ToolSettings.model_validate(
        {"updateAgentIdentity": {"policy": "disabled"}}
    )
    capabilities = set()
    assert "update_agent_identity" not in {
        schema["name"]
        for schema in tool_registry.schemas(
            IntegrationSettings(), "read-only", tool_capabilities=capabilities
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
    result = await tool_registry.execute(call, _context(tmp_path, call))

    assert result.is_error
    assert result.metadata == {"errorCode": "tool.unavailable"}


@pytest.mark.parametrize(
    ("call", "expected_error"),
    [
        (
            ToolCall(id="unknown", name="missing", arguments={}),
            {"code": "tool.unknown"},
        ),
        (
            ToolCall(id="invalid", name="example", arguments={"unexpected": True}),
            {"code": "tool.invalidArguments", "field": "value"},
        ),
    ],
)
async def test_tool_registry_returns_stable_errors_for_unknown_tools_and_invalid_arguments(
    call: ToolCall, expected_error: dict[str, str]
) -> None:
    result = await ToolRegistry([_definition()]).execute(call, Mock())

    assert result.metadata == {"errorCode": expected_error["code"]}
    assert json.loads(result.content) == {
        "error": {
            **expected_error,
            "message": "The tool operation could not be completed.",
        }
    }


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


@pytest.mark.parametrize("target_id", [None, uuid4()])
async def test_session_read_uses_current_or_explicit_target_session(tmp_path, target_id) -> None:
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
        working_directory=None,
        workspace_access="read-only",
    )
    messages = Mock()
    messages.all.return_value = []
    context.db.scalar.return_value = target
    context.db.scalars.return_value = messages

    result = await tool_registry.execute(call, context)

    assert result.is_error is False
    assert json.loads(result.content)["session"] == {"id": str(expected_id), "title": None}
    request = context.ask_handler.await_args.args[0]
    assert request.resource_scope == str(expected_id)
    context.db.scalar.assert_awaited_once()
    context.db.scalars.assert_awaited_once()


@pytest.mark.parametrize(
    ("source_trust", "reader_trust", "expected_title"),
    [
        (TrustLevel.PUBLIC, TrustLevel.PUBLIC, "Ziel"),
        (TrustLevel.PRIVATE, TrustLevel.PUBLIC, None),
        (TrustLevel.PUBLIC, TrustLevel.PRIVATE, "Ziel"),
        (TrustLevel.PRIVATE, TrustLevel.PRIVATE, "Ziel"),
    ],
)
async def test_session_read_derives_title_visibility_from_first_user_message(
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
    context.db.scalars.return_value = messages

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content)["session"]["title"] == expected_title


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
    context.db.scalars.return_value = rows

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
    context.db.scalars.return_value = rows

    result = await tool_registry.execute(call, context)
    payload = json.loads(result.content)

    assert len(payload["messages"]) == 5
    assert payload["nextAfterMessageId"] == str(messages[4].id)


@pytest.mark.parametrize(
    ("name", "result_key"),
    [("session_browse", "sessions"), ("session_search", "results")],
)
async def test_session_discovery_returns_private_anchor_for_direct_read(
    tmp_path, name: str, result_key: str
) -> None:
    session = ChatSession(id=uuid4(), user_id=uuid4(), title="Projekt Aurora")
    message_id = uuid4()
    message_at = datetime(2026, 7, 20, 10, tzinfo=UTC)
    call = ToolCall(
        id="discover",
        name=name,
        arguments={} if name == "session_browse" else {"query": "Aurora"},
    )
    context = _context(tmp_path, call)
    context.memory_trust_level = TrustLevel.PRIVATE
    session.user_id = context.session.user_id
    rows = Mock()
    rows.all.return_value = [
        (
            session,
            message_id,
            "user",
            "Aurora bleibt unser Hauptprojekt.",
            message_at,
            TrustLevel.PRIVATE.value,
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
            TrustLevel.PUBLIC.value,
        )
    ]
    context.db.execute.return_value = rows

    result = await tool_registry.execute(call, context)
    item = json.loads(result.content)["sessions"][0]

    assert item["title"] == "Öffentlicher Titel"
    assert item["anchorMessage"]["preview"] == "Öffentlicher Ausschnitt"
    assert "messages.trust_level" in str(context.db.execute.await_args.args[0])


async def test_session_search_hides_private_derived_titles_from_public_models(
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
            TrustLevel.PRIVATE.value,
        )
    ]
    context.db.execute.return_value = rows

    result = await tool_registry.execute(call, context)
    item = json.loads(result.content)["results"][0]

    assert item["title"] is None
    statement = str(context.db.execute.await_args.args[0])
    assert "chat_sessions.title" in statement
    assert "messages.trust_level" in statement


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

    assert "required" not in browse["parameters"]
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


async def test_session_tool_result_read_returns_bounded_continuation_chunks(tmp_path) -> None:
    message = Message(
        id=uuid4(),
        session_id=uuid4(),
        role="tool",
        content="a" * 12_000 + "rest",
        trust_level=TrustLevel.PUBLIC.value,
    )
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(message.id)},
    )
    context = _context(tmp_path, call)
    message.session_id = context.session.id
    context.db.scalar.return_value = message

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
        "hasMore": True,
        "nextOffset": 12_000,
        "contentIsUntrusted": True,
    }
    assert second_payload == {
        "messageId": str(message.id),
        "offset": 12_000,
        "content": "rest",
        "totalCharacters": 12_004,
        "hasMore": False,
        "nextOffset": None,
        "contentIsUntrusted": True,
    }
    context.ask_handler.assert_not_awaited()


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
        processing_status="ready",
        mime_type="image/png",
        size_bytes=123,
        sha256="a" * 64,
        storage_path=f"generated/{uuid4()}/generated.png",
    )
    message.attachments = [attachment]
    call = ToolCall(
        id="read-result",
        name="session_tool_result_read",
        arguments={"message_id": str(message.id)},
    )
    context = _context(tmp_path, call)
    message.session_id = context.session.id
    context.db.scalar.return_value = message

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content)["attachments"] == [
        {
            "id": str(attachment.id),
            "filename": "generated.png",
            "kind": "image",
            "mimeType": "image/png",
            "category": "generated",
            "processingStatus": "ready",
            "path": attachment.storage_path,
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
    assert "messages.trust_level" in statement
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
    context.db.scalar.return_value = message

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
    context.db.scalar.return_value = message

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
        storage_path=f"attachments/{uuid4()}/bericht.pdf",
    )
    message.attachments = [attachment]
    records = Mock()
    records.all.return_value = [message]
    context.db.scalar.return_value = target
    context.db.scalars.return_value = records

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
            "path": attachment.storage_path,
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
    context.db.scalars.return_value = records

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
    context.db.scalars.return_value = records

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
    context.db.scalars.return_value = records

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
    context.db.scalars.return_value = records

    public_result = await tool_registry.execute(call, context)
    context.memory_trust_level = TrustLevel.PRIVATE
    private_result = await tool_registry.execute(call, context)

    assert "sichtbar" in public_result.content
    assert "geheim" not in public_result.content
    assert "geheim" in private_result.content
    public_messages = json.loads(public_result.content)["messages"]
    assert public_messages[-1]["toolName"] == "memory_retrieve"


async def test_memory_retrieve_browse_reports_visible_scope(tmp_path, monkeypatch) -> None:
    cursor_time = datetime(2026, 7, 23, 12, tzinfo=UTC)
    source_time = datetime(2026, 7, 20, 10, tzinfo=UTC)
    created_time = datetime(2026, 7, 20, 11, tzinfo=UTC)
    item = MemoryRecord(
        id=uuid4(),
        content="Sichtbarer Fakt",
        category="fact",
        pinned=False,
        trust_level="public",
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
    assert "browse all visible memories" in schema["description"]
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
            "message": "The tool operation could not be completed.",
            "field": "memory_id",
        }
    }


def test_memory_write_schema_keeps_update_arguments_explicit() -> None:
    schema = tool_registry._definitions["memory_write"].schema()

    assert "action" in schema["parameters"]["required"]
    assert "content" in schema["parameters"]["required"]
    assert "category" in schema["parameters"]["required"]
    assert "Create without memory_id or expected_revision" in schema["description"]


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
    monkeypatch.setattr(tools_module.MemoryService, "create_tool", create_memory)
    call = ToolCall(
        id="memory",
        name="memory_write",
        arguments={
            "action": "create",
            "content": "Der Nutzer heißt Max.",
            "category": "identity",
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
    create_memory.assert_not_awaited()


async def test_provide_file_does_not_open_after_denial(tmp_path) -> None:
    call = ToolCall(
        id="provide",
        name="provide_file",
        arguments={"root": "session", "path": "files/report.pdf"},
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
        arguments={"root": "session", "path": "files/report.pdf"},
    )
    context = _context(tmp_path, call)
    context.root_files.write_file("session", "files/report.pdf", "report")

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    query = parse_qs(urlparse(payload["downloadUrl"]).query)
    assert payload["filename"] == "report.pdf"
    assert payload["sizeBytes"] == 6
    assert query["scope"] == ["session"]
    assert query["root_identity"] == [str(context.session.id)]


async def test_read_file_does_not_read_after_denial(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("secret", encoding="utf-8")
    call = ToolCall(
        id="read", name="read_file", arguments={"root": "session", "path": "files/notes.txt"}
    )
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    context.root_files.read_file = Mock(side_effect=AssertionError("must not read"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    context.root_files.read_file.assert_not_called()


def test_webfetch_schema_has_the_expected_contract() -> None:
    schema = next(
        item.schema()
        for item in tool_registry.available(
            IntegrationSettings(), "read-only", webfetch_enabled=True
        )
        if item.name == "webfetch"
    )

    assert schema["name"] == "webfetch"
    assert schema["parameters"]["properties"]["url"]["maxLength"] == 1200
    assert schema["parameters"]["properties"]["format"]["default"] == "markdown"
    assert schema["parameters"]["properties"]["timeout"] == {
        "default": 30,
        "maximum": 120,
        "minimum": 1,
        "type": "integer",
    }


def test_build_skill_schema_delegates_only_a_compact_brief() -> None:
    schema = next(
        item.schema()
        for item in tool_registry.available(IntegrationSettings(), "read-only")
        if item.name == "build_skill"
    )["parameters"]

    assert set(schema["properties"]) == {"brief", "name_hint"}
    assert schema["required"] == ["brief"]


def test_tool_schemas_remove_annotative_titles_recursively() -> None:
    schemas = [
        item.schema()
        for item in tool_registry.available(
            IntegrationSettings(),
            "read-write",
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
    ("policy", "allowed_origins", "expected_asks"),
    [
        (WebFetchPolicy.ASK, (), 1),
        (WebFetchPolicy.AUTO_PUBLIC, (), 0),
        (WebFetchPolicy.AUTO_PUBLIC, ("https://example.com",), 1),
        (WebFetchPolicy.AUTO_ALLOWED, ("https://example.com",), 0),
    ],
)
async def test_webfetch_applies_the_effective_policy(
    tmp_path, monkeypatch, policy, allowed_origins, expected_asks
) -> None:
    fetch_kwargs = {}

    async def fake_fetch(url, **kwargs):
        fetch_kwargs.update(kwargs)
        await kwargs["authorize_url"]("https://example.com/page?token=secret")
        return WebFetchResult(
            final_url=url,
            content_type="text/plain",
            content="Inhalt",
            is_image=False,
            status_code=200,
            output_format="markdown",
            title="  Example   page  ",
        )

    monkeypatch.setattr("mato_loop_backend.agent.tools.fetch_web", fake_fetch)
    call = ToolCall(
        id="fetch",
        name="webfetch",
        arguments={"url": "https://example.com/page?token=secret"},
    )
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=policy,
        allowed_private_origins=allowed_origins,
    )
    context.tool_settings.web_fetch.user_agent = "ConfiguredBrowser/1.0"
    context.db.scalar.return_value.data = context.tool_settings.model_dump(mode="json")

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload == {
        "sourceId": payload["sourceId"],
        "url": "https://example.com/page?REDACTED",
        "kind": "webfetch",
        "citationEligible": False,
        "title": "Example page",
        "statusCode": 200,
        "contentType": "text/plain",
        "outputFormat": "markdown",
        "content": "Inhalt",
    }
    assert payload["sourceId"].startswith("src-")
    assert result.metadata == {
        "sources": [
            {
                "sourceId": payload["sourceId"],
                "url": "https://example.com/page?REDACTED",
                "kind": "webfetch",
                "citationEligible": False,
                "title": "Example page",
            }
        ]
    }
    assert context.ask_handler.await_count == expected_asks
    assert fetch_kwargs["max_response_bytes"] == 64 * 1024 * 1024
    assert fetch_kwargs["pdf_vision_handler"] is None
    assert fetch_kwargs["user_agent"] == "ConfiguredBrowser/1.0"
    if expected_asks:
        request = context.ask_handler.await_args.args[0]
        assert request.capability == "network.fetch"
        assert request.resource_type == "url"
        assert "secret" not in request.summary


async def test_webfetch_denial_stops_before_the_fetch_continues(tmp_path, monkeypatch) -> None:
    continued = False

    async def fake_fetch(url, **kwargs):
        nonlocal continued
        await kwargs["authorize_url"](url)
        continued = True
        raise AssertionError("The request must not continue after denial")

    monkeypatch.setattr("mato_loop_backend.agent.tools.fetch_web", fake_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com"})
    context = _context(
        tmp_path,
        call,
        decision=PermissionDecision.DENY,
        web_fetch_policy=WebFetchPolicy.ASK,
    )

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    assert continued is False


async def test_webfetch_rejects_changed_bound_settings(tmp_path, monkeypatch) -> None:
    settings = ToolSettings()
    effective = tool_registry.effective(
        IntegrationSettings(),
        "read-only",
        webfetch_enabled=True,
        tool_settings=settings,
    )
    disabled = ToolSettings.model_validate({"webFetch": {"enabled": False}})
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=disabled))
    fetch = AsyncMock(side_effect=AssertionError("Disabled webfetch must not run"))
    monkeypatch.setattr(tools_module, "fetch_web", fetch)
    call = ToolCall(
        id="fetch", name="webfetch", arguments={"url": "https://example.com"}
    )
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=WebFetchPolicy.AUTO_PUBLIC,
    )

    result = await effective.execute(call, context)

    assert result.metadata == {"errorCode": "tool.unavailable"}
    fetch.assert_not_awaited()


async def test_webfetch_source_ids_distinguish_redacted_query_values(
    tmp_path, monkeypatch
) -> None:
    async def fake_fetch(url, **_kwargs):
        return WebFetchResult(
            final_url=url,
            content_type="text/plain",
            content="Inhalt",
            is_image=False,
            status_code=200,
            output_format="markdown",
        )

    monkeypatch.setattr("mato_loop_backend.agent.tools.fetch_web", fake_fetch)
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
                    web_fetch_policy=WebFetchPolicy.AUTO_PUBLIC,
                ),
            )
        )

    payloads = [json.loads(result.content) for result in results]
    assert payloads[0]["sourceId"] != payloads[1]["sourceId"]
    assert payloads[0]["citationEligible"] is False
    assert payloads[1]["citationEligible"] is False
    assert payloads[0]["url"] == payloads[1]["url"] == (
        "https://example.com/product?REDACTED"
    )


async def test_webfetch_returns_images_as_tool_attachments(tmp_path, monkeypatch) -> None:
    image = b"\x89PNG\r\n\x1a\ncontent"

    async def fake_fetch(url, **kwargs):
        await kwargs["authorize_url"](url)
        return WebFetchResult(
            final_url=url,
            content_type="image/png",
            content=image,
            is_image=True,
            status_code=200,
            output_format="image",
            image_mime_type="image/png",
            image_filename="diagram.png",
        )

    monkeypatch.setattr("mato_loop_backend.agent.tools.fetch_web", fake_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com/a"})
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=WebFetchPolicy.AUTO_PUBLIC,
    )

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload["url"] == "https://example.com/a"
    assert payload["citationEligible"] is True
    assert payload["statusCode"] == 200
    assert payload["contentType"] == "image/png"
    assert payload["outputFormat"] == "image"
    assert payload["attachment"] == "diagram.png"
    assert result.metadata["sources"][0]["sourceId"] == payload["sourceId"]
    assert result.attachments == (
        ToolAttachment(data=image, mime_type="image/png", filename="diagram.png"),
    )


async def test_webfetch_returns_stable_integration_errors(tmp_path, monkeypatch) -> None:
    async def failing_fetch(*args, **kwargs):
        raise WebFetchError("webfetch.requestFailed", params={"statusCode": 404})

    monkeypatch.setattr("mato_loop_backend.agent.tools.fetch_web", failing_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com"})
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=WebFetchPolicy.AUTO_PUBLIC,
    )

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "webfetch.requestFailed"}
    assert json.loads(result.content)["error"]["params"] == {"statusCode": 404}


def test_permission_for_is_safe_for_webfetch_and_unknown_tools(tmp_path) -> None:
    call = ToolCall(id="call", name="read_file", arguments={})
    root_files = _context(tmp_path, call).root_files

    assert permission_for(
        "webfetch", {"url": "https://example.com"}, root_files
    ).resource_type == "url"
    assert permission_for("webfetch", {"url": "not a URL"}, root_files) is None
    assert permission_for("unknown", {}, root_files) is None
