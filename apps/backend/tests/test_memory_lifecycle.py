from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.tools import MemoryWriteArguments
from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.memories import (
    MemoryRequest,
    MemorySettingsRequest,
    MemoryUpdateRequest,
    put_memory_settings,
    update_memory,
)
from mato_loop_backend.memory import retrieval as retrieval_module
from mato_loop_backend.memory.dreaming import (
    DreamCandidates,
    DreamingService,
    DreamUsageAttribution,
    _exposure_context,
    _record_candidates_tool,
)
from mato_loop_backend.memory.indexing import MemoryIndexService
from mato_loop_backend.memory.retrieval import MemoryRetrievalService, MemoryRetrieveMode
from mato_loop_backend.memory.service import (
    MemoryService,
    MemoryTierBudgetError,
    normalize_content,
)
from mato_loop_backend.models import (
    MemoryCategory,
    MemoryExposure,
    MemoryExposureStatus,
    MemoryRecord,
    MemoryRetrievalLevel,
    MemorySourceType,
    UserAccount,
)
from mato_loop_backend.settings import (
    AiSettings,
    MemorySettings,
    SettingsStore,
    TrustLevel,
)


def _rows(items):
    result = Mock()
    result.all.return_value = items
    return result


def test_memory_writers_cannot_choose_a_tier() -> None:
    payload = {
        "content": "Fakt",
        "category": "fact",
        "trustLevel": "public",
        "retrievalLevel": "retrieve",
    }
    with pytest.raises(ValidationError):
        MemoryRequest.model_validate(payload)
    with pytest.raises(ValidationError):
        MemoryWriteArguments.model_validate(
            {"content": "Fakt", "category": "fact", "retrieval_level": "retrieve"}
        )
    with pytest.raises(ValidationError):
        DreamCandidates.model_validate(
            {
                "candidates": [
                    {
                        "memory": "Fakt",
                        "category": "fact",
                        "retrievalLevel": "retrieve",
                        "evidence": [str(uuid4())],
                    }
                ]
            }
        )


def test_manual_memory_update_requires_a_tier_policy() -> None:
    payload = {
        "content": "Fakt",
        "category": "fact",
        "trustLevel": "public",
    }

    with pytest.raises(ValidationError):
        MemoryUpdateRequest.model_validate(payload)
    update = MemoryUpdateRequest.model_validate(
        {**payload, "tierPolicy": "fixed_retrieve"}
    )
    assert update.tier_policy == "fixed_retrieve"
    with pytest.raises(ValidationError):
        MemoryUpdateRequest.model_validate({**payload, "tierPolicy": "instant"})


@pytest.mark.parametrize(
    ("tier_policy", "current_level", "expected_level", "expected_locked"),
    [
        ("automatic", "instant", MemoryRetrievalLevel.INSTANT, False),
        ("fixed_instant", "retrieve", MemoryRetrievalLevel.INSTANT, True),
        ("fixed_retrieve", "instant", MemoryRetrievalLevel.RETRIEVE, True),
    ],
)
async def test_manual_memory_update_applies_tier_policy_in_same_transaction(
    monkeypatch,
    tier_policy,
    current_level,
    expected_level,
    expected_locked,
) -> None:
    item = Mock(
        id=uuid4(),
        retrieval_level=current_level,
        tier_locked=tier_policy == "automatic",
    )
    user = UserAccount(id=uuid4(), username="memory", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)
    events = []

    async def set_tier(*_args, **kwargs):
        events.append(("tier", kwargs["retrieval_level"], kwargs["locked"]))

    async def update(*_args, **_kwargs):
        events.append(("update",))

    monkeypatch.setattr(MemoryService, "lock_capacity", AsyncMock())
    monkeypatch.setattr(MemoryService, "get", AsyncMock(side_effect=[item, item]))
    monkeypatch.setattr(MemoryService, "set_tier", set_tier)
    monkeypatch.setattr(MemoryService, "update", update)
    monkeypatch.setattr(MemoryService, "usage_counts", AsyncMock(return_value={}))
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=MemorySettings()))
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.memories.memory_payload",
        lambda *_args, **_kwargs: {"id": str(item.id)},
    )
    payload = MemoryUpdateRequest.model_validate(
        {
            "content": "Aktualisierter Fakt",
            "category": "fact",
            "trustLevel": "public",
            "tierPolicy": tier_policy,
        }
    )

    result = await update_memory(item.id, payload, user, db)

    assert events == [("tier", expected_level, expected_locked), ("update",)]
    db.commit.assert_awaited_once()
    assert result == {"id": str(item.id)}


async def test_capacity_demotes_oldest_unlocked_instant_memories(monkeypatch) -> None:
    now = datetime.now(UTC)
    locked = MemoryRecord(
        id=uuid4(), content="1234", retrieval_level="instant", tier_locked=True
    )
    old = MemoryRecord(
        id=uuid4(),
        content="abc",
        retrieval_level="instant",
        tier_locked=False,
        retrieval_level_changed_at=now - timedelta(days=2),
    )
    recent = MemoryRecord(
        id=uuid4(),
        content="de",
        retrieval_level="instant",
        tier_locked=False,
        retrieval_level_changed_at=now,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = _rows([old, locked, recent])
    enqueue = AsyncMock()
    monkeypatch.setattr(MemoryIndexService, "enqueue", enqueue)

    await MemoryService.enforce_capacity(db, 6)

    assert old.retrieval_level == "retrieve"
    assert locked.retrieval_level == "instant"
    assert recent.retrieval_level == "instant"
    enqueue.assert_awaited_once_with(db, old)


async def test_capacity_rejects_locked_instant_sum_over_budget() -> None:
    item = MemoryRecord(
        id=uuid4(), content="12345", retrieval_level="instant", tier_locked=True
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = _rows([item])

    with pytest.raises(MemoryTierBudgetError):
        await MemoryService.enforce_capacity(db, 4)

    assert item.retrieval_level == "instant"


async def test_instant_unlock_restarts_grace_period(monkeypatch) -> None:
    old_changed_at = datetime.now(UTC) - timedelta(days=100)
    item = MemoryRecord(
        id=uuid4(),
        content="fixed",
        retrieval_level="instant",
        retrieval_level_changed_at=old_changed_at,
        tier_locked=True,
    )
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())

    await MemoryService.set_tier(
        db,
        item,
        retrieval_level=MemoryRetrievalLevel.INSTANT,
        locked=False,
        max_context_characters=20_000,
    )

    assert item.tier_locked is False
    assert item.tier_locked_at is None
    assert item.retrieval_level_changed_at > old_changed_at


async def test_content_change_resets_usage_tier_and_current_provenance(monkeypatch) -> None:
    _, old_hash = normalize_content("Alter Fakt")
    item = MemoryRecord(
        id=uuid4(),
        content="Alter Fakt",
        normalized_content_hash=old_hash,
        source_type="manual",
        retrieval_level="retrieve",
        tier_locked=False,
        trust_level="public",
        category="fact",
        revision=1,
        last_used_at=datetime.now(UTC),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    @asynccontextmanager
    async def nested():
        yield

    db.begin_nested = Mock(return_value=nested())
    monkeypatch.setattr(MemoryIndexService, "enqueue", AsyncMock())
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())
    session_id = uuid4()
    message_id = uuid4()
    run_id = uuid4()

    await MemoryService.update(
        db,
        item,
        content="Neuer Fakt",
        category=MemoryCategory.FACT,
        trust_level=TrustLevel.PUBLIC,
        source_type=MemorySourceType.TOOL_CALL,
        source_session_id=session_id,
        source_message_id=message_id,
        source_run_id=run_id,
        source_tool_call_id="write",
        max_context_characters=20_000,
    )

    assert item.retrieval_level == "instant"
    assert item.last_used_at is None
    assert item.source_type == "tool_call"
    assert item.source_session_id == session_id
    assert item.source_message_id == message_id
    assert item.source_run_id == run_id
    assert item.source_tool_call_id == "write"
    assert "memory_exposures" in str(db.execute.await_args_list[0].args[0])


async def test_metadata_only_change_preserves_tier_source_and_usage(monkeypatch) -> None:
    _, content_hash = normalize_content("Gleicher Fakt")
    source_session_id = uuid4()
    used_at = datetime.now(UTC)
    item = MemoryRecord(
        id=uuid4(),
        content="Gleicher Fakt",
        normalized_content_hash=content_hash,
        source_session_id=source_session_id,
        source_type="dreaming",
        retrieval_level="retrieve",
        tier_locked=False,
        trust_level="public",
        category="fact",
        revision=1,
        last_used_at=used_at,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    @asynccontextmanager
    async def nested():
        yield

    db.begin_nested = Mock(return_value=nested())
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())

    await MemoryService.update(
        db,
        item,
        content=" gleicher   fakt ",
        category=MemoryCategory.CONTEXT,
        trust_level=TrustLevel.PRIVATE,
        source_type=MemorySourceType.MANUAL,
        max_context_characters=20_000,
    )

    assert item.retrieval_level == "retrieve"
    assert item.source_type == "dreaming"
    assert item.source_session_id == source_session_id
    assert item.last_used_at == used_at
    assert db.execute.await_count == 0


async def test_usage_attribution_marks_unmentioned_exposures_unused() -> None:
    run_id = uuid4()
    used_id = uuid4()
    unused_id = uuid4()
    exposed_at = datetime.now(UTC)
    used = MemoryExposure(
        memory_id=used_id,
        memory_content_hash="used-hash",
        agent_run_id=run_id,
        status="pending",
        exposed_at=exposed_at,
    )
    unused = MemoryExposure(
        memory_id=unused_id,
        memory_content_hash="unused-hash",
        agent_run_id=run_id,
        status="pending",
        exposed_at=exposed_at,
    )
    memory = MemoryRecord(id=used_id, content="used", last_used_at=None)
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [_rows([memory]), _rows([used, unused])]

    await DreamingService._apply_usage_attributions(
        db,
        {run_id: {used_id, unused_id}},
        [DreamUsageAttribution(runId=run_id, usedMemoryIds=[used_id])],
    )

    assert used.status == MemoryExposureStatus.USED.value
    assert unused.status == MemoryExposureStatus.UNUSED.value
    assert used.evaluated_at is not None
    assert unused.evaluated_at is not None
    assert memory.last_used_at == exposed_at


async def test_record_exposure_persists_content_hash() -> None:
    memory = MemoryRecord(
        id=uuid4(),
        revision=7,
        content="Versionierter Fakt",
        normalized_content_hash="content-hash",
    )
    db = AsyncMock(spec=AsyncSession)

    await MemoryService.record_exposures(
        db,
        [memory],
        run_id=uuid4(),
        assistant_message_id=uuid4(),
    )

    statement = db.execute.await_args.args[0]
    assert "content-hash" in statement.compile().params.values()


def test_exposure_context_contains_one_catalog_and_final_response() -> None:
    run_id = uuid4()
    assistant_message_id = uuid4()
    first = MemoryRecord(id=uuid4(), revision=2, content="Erster Fakt")
    second = MemoryRecord(id=uuid4(), revision=3, content="Zweiter Fakt")

    payload = _exposure_context(
        {run_id: {first.id, second.id}},
        {run_id: assistant_message_id},
        {run_id: "Antwort mit Erinnerung"},
        {first.id: first, second.id: second},
    )

    assert {item["id"] for item in payload["memories"]} == {
        str(first.id),
        str(second.id),
    }
    assert payload["runs"] == [
        {
            "runId": str(run_id),
            "assistantMessageId": str(assistant_message_id),
            "assistantResponse": "Antwort mit Erinnerung",
            "exposedMemoryIds": sorted([str(first.id), str(second.id)]),
        }
    ]


async def test_daily_tiering_respects_pending_and_promotes_by_usage(monkeypatch) -> None:
    now = datetime.now(UTC)
    demote = MemoryRecord(
        id=uuid4(),
        content="old",
        retrieval_level="instant",
        tier_locked=False,
        retrieval_level_changed_at=now - timedelta(days=31),
    )
    pending = MemoryRecord(
        id=uuid4(),
        content="pending",
        retrieval_level="instant",
        tier_locked=False,
        retrieval_level_changed_at=now - timedelta(days=31),
    )
    promote = MemoryRecord(
        id=uuid4(),
        content="popular",
        retrieval_level="retrieve",
        tier_locked=False,
        retrieval_level_changed_at=now - timedelta(days=31),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [_rows([demote, pending, promote]), _rows([pending.id])]
    monkeypatch.setattr(
        MemoryService,
        "usage_counts",
        AsyncMock(return_value={promote.id: 3}),
    )
    change_level = AsyncMock()
    monkeypatch.setattr(MemoryService, "_change_retrieval_level", change_level)
    monkeypatch.setattr(MemoryService, "enforce_capacity", AsyncMock())

    changed = await MemoryService.apply_tiering(db, MemorySettings(), now=now)

    assert changed == 2
    changed_ids = [call.args[1].id for call in change_level.await_args_list]
    assert changed_ids == [demote.id, promote.id]


async def test_instant_index_enqueue_deletes_embedding() -> None:
    memory = MemoryRecord(
        id=uuid4(),
        content="instant",
        normalized_content_hash="hash",
        retrieval_level="instant",
    )
    db = AsyncMock(spec=AsyncSession)

    await MemoryIndexService.enqueue(db, memory)

    statement = db.execute.await_args.args[0]
    assert "DELETE FROM memory_embeddings" in str(statement)


async def test_index_reconcile_enqueues_missing_retrieve_embedding(monkeypatch) -> None:
    memory_id = uuid4()
    selected = Mock()
    selected.all.return_value = [(memory_id, "hash")]
    removed = Mock(rowcount=0)
    db = AsyncMock(spec=AsyncSession)
    db.execute.side_effect = [selected, removed]
    db.get.side_effect = [None, MemoryRecord(
        id=memory_id,
        normalized_content_hash="hash",
        retrieval_level=MemoryRetrievalLevel.RETRIEVE.value,
    )]
    enqueue = AsyncMock()
    monkeypatch.setattr(MemoryIndexService, "enqueue", enqueue)

    changed = await MemoryIndexService.reconcile(db)

    assert changed == 1
    queued = enqueue.await_args.args[1]
    assert queued.retrieval_level == MemoryRetrievalLevel.RETRIEVE.value


async def test_search_always_filters_to_retrieve(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = _rows([])
    monkeypatch.setattr(
        retrieval_module,
        "get_runtime_config",
        lambda: SimpleNamespace(embedding_enabled=False),
    )

    await MemoryRetrievalService.retrieve(
        db,
        mode=MemoryRetrieveMode.SEARCH,
        query="Fakt",
        readable_by=TrustLevel.PRIVATE,
        retrieval_level=None,
    )

    statement = db.scalars.await_args.args[0]
    assert "memories.retrieval_level" in str(statement)
    assert "retrieve" in statement.compile().params.values()


def test_dreaming_usage_contract_is_added_only_for_pending_exposures() -> None:
    run_id = uuid4()
    memory_id = uuid4()
    plain = _record_candidates_tool()["parameters"]
    attributed_tool = _record_candidates_tool({run_id: {memory_id}})
    attributed = attributed_tool["parameters"]

    assert "usageAttributions" not in plain["properties"]
    assert "usageAttributions" in attributed["required"]
    usage = attributed["properties"]["usageAttributions"]
    assert "minItems" not in usage
    assert usage["maxItems"] == 500
    assert "exactly one usage attribution" in attributed_tool["description"]


async def test_settings_budget_error_uses_stable_api_error(monkeypatch) -> None:
    settings = MemorySettings()

    async def load(_db, settings_type, **_kwargs):
        return settings if settings_type is MemorySettings else AiSettings()

    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(
        MemoryService,
        "enforce_capacity",
        AsyncMock(side_effect=MemoryTierBudgetError),
    )
    user = UserAccount(id=uuid4(), username="memory", password_hash="hash")

    with pytest.raises(ApiError) as exc_info:
        await put_memory_settings(
            MemorySettingsRequest(maxContextCharacters=100),
            user,
            AsyncMock(spec=AsyncSession),
        )

    assert exc_info.value.code == "memory.tierBudgetExceeded"
