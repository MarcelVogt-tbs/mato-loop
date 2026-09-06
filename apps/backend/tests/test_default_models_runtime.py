import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent.runtime import AgentRuntime, _generated_session_title
from mato_loop_backend.agent.title_generation import repair_title_output_budget, title_input
from mato_loop_backend.events import run_events
from mato_loop_backend.models import ChatSession, Message, MessageAttachment, TitleGenerationStatus
from mato_loop_backend.providers.base import ProviderResult, PublicReasoning
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AgentRuntimeSettings,
    AiSettings,
    ReasoningEffort,
    SettingsStore,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig

_VALID_SUMMARY = """Goal
Continue.

Facts
Known facts.

Decisions
None.

Tool effects
None.

Open items
None.

Source message IDs
None.
"""


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return None


async def test_compaction_uses_global_model_before_session_model(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    active = ModelConfig(provider_id=provider.id, model_id="agent")
    compaction = ModelConfig(provider_id=provider.id, model_id="compact")
    settings = AiSettings(
        providers=[provider],
        models=[active, compaction],
        compaction_model_id=compaction.id,
    )
    complete = AsyncMock(return_value=SimpleNamespace(content=_VALID_SUMMARY))
    monkeypatch.setattr(ProviderService, "complete", complete)

    result = await AgentRuntime._summarize(
        AsyncMock(spec=AsyncSession),
        "USER: Test",
        512,
        ai_settings=settings,
        active_model=active,
        runtime_settings=AgentRuntimeSettings(),
    )

    assert result.model_config_id == compaction.id
    assert complete.await_args.kwargs["model"] is compaction
    assert complete.await_args.kwargs["tools"] == []


async def test_compaction_falls_back_to_active_model_after_specialist_failure(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    active = ModelConfig(provider_id=provider.id, model_id="agent")
    compaction = ModelConfig(provider_id=provider.id, model_id="compact")
    settings = AiSettings(
        providers=[provider],
        models=[active, compaction],
        compaction_model_id=compaction.id,
    )
    complete = AsyncMock(
        side_effect=[RuntimeError("kaputt"), SimpleNamespace(content=_VALID_SUMMARY)]
    )
    monkeypatch.setattr(ProviderService, "complete", complete)

    result = await AgentRuntime._summarize(
        AsyncMock(spec=AsyncSession),
        "USER: Test",
        512,
        ai_settings=settings,
        active_model=active,
        runtime_settings=AgentRuntimeSettings(),
    )

    assert result.model_config_id == active.id
    assert result.is_fallback is False
    assert [call.kwargs["model"] for call in complete.await_args_list] == [
        compaction,
        active,
    ]


async def test_compaction_streams_visible_output_and_resets_failed_candidate(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    active = ModelConfig(provider_id=provider.id, model_id="agent")
    compaction = ModelConfig(provider_id=provider.id, model_id="compact")
    settings = AiSettings(
        providers=[provider],
        models=[active, compaction],
        compaction_model_id=compaction.id,
    )
    calls = 0

    async def complete(_db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            await kwargs["on_delta"]("Discarded draft")
            raise RuntimeError("failed candidate")
        await kwargs["on_delta"](_VALID_SUMMARY[:20])
        await kwargs["on_delta"](_VALID_SUMMARY[20:])
        return ProviderResult(content=_VALID_SUMMARY)

    monkeypatch.setattr(ProviderService, "complete", complete)
    updates = []

    async def on_update(content, is_fallback):
        updates.append((content, is_fallback))

    result = await AgentRuntime._summarize(
        AsyncMock(spec=AsyncSession),
        "USER: Test",
        512,
        ai_settings=settings,
        active_model=active,
        runtime_settings=AgentRuntimeSettings(),
        on_update=on_update,
    )

    assert result.model_config_id == active.id
    assert ("Discarded draft", False) in updates
    discarded_index = updates.index(("Discarded draft", False))
    assert ("", False) in updates[discarded_index + 1 :]
    assert updates[-1] == (_VALID_SUMMARY.strip(), False)


async def test_compaction_disables_optional_reasoning(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="Qwen3.8-27B-GGUF-UD-Q4_K_XL")
    settings = AiSettings(providers=[provider], models=[model])
    complete = AsyncMock(return_value=ProviderResult(content=_VALID_SUMMARY))
    monkeypatch.setattr(ProviderService, "complete", complete)

    await AgentRuntime._summarize(
        AsyncMock(spec=AsyncSession),
        "USER: Test",
        512,
        ai_settings=settings,
        active_model=model,
        runtime_settings=AgentRuntimeSettings(),
    )

    assert complete.await_args.kwargs["reasoning_effort"] == ReasoningEffort.NONE


async def test_compaction_marks_host_fallback_after_empty_visible_output(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="compact")
    settings = AiSettings(providers=[provider], models=[model])
    monkeypatch.setattr(
        ProviderService,
        "complete",
        AsyncMock(return_value=ProviderResult(reasoning=PublicReasoning("Only reasoning"))),
    )

    result = await AgentRuntime._summarize(
        AsyncMock(spec=AsyncSession),
        "USER: Test",
        512,
        ai_settings=settings,
        active_model=model,
        runtime_settings=AgentRuntimeSettings(),
    )

    assert result.is_fallback is True
    assert result.model_config_id is None
    assert "USER: Test" in result.content


async def test_title_generation_falls_back_to_session_model(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    agent = ModelConfig(provider_id=provider.id, model_id="agent")
    session_model = ModelConfig(provider_id=provider.id, model_id="session")
    settings = AiSettings(
        providers=[provider],
        models=[agent, session_model],
        default_model_id=agent.id,
    )
    session = ChatSession(
        id=uuid4(),
        title="Default Models in Settings einbauen",
        model_config_id=session_model.id,
        title_generation_status=TitleGenerationStatus.RUNNING.value,
    )
    db = AsyncMock(spec=AsyncSession)
    message = Message(id=uuid4(), session_id=session.id, role="user", content=session.title)
    db.get.side_effect = [session, message]

    async def load(_db, settings_type):
        return settings if settings_type is AiSettings else AgentRuntimeSettings()

    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    complete = AsyncMock(return_value=SimpleNamespace(content="Standardmodelle konfigurieren"))
    monkeypatch.setattr(ProviderService, "complete", complete)
    publish = AsyncMock()
    monkeypatch.setattr(run_events, "publish", publish)
    db.scalar.return_value = session.id
    run_id = uuid4()

    await AgentRuntime._generate_title(
        run_id,
        session.id,
        message.id,
        "Default Models in Settings einbauen",
    )

    assert complete.await_args.kwargs["model"].id == session_model.id
    assert complete.await_args.kwargs["max_output_tokens"] == 2_048
    statement = db.scalar.await_args.args[0]
    assert "chat_sessions.title" in str(statement)
    assert "Standardmodelle konfigurieren" in statement.compile().params.values()
    assert TitleGenerationStatus.COMPLETED.value in statement.compile().params.values()
    db.commit.assert_awaited_once()
    publish.assert_awaited_once_with(
        run_id,
        {
            "type": "session.title.updated",
            "sessionId": str(session.id),
            "title": "Standardmodelle konfigurieren",
        },
    )


async def test_title_generation_waits_for_agent_run(monkeypatch) -> None:
    release_run = asyncio.Event()
    factory_calls = 0
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = None

    async def run_worker() -> None:
        await release_run.wait()

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return _SessionContext(db)

    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", session_factory)
    run_task = asyncio.create_task(run_worker())
    title_task = asyncio.create_task(
        AgentRuntime._generate_title(
            uuid4(), uuid4(), uuid4(), "Erste Nachricht", after=run_task
        )
    )
    await asyncio.sleep(0)

    assert factory_calls == 0

    release_run.set()
    await title_task
    assert factory_calls == 1


async def test_title_generation_skips_cancelled_agent_run(monkeypatch) -> None:
    session = ChatSession(
        title="Erste Nachricht",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    complete = AsyncMock()
    monkeypatch.setattr(ProviderService, "complete", complete)
    run_task = asyncio.create_task(asyncio.sleep(10))
    run_task.cancel()
    await asyncio.gather(run_task, return_exceptions=True)

    await AgentRuntime._generate_title(
        uuid4(), session.id, uuid4(), "Erste Nachricht", after=run_task
    )

    complete.assert_not_awaited()
    assert session.title_generation_status == TitleGenerationStatus.SKIPPED.value
    db.commit.assert_awaited_once()


async def test_title_generation_does_not_overwrite_manual_title(monkeypatch) -> None:
    session = ChatSession(
        title="Manuell gesetzt",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    complete = AsyncMock()
    monkeypatch.setattr(ProviderService, "complete", complete)

    await AgentRuntime._generate_title(
        uuid4(),
        session.id,
        uuid4(),
        "Erste Nachricht",
    )

    complete.assert_not_awaited()
    db.execute.assert_not_awaited()
    assert session.title_generation_status == TitleGenerationStatus.SKIPPED.value
    db.commit.assert_awaited_once()


def test_generated_title_is_normalized_and_bounded() -> None:
    assert _generated_session_title('  "Kurzer   Titel"  ') == "Kurzer Titel"
    assert _generated_session_title("x" * 100) == "x" * 80
    assert _generated_session_title("<think>Nur Reasoning</think>") is None


def test_title_repair_budget_uses_provider_signals_and_model_limit() -> None:
    reasoning_only = ProviderResult(
        reasoning=PublicReasoning("Analyse"),
        usage={"output_tokens": 512, "reasoning_tokens": 512},
        finish_reason="length",
    )

    assert repair_title_output_budget(1_024, 16_384, reasoning_only) == 4_096
    assert repair_title_output_budget(1_024, 2_048, reasoning_only) == 2_048
    assert repair_title_output_budget(256, 300, ProviderResult()) == 300


async def test_title_generation_repairs_empty_visible_output(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="title")
    settings = AiSettings(
        providers=[provider], models=[model], title_generation_model_id=model.id
    )
    session = ChatSession(
        id=uuid4(),
        title="Ausgangstitel",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
    )
    db = AsyncMock(spec=AsyncSession)
    message = Message(id=uuid4(), session_id=session.id, role="user", content=session.title)
    db.get.side_effect = [session, message]
    db.scalar.return_value = session.id

    async def load(_db, settings_type):
        return settings if settings_type is AiSettings else AgentRuntimeSettings()

    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                reasoning=PublicReasoning("Nur Reasoning"),
                usage={"output_tokens": 1_024, "reasoning_tokens": 1_024},
                finish_reason="length",
            ),
            ProviderResult(content="Reparierter Titel", finish_reason="stop"),
        ]
    )
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(ProviderService, "complete", complete)
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    await AgentRuntime._generate_title(
        uuid4(), session.id, message.id, "Ausgangstitel"
    )

    assert complete.await_count == 2
    assert [
        call.kwargs["max_output_tokens"] for call in complete.await_args_list
    ] == [2_048, 4_096]
    repair_prompt = complete.await_args.kwargs["messages"][0]["content"]
    assert "previous attempt" in repair_prompt
    assert "only the final title" in repair_prompt
    statement = db.scalar.await_args.args[0]
    assert "Reparierter Titel" in statement.compile().params.values()


async def test_title_generation_persists_failed_status(monkeypatch) -> None:
    provider = LlmProviderConfig(name="Local", adapter_type="openai-compatible")
    model = ModelConfig(provider_id=provider.id, model_id="title")
    settings = AiSettings(
        providers=[provider], models=[model], title_generation_model_id=model.id
    )
    session = ChatSession(
        id=uuid4(),
        title="Fehlerfall",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
    )
    db = AsyncMock(spec=AsyncSession)
    message = Message(id=uuid4(), session_id=session.id, role="user", content=session.title)
    db.get.side_effect = [session, message]

    async def load(_db, settings_type):
        return settings if settings_type is AiSettings else AgentRuntimeSettings()

    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(
        ProviderService, "complete", AsyncMock(side_effect=RuntimeError("kaputt"))
    )

    await AgentRuntime._generate_title(
        uuid4(), session.id, message.id, "Fehlerfall"
    )

    db.rollback.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    assert TitleGenerationStatus.FAILED.value in statement.compile().params.values()
    db.commit.assert_awaited_once()


def test_title_input_uses_extracted_text_and_bounds_filename_fallback() -> None:
    assert "Quarterly revenue" in title_input(
        "", [("report.pdf", "Quarterly revenue increased")]
    )
    fallback = title_input("", [("a" * 3_000, None), ("photo.png", None)])
    assert fallback.startswith("Attachments: ")
    assert len(fallback) <= 4_000
    assert fallback.endswith("photo.png")
    mixed = title_input(
        "", [("report.pdf", "Quarterly revenue increased"), ("recording.wav", None)]
    )
    assert "Quarterly revenue" in mixed
    assert "recording.wav" in mixed
    long_mixed = title_input(
        "", [("report.pdf", "x" * 5_000), ("recording.wav", None)]
    )
    assert len(long_mixed) <= 4_000
    assert long_mixed.endswith("recording.wav")
    many_filenames = title_input(
        "User question",
        [(f"{index}-{'x' * 990}.txt", None) for index in range(8)],
    )
    assert many_filenames.startswith("User question")
    assert "7-" in many_filenames


@pytest.mark.parametrize(
    ("title_supports_images", "fallback_enabled", "expected_model_id"),
    [
        (True, True, "title"),
        (False, True, "vision"),
        (False, False, "title"),
    ],
)
async def test_title_generation_routes_current_images_in_one_request(
    monkeypatch,
    title_supports_images: bool,
    fallback_enabled: bool,
    expected_model_id: str,
) -> None:
    provider = LlmProviderConfig(name="Public", adapter_type="openai-compatible")
    title_model = ModelConfig(
        provider_id=provider.id,
        model_id="title",
        supports_image_input=title_supports_images,
    )
    vision_model = ModelConfig(
        provider_id=provider.id,
        model_id="vision",
        supports_image_input=True,
    )
    settings = AiSettings(
        providers=[provider],
        models=[title_model, vision_model],
        title_generation_model_id=title_model.id,
        vision_model_id=vision_model.id,
        title_vision_fallback_enabled=fallback_enabled,
    )
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="report.pdf, photo.png",
        title_generation_status=TitleGenerationStatus.RUNNING.value,
    )
    message = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content="",
        trust_level="private",
    )
    document = MessageAttachment(
        id=uuid4(),
        message_id=message.id,
        position=0,
        kind="document",
        mime_type="application/pdf",
        size_bytes=100,
        sha256="a" * 64,
        file_path=f"attachments/{uuid4()}/report.pdf",
        filename="report.pdf",
        processing_status="ready",
    )
    image = MessageAttachment(
        id=uuid4(),
        message_id=message.id,
        position=1,
        kind="image",
        mime_type="image/png",
        size_bytes=100,
        sha256="b" * 64,
        file_path=f"attachments/{uuid4()}/photo.png",
        filename="photo.png",
        processing_status="ready",
    )
    message.attachments = [document, image]
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [session, message]
    db.scalar.return_value = session.id

    async def load(_db, settings_type):
        return settings if settings_type is AiSettings else AgentRuntimeSettings()

    attachment_service = SimpleNamespace(
        read_derived_text=lambda _attachment, _session_id: "Quarterly revenue increased",
        read_image=AsyncMock(return_value=b"verified image"),
    )
    complete = AsyncMock(return_value=ProviderResult(content="Quarterly report"))
    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(runtime_module, "AttachmentService", lambda: attachment_service)
    monkeypatch.setattr(ProviderService, "complete", complete)
    monkeypatch.setattr(run_events, "publish", AsyncMock())

    await AgentRuntime._generate_title(
        uuid4(), session.id, message.id, session.title
    )

    assert complete.await_count == 1
    assert complete.await_args.kwargs["model"].model_id == expected_model_id
    assert "Quarterly revenue increased" in complete.await_args.kwargs["messages"][1][
        "content"
    ]
    image_inputs = complete.await_args.kwargs["image_inputs"]
    if title_supports_images or fallback_enabled:
        assert len(image_inputs) == 1
        assert image_inputs[0].trust_level == "private"
        attachment_service.read_image.assert_awaited_once_with(image, session.id, current_only=True)
    else:
        assert image_inputs == []
        attachment_service.read_image.assert_not_awaited()
