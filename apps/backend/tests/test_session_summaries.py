import json
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from mato_loop_backend.agent.memory_context import (
    MemoryCache,
    historical_memory_contexts,
    memory_context_snapshot,
    prepare_memory_context,
)
from mato_loop_backend.agent.projection import _projection_messages
from mato_loop_backend.agent.runtime_memory import (
    _bounded_automatic_payload,
    _cross_session_context_text,
)
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.memory.dreaming import DreamingService
from mato_loop_backend.memory.embeddings import embedding_client
from mato_loop_backend.memory.episode_indexing import (
    EpisodeIndexService,
    replace_summary_search_chunk,
)
from mato_loop_backend.memory.episode_retrieval import EpisodeReference, EpisodeRetrievalService
from mato_loop_backend.memory.policy import EffectiveMemory
from mato_loop_backend.memory.service import MemoryService
from mato_loop_backend.memory.session_summaries import (
    invalidate_summaries,
    prepare_summary,
    process_summary,
    selected_summaries,
    validated_summary,
)
from mato_loop_backend.models import (
    ApprovalRequest,
    ChatSession,
    MemoryDreamRun,
    MemoryDreamState,
    MemoryDreamTrigger,
    MemoryRecord,
    MemorySourceMessage,
    Message,
    Project,
    UserAccount,
)
from mato_loop_backend.models.memory import MemoryRetirement
from mato_loop_backend.providers import service as provider_module
from mato_loop_backend.providers.base import ProviderError, ProviderResult, ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AgentRuntimeSettings,
    AiSettings,
    MemorySettings,
    SettingsStore,
    TrustLevel,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


def summary_content(message_id, text="Port 19482; COLD-BOOT offen"):
    return {"overview": text, "items": [{"text": text, "status": "open", "evidence": [str(message_id)]}]}


def test_summary_rejects_invented_evidence():
    with pytest.raises(ProviderError, match="Invalid summary evidence"):
        validated_summary([ToolCall("summary", "record_session_summary", summary_content(uuid4()))], {uuid4()})


def test_older_summary_does_not_replace_newer_topical_evidence():
    now = datetime.now(UTC)
    reference = EpisodeReference(
        session_id=uuid4(), session_title="Project", anchor_message_id=uuid4(),
        anchor_created_at=now, anchor_role="user", anchor_content="Neuer Port: 20563", excerpt=(),
    )
    summary = {
        **summary_content(uuid4()), "sourceRevision": 0, "coveredThroughMessageId": str(uuid4()),
        "coveredThroughAt": (now - timedelta(hours=1)).isoformat(),
    }
    _, episodes, _ = _bounded_automatic_payload(
        [], [reference], [], max_characters=4096, project_session=True,
        session_summaries={reference.session_id: summary},
    )
    assert episodes[0]["anchorMessage"]["preview"] == "Neuer Port: 20563"


@pytest.fixture
async def source(request):
    if not request.config.getoption("--memory-db"):
        pytest.skip("requires the migrated development PostgreSQL database")
    engine = create_async_engine(get_runtime_config().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.begin()
            async with AsyncSession(connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as db:
                user = UserAccount(username=f"summary-test-{uuid4().hex}", password_hash="test")
                db.add(user)
                await db.flush()
                project = Project(user_id=user.id, name="Summary test")
                db.add(project)
                await db.flush()
                session = ChatSession(user_id=user.id, project_id=project.id, trust_level="public")
                current = ChatSession(user_id=user.id, project_id=project.id, trust_level="public")
                db.add_all([session, current])
                await db.flush()
                now = datetime.now(UTC) - timedelta(hours=1)
                messages = [Message(session_id=session.id, role=role, content=content,
                                    created_at=now + timedelta(minutes=index)) for index, (role, content) in enumerate([
                    ("user", "Port 19482 ist konfiguriert. COLD-BOOT bleibt offen."), ("assistant", "Notiert."),
                ])]
                db.add_all(messages)
                settings = MemorySettings(session_summaries_enabled=True, session_summaries_enabled_since=datetime.now(UTC))
                await SettingsStore.save(db, settings, user_id=user.id)
                await db.flush()
                yield SimpleNamespace(db=db, user=user, project=project, session=session,
                                      current=current, messages=messages, settings=settings)
    finally:
        await embedding_client.close()
        await engine.dispose()


async def test_session_discovery_sends_sonobin_context_without_a_memory(source):
    p = source
    p.session.title = "Musikwiedergabe"
    p.messages[0].content = "Ich entwickle SonoBin. Die Wiedergabe geht auch neben der Navigation."
    p.session.session_summary = {
        **summary_content(p.messages[0].id, "Wiedergabe in SonoBin; geprüft mit dem Android-Auto-Client."),
        "sourceRevision": 0, "coveredThroughMessageId": str(p.messages[-1].id),
        "coveredThroughAt": p.messages[-1].created_at.isoformat(),
    }
    await p.db.flush()
    for title, content in [
        ("Deployment", "Docker-Image für SonoBin auf latest lassen."),
        ("Android Auto", "Wir suchen einen Android-Auto-Emulator für die Navigation."),
    ]:
        distractor = ChatSession(user_id=p.user.id, project_id=p.project.id, title=title, trust_level="public")
        p.db.add(distractor)
        await p.db.flush()
        p.db.add(Message(session_id=distractor.id, role="user", content=content))
    await p.db.flush()
    await EpisodeIndexService.reconcile(p.db)  # Also backfills already saved summaries.

    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="test")
    earlier = Message(id=uuid4(), role="user", content="Emulator für die Navigation", attachments=[])
    latest = Message(id=uuid4(), role="user", content="Und die Wiedergabe in sonobin?", attachments=[])
    cache = MemoryCache()
    args = dict(model=model, trust_level=TrustLevel.PUBLIC, memory_state=EffectiveMemory(True, True, True),
                settings=p.settings.model_copy(update={"automatic_session_snippet_limit": 1}),
                runtime_settings=AgentRuntimeSettings(), excluded_content_hashes=frozenset(),
                embedding_enabled=lambda: False, cache=cache)
    turn = await prepare_memory_context(p.db, p.current, [earlier, latest], **args)
    text, memory_count, episode_count = turn.candidates[0]
    prepared = await ProviderService.prepare_request(
        provider=provider, model=model, tools=[], pseudonymization_enabled=False,
        messages=_projection_messages("System", None, [latest], TrustLevel.PUBLIC, memory_context=text),
    )
    wire = json.dumps(provider_module._chat_body(prepared.request)["messages"], ensure_ascii=False)
    assert (memory_count, episode_count) == (0, 1)
    assert "Ich entwickle SonoBin" in wire
    assert "geprüft mit dem Android-Auto-Client" in wire
    p.session.session_summary = {**p.session.session_summary, "overview": "Regenerated summary"}
    await p.db.flush()
    assert (await prepare_memory_context(p.db, p.current, [earlier, latest], **args)).candidates[0] == turn.candidates[0]

    # A distinct term that occurs only in the summary can find the session,
    # but not when summaries are disabled or the source is invalidated.
    summary_args = dict(owner_id=p.user.id, current_session_id=p.current.id, readable_by=TrustLevel.PUBLIC,
                        query="Android-Auto-Client", anchor_only=True, semantic_enabled=False,
                        project_id=p.project.id, full_sessions_only=True)
    await replace_summary_search_chunk(p.db, p.session.id, summary=p.session.session_summary, anchor=p.messages[-1])
    await p.db.flush()
    assert (await EpisodeRetrievalService.retrieve(p.db, **summary_args, include_summaries=True)).references[0].summary_anchor
    assert not (await EpisodeRetrievalService.retrieve(p.db, **summary_args)).references
    await invalidate_summaries(p.db, p.session.id)
    assert not (await EpisodeRetrievalService.retrieve(p.db, **summary_args, include_summaries=True)).references


@pytest.mark.parametrize("live", [False, True], ids=["contract", "qwen"])
async def test_dreaming_requires_independent_personal_evidence(source, monkeypatch, live):
    base_url = os.getenv("MATO_LOOP_TEST_DREAMING_URL")
    if live and not base_url:
        pytest.skip("requires an explicit local Qwen endpoint")
    p = source
    provider = LlmProviderConfig(
        name="Dreaming test", adapter_type="openai-compatible", trust_level="private",
        config={"base_url": base_url} if live else {},
    )
    model = ModelConfig(provider_id=provider.id, model_id="qwen3.8-27b", supports_tools=True)
    ai = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    monkeypatch.setattr("mato_loop_backend.memory.dreaming.load_effective_ai", AsyncMock(return_value=ai))
    await SettingsStore.save(p.db, MemorySettings(dreaming_remove_policy="auto"), user_id=p.user.id)

    calls = []
    live_outputs = []
    real_complete = ProviderService.complete

    async def complete(db, **kwargs):
        tool = kwargs["tools"][0]["name"]
        text = kwargs["messages"][1]["content"]
        calls.append(tool)
        if live:
            result = await real_complete(db, **kwargs)
            live_outputs.append((tool, [call.arguments for call in result.tool_calls]))
            return result
        if tool == "record_memory_candidates":
            message_id, quote = re.search(r"MESSAGE ([a-f0-9-]+) \[USER\][^\n]*\n([^\n]+)", text.rsplit("NEW MESSAGES\n", 1)[-1]).groups()
            admission, category, remove = "clear", "fact", False
            if "Paperless" in quote:
                subject, memory, admission = "Paperless", "Betreibt Paperless zu Hause.", "incidental"
            elif "bootet" in quote:
                # A quoted but ineligible result must still pass the separate relevance check.
                subject, memory = "Xiaomi", quote
            elif "Sohn" in quote:
                subject, memory, category = "Sohn", "Hat einen Sohn.", "identity"
            elif "Geburtstag" in quote:
                subject, memory = "Frau", "Die Ehefrau hat am 12. Mai Geburtstag."
            elif "verkauft" in quote:
                subject, memory, admission, remove = "Acme X1", "Besitzt ein Acme X1.", "retire", True
            elif "Acme X1" in quote or "Acme-X1" in quote:
                subject = "Acme-X1" if "Acme-X1" in quote else "Acme X1"
                memory = f"Besitzt ein {subject}."
            else:
                subject, memory = "AI-PC", "Der AI-PC hat 192 GB RAM." if "192" in quote else "Der AI-PC hat 128 GB RAM."
            arguments = {"candidates": [{"remove": remove, "evidence": [message_id], "subject": subject,
                                         "memory": memory, "category": category, "quote": quote, "admission": admission}]}
        elif tool == "confirm_personal_fact":
            statements = json.loads(text)["otherStatements"]
            arguments = {"indices": [1, 2], "quotes": statements[:2]}
        else:
            decisions = []
            for item in json.loads(text)["candidates"]:
                action, targets = "create", []
                if item.get("retire"):
                    action = "retire"
                    targets = [m["id"] for m in item["possibleMemories"] if "Acme X1" in m["content"]]
                elif "bootet" in item["memory"]:
                    action = "skip"
                elif "192" in item["memory"]:
                    action = "update"
                    targets = [m["id"] for m in item["possibleMemories"] if "128 GB" in m["content"]]
                decisions.append({"index": item["index"], "action": action, "memoryIds": targets,
                                  "category": item["category"], "memory": item["memory"]})
            arguments = {"decisions": decisions}
        return ProviderResult(tool_calls=[ToolCall("test", tool, arguments)])

    monkeypatch.setattr(ProviderService, "complete", complete)

    async def turn(text, n, *, session=None, trust="public"):
        if session is None:
            session = ChatSession(user_id=p.user.id, trust_level=trust)
            p.db.add(session)
            await p.db.flush()
        now = datetime.now(UTC) - timedelta(hours=2) + timedelta(minutes=2*n)
        user = Message(session_id=session.id, role="user", content=text, created_at=now, trust_level=trust)
        reply = Message(session_id=session.id, role="assistant", content="Notiert.", created_at=now + timedelta(seconds=1))
        p.db.add_all([user, reply])
        await p.db.flush()
        await EpisodeIndexService.enqueue(p.db, user)
        runs = await DreamingService.enqueue(p.db, trigger=MemoryDreamTrigger.MANUAL, session_id=session.id, user_id=p.user.id)
        assert len(runs) == 1
        await DreamingService.process_next(p.db)
        assert runs[0].status == "completed", (runs[0].status, runs[0].error)
        rows = list((await p.db.scalars(select(MemoryRecord).where(MemoryRecord.user_id == p.user.id))).all())
        return session, rows

    _, rows = await turn("Mein Xiaomi bootet ohne Akku.", 1)
    assert not rows, live_outputs
    _, rows = await turn("Ich habe einen Sohn.", 2)
    assert len(rows) == 1 and "Sohn" in rows[0].content, live_outputs
    _, rows = await turn("Meine Frau hat am 12. Mai Geburtstag.", 3)
    assert len(rows) == 2 and any("12. Mai" in row.content for row in rows), live_outputs
    _, rows = await turn("Mein AI-PC hat dauerhaft 128 GB RAM.", 4)
    assert len(rows) == 3 and any("128 GB" in row.content for row in rows), live_outputs
    _, rows = await turn("Ich habe meinen AI-PC aufgerüstet. Mein AI-PC hat jetzt 192 statt 128 GB RAM.", 5)
    assert len(rows) == 3 and any("192 GB" in row.content for row in rows), repr(([row.content for row in rows], live_outputs[-4:]))
    assert not any("128 GB" in row.content for row in rows), repr(([row.content for row in rows], live_outputs[-3:]))
    _, rows = await turn("Ich besitze ein Acme X1 als mein Smartphone.", 6)
    assert len(rows) == 4 and any("Acme X1" in row.content for row in rows), live_outputs
    if not live:
        await SettingsStore.save(p.db, MemorySettings(dreaming_remove_policy="ask"), user_id=p.user.id)
    _, rows = await turn("Ich habe mein Acme X1 verkauft. Ich besitze es nicht mehr.", 8)
    if not live:
        assert len(rows) == 4
        assert await p.db.scalar(select(MemoryRetirement).where(MemoryRetirement.user_id == p.user.id)) is None
        approval = await p.db.scalar(select(ApprovalRequest).where(
            ApprovalRequest.user_id == p.user.id, ApprovalRequest.action_kind == "memory_remove",
        ))
        assert approval is not None
        assert await MemoryService.apply_approved_change(p.db, approval, owner_id=p.user.id) == "applied"
        rows = list((await p.db.scalars(select(MemoryRecord).where(MemoryRecord.user_id == p.user.id))).all())
    assert len(rows) == 3 and not any("Acme X1" in row.content for row in rows), live_outputs
    older_statement = "Mein Acme X1 ist mein Smartphone." if live else "Mein Acme-X1 ist mein Smartphone."
    _, rows = await turn(older_statement, 7)
    assert len(rows) == 3 and not any("Acme X1" in row.content for row in rows), live_outputs
    if not live:
        _, rows = await turn("Wenn ich zu Hause etwas scanne, landet es bei mir in Paperless.", 9, trust="private")
        assert len(rows) == 3
        _, rows = await turn("Zu Hause hat Paperless meinen letzten Scan einsortiert.", 10)
        assert len(rows) == 3
        _, rows = await turn("Die Scans von zu Hause landen bei mir immer wieder in Paperless.", 11)
        assert len(rows) == 3  # The private source may not confirm this public candidate.
        _, rows = await turn("Meine Dokumente kommen daheim über den Scanner zu Paperless.", 12)
        setup = next(row for row in rows if "Paperless" in row.content)
        assert len(rows) == 4 and calls.count("confirm_personal_fact") == 1
        assert len((await p.db.scalars(select(MemorySourceMessage).where(MemorySourceMessage.memory_id == setup.id))).all()) == 3


async def test_incremental_summary_keeps_checkpoint_on_invalid_output(source, monkeypatch):
    p = source
    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="qwen3.8-27b")
    complete = AsyncMock()
    monkeypatch.setattr(ProviderService, "complete", complete)

    async def generate():
        manifest, messages = await prepare_summary(p.db, p.session, p.settings, model, explicit=True)
        run = MemoryDreamRun(session_id=p.session.id, status="running", trigger="manual", summary_status="pending",
                             source_from_message_id=messages[0].id, source_to_message_id=messages[-1].id, summary=manifest)
        p.db.add(run)
        await p.db.flush()
        await process_summary(p.db, run, p.session, model, provider, reasoning_effort="medium")
        return run

    # Enabling summaries must not silently backfill an inactive old session.
    assert await prepare_summary(p.db, p.session, p.settings, model, explicit=False) == (None, [])
    complete.return_value = ProviderResult(tool_calls=[ToolCall("summary", "record_session_summary", summary_content(p.messages[0].id))])
    await generate()
    assert complete.await_args.kwargs["reasoning_effort"] == "medium"
    previous = p.session.session_summary
    correction = Message(session_id=p.session.id, role="user", content="Jetzt gilt Port 20563; COLD-BOOT ist erfolgreich.",
                         created_at=datetime.now(UTC) - timedelta(minutes=20))
    answer = Message(session_id=p.session.id, role="assistant", content="Aktualisiert.", created_at=correction.created_at + timedelta(seconds=1))
    p.db.add_all([correction, answer])
    await p.db.flush()
    complete.return_value = ProviderResult(tool_calls=[ToolCall("summary", "record_session_summary", summary_content(uuid4()))])
    with pytest.raises(ProviderError):
        await generate()
    assert p.session.session_summary == previous
    payload = json.loads(complete.await_args.kwargs["messages"][1]["content"])
    assert [item["id"] for item in payload["newMessages"]] == [str(correction.id), str(answer.id)]
    assert payload["previousSummary"]["overview"] == previous["overview"]
    complete.return_value = ProviderResult(tool_calls=[ToolCall("summary", "record_session_summary", summary_content(correction.id, "Port 20563 verifiziert"))])
    await generate()
    assert p.session.session_summary["coveredThroughMessageId"] == str(answer.id)


@pytest.mark.parametrize("trigger", [MemoryDreamTrigger.MANUAL, MemoryDreamTrigger.SCHEDULED])
@pytest.mark.parametrize("extraction_at_enqueue", [False, True])
async def test_summary_only_processing_leaves_memory_progress_untouched(source, monkeypatch, trigger, extraction_at_enqueue):
    p = source
    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="test")
    ai = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    monkeypatch.setattr("mato_loop_backend.memory.dreaming.load_effective_ai", AsyncMock(return_value=ai))
    complete = AsyncMock(return_value=ProviderResult(tool_calls=[
        ToolCall("summary", "record_session_summary", summary_content(p.messages[0].id)),
    ]))
    monkeypatch.setattr(ProviderService, "complete", complete)
    p.settings.memory_extraction_enabled = extraction_at_enqueue
    p.settings.automatic_dreaming_enabled = True
    p.settings.session_summaries_enabled_since = p.messages[0].created_at - timedelta(seconds=1)
    await SettingsStore.save(p.db, p.settings, user_id=p.user.id)
    runs = await DreamingService.enqueue(p.db, trigger=trigger, user_id=p.user.id)
    p.settings.memory_extraction_enabled = False
    await SettingsStore.save(p.db, p.settings, user_id=p.user.id)
    await p.db.commit()
    await DreamingService._process(p.db, runs[0].id)
    state = await p.db.scalar(select(MemoryDreamState).where(MemoryDreamState.session_id == p.session.id))
    memories = (await p.db.scalars(select(MemoryRecord).where(MemoryRecord.user_id == p.user.id))).all()
    assert (runs[0].status, runs[0].summary_status, state.last_processed_message_id, memories, complete.await_count) == ("completed", "completed", None, [], 1)


async def test_manual_retry_adds_summary_to_reused_extraction_run(source, monkeypatch):
    p = source
    provider = LlmProviderConfig(name="Test", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="test")
    ai = AiSettings(providers=[provider], models=[model], default_model_id=model.id)
    monkeypatch.setattr("mato_loop_backend.memory.dreaming.load_effective_ai", AsyncMock(return_value=ai))
    previous = MemoryDreamRun(
        session_id=p.session.id, status="failed", trigger="manual", summary_status="disabled",
        source_from_message_id=p.messages[0].id, source_to_message_id=p.messages[-1].id,
        source_message_count=len(p.messages),
    )
    p.db.add(previous)
    await p.db.flush()
    runs = await DreamingService.enqueue(p.db, trigger=MemoryDreamTrigger.MANUAL, session_id=p.session.id, user_id=p.user.id)
    assert (runs[0].id, runs[0].summary_status, runs[0].summary["toMessageId"]) == (previous.id, "pending", str(p.messages[-1].id))


@pytest.mark.parametrize("serialize, history_key", [(provider_module._chat_body, "messages"), (provider_module._responses_body, "input")])
@pytest.mark.parametrize("project_session", [False, True])
async def test_summary_replay_preserves_serialized_prefix_and_revokes_deleted_sources(source, serialize, history_key, project_session):
    p = source
    p.session.session_summary = {
        **summary_content(p.messages[0].id), "sourceRevision": 0,
        "coveredThroughMessageId": str(p.messages[-1].id), "coveredThroughAt": p.messages[-1].created_at.isoformat(),
    }
    await p.db.flush()
    reference = (await EpisodeRetrievalService.recent(
        p.db, owner_id=p.user.id, current_session_id=p.current.id,
        readable_by=TrustLevel.PUBLIC, full_sessions_only=True, project_id=p.project.id,
    )).references
    options = dict(owner_id=p.user.id, current_session_id=p.current.id, project_id=p.project.id, trust=TrustLevel.PUBLIC)
    cached = await selected_summaries(p.db, reference, **options)
    _, episodes, _ = _bounded_automatic_payload([], reference, [], max_characters=4096, project_session=project_session, session_summaries=cached)
    text = _cross_session_context_text([], episodes)
    first = Message(session_id=p.current.id, role="user", content="Mach dort weiter.", attachments=[],
                    provider_data={"_matoloop": {"memoryContext": memory_context_snapshot(text, text, p.current, TrustLevel.PUBLIC)}})
    p.db.add(first)
    await p.db.flush()
    model = ModelConfig(provider_id=uuid4(), model_id="test")
    provider = LlmProviderConfig(id=model.provider_id, name="Test", adapter_type="openai-compatible")
    prepared = await ProviderService.prepare_request(provider=provider, model=model, tools=[], pseudonymization_enabled=False,
        messages=_projection_messages("System", None, [first], TrustLevel.PUBLIC, memory_context=text))
    initial = serialize(prepared.request)
    p.session.session_summary = {**p.session.session_summary, "overview": "Newer snapshot, not old-turn content"}
    await p.db.flush()
    frozen = await selected_summaries(p.db, reference, cached=cached, **options)
    assert frozen == cached
    p.db.expire(first)
    await p.db.refresh(first)
    await p.db.refresh(first, attribute_names=["attachments"])
    call = Message(session_id=p.current.id, role="assistant", content="", attachments=[], tool_calls=[
        {"id": "read", "name": "session_read", "arguments": {"session_id": str(p.session.id)}},
    ])
    result = Message(session_id=p.current.id, role="tool", content="Original source", tool_call_id="read", attachments=[])
    p.db.add_all([call, result])
    await p.db.flush()
    prepared = await ProviderService.prepare_request(provider=provider, model=model, tools=[], pseudonymization_enabled=False,
        messages=_projection_messages("System", None, [first, call, result], TrustLevel.PUBLIC, memory_context=text))
    tool_round = serialize(prepared.request)
    assert tool_round[history_key][:len(initial[history_key])] == initial[history_key]
    p.db.expire(call)
    p.db.expire(result)
    await p.db.refresh(call)
    await p.db.refresh(result)
    await p.db.refresh(call, attribute_names=["attachments"])
    await p.db.refresh(result, attribute_names=["attachments"])
    followup = Message(id=uuid4(), session_id=p.current.id, role="user", content="Und weiter?", created_at=datetime.now(UTC), attachments=[])
    contexts = await historical_memory_contexts(p.db, p.current, [first, call, result, followup], trust_level=TrustLevel.PUBLIC,
                                               excluded_content_hashes=frozenset(), summaries_enabled=True)
    prepared = await ProviderService.prepare_request(provider=provider, model=model, tools=[], pseudonymization_enabled=False,
        messages=_projection_messages("System", None, [first, call, result, followup], TrustLevel.PUBLIC, memory_contexts=contexts))
    later = serialize(prepared.request)
    assert later[history_key][:len(tool_round[history_key])] == tool_round[history_key]
    await invalidate_summaries(p.db, p.session.id)
    assert await historical_memory_contexts(p.db, p.current, [first, followup], trust_level=TrustLevel.PUBLIC,
        excluded_content_hashes=frozenset(), summaries_enabled=True) == {}


async def test_summary_selection_keeps_owner_project_and_trust_boundaries(source):
    p = source
    summary = {**summary_content(p.messages[0].id), "sourceRevision": 0}
    p.session.session_summary = summary
    other_project = Project(user_id=p.user.id, name="Other")
    outsider = UserAccount(username=f"summary-outsider-{uuid4().hex}", password_hash="test")
    p.db.add_all([other_project, outsider])
    await p.db.flush()
    excluded = [ChatSession(user_id=p.user.id, project_id=other_project.id, trust_level="public", session_summary=summary),
                ChatSession(user_id=p.user.id, project_id=p.project.id, trust_level="private", session_summary=summary),
                ChatSession(user_id=outsider.id, trust_level="public", session_summary=summary)]
    p.db.add_all(excluded)
    await p.db.flush()
    references = [SimpleNamespace(session_id=item.id) for item in [p.session, *excluded]]
    result = await selected_summaries(p.db, references, owner_id=p.user.id, current_session_id=p.current.id,
                                      project_id=p.project.id, trust=TrustLevel.PUBLIC)
    assert set(result) == {p.session.id}
