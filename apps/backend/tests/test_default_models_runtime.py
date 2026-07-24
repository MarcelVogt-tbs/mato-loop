from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import runtime as runtime_module
from mato_loop_backend.agent.runtime import AgentRuntime, _generated_session_title
from mato_loop_backend.agent.title_generation import repair_title_output_budget
from mato_loop_backend.events import run_events
from mato_loop_backend.models import ChatSession, TitleGenerationStatus
from mato_loop_backend.providers.base import ProviderResult, PublicReasoning
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import AgentRuntimeSettings, AiSettings, SettingsStore
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


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
    complete = AsyncMock(return_value=SimpleNamespace(content="Kompakte Zusammenfassung"))
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
        side_effect=[RuntimeError("kaputt"), SimpleNamespace(content="Fallback")]
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
    db.get.return_value = session

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
        "Default Models in Settings einbauen",
        "Default Models in Settings einbauen",
    )

    assert complete.await_args.kwargs["model"] is session_model
    assert complete.await_args.kwargs["max_output_tokens"] == 512
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
        "Erste Nachricht",
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

    assert repair_title_output_budget(512, 16_384, reasoning_only) == 4_096
    assert repair_title_output_budget(512, 2_048, reasoning_only) == 2_048
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
    db.get.return_value = session
    db.scalar.return_value = session.id

    async def load(_db, settings_type):
        return settings if settings_type is AiSettings else AgentRuntimeSettings()

    complete = AsyncMock(
        side_effect=[
            ProviderResult(
                reasoning=PublicReasoning("Nur Reasoning"),
                usage={"output_tokens": 512, "reasoning_tokens": 512},
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
        uuid4(), session.id, "Ausgangstitel", "Ausgangstitel"
    )

    assert complete.await_count == 2
    assert [
        call.kwargs["max_output_tokens"] for call in complete.await_args_list
    ] == [512, 4_096]
    repair_prompt = complete.await_args.kwargs["messages"][0]["content"]
    assert "vorherige Versuch" in repair_prompt
    assert "ausschliesslich den finalen Titel" in repair_prompt
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
    db.get.return_value = session

    async def load(_db, settings_type):
        return settings if settings_type is AiSettings else AgentRuntimeSettings()

    monkeypatch.setattr(SettingsStore, "load", load)
    monkeypatch.setattr(runtime_module, "AsyncSessionFactory", lambda: _SessionContext(db))
    monkeypatch.setattr(
        ProviderService, "complete", AsyncMock(side_effect=RuntimeError("kaputt"))
    )

    await AgentRuntime._generate_title(
        uuid4(), session.id, "Fehlerfall", "Fehlerfall"
    )

    db.rollback.assert_awaited_once()
    statement = db.execute.await_args.args[0]
    assert TitleGenerationStatus.FAILED.value in statement.compile().params.values()
    db.commit.assert_awaited_once()
