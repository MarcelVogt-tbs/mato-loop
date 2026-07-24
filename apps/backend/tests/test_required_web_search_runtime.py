import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.context import ContextManager, ContextProjection
from mato_loop_backend.agent.runtime import AgentRunError, AgentRuntime, _VisibleResponse
from mato_loop_backend.agent.tools import ToolResult, tool_registry
from mato_loop_backend.events import run_events
from mato_loop_backend.models import AgentRun, ChatSession, Message, WebSearchPolicy
from mato_loop_backend.providers.base import ProviderResult, ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    AgentRuntimeSettings,
    AiSettings,
    GeneralSettings,
    IntegrationSettings,
    MemorySettings,
    SettingsStore,
    ToolSettings,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig


async def _runtime_setup(monkeypatch, results: list[ProviderResult]):
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "https://llm.example.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="local-model")
    ai_settings = AiSettings(
        providers=[provider], models=[model], default_model_id=model.id
    )
    integration_settings = IntegrationSettings()
    runtime_settings = AgentRuntimeSettings(max_steps=10, max_model_requests=10)
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=model.id,
        memory_mode="disabled",
        web_search_policy=WebSearchPolicy.REQUIRED.value,
        workspace_access="read-only",
    )
    run = AgentRun(id=uuid4(), session_id=session.id, status="running")
    user = Message(id=uuid4(), session_id=session.id, role="user", content="Aktuelle Frage")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            AiSettings: ai_settings,
            AgentIdentitySettings: AgentIdentitySettings(),
            AgentRuntimeSettings: runtime_settings,
            GeneralSettings: GeneralSettings(default_timezone="Europe/Berlin"),
            MemorySettings: MemorySettings(enabled=False),
            IntegrationSettings: integration_settings,
            ToolSettings: ToolSettings(),
        }[settings_type]

    projections = []

    async def build_context(*_args, **kwargs):
        projections.append(kwargs)
        return ContextProjection(
            messages=[
                {"role": "system", "content": kwargs["system_prompt"]},
                {"role": "user", "content": user.content},
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

    events = []

    async def publish(_run_id, event):
        events.append(event)

    monkeypatch.setattr(SettingsStore, "load", load_settings)
    monkeypatch.setattr(AgentRuntime, "_records", AsyncMock(return_value=[user]))
    monkeypatch.setattr(ContextManager, "build", build_context)
    monkeypatch.setattr(ProviderService, "complete", staticmethod(complete))
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            return_value=ToolResult.json(
                [
                    {
                        "title": "Quelle",
                        "url": "https://example.test/source",
                        "content": "Aktuelle Information",
                    }
                ]
            )
        ),
    )
    monkeypatch.setattr(run_events, "publish", publish)
    return db, run, requests, projections, events


async def test_required_search_retries_hidden_text_and_keeps_normal_tools(monkeypatch) -> None:
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(content="Veraltete Antwort", finish_reason="stop"),
            ProviderResult(
                content="Ich recherchiere.",
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Belegte Antwort", finish_reason="stop"),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "required",
        "required",
        "auto",
    ]
    assert {tool["name"] for tool in requests[0]["tools"]} >= {
        "read_file",
        "web_search",
    }
    assert "previous final draft was rejected" in projections[1]["system_prompt"]
    assert all(
        "Current local date and time:" in projection["system_prompt"]
        for projection in projections
    )
    assert "satisfies only the tool-use requirement" in projections[2]["system_prompt"]
    assert "including every named recommendation" in projections[2]["system_prompt"]
    assert "search again or omit it" in projections[2]["system_prompt"]
    assert requests[1]["messages"][-2:] == [
        {"role": "assistant", "content": "Veraltete Antwort"},
        {
            "role": "system",
            "content": (
                "Runtime validation error: web_search is required before the final answer. "
                "The previous draft was rejected. Call web_search now and do not provide "
                "another final answer first."
            ),
        },
    ]
    persisted_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert "Veraltete Antwort" not in persisted_contents
    assert "Ich recherchiere." in persisted_contents
    assert "Belegte Antwort" in persisted_contents
    assert all(
        event.get("delta") != "Veraltete Antwort"
        for event in events
        if event["type"] == "message.delta"
    )


async def test_required_search_stops_after_two_semantic_retries(monkeypatch) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(content="Antwort 1", finish_reason="stop"),
            ProviderResult(content="Antwort 2", finish_reason="stop"),
            ProviderResult(content="Antwort 3", finish_reason="stop"),
        ],
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.requiredWebSearchMissing"
    assert len(requests) == 3
    assert db.add.call_count == 0
    assert all(event["type"] != "message.delta" for event in events)


async def test_cancel_does_not_capture_hidden_required_search_draft(monkeypatch) -> None:
    db, run, _, _, events = await _runtime_setup(monkeypatch, [])
    visible_response = _VisibleResponse()

    async def cancel_during_hidden_draft(_db, **kwargs):
        await kwargs["on_delta"]("Nicht freigegebener Entwurf")
        raise asyncio.CancelledError

    monkeypatch.setattr(
        ProviderService,
        "complete",
        staticmethod(cancel_during_hidden_draft),
    )

    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._execute_loop(db, run, visible_response)

    assert visible_response.content == ""
    assert all(event["type"] != "message.delta" for event in events)
