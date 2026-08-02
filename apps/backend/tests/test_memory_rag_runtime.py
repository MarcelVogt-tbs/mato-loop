import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.context import ContextManager, ContextProjection
from mato_loop_backend.agent.runtime import (
    AUTOMATIC_MEMORY_MAX_DISTANCE,
    AUTOMATIC_MEMORY_MAX_ITEMS,
    MEMORY_OVERVIEW_MAX_CHARACTERS,
    AgentRuntime,
    _bounded_memory_overview,
    _memory_overview_requested,
)
from mato_loop_backend.agent.tools import EffectiveToolSet, ToolResult
from mato_loop_backend.events import run_events
from mato_loop_backend.memory.retrieval import (
    MemoryRetrieval,
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.memory.service import MemoryScope, MemoryService
from mato_loop_backend.models import AgentRun, ChatSession, MemoryRecord, Message
from mato_loop_backend.providers.base import ProviderResult, ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    AgentRuntimeSettings,
    AiSettings,
    IntegrationSettings,
    MemorySettings,
    SettingsStore,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


async def _runtime_setup(
    monkeypatch,
    results: list[ProviderResult],
    *,
    records: list[Message],
    automatic_items: list[MemoryRecord] | None = None,
):
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        trust_level=TrustLevel.PRIVATE,
        config={"base_url": "https://llm.example.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="local-model")
    ai_settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    runtime_settings = AgentRuntimeSettings(max_steps=10, max_model_requests=10)
    session = ChatSession(
        id=records[-1].session_id,
        user_id=uuid4(),
        title="Memory",
        model_config_id=model.id,
        memory_mode="full",
        web_search_policy="available",
        workspace_access="read-only",
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        model_config_id=model.id,
        trust_level="private",
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            AiSettings: ai_settings,
            AgentIdentitySettings: AgentIdentitySettings(),
            AgentRuntimeSettings: runtime_settings,
            MemorySettings: MemorySettings(
                enabled=True, max_context_items=10, max_context_characters=20_000
            ),
            IntegrationSettings: IntegrationSettings(),
            ToolSettings: ToolSettings(),
        }[settings_type]

    projections = []

    async def build_context(*_args, **kwargs):
        projections.append(kwargs)
        return ContextProjection(
            messages=[
                {"role": "system", "content": kwargs["system_prompt"]},
                {"role": "user", "content": records[-1].content},
            ],
            estimated_input_tokens=100,
            input_budget_tokens=1_000,
        )

    requests = []
    queue = list(results)

    async def complete(_db, **kwargs):
        requests.append(kwargs)
        result = queue.pop(0)
        if result.content and kwargs.get("on_delta"):
            await kwargs["on_delta"](result.content)
        return result

    retrieve = AsyncMock(
        return_value=MemoryRetrieval(
            items=automatic_items or [],
            mode=MemoryRetrieveMode.SEARCH,
            strategy=MemoryRetrievalStrategy.HYBRID,
            readable_by=TrustLevel.PRIVATE,
            offset=0,
            limit=AUTOMATIC_MEMORY_MAX_ITEMS,
            has_more=False,
        )
    )
    execute_tool = AsyncMock(
        return_value=ToolResult.json(
            {
                "mode": "browse",
                "items": [],
                "hasMore": False,
                "nextCursor": None,
            },
            metadata={"hasMore": False, "nextCursor": None},
        )
    )
    monkeypatch.setattr(SettingsStore, "load", load_settings)
    monkeypatch.setattr(AgentRuntime, "_records", AsyncMock(return_value=records))
    monkeypatch.setattr(ContextManager, "build", build_context)
    monkeypatch.setattr(ProviderService, "complete", staticmethod(complete))
    monkeypatch.setattr(MemoryService, "instant_context", AsyncMock(return_value=[]))
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())
    monkeypatch.setattr(MemoryService, "record_exposures", AsyncMock())
    monkeypatch.setattr(
        MemoryService,
        "scope",
        AsyncMock(return_value=MemoryScope(total=15, instant=2, retrieve=13)),
    )
    monkeypatch.setattr(MemoryRetrievalService, "retrieve", retrieve)
    monkeypatch.setattr(EffectiveToolSet, "execute", execute_tool)
    monkeypatch.setattr(run_events, "publish", AsyncMock())
    return db, run, requests, projections, retrieve, execute_tool


async def test_automatic_rag_injects_bounded_relevant_memories(monkeypatch) -> None:
    session_id = uuid4()
    source_at = datetime(2026, 7, 20, 10, tzinfo=UTC)
    created_at = datetime(2026, 7, 20, 11, tzinfo=UTC)
    updated_at = datetime(2026, 7, 21, 12, tzinfo=UTC)
    user = Message(
        id=uuid4(), session_id=session_id, role="user", content="Wie alt ist mein Sohn?"
    )
    relevant = MemoryRecord(
        id=uuid4(),
        content="Der Sohn des Nutzers ist neun Monate alt.",
        category="fact",
        retrieval_level="retrieve",
        trust_level="private",
        revision=1,
        created_at=created_at,
        updated_at=updated_at,
    )
    relevant.source_message = Message(created_at=source_at)
    db, run, requests, projections, retrieve, _ = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="Neun Monate.", finish_reason="stop")],
        records=[user],
        automatic_items=[relevant],
    )

    await AgentRuntime._execute_loop(db, run)

    retrieve.assert_awaited_once()
    kwargs = retrieve.await_args.kwargs
    assert kwargs["query"] == "Wie alt ist mein Sohn?"
    assert kwargs["retrieval_level"].value == "retrieve"
    assert kwargs["limit"] == AUTOMATIC_MEMORY_MAX_ITEMS
    assert kwargs["max_semantic_distance"] == AUTOMATIC_MEMORY_MAX_DISTANCE
    instant_kwargs = MemoryService.instant_context.await_args.kwargs
    assert "max_items" not in instant_kwargs
    assert instant_kwargs["max_characters"] == 20_000
    assert '"source": "relevant"' in projections[0]["system_prompt"]
    assert relevant.content in projections[0]["system_prompt"]
    assert f'"sourceMessageAt": "{source_at.isoformat()}"' in projections[0][
        "system_prompt"
    ]
    assert f'"memoryCreatedAt": "{created_at.isoformat()}"' in projections[0][
        "system_prompt"
    ]
    assert f'"memoryUpdatedAt": "{updated_at.isoformat()}"' in projections[0][
        "system_prompt"
    ]
    assert "15 total, 2 active, 13 long-term" in projections[0]["system_prompt"]
    assert requests[0]["tool_choice"] == "auto"


async def test_memory_write_workflow_discards_drafts_until_write_resolves(
    monkeypatch,
) -> None:
    session_id = uuid4()
    user = Message(
        id=uuid4(),
        session_id=session_id,
        role="user",
        content="Ich komme aus Ascholding.",
    )
    results = [
        ProviderResult(
            tool_calls=[
                ToolCall(
                    id="lookup",
                    name="memory_retrieve",
                    arguments={
                        "purpose": "write",
                        "mode": "search",
                        "query": "Herkunft Ascholding",
                    },
                )
            ]
        ),
        ProviderResult(content="Ascholding ist gespeichert.", finish_reason="stop"),
        ProviderResult(
            tool_calls=[
                ToolCall(
                    id="invalid-write",
                    name="memory_write",
                    arguments={
                        "action": "update",
                        "memory_id": str(uuid4()),
                        "content": "Der Nutzer kommt aus Ascholding.",
                        "category": "identity",
                    },
                )
            ]
        ),
        ProviderResult(
            tool_calls=[
                ToolCall(
                    id="valid-write",
                    name="memory_write",
                    arguments={
                        "action": "create",
                        "content": "Der Nutzer kommt aus Ascholding.",
                        "category": "identity",
                    },
                ),
                ToolCall(
                    id="duplicate-write",
                    name="memory_write",
                    arguments={
                        "action": "create",
                        "content": "Der Nutzer kommt aus Ascholding.",
                        "category": "identity",
                    },
                ),
            ]
        ),
        ProviderResult(content="Ich habe deine Herkunft gespeichert.", finish_reason="stop"),
    ]
    db, run, requests, projections, _retrieve, execute_tool = await _runtime_setup(
        monkeypatch,
        results,
        records=[user],
    )
    execute_tool.side_effect = [
        ToolResult.json(
            {"purpose": "write", "items": []},
            metadata={"memoryWritePending": True},
        ),
        ToolResult.error("tool.invalidArguments", field="expected_revision"),
        ToolResult.json(
            {"stored": True, "updated": False},
            metadata={"memoryWriteOutcome": "stored"},
        ),
        ToolResult.json(
            {"stored": False, "updated": False, "duplicate": True},
            metadata={"memoryWriteOutcome": "duplicate"},
        ),
    ]

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "auto",
        "required",
        "required",
        "required",
        "auto",
    ]
    assert all(
        {tool["name"] for tool in request["tools"]}
        == {"memory_retrieve", "memory_write"}
        for request in requests[1:4]
    )
    assert "Required memory mutation" in projections[1]["system_prompt"]
    assert "Host-confirmed memory outcome" in projections[-1]["system_prompt"]
    assert "memory_write succeeded" in projections[-1]["system_prompt"]
    assert "already stored" not in projections[-1]["system_prompt"]
    persisted_assistant_texts = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "assistant"
    ]
    assert "Ascholding ist gespeichert." not in persisted_assistant_texts
    assert persisted_assistant_texts[-1] == "Ich habe deine Herkunft gespeichert."
    assert execute_tool.await_count == 4


def test_memory_overview_stops_before_exceeding_its_character_budget() -> None:
    items = [
        MemoryRecord(
            id=uuid4(),
            content=character * 7_000,
            category="fact",
            retrieval_level="retrieve",
            trust_level="private",
            revision=1,
        )
        for character in ("a", "b")
    ]

    payload, last_item, omitted_items = _bounded_memory_overview(items)

    assert sum(len(item["content"]) for item in payload) <= (
        MEMORY_OVERVIEW_MAX_CHARACTERS
    )
    assert len(payload) == 1
    assert last_item is items[0]
    assert omitted_items is True


async def test_memory_overview_is_prefetched_by_the_host(monkeypatch) -> None:
    session_id = uuid4()
    source_at = datetime(2026, 7, 20, 10, tzinfo=UTC)
    created_at = datetime(2026, 7, 20, 11, tzinfo=UTC)
    updated_at = datetime(2026, 7, 23, 12, tzinfo=UTC)
    user = Message(
        id=uuid4(),
        session_id=session_id,
        role="user",
        content="Was hast du alles in deiner Erinnerung?",
    )
    memory = MemoryRecord(
        id=uuid4(),
        content="Der Nutzer bevorzugt lokale Modelle.",
        category="preference",
        retrieval_level="retrieve",
        trust_level="private",
        revision=1,
        created_at=created_at,
        updated_at=updated_at,
    )
    memory.source_message = Message(created_at=source_at)
    db, run, requests, projections, retrieve, execute_tool = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="Übersicht", finish_reason="stop")],
        records=[user],
    )
    retrieve.return_value = MemoryRetrieval(
        items=[memory],
        mode=MemoryRetrieveMode.BROWSE,
        strategy=MemoryRetrievalStrategy.BROWSE,
        readable_by=TrustLevel.PRIVATE,
        offset=0,
        limit=50,
        has_more=False,
        total=1,
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["auto"]
    assert "host prefetched a persistent-memory overview page" in projections[0][
        "system_prompt"
    ]
    assert memory.content in projections[0]["system_prompt"]
    assert f'"sourceMessageAt": "{source_at.isoformat()}"' in projections[0][
        "system_prompt"
    ]
    assert f'"memoryCreatedAt": "{created_at.isoformat()}"' in projections[0][
        "system_prompt"
    ]
    assert f'"memoryUpdatedAt": "{updated_at.isoformat()}"' in projections[0][
        "system_prompt"
    ]
    assert retrieve.await_args.kwargs["mode"] == MemoryRetrieveMode.BROWSE
    assert retrieve.await_args.kwargs["limit"] == 50
    execute_tool.assert_not_awaited()
    persisted = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assistant = next(item for item in persisted if item.role == "assistant")
    assert assistant.provider_data["_matoloop"]["memoryOverview"] == {
        "hasMore": False,
        "nextCursor": None,
    }


async def test_memory_overview_follow_up_uses_persisted_cursor(monkeypatch) -> None:
    session_id = uuid4()
    cursor_time = datetime(2026, 7, 23, 12, tzinfo=UTC)
    cursor_id = uuid4()
    previous_user = Message(
        id=uuid4(),
        session_id=session_id,
        role="user",
        content="Was weißt du über mich?",
    )
    previous_assistant = Message(
        id=uuid4(),
        session_id=session_id,
        role="assistant",
        content="Erste Seite",
        provider_data={
            "_matoloop": {
                "memoryOverview": {
                    "hasMore": True,
                    "nextCursor": {
                        "updatedAt": cursor_time.isoformat(),
                        "id": str(cursor_id),
                    },
                }
            }
        },
    )
    user = Message(
        id=uuid4(), session_id=session_id, role="user", content="Was noch?"
    )
    db, run, requests, projections, retrieve, execute_tool = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="Nächste Seite", finish_reason="stop")],
        records=[previous_user, previous_assistant, user],
    )
    retrieve.return_value = MemoryRetrieval(
        items=[],
        mode=MemoryRetrieveMode.BROWSE,
        strategy=MemoryRetrievalStrategy.BROWSE,
        readable_by=TrustLevel.PRIVATE,
        offset=0,
        limit=50,
        has_more=False,
        total=15,
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["auto"]
    assert retrieve.await_args.kwargs["cursor_updated_at"] == cursor_time
    assert retrieve.await_args.kwargs["cursor_id"] == cursor_id
    assert "exhausts the currently visible" in projections[0]["system_prompt"]
    execute_tool.assert_not_awaited()


def test_memory_overview_follow_up_is_detected_after_browse() -> None:
    session_id = uuid4()
    records = [
        Message(
            id=uuid4(),
            session_id=session_id,
            role="user",
            content="Was weißt du über mich?",
        ),
        Message(
            id=uuid4(),
            session_id=session_id,
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "browse",
                    "name": "memory_retrieve",
                    "arguments": {"mode": "browse", "offset": 0},
                }
            ],
        ),
        Message(
            id=uuid4(),
            session_id=session_id,
            role="tool",
            content=json.dumps({"items": [], "hasMore": False}),
            tool_call_id="browse",
        ),
        Message(
            id=uuid4(),
            session_id=session_id,
            role="user",
            content="Das ist im Kontext, aber was weißt du noch?",
        ),
    ]

    assert _memory_overview_requested(records) is True
