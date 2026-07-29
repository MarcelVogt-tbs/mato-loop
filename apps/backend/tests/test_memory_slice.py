import json
from datetime import UTC, datetime
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
    _reconciliation_tool,
    _record_candidates_tool,
    dreaming_availability,
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
    MemorySourceMessage,
    UserAccount,
)
from mato_loop_backend.providers import ToolCall
from mato_loop_backend.providers.base import ProviderError, ProviderResult
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AiSettings,
    IntegrationSettings,
    MemorySettings,
    SettingsStore,
    TrustLevel,
)
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelConfig,
    effective_model_pseudonymization,
    effective_model_trust,
)
from mato_loop_backend.workspace import RootFileService


def _root_files(tmp_path, session: ChatSession) -> RootFileService:
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    return RootFileService(
        session.id,
        session_storage=storage,
        workspace_home=tmp_path,
        working_directory=session.working_directory,
    )


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
        max_characters=7,
    )

    assert [item.content for item in selected] == ["abc", "de", "f"]
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
        trust_level=TrustLevel.PRIVATE,
        session_id=uuid4(),
        run_id=uuid4(),
        evidence_message_ids=[evidence_id],
        max_context_characters=20_000,
    )

    assert created.duplicate is True
    assert existing.trust_level == "private"
    evidence_insert = next(
        call.args[0]
        for call in db.execute.await_args_list
        if "memory_source_messages" in str(call.args[0])
    )
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
            max_context_characters=20_000,
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
        }
    )

    assert valid.category == MemoryCategory.FACT
    assert "retrieval_level" not in type(valid).model_fields
    with pytest.raises(ValidationError):
        MemoryWriteArguments.model_validate(
            {"content": "Der Nutzer kommt aus Ascholding.", "category": "identity"}
        )
    create = MemoryWriteArguments.model_validate(
        {
            "action": "create",
            "memory_id": str(uuid4()),
            "expected_revision": 7,
            "content": "Der Nutzer kommt aus Ascholding.",
            "category": "identity",
        }
    )
    assert create.memory_id is None
    assert create.expected_revision is None
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
        readable_by=TrustLevel.PUBLIC,
        write_trust_level=TrustLevel.PRIVATE,
        session_id=uuid4(),
        evidence_message_id=evidence_id,
        run_id=uuid4(),
        tool_call_id="memory",
        max_context_characters=20_000,
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
        readable_by=TrustLevel.PUBLIC,
        write_trust_level=TrustLevel.PUBLIC,
        session_id=uuid4(),
        evidence_message_id=uuid4(),
        run_id=uuid4(),
        tool_call_id="memory",
        max_context_characters=20_000,
    )

    assert result.conflict is True
    update_memory.assert_not_awaited()


async def test_tool_update_merges_exact_duplicate_and_preserves_evidence(
    monkeypatch,
) -> None:
    source_message_id = uuid4()
    linked_evidence_id = uuid4()
    current_evidence_id = uuid4()
    stale = MemoryRecord(
        id=uuid4(),
        source_message_id=source_message_id,
        source_type="dreaming",
        retrieval_level="retrieve",
        trust_level="private",
        category="fact",
        content="Der Benutzer kommt aus Aschodling.",
        revision=1,
    )
    canonical = MemoryRecord(
        id=uuid4(),
        source_type="tool_call",
        retrieval_level="retrieve",
        trust_level="public",
        category="context",
        content="Der Benutzer kommt aus Wolfratshausen.",
        revision=2,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [stale, canonical]
    linked = Mock()
    linked.all.return_value = [linked_evidence_id]
    db.scalars.return_value = linked
    update_memory = AsyncMock(side_effect=MemoryDuplicateError)
    monkeypatch.setattr(MemoryService, "update", update_memory)
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())
    change_level = AsyncMock()
    monkeypatch.setattr(MemoryService, "_change_retrieval_level", change_level)

    result = await MemoryService.update_tool(
        db,
        memory_id=stale.id,
        expected_revision=1,
        content=canonical.content,
        category=MemoryCategory.FACT,
        readable_by=TrustLevel.PRIVATE,
        write_trust_level=TrustLevel.PRIVATE,
        session_id=uuid4(),
        evidence_message_id=current_evidence_id,
        run_id=uuid4(),
        tool_call_id="memory",
        max_context_characters=20_000,
    )

    assert result.item is canonical
    assert result.updated is True
    assert result.duplicate is False
    assert canonical.trust_level == TrustLevel.PRIVATE.value
    assert canonical.revision == 3
    assert canonical.source_type == "tool_call"
    assert canonical.source_message_id == current_evidence_id
    change_level.assert_awaited_once()
    evidence_insert = next(
        call.args[0]
        for call in db.execute.await_args_list
        if "memory_source_messages" in str(call.args[0])
    )
    evidence_params = evidence_insert.compile().params
    assert {
        evidence_params["message_id_m0"],
        evidence_params["message_id_m1"],
        evidence_params["message_id_m2"],
    } == {linked_evidence_id, source_message_id, current_evidence_id}
    assert {
        evidence_params["memory_id_m0"],
        evidence_params["memory_id_m1"],
        evidence_params["memory_id_m2"],
    } == {canonical.id}
    db.delete.assert_awaited_once_with(stale)


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
        },
    )
    session = ChatSession(id=uuid4(), title="Test", workspace_access="read-only")
    context = ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=_root_files(tmp_path, session),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
        assistant_message_id=uuid4(),
        memory_read_enabled=True,
        memory_write_enabled=True,
    )
    monkeypatch.setattr(
        SettingsStore, "load", AsyncMock(return_value=MemorySettings())
    )

    result = await tool_registry.execute(call, context)

    assert json.loads(result.content) == {
        "stored": False,
        "updated": False,
        "duplicate": True,
    }


def test_memory_tools_are_gated_independently_from_workspace_write_mode() -> None:
    without_memory = tool_registry.schemas(IntegrationSettings(), "read-only")
    with_read_memory = tool_registry.schemas(
        IntegrationSettings(), "read-only", memory_enabled=True
    )
    with_full_memory = tool_registry.schemas(
        IntegrationSettings(), "read-only", memory_enabled=True, memory_write_enabled=True
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
        root_files=_root_files(tmp_path, source),
        integration_settings=IntegrationSettings(),
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
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())
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


async def test_dreaming_source_query_excludes_runtime_error_messages() -> None:
    rows = Mock()
    rows.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = rows

    await DreamingService._next_messages(db, uuid4(), None, 20)

    statement = db.scalars.await_args.args[0]
    assert "messages.error_code IS NULL" in str(statement)


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
                    "evidenceMessageIds": [str(message_id)],
                }
            ]
        }
    )

    assert output.candidates[0].evidence_message_ids == [message_id]
    assert output.candidates[0].category == MemoryCategory.FACT

    with pytest.raises(ValueError):
        DreamCandidates.model_validate(
            {
                "candidates": [
                    {
                        "content": "   ",
                        "category": "fact",
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
        "evidence",
    }
    assert DreamCandidates.model_validate(
        {
            "candidates": [
                {
                    "memory": "Der Nutzer ist verheiratet.",
                    "category": "identity",
                    "evidence": [str(uuid4())],
                }
            ]
        }
    ).candidates[0].content == "Der Nutzer ist verheiratet."


def test_dreaming_tool_schemas_are_inline_for_local_models() -> None:
    candidate_schema = _record_candidates_tool()["parameters"]
    candidate = candidate_schema["properties"]["candidates"]["items"]
    reconciliation_schema = _reconciliation_tool(2)["parameters"]
    decisions = reconciliation_schema["properties"]["decisions"]

    assert "$defs" not in candidate_schema
    assert "$ref" not in json.dumps(candidate_schema)
    assert candidate_schema["required"] == ["candidates"]
    assert set(candidate["required"]) == {
        "memory",
        "category",
        "evidence",
    }
    assert "$defs" not in reconciliation_schema
    assert "$ref" not in json.dumps(reconciliation_schema)
    assert reconciliation_schema["required"] == ["decisions"]
    assert "minItems" not in decisions
    assert decisions["maxItems"] == 2
    assert decisions["items"]["properties"]["index"] == {
        "type": "integer",
        "minimum": 0,
        "maximum": 1,
    }
    assert set(decisions["items"]["required"]) == {
        "index",
        "action",
        "memoryId",
        "memory",
        "category",
    }
    assert decisions["items"]["properties"]["memoryId"]["anyOf"][-1] == {
        "type": "null"
    }


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

    output = await _extract_candidates(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        system_prompt=DREAMING_SYSTEM_PROMPT,
        source=f"MESSAGE {evidence_id} [USER]\nmeine Frau und unser Kind",
        instant_memories=[],
        max_candidates=10,
        evidence_ids={evidence_id},
        user_evidence_ids={evidence_id},
    )

    assert output.candidates[0].content == "Der Nutzer ist verheiratet und hat ein Kind."
    assert complete.await_count == 2
    retry_messages = complete.await_args_list[1].kwargs["messages"]
    assert retry_messages[-1]["role"] == "tool"
    assert "memory, category, and evidence" in retry_messages[-1]["content"]
    assert "usage attribution" not in retry_messages[-1]["content"]


async def test_dreaming_repairs_assistant_only_evidence(monkeypatch) -> None:
    user_id = uuid4()
    assistant_id = uuid4()
    provider = LlmProviderConfig(
        name="Lokal", adapter_type="openai-compatible", trust_level="private"
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="assistant-only",
                        name="record_memory_candidates",
                        arguments={
                            "candidates": [
                                {
                                    "memory": "Der Nutzer bevorzugt kurze Antworten.",
                                    "category": "preference",
                                    "evidence": [str(assistant_id)],
                                }
                            ]
                        },
                    )
                ]
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="user-supported",
                        name="record_memory_candidates",
                        arguments={
                            "candidates": [
                                {
                                    "memory": "Der Nutzer bevorzugt kurze Antworten.",
                                    "category": "preference",
                                    "evidence": [str(user_id)],
                                }
                            ]
                        },
                    )
                ]
            ),
        ]
    )
    monkeypatch.setattr(ProviderService, "complete", complete)

    output = await _extract_candidates(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        system_prompt=DREAMING_SYSTEM_PROMPT,
        source=(
            f"MESSAGE {user_id} [USER]\nBitte antworte kurz.\n\n"
            f"MESSAGE {assistant_id} [ASSISTANT]\nVerstanden."
        ),
        instant_memories=[],
        max_candidates=10,
        evidence_ids={user_id, assistant_id},
        user_evidence_ids={user_id},
    )

    assert output.candidates[0].evidence_message_ids == [user_id]
    assert complete.await_count == 2
    assert "at least one USER message UUID" in complete.await_args_list[1].kwargs[
        "messages"
    ][-1]["content"]


async def test_dreaming_repairs_missing_usage_attribution(monkeypatch) -> None:
    user_id = uuid4()
    assistant_id = uuid4()
    run_id = uuid4()
    memory = MemoryRecord(
        id=uuid4(),
        source_type="manual",
        retrieval_level="instant",
        trust_level="private",
        category="fact",
        revision=1,
        content="Der Nutzer verwendet OpenCode.",
    )
    provider = LlmProviderConfig(
        name="Lokal", adapter_type="openai-compatible", trust_level="private"
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="missing-attribution",
                        name="record_memory_candidates",
                        arguments={"candidates": []},
                    )
                ]
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="valid-attribution",
                        name="record_memory_candidates",
                        arguments={
                            "candidates": [],
                            "usageAttributions": [
                                {
                                    "runId": str(run_id),
                                    "usedMemoryIds": [str(memory.id)],
                                }
                            ],
                        },
                    )
                ]
            ),
        ]
    )
    monkeypatch.setattr(ProviderService, "complete", complete)

    output = await _extract_candidates(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        system_prompt=DREAMING_SYSTEM_PROMPT,
        source=(
            f"MESSAGE {user_id} [USER]\nHilf mir mit meiner Coding-Lösung.\n\n"
            f"MESSAGE {assistant_id} [ASSISTANT]\nDu verwendest OpenCode."
        ),
        instant_memories=[memory],
        max_candidates=10,
        evidence_ids={user_id, assistant_id},
        user_evidence_ids={user_id},
        exposed_by_run={run_id: {memory.id}},
        exposure_anchors={run_id: assistant_id},
        exposure_responses={run_id: "Du verwendest OpenCode."},
        exposed_memories={memory.id: memory},
    )

    assert output.usage_attributions[0].used_memory_ids == [memory.id]
    assert complete.await_count == 2
    retry = complete.await_args_list[1].kwargs["messages"][-1]["content"]
    assert "exactly one attribution" in retry


async def test_dreaming_uses_instant_context_and_merges_candidate_calls(
    monkeypatch,
) -> None:
    first_evidence_id = uuid4()
    second_evidence_id = uuid4()
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
    complete = AsyncMock(
        return_value=ProviderResult(
            tool_calls=[
                ToolCall(
                    id="record-1",
                    name="record_memory_candidates",
                    arguments={
                        "candidates": [
                            {
                                "memory": "Der Nutzer hat eine Familie.",
                                "category": "identity",
                                "evidence": [str(first_evidence_id)],
                            }
                        ]
                    },
                ),
                ToolCall(
                    id="record-2",
                    name="record_memory_candidates",
                    arguments={
                        "candidates": [
                            {
                                "memory": " der nutzer hat eine familie. ",
                                "category": "identity",
                                "evidence": [str(second_evidence_id)],
                            }
                        ]
                    },
                ),
            ]
        )
    )
    monkeypatch.setattr(ProviderService, "complete", complete)
    db = AsyncMock(spec=AsyncSession)

    output = await _extract_candidates(
        db,
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        system_prompt=DREAMING_SYSTEM_PROMPT,
        source=(
            f"MESSAGE {first_evidence_id} [USER]\nMeine Familie ist wichtig.\n\n"
            f"MESSAGE {second_evidence_id} [ASSISTANT]\nVerstanden."
        ),
        instant_memories=[instant],
        max_candidates=10,
        evidence_ids={first_evidence_id, second_evidence_id},
        user_evidence_ids={first_evidence_id},
    )

    assert len(output.candidates) == 1
    assert output.candidates[0].content == "Der Nutzer hat eine Familie."
    assert output.candidates[0].evidence_message_ids == [
        first_evidence_id,
        second_evidence_id,
    ]
    request = complete.await_args.kwargs
    assert instant.content in request["messages"][1]["content"]
    assert [tool["name"] for tool in request["tools"]] == [
        "record_memory_candidates"
    ]


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
    )

    assert result.decisions[0].action == "unchanged"
    assert complete.await_count == 2
    assert complete.await_args_list[0].kwargs["messages"][0]["content"] == (
        RECONCILIATION_SYSTEM_PROMPT
    )


async def test_dreaming_reconciliation_merges_parallel_outputs(monkeypatch) -> None:
    memory_id = uuid4()
    provider = LlmProviderConfig(
        name="Lokal", adapter_type="openai-compatible", trust_level="private"
    )
    model = ModelConfig(provider_id=provider.id, model_id="model")
    complete = AsyncMock(
        return_value=ProviderResult(
            tool_calls=[
                ToolCall(
                    id="decision-1",
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
                ),
                ToolCall(
                    id="decision-2",
                    name="reconcile_memory_candidates",
                    arguments={"decisions": [{"index": 1, "action": "create"}]},
                ),
            ]
        )
    )
    monkeypatch.setattr(ProviderService, "complete", complete)

    result = await _reconcile_candidates(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        input_trust=TrustLevel.PRIVATE,
        source="{}",
        candidate_count=2,
        supplied_ids={memory_id},
    )

    assert [decision.candidate_index for decision in result.decisions] == [0, 1]
    assert [decision.action for decision in result.decisions] == ["unchanged", "create"]
    complete.assert_awaited_once()


def test_dreaming_system_prompt_remains_isolated_from_agent_context() -> None:
    assert "durable cross-session knowledge" in DREAMING_SYSTEM_PROMPT
    assert "empty candidates array" in DREAMING_SYSTEM_PROMPT
    assert "one-off questions" in DREAMING_SYSTEM_PROMPT
    assert "single request does not establish" in DREAMING_SYSTEM_PROMPT
    assert "at least one user message ID" in DREAMING_SYSTEM_PROMPT
    assert "memory_retrieve" not in DREAMING_SYSTEM_PROMPT
    assert "record_memory_candidates" in DREAMING_SYSTEM_PROMPT
    assert "retrieval level" not in DREAMING_SYSTEM_PROMPT
    assert "web_search" not in DREAMING_SYSTEM_PROMPT


def test_dreaming_reconciliation_requires_complete_safe_updates() -> None:
    memory_id = uuid4()
    create = DreamReconciliation.model_validate(
        {
            "decisions": [
                {
                    "index": 0,
                    "action": "create",
                    "memory": "Vom Modell wiederholter Kandidat",
                }
            ]
        }
    ).decisions[0]
    result = DreamReconciliation.model_validate(
        {
            "decisions": [
                {
                    "candidateIndex": 0,
                    "action": "update",
                    "memoryId": str(memory_id),
                    "content": "Aktuelle Präferenz",
                    "category": "preference",
                }
            ]
        }
    )

    assert create.content is None
    assert result.decisions[0].memory_id == memory_id
    assert "Reference only supplied memory IDs" in RECONCILIATION_SYSTEM_PROMPT
    assert "broader or weaker restatement" in RECONCILIATION_SYSTEM_PROMPT
    assert "usefully connects" not in RECONCILIATION_SYSTEM_PROMPT
    with pytest.raises(ValidationError):
        DreamReconciliation.model_validate(
            {
                "decisions": [
                    {"candidateIndex": 0, "action": "update", "memoryId": str(memory_id)}
                ]
            }
        )
