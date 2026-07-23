import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.runtime import _bounded_memory_trust
from mato_loop_backend.agent.tools import MemoryWriteArguments, ToolContext, tool_registry
from mato_loop_backend.api.routes.memories import (
    MemoryAcknowledgementRequest,
    MemorySettingsRequest,
    acknowledge_memories,
    memory_payload,
    put_memory_settings,
)
from mato_loop_backend.memory.dreaming import (
    DREAMING_SYSTEM_PROMPT,
    RECONCILIATION_SYSTEM_PROMPT,
    DreamCandidates,
    DreamingService,
    DreamReconciliation,
    _extract_candidates,
    _memory_read_trust,
    _reconcile_candidates,
    dreaming_availability,
)
from mato_loop_backend.memory.retrieval import (
    MemoryRetrievalService,
    MemoryRetrievalStrategy,
    MemoryRetrieveMode,
)
from mato_loop_backend.memory.service import (
    MemoryDuplicateError,
    MemoryMutation,
    MemoryService,
    normalize_content,
)
from mato_loop_backend.models import (
    ChatSession,
    MemoryCategory,
    MemoryDreamRun,
    MemoryRecord,
    MemoryRetrievalLevel,
    MemorySourceMessage,
    UserAccount,
)
from mato_loop_backend.providers import ToolCall
from mato_loop_backend.providers.base import ProviderError, ProviderResult
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AiSettings,
    MemorySettings,
    SearchSettings,
    SettingsStore,
    TrustLevel,
)
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelConfig,
    effective_model_pseudonymization,
    effective_model_trust,
)
from mato_loop_backend.workspace import WorkspaceService


def test_trust_defaults_and_inheritance() -> None:
    provider = LlmProviderConfig(name="Public", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=uuid4(), model_id="public")

    assert provider.trust_level == TrustLevel.PUBLIC
    assert model.trust_level is None
    assert effective_model_trust(provider, model) == TrustLevel.PUBLIC
    provider.trust_level = TrustLevel.PRIVATE
    assert effective_model_trust(provider, model) == TrustLevel.PRIVATE
    model.trust_level = TrustLevel.PUBLIC
    assert effective_model_trust(provider, model) == TrustLevel.PUBLIC
    assert effective_model_pseudonymization(provider, model) is True
    model.trust_level = TrustLevel.PRIVATE
    assert effective_model_pseudonymization(provider, model) is False


def test_running_tool_call_cannot_gain_memory_trust_after_model_change() -> None:
    assert (
        _bounded_memory_trust(TrustLevel.PUBLIC, TrustLevel.PRIVATE)
        == TrustLevel.PUBLIC
    )
    assert (
        _bounded_memory_trust(TrustLevel.PRIVATE, TrustLevel.PUBLIC)
        == TrustLevel.PUBLIC
    )
    assert (
        _bounded_memory_trust(TrustLevel.PRIVATE, TrustLevel.PRIVATE)
        == TrustLevel.PRIVATE
    )


@pytest.mark.parametrize(
    ("input_trust", "model_trust", "expected"),
    [
        (TrustLevel.PUBLIC, TrustLevel.PUBLIC, TrustLevel.PUBLIC),
        (TrustLevel.PUBLIC, TrustLevel.PRIVATE, TrustLevel.PUBLIC),
        (TrustLevel.PRIVATE, TrustLevel.PUBLIC, TrustLevel.PUBLIC),
        (TrustLevel.PRIVATE, TrustLevel.PRIVATE, TrustLevel.PRIVATE),
    ],
)
def test_dreaming_memory_reads_use_intersection_of_input_and_model_trust(
    input_trust: TrustLevel,
    model_trust: TrustLevel,
    expected: TrustLevel,
) -> None:
    assert _memory_read_trust(input_trust, model_trust) == expected


def test_dreaming_batch_requires_user_and_assistant_capacity() -> None:
    with pytest.raises(ValidationError):
        MemorySettingsRequest(
            enabled=True,
            max_context_items=10,
            max_context_characters=10_000,
            automatic_dreaming_enabled=True,
            dreaming_interval_minutes=60,
            dreaming_idle_minutes=10,
            dreaming_max_batch_messages=1,
            dreaming_max_candidates=10,
        )

    with pytest.raises(ValidationError):
        MemorySettingsRequest(enabled=None)


def test_automatic_dreaming_is_independent_from_manual_dreaming() -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="model")
    ai = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    settings = MemorySettings(enabled=True, automatic_dreaming_enabled=False)

    assert dreaming_availability(settings, ai, automatic=True) == (
        False,
        "memory.dreamingDisabled",
    )
    assert dreaming_availability(settings, ai, automatic=False) == (True, None)

    settings.enabled = False
    assert dreaming_availability(settings, ai, automatic=False) == (
        False,
        "memory.disabled",
    )


async def test_provider_rejects_model_from_another_provider_before_database_access() -> None:
    db = AsyncMock(spec=AsyncSession)
    provider = LlmProviderConfig(
        name="Provider A",
        adapter_type="openai-compatible",
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(provider_id=uuid4(), model_id="foreign")

    with pytest.raises(ProviderError) as exc_info:
        await ProviderService.complete(
            db,
            provider=provider,
            model=model,
            input_trust_level=TrustLevel.PUBLIC,
            messages=[],
            tools=[],
        )

    assert exc_info.value.public_code == "provider.configurationInvalid"
    db.get.assert_not_awaited()


async def test_provider_allows_private_input_for_public_pseudonymized_request(
    monkeypatch,
) -> None:
    db = AsyncMock(spec=AsyncSession)
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="public")

    complete = AsyncMock(return_value=ProviderResult(content="OK"))
    monkeypatch.setattr(
        "mato_loop_backend.providers.service.chat_completions", complete
    )

    result = await ProviderService.complete(
        db,
        provider=provider,
        model=model,
        input_trust_level=TrustLevel.PRIVATE,
        messages=[],
        tools=[],
    )

    assert result.content == "OK"
    db.get.assert_not_awaited()


def test_memory_normalization_is_whitespace_and_case_stable() -> None:
    first, first_hash = normalize_content("  Ein\n  FAKT  ")
    second, second_hash = normalize_content("ein fakt")

    assert first == "ein fakt"
    assert first_hash == second_hash


async def test_instant_context_uses_only_whole_items_within_budget() -> None:
    records = [
        MemoryRecord(content="viel-zu-lang", retrieval_level="instant"),
        MemoryRecord(content="abc", retrieval_level="instant"),
        MemoryRecord(content="de", retrieval_level="instant"),
        MemoryRecord(content="f", retrieval_level="instant"),
    ]
    result = Mock()
    result.all.return_value = records
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = result

    selected = await MemoryService.instant_context(
        db,
        input_trust_level=TrustLevel.PUBLIC,
        max_items=3,
        max_characters=7,
    )

    assert [item.content for item in selected] == ["abc", "de"]
    assert "memories.trust_level" in str(db.scalars.await_args.args[0])


async def test_private_dreaming_candidate_upgrades_public_duplicate() -> None:
    existing = MemoryRecord(
        id=uuid4(),
        source_type="dreaming",
        retrieval_level="retrieve",
        trust_level="public",
        category="fact",
        content="Dauerhafter Fakt",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = existing
    linked = Mock()
    linked.all.return_value = []
    db.scalars.return_value = linked
    evidence_id = uuid4()

    created = await MemoryService.create_dreaming(
        db,
        content="Dauerhafter Fakt",
        category=MemoryCategory.FACT,
        retrieval_level=MemoryRetrievalLevel.INSTANT,
        trust_level=TrustLevel.PRIVATE,
        session_id=uuid4(),
        run_id=uuid4(),
        evidence_message_ids=[evidence_id],
    )

    assert created.duplicate is True
    assert existing.trust_level == "private"
    evidence_insert = db.execute.await_args.args[0]
    assert evidence_insert.compile().params["memory_id_m0"] == existing.id
    assert evidence_insert.compile().params["message_id_m0"] == evidence_id


async def test_manual_memory_rejects_normalized_duplicate() -> None:
    existing = MemoryRecord(
        id=uuid4(),
        source_type="manual",
        retrieval_level="retrieve",
        trust_level="public",
        category="fact",
        content="Ein Fakt",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = existing

    with pytest.raises(MemoryDuplicateError):
        await MemoryService.create_manual(
            db,
            content="  EIN\nFAKT ",
            category=MemoryCategory.FACT,
            trust_level=TrustLevel.PUBLIC,
            retrieval_level=MemoryRetrievalLevel.RETRIEVE,
        )

    db.add.assert_not_called()


async def test_manual_memory_update_loads_target_for_update() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    await MemoryService.get(db, uuid4(), for_update=True)

    statement = db.scalar.await_args.args[0]
    assert "FOR UPDATE" in str(statement)
    assert statement.get_execution_options()["populate_existing"] is True


def test_memory_write_arguments_require_explicit_update_target() -> None:
    valid = MemoryWriteArguments.model_validate(
        {
            "action": "update",
            "memory_id": str(uuid4()),
            "expected_revision": 3,
            "content": "Aktualisierter Fakt",
            "category": "fact",
            "retrieval_level": "instant",
        }
    )

    assert valid.category == MemoryCategory.FACT
    assert valid.retrieval_level == MemoryRetrievalLevel.INSTANT
    with pytest.raises(ValidationError):
        MemoryWriteArguments.model_validate(
            {"action": "update", "content": "Fakt", "category": "fact"}
        )


async def test_private_tool_update_upgrades_trust_and_adds_evidence(monkeypatch) -> None:
    item = MemoryRecord(
        id=uuid4(),
        source_type="manual",
        retrieval_level="retrieve",
        trust_level="public",
        category="fact",
        content="Alter Fakt",
        revision=3,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = item
    update_memory = AsyncMock(return_value=item)
    monkeypatch.setattr(MemoryService, "update", update_memory)
    evidence_id = uuid4()

    result = await MemoryService.update_tool(
        db,
        memory_id=item.id,
        expected_revision=3,
        content="Neuer Fakt",
        category=MemoryCategory.FACT,
        retrieval_level=MemoryRetrievalLevel.INSTANT,
        readable_by=TrustLevel.PUBLIC,
        write_trust_level=TrustLevel.PRIVATE,
        evidence_message_id=evidence_id,
    )

    assert result.updated is True
    assert update_memory.await_args.kwargs["trust_level"] == TrustLevel.PRIVATE
    assert update_memory.await_args.kwargs["evidence_message_ids"] == [evidence_id]


async def test_tool_update_rejects_stale_memory_snapshot(monkeypatch) -> None:
    item = MemoryRecord(
        id=uuid4(),
        source_type="manual",
        retrieval_level="retrieve",
        trust_level="public",
        category="fact",
        content="Inzwischen geändert",
        revision=4,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = item
    update_memory = AsyncMock(return_value=item)
    monkeypatch.setattr(MemoryService, "update", update_memory)

    result = await MemoryService.update_tool(
        db,
        memory_id=item.id,
        expected_revision=3,
        content="Veraltetes Update",
        category=MemoryCategory.FACT,
        retrieval_level=MemoryRetrievalLevel.RETRIEVE,
        readable_by=TrustLevel.PUBLIC,
        write_trust_level=TrustLevel.PUBLIC,
        evidence_message_id=uuid4(),
    )

    assert result.conflict is True
    update_memory.assert_not_awaited()


async def test_memory_write_reports_duplicate_without_storing(monkeypatch, tmp_path) -> None:
    duplicate = MemoryRecord(
        id=uuid4(),
        source_type="manual",
        retrieval_level="retrieve",
        trust_level="public",
        category="preference",
        content="Kurze Antworten",
    )
    create_tool = AsyncMock(
        return_value=MemoryMutation(duplicate, duplicate=True)
    )
    monkeypatch.setattr(MemoryService, "create_tool", create_tool)
    call = ToolCall(
        id="memory",
        name="memory_write",
        arguments={
            "action": "create",
            "content": "Kurze Antworten",
            "category": "preference",
            "retrieval_level": "retrieve",
        },
    )
    context = ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=ChatSession(id=uuid4(), title="Test", workspace_access="read-only"),
        tool_call=call,
        workspace=WorkspaceService(tmp_path),
        search_settings=SearchSettings(),
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
        assistant_message_id=uuid4(),
        memory_read_enabled=True,
        memory_write_enabled=True,
    )

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content) == {
        "stored": False,
        "updated": False,
        "duplicate": True,
    }


def test_memory_tools_are_gated_independently_from_workspace_write_mode() -> None:
    without_memory = tool_registry.schemas(SearchSettings(), "read-only")
    with_read_memory = tool_registry.schemas(
        SearchSettings(), "read-only", memory_enabled=True
    )
    with_full_memory = tool_registry.schemas(
        SearchSettings(), "read-only", memory_enabled=True, memory_write_enabled=True
    )

    assert not {"memory_write", "memory_retrieve"} & {
        item["name"] for item in without_memory
    }
    assert {item["name"] for item in with_read_memory} & {
        "memory_write", "memory_retrieve"
    } == {"memory_retrieve"}
    assert {"memory_write", "memory_retrieve"}.issubset(
        {item["name"] for item in with_full_memory}
    )


async def test_public_model_can_read_private_session_after_approval(
    tmp_path,
) -> None:
    source = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Public",
        trust_level="public",
        workspace_access="read-only",
    )
    target = ChatSession(
        id=uuid4(),
        user_id=source.user_id,
        title="Privat",
        trust_level="private",
        workspace_access="read-only",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = target
    records = Mock()
    records.all.return_value = []
    db.scalars.return_value = records
    call = ToolCall(
        id="read", name="session_read", arguments={"session_id": str(target.id)}
    )
    context = ToolContext(
        db=db,
        run_id=uuid4(),
        session=source,
        tool_call=call,
        workspace=WorkspaceService(tmp_path),
        search_settings=SearchSettings(),
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
    )

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content)["session"]["id"] == str(target.id)
    db.scalars.assert_awaited_once()


def test_memory_payload_contains_camel_case_provenance() -> None:
    message_id = uuid4()
    seen_at = datetime.now(UTC)
    session = ChatSession(id=uuid4(), title="Quelle")
    item = MemoryRecord(
        id=uuid4(),
        source_type="dreaming",
        retrieval_level="retrieve",
        trust_level="private",
        category="fact",
        content="Fakt",
        seen_at=seen_at,
    )
    item.source_session = session
    item.source_messages = [
        MemorySourceMessage(memory_id=item.id, message_id=message_id)
    ]

    payload = memory_payload(item)

    assert payload["sourceType"] == "dreaming"
    assert payload["retrievalLevel"] == "retrieve"
    assert payload["trustLevel"] == "private"
    assert payload["category"] == "fact"
    assert payload["seenAt"] == seen_at.isoformat()
    assert payload["sourceSession"] == {"id": str(session.id), "title": "Quelle"}
    assert payload["sourceMessageIds"] == [str(message_id)]


def test_memory_payload_uses_dream_run_provenance() -> None:
    dream_run_id = uuid4()
    item = MemoryRecord(
        id=uuid4(),
        source_type="dreaming",
        retrieval_level="retrieve",
        trust_level="public",
        category="fact",
        content="Fakt",
        source_dream_run_id=dream_run_id,
    )
    item.source_session = None
    item.source_messages = []

    assert memory_payload(item)["sourceRunId"] == str(dream_run_id)


async def test_memory_acknowledgement_marks_exact_ids_and_commits(monkeypatch) -> None:
    memory_ids = [uuid4(), uuid4()]
    mark_seen = AsyncMock(return_value=2)
    monkeypatch.setattr(MemoryService, "mark_seen", mark_seen)
    db = AsyncMock(spec=AsyncSession)

    result = await acknowledge_memories(
        MemoryAcknowledgementRequest(memoryIds=memory_ids), db
    )

    assert result == {"acknowledged": 2}
    mark_seen.assert_awaited_once_with(db, memory_ids)
    db.commit.assert_awaited_once()


async def test_mark_seen_updates_only_unseen_memories_without_touching_updated_at() -> None:
    memory_ids = [uuid4(), uuid4()]
    rows = Mock()
    rows.all.return_value = memory_ids
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = rows

    acknowledged = await MemoryService.mark_seen(
        db, [memory_ids[0], memory_ids[1], memory_ids[0]]
    )

    statement = db.scalars.await_args.args[0]
    sql = str(statement)
    assert acknowledged == 2
    assert "memories.seen_at IS NULL" in sql
    assert "updated_at=memories.updated_at" in sql


async def test_memory_settings_update_locks_saves_and_commits(monkeypatch) -> None:
    settings = MemorySettings()
    load = AsyncMock(
        side_effect=lambda _db, settings_type, **_kwargs: (
            settings if settings_type is MemorySettings else AiSettings()
        )
    )
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", save)
    db = AsyncMock(spec=AsyncSession)
    payload = MemorySettingsRequest(
        enabled=True,
        max_context_items=12,
        max_context_characters=12_000,
        automatic_dreaming_enabled=True,
        dreaming_instructions="Nur bestätigte Präferenzen",
        dreaming_interval_minutes=30,
        dreaming_idle_minutes=10,
        dreaming_max_batch_messages=25,
        dreaming_max_candidates=5,
    )

    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    response = await put_memory_settings(payload, user, db)

    assert load.await_args_list[0].kwargs == {
        "user_id": user.id,
        "for_update": True,
    }
    assert settings.dreaming_interval_minutes == 30
    save.assert_awaited_once_with(db, settings, user_id=user.id)
    db.commit.assert_awaited_once()
    assert response["effectiveAutomaticDreamingEnabled"] is False
    assert response["manualDreamingAvailable"] is False


async def test_dreaming_restart_requeues_running_without_own_commit() -> None:
    db = AsyncMock(spec=AsyncSession)

    await DreamingService.recover(db)

    assert db.execute.await_count == 2
    parameters = [
        list(call.args[0].compile().params.values())
        for call in db.execute.await_args_list
    ]
    assert any("running" in params and "queued" in params for params in parameters)
    assert any("running" in params and "failed" in params for params in parameters)
    assert all("memory.backendRestarted" in params for params in parameters)
    db.commit.assert_not_awaited()


async def test_dreaming_failure_reloads_run_by_stable_id_after_rollback(
    monkeypatch,
) -> None:
    run = MemoryDreamRun(
        id=uuid4(),
        session_id=uuid4(),
        status="queued",
        trigger="manual",
        source_from_message_id=uuid4(),
        source_to_message_id=uuid4(),
        source_message_count=2,
        attempts=0,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = run
    db.get.return_value = run
    monkeypatch.setattr(
        DreamingService,
        "_process",
        AsyncMock(side_effect=RuntimeError("memory.dreamingOutputInvalid")),
    )

    assert await DreamingService.process_next(db) is True

    db.get.assert_awaited_once_with(MemoryDreamRun, run.id)
    assert run.status == "queued"
    assert run.error == "memory.dreamingFailed"
    assert run.next_attempt_at is not None
    assert db.commit.await_count == 2


def test_dreaming_output_accepts_empty_and_validates_evidence_shape() -> None:
    assert DreamCandidates.model_validate({"candidates": []}).candidates == []
    message_id = uuid4()
    output = DreamCandidates.model_validate(
        {
            "candidates": [
                {
                    "content": "Dauerhafter Fakt",
                    "category": "fact",
                    "retrievalLevel": "retrieve",
                    "evidenceMessageIds": [str(message_id)],
                }
            ]
        }
    )

    assert output.candidates[0].evidence_message_ids == [message_id]
    assert output.candidates[0].category == MemoryCategory.FACT
    assert output.candidates[0].retrieval_level == MemoryRetrievalLevel.RETRIEVE

    with pytest.raises(ValueError):
        DreamCandidates.model_validate(
            {
                "candidates": [
                    {
                        "content": "   ",
                        "category": "fact",
                        "retrievalLevel": "retrieve",
                        "evidenceMessageIds": [str(message_id)],
                    }
                ]
            }
        )


def test_dreaming_candidate_schema_uses_small_model_friendly_fields() -> None:
    schema = DreamCandidates.model_json_schema(by_alias=True)
    candidate = schema["$defs"]["DreamCandidate"]

    assert set(candidate["required"]) == {
        "memory",
        "category",
        "retrievalLevel",
        "evidence",
    }
    assert DreamCandidates.model_validate(
        {
            "candidates": [
                {
                    "memory": "Der Nutzer ist verheiratet.",
                    "category": "identity",
                    "retrievalLevel": "instant",
                    "evidence": [str(uuid4())],
                }
            ]
        }
    ).candidates[0].content == "Der Nutzer ist verheiratet."


async def test_dreaming_repairs_invalid_candidate_tool_arguments(monkeypatch) -> None:
    evidence_id = uuid4()
    provider = LlmProviderConfig(
        name="Lokal", adapter_type="openai-compatible", trust_level="private"
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="invalid",
                        name="record_memory_candidates",
                        arguments={
                            "candidates": [
                                {
                                    "category": "identity",
                                    "statement": "Der Nutzer ist Ehemann und Vater.",
                                }
                            ]
                        },
                    )
                ]
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="valid",
                        name="record_memory_candidates",
                        arguments={
                            "candidates": [
                                {
                                    "memory": "Der Nutzer ist verheiratet und hat ein Kind.",
                                    "category": "identity",
                                    "retrievalLevel": "instant",
                                    "evidence": [str(evidence_id)],
                                }
                            ]
                        },
                    )
                ]
            ),
        ]
    )
    monkeypatch.setattr(ProviderService, "complete", complete)

    output, references = await _extract_candidates(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        memory_read_trust=TrustLevel.PRIVATE,
        system_prompt=DREAMING_SYSTEM_PROMPT,
        source=f"MESSAGE {evidence_id} [USER]\nmeine Frau und unser Kind",
        instant_memories=[],
        max_candidates=10,
        evidence_ids={evidence_id},
    )

    assert output.candidates[0].content == "Der Nutzer ist verheiratet und hat ein Kind."
    assert references == []
    assert complete.await_count == 2
    retry_messages = complete.await_args_list[1].kwargs["messages"]
    assert retry_messages[-1]["role"] == "tool"
    assert "memory, category, retrievalLevel, and evidence" in retry_messages[-1]["content"]


async def test_dreaming_uses_instant_context_and_memory_retrieve(monkeypatch) -> None:
    evidence_id = uuid4()
    provider = LlmProviderConfig(
        name="Lokal", adapter_type="openai-compatible", trust_level="private"
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    instant = MemoryRecord(
        id=uuid4(),
        source_type="manual",
        retrieval_level="instant",
        trust_level="private",
        category="identity",
        revision=1,
        content="Der Nutzer hat eine Familie.",
    )
    retrieved = MemoryRecord(
        id=uuid4(),
        source_type="dreaming",
        retrieval_level="retrieve",
        trust_level="private",
        category="context",
        revision=2,
        content="Ascholding ist vermutlich ein relevanter Ort für den Nutzer.",
    )
    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="lookup",
                        name="memory_retrieve",
                        arguments={"query": "Familie Kind", "limit": 5},
                    )
                ]
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="record",
                        name="record_memory_candidates",
                        arguments={"candidates": []},
                    )
                ]
            ),
        ]
    )
    retrieve_memories = AsyncMock(
        return_value=SimpleNamespace(
            items=[retrieved],
            mode=MemoryRetrieveMode.SEARCH,
            strategy=MemoryRetrievalStrategy.HYBRID,
        )
    )
    monkeypatch.setattr(ProviderService, "complete", complete)
    monkeypatch.setattr(MemoryRetrievalService, "retrieve", retrieve_memories)
    db = AsyncMock(spec=AsyncSession)

    output, references = await _extract_candidates(
        db,
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        memory_read_trust=TrustLevel.PRIVATE,
        system_prompt=DREAMING_SYSTEM_PROMPT,
        source=f"MESSAGE {evidence_id} [USER]\nErzähl mir etwas.",
        instant_memories=[instant],
        max_candidates=10,
        evidence_ids={evidence_id},
    )

    assert output.candidates == []
    assert {item.id for item in references} == {instant.id, retrieved.id}
    first_messages = complete.await_args_list[0].kwargs["messages"]
    assert instant.content in first_messages[1]["content"]
    second_messages = complete.await_args_list[1].kwargs["messages"]
    assert retrieved.content in second_messages[-1]["content"]
    retrieve_memories.assert_awaited_once_with(
        db,
        mode=MemoryRetrieveMode.SEARCH,
        query="Familie Kind",
        retrieval_level=MemoryRetrievalLevel.RETRIEVE,
        readable_by=TrustLevel.PRIVATE,
        limit=5,
    )


async def test_dreaming_reconciliation_repairs_invalid_output(monkeypatch) -> None:
    memory_id = uuid4()
    provider = LlmProviderConfig(
        name="Lokal", adapter_type="openai-compatible", trust_level="private"
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="invalid",
                        name="reconcile_memory_candidates",
                        arguments={"decisions": [{"index": 0, "action": "update"}]},
                    )
                ]
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="valid",
                        name="reconcile_memory_candidates",
                        arguments={
                            "decisions": [
                                {
                                    "index": 0,
                                    "action": "unchanged",
                                    "memoryId": str(memory_id),
                                }
                            ]
                        },
                    )
                ]
            ),
        ]
    )
    monkeypatch.setattr(ProviderService, "complete", complete)

    result = await _reconcile_candidates(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        source="{}",
        candidate_count=1,
        supplied_ids={memory_id},
        system_prompt=RECONCILIATION_SYSTEM_PROMPT + "\nCUSTOM CATEGORY GUIDANCE",
    )

    assert result.decisions[0].action == "unchanged"
    assert complete.await_count == 2
    assert "CUSTOM CATEGORY GUIDANCE" in complete.await_args_list[0].kwargs["messages"][0]["content"]


def test_dreaming_system_prompt_remains_isolated_from_agent_context() -> None:
    assert "cross-session knowledge" in DREAMING_SYSTEM_PROMPT
    assert "Reason one or two steps beyond literal wording" in DREAMING_SYSTEM_PROMPT
    assert "weather query for a place" in DREAMING_SYSTEM_PROMPT
    assert "Write each memory" in DREAMING_SYSTEM_PROMPT
    assert "memory_retrieve" in DREAMING_SYSTEM_PROMPT
    assert "record_memory_candidates exactly once" in DREAMING_SYSTEM_PROMPT
    assert "evidence message IDs from the supplied batch" in DREAMING_SYSTEM_PROMPT
    assert "retrieval level" in DREAMING_SYSTEM_PROMPT
    assert "web_search" not in DREAMING_SYSTEM_PROMPT


def test_dreaming_reconciliation_requires_complete_safe_updates() -> None:
    memory_id = uuid4()
    result = DreamReconciliation.model_validate(
        {
            "decisions": [
                {
                    "candidateIndex": 0,
                    "action": "update",
                    "memoryId": str(memory_id),
                    "content": "Aktuelle Präferenz",
                    "category": "preference",
                    "retrievalLevel": "instant",
                }
            ]
        }
    )

    assert result.decisions[0].memory_id == memory_id
    assert result.decisions[0].retrieval_level == MemoryRetrievalLevel.INSTANT
    assert "Reference only supplied memory IDs" in RECONCILIATION_SYSTEM_PROMPT
    with pytest.raises(ValidationError):
        DreamReconciliation.model_validate(
            {
                "decisions": [
                    {"candidateIndex": 0, "action": "update", "memoryId": str(memory_id)}
                ]
            }
        )
