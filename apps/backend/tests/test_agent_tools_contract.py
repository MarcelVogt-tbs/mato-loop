import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

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
from mato_loop_backend.memory.retrieval import (
    MemoryRetrieval,
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.models import ChatSession, MemoryRecord, Message
from mato_loop_backend.providers import ToolCall
from mato_loop_backend.settings import SearchSettings, TrustLevel, WebFetchPolicy
from mato_loop_backend.workspace import WorkspaceService


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
    return ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=_session(),
        tool_call=call,
        workspace=WorkspaceService(tmp_path),
        search_settings=SearchSettings(),
        ask_handler=AsyncMock(return_value=decision),
        web_fetch_policy=web_fetch_policy,
        allowed_private_origins=allowed_private_origins,
    )


def _context_with_workdir(
    tmp_path, call: ToolCall, *, decision=PermissionDecision.ALLOW
) -> ToolContext:
    context = _context(tmp_path, call, decision=decision)
    context.session.working_directory = "project"
    return context


def test_tool_registry_rejects_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate tool: example"):
        ToolRegistry([_definition(), _definition()])


def test_webfetch_schema_describes_verified_image_attachments() -> None:
    schemas = tool_registry.schemas(SearchSettings(), "read-only", webfetch_enabled=True)
    webfetch = next(item for item in schemas if item["name"] == "webfetch")

    assert "Successful images are validated and attached" in webfetch["description"]
    assert "verify exact image URLs" in webfetch["description"]


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
    assert json.loads(result.content) == {"error": expected_error}


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
        "error": {"code": "tool.invalidArguments", "field": "memory_id"}
    }


def test_memory_write_schema_describes_stable_self_context() -> None:
    schema = tool_registry._definitions["memory_write"].schema()

    assert "directly states stable self-context" in schema["description"]
    assert "transient, hypothetical, quoted, or third-party" in schema["description"]


async def test_read_file_returns_numbered_page_and_continuation(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
    call = ToolCall(
        id="read",
        name="read_file",
        arguments={"path": "notes.txt", "offset": 2, "limit": 1},
    )
    context = _context_with_workdir(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.content == "2: two\n\n[Weitere Zeilen verfügbar. Mit offset=3 weiterlesen.]"
    assert result.metadata == {
        "path": "project/notes.txt",
        "offset": 2,
        "limit": 1,
        "linesRead": 1,
        "hasMore": True,
        "nextOffset": 3,
        "truncatedLines": [],
    }


async def test_read_file_authorizes_canonical_symlink_target(tmp_path) -> None:
    project = tmp_path / "project"
    private = tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (private / "notes.txt").write_text("secret", encoding="utf-8")
    (project / "link").symlink_to(private, target_is_directory=True)
    call = ToolCall(id="read", name="read_file", arguments={"path": "link/notes.txt"})
    context = _context_with_workdir(tmp_path, call)

    result = await tool_registry.execute(call, context)

    assert result.content == "1: secret"
    request = context.ask_handler.await_args.args[0]
    assert request.resource_scope == "private/notes.txt"
    assert result.metadata["path"] == "private/notes.txt"


async def test_list_directory_authorizes_canonical_symlink_target(tmp_path) -> None:
    project = tmp_path / "project"
    private = tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (private / "notes.txt").write_text("secret", encoding="utf-8")
    (project / "link").symlink_to(private, target_is_directory=True)
    call = ToolCall(id="list", name="list_directory", arguments={"path": "link"})
    context = _context_with_workdir(tmp_path, call)

    result = await tool_registry.execute(call, context)

    request = context.ask_handler.await_args.args[0]
    assert request.resource_scope == "private"
    assert json.loads(result.content)[0]["path"] == "private/notes.txt"


async def test_read_file_does_not_read_after_denial(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("secret", encoding="utf-8")
    call = ToolCall(id="read", name="read_file", arguments={"path": "notes.txt"})
    context = _context(tmp_path, call, decision=PermissionDecision.DENY)
    context.workspace.read_file = Mock(side_effect=AssertionError("must not read"))

    result = await tool_registry.execute(call, context)

    assert result.metadata == {"errorCode": "permission.denied"}
    context.workspace.read_file.assert_not_called()


def test_webfetch_schema_has_the_expected_contract() -> None:
    schema = next(
        item.schema()
        for item in tool_registry.available(
            SearchSettings(enabled=False), "read-only", webfetch_enabled=True
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
        "title": "Timeout",
        "type": "integer",
    }


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

    assert result.content == "Inhalt"
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

    assert result.content == "Bild abgerufen: diagram.png"
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
    workspace = WorkspaceService(tmp_path)

    assert permission_for(
        "webfetch", {"url": "https://example.com"}, workspace, None
    ).resource_type == "url"
    assert permission_for("webfetch", {"url": "not a URL"}, workspace, None) is None
    assert permission_for("unknown", {}, workspace, None) is None
