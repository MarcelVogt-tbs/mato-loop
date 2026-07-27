import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.agent.tools as tools_module
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.tools import (
    ToolAttachment,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    permission_for,
    tool_registry,
)
from mato_loop_backend.integrations import WebFetchError, WebFetchResult
from mato_loop_backend.integrations.contracts import (
    IntegrationContributions,
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
    ToolSettings,
    TrustLevel,
    WebFetchPolicy,
)
from mato_loop_backend.workspace import RootFileService


class _RequiredArguments(BaseModel):
    value: str


async def _successful_handler(_arguments, _context) -> ToolResult:
    return ToolResult.json({"ok": True})


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
    return ToolContext(
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
        web_fetch_policy=web_fetch_policy,
        allowed_private_origins=allowed_private_origins,
    )


def _context_with_workdir(
    tmp_path, call: ToolCall, *, decision=PermissionDecision.ALLOW
) -> ToolContext:
    context = _context(tmp_path, call, decision=decision)
    context.session.working_directory = "project"
    context.root_files.working_directory = "project"
    return context


def test_tool_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate tool: example"):
        ToolRegistry([_definition(), _definition()])


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


def test_webfetch_schema_describes_verified_image_attachments() -> None:
    schemas = tool_registry.schemas(IntegrationSettings(), "read-only", webfetch_enabled=True)
    webfetch = next(item for item in schemas if item["name"] == "webfetch")

    assert "Successful images are validated and attached" in webfetch["description"]
    assert "whether the URL is citationEligible" in webfetch["description"]


def test_file_tool_schemas_require_explicit_roots_and_exact_paths() -> None:
    schemas = {
        item["name"]: item
        for item in tool_registry.schemas(IntegrationSettings(), "read-write")
    }

    for name in {
        "list_directory",
        "read_file",
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
        assert "exact new path" in schemas[name]["description"]


async def test_read_only_mode_offers_only_read_file_tools_and_blocks_stale_mutations(
    tmp_path,
) -> None:
    file_tools = {
        "list_directory",
        "read_file",
        "write_file",
        "create_directory",
        "delete_path",
        "copy_path",
        "move_path",
    }
    offered = {
        item["name"] for item in tool_registry.schemas(IntegrationSettings(), "read-only")
    }
    assert offered & file_tools == {"list_directory", "read_file"}

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


async def test_web_search_normalizes_a_removed_provider_to_no_key_search(
    tmp_path, monkeypatch
) -> None:
    integration = IntegrationInstance(
        name="Intern",
        integration_type="search",
        adapter_type="searxng",
        config={"base_url": "https://search.example.com"},
    )
    settings = IntegrationSettings(instances=[integration])
    effective = tool_registry.effective(settings, "read-only")
    call = ToolCall(
        id="stale-search",
        name="web_search",
        arguments={"query": "MatoLoop"},
    )
    context = _context(tmp_path, call)
    context.integration_settings = settings
    current_tools = ToolSettings.model_validate(
        {"webSearch": {"primaryProvider": f"searxng:{integration.id}"}}
    )

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
        "provider": "no_key",
        "fallback_provider": None,
    }


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


async def test_copy_and_move_handlers_check_both_roots_before_effect(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    copy_call = ToolCall(
        id="copy",
        name="copy_path",
        arguments={
            "source_root": "session",
            "source_path": "generated/result.txt",
            "destination_root": "workspace",
            "destination_path": "result.txt",
        },
    )
    context = _context_with_workdir(tmp_path, copy_call)
    context.session.workspace_access = "read-write"
    session_root = context.root_files.session_storage.session_path(context.session.id)
    (session_root / "generated" / "result.txt").write_text(
        "Ergebnis", encoding="utf-8"
    )

    copied = await tool_registry.execute(copy_call, context)

    assert json.loads(copied.content) == {"root": "workspace", "copied": "result.txt"}
    assert (project / "result.txt").read_text(encoding="utf-8") == "Ergebnis"
    assert context.ask_handler.await_count == 2

    move_call = ToolCall(
        id="move",
        name="move_path",
        arguments={
            "source_root": "workspace",
            "source_path": "result.txt",
            "destination_root": "session",
            "destination_path": "files/result.txt",
        },
    )
    context.tool_call = move_call
    moved = await tool_registry.execute(move_call, context)

    assert json.loads(moved.content) == {"root": "session", "moved": "files/result.txt"}
    assert not (project / "result.txt").exists()
    assert (session_root / "files" / "result.txt").read_text(encoding="utf-8") == "Ergebnis"


async def test_copy_denied_destination_has_no_partial_effect(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    call = ToolCall(
        id="copy",
        name="copy_path",
        arguments={
            "source_root": "session",
            "source_path": "files/source.txt",
            "destination_root": "workspace",
            "destination_path": "destination.txt",
        },
    )
    context = _context_with_workdir(tmp_path, call)
    context.session.workspace_access = "read-write"
    context.ask_handler.side_effect = [PermissionDecision.ALLOW, PermissionDecision.DENY]
    session_root = context.root_files.session_storage.session_path(context.session.id)
    (session_root / "files" / "source.txt").write_text("Quelle", encoding="utf-8")

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    assert not (project / "destination.txt").exists()


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
    assert json.loads(result.content)["session"] == {"id": str(expected_id), "title": "Ziel"}
    request = context.ask_handler.await_args.args[0]
    assert request.resource_scope == str(expected_id)
    context.db.scalar.assert_awaited_once()
    context.db.scalars.assert_awaited_once()


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
    item = MemoryRecord(
        id=uuid4(),
        content="Sichtbarer Fakt",
        category="fact",
        retrieval_level="retrieve",
        trust_level="public",
        revision=2,
    )
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
        "retrievalLevel": "all",
    }
    assert payload["items"][0]["revision"] == 2
    assert payload["total"] is None
    assert payload["nextCursor"] == {
        "cursor_updated_at": cursor_time.isoformat(),
        "cursor_id": str(item.id),
    }
    retrieve.assert_awaited_once()
    assert retrieve.await_args.kwargs["retrieval_level"] is None
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
    assert "purpose=write" in schema["description"]
    assert "browse all visible memories" in schema["description"]
    assert "session_search" in schema["description"]


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


def test_memory_write_schema_describes_stable_self_context() -> None:
    schema = tool_registry._definitions["memory_write"].schema()

    assert "implicitly supplies stable self-context" in schema["description"]
    assert "persist it in the same turn" in schema["description"]
    assert "transient, hypothetical, quoted, or third-party" in schema["description"]
    assert "never enrich it with inferred geography" in schema["description"]


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


async def test_read_file_returns_numbered_page_and_continuation(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    call = ToolCall(
        id="read",
        name="read_file",
        arguments={"root": "workspace", "path": "notes.txt", "offset": 2, "limit": 1},
    )
    context = _context_with_workdir(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.content == "2: two\n\n[Weitere Zeilen verfügbar. Mit offset=3 weiterlesen.]"
    assert result.metadata == {
        "root": "workspace",
        "path": "notes.txt",
        "offset": 2,
        "limit": 1,
        "linesRead": 1,
        "hasMore": True,
        "nextOffset": 3,
        "truncatedLines": [],
    }


async def test_read_file_rejects_workspace_symlinks(tmp_path) -> None:
    project = tmp_path / "project"
    private = tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (private / "notes.txt").write_text("secret", encoding="utf-8")
    (project / "link").symlink_to(private, target_is_directory=True)
    call = ToolCall(
        id="read",
        name="read_file",
        arguments={"root": "workspace", "path": "link/notes.txt"},
    )
    context = _context_with_workdir(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "file.symlinkForbidden"}
    request = context.ask_handler.await_args.args[0]
    assert request.resource_display == "workspace:link/notes.txt"


async def test_list_directory_rejects_workspace_symlinks(tmp_path) -> None:
    project = tmp_path / "project"
    private = tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (private / "notes.txt").write_text("secret", encoding="utf-8")
    (project / "link").symlink_to(private, target_is_directory=True)
    call = ToolCall(
        id="list",
        name="list_directory",
        arguments={"root": "workspace", "path": "link"},
    )
    context = _context_with_workdir(tmp_path, call)

    result = await tool_registry.execute(call, context)

    request = context.ask_handler.await_args.args[0]
    assert request.resource_display == "workspace:link"
    assert result.metadata == {"errorCode": "file.symlinkForbidden"}


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

    def contains_title(value) -> bool:
        if isinstance(value, dict):
            return "title" in value or any(contains_title(item) for item in value.values())
        if isinstance(value, list):
            return any(contains_title(item) for item in value)
        return False

    assert not contains_title(schemas)
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
    async def fake_fetch(url, **kwargs):
        await kwargs["authorize_url"]("https://example.com/page?token=secret")
        return WebFetchResult(
            final_url=url,
            content_type="text/plain",
            content="Inhalt",
            is_image=False,
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

    result = await tool_registry.execute(call, context)

    payload = json.loads(result.content)
    assert payload == {
        "sourceId": payload["sourceId"],
        "url": "https://example.com/page?REDACTED",
        "kind": "webfetch",
        "citationEligible": False,
        "title": "Example page",
        "contentType": "text/plain",
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
        raise AssertionError("Der Request darf nach Deny nicht fortgesetzt werden")

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


async def test_webfetch_source_ids_distinguish_redacted_query_values(
    tmp_path, monkeypatch
) -> None:
    async def fake_fetch(url, **_kwargs):
        return WebFetchResult(
            final_url=url,
            content_type="text/plain",
            content="Inhalt",
            is_image=False,
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
    assert payload["contentType"] == "image/png"
    assert payload["attachment"] == "diagram.png"
    assert result.metadata["sources"][0]["sourceId"] == payload["sourceId"]
    assert result.attachments == (
        ToolAttachment(data=image, mime_type="image/png", filename="diagram.png"),
    )


async def test_webfetch_returns_stable_integration_errors(tmp_path, monkeypatch) -> None:
    async def failing_fetch(*args, **kwargs):
        raise WebFetchError("webfetch.timeout")

    monkeypatch.setattr("mato_loop_backend.agent.tools.fetch_web", failing_fetch)
    call = ToolCall(id="fetch", name="webfetch", arguments={"url": "https://example.com"})
    context = _context(
        tmp_path,
        call,
        web_fetch_policy=WebFetchPolicy.AUTO_PUBLIC,
    )

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "webfetch.timeout"}


def test_permission_for_is_safe_for_webfetch_and_unknown_tools(tmp_path) -> None:
    call = ToolCall(id="call", name="read_file", arguments={})
    root_files = _context(tmp_path, call).root_files

    assert permission_for(
        "webfetch", {"url": "https://example.com"}, root_files
    ).resource_type == "url"
    assert permission_for("webfetch", {"url": "not a URL"}, root_files) is None
    assert permission_for("unknown", {}, root_files) is None
