import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.context import ContextManager, ContextProjection
from mato_loop_backend.agent.runtime import (
    AgentRunError,
    AgentRuntime,
    _linked_research_source_ids,
    _VisibleResponse,
)
from mato_loop_backend.agent.tools import ToolFileAction, ToolResult, tool_registry
from mato_loop_backend.events import run_events
from mato_loop_backend.models import AgentRun, ChatSession, Message, WebSearchPolicy
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
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig, TrustLevel

SOURCE_ID = "src-test"
SOURCE_URL = "https://example.test/source"
SOURCE_TITLE = "Quelle"
SOURCE_CITATION = f"[{SOURCE_TITLE}]({SOURCE_URL})"


def _search_call(call_id: str, query: str) -> ToolCall:
    return ToolCall(id=call_id, name="web_search", arguments={"query": query})


def _search_result(*, empty: bool = False) -> ToolResult:
    if empty:
        return ToolResult.json([], metadata={"resultCount": 0, "sources": []})
    source = {
        "sourceId": SOURCE_ID,
        "title": SOURCE_TITLE,
        "url": SOURCE_URL,
        "kind": "search",
    }
    return ToolResult.json(
        [{**source, "snippet": "Aktuelle Information"}],
        metadata={"resultCount": 1, "sources": [source]},
    )


async def _runtime_setup(
    monkeypatch,
    results: list[ProviderResult],
    *,
    max_tool_calls: int = 20,
    max_research_tool_calls: int = 8,
    max_model_requests: int = 10,
    search_policy: WebSearchPolicy = WebSearchPolicy.REQUIRED,
    active_supports_image_input: bool = False,
    vision_fallback: bool = False,
    vision_fallback_private: bool = False,
    tool_result: ToolResult | None = None,
    workspace_access: str = "read-only",
):
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "https://llm.example.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="local-model",
        supports_image_input=active_supports_image_input,
    )
    vision_model = (
        ModelConfig(
            provider_id=provider.id,
            model_id="vision-model",
            supports_image_input=True,
            supports_tools=False,
            trust_level=(TrustLevel.PRIVATE if vision_fallback_private else None),
        )
        if vision_fallback
        else None
    )
    ai_settings = AiSettings(
        providers=[provider],
        models=[item for item in (model, vision_model) if item is not None],
        default_model_id=model.id,
        vision_model_id=vision_model.id if vision_model is not None else None,
    )
    runtime_settings = AgentRuntimeSettings(
        max_steps=10,
        max_model_requests=max_model_requests,
        max_tool_calls=max_tool_calls,
        max_research_tool_calls=max_research_tool_calls,
    )
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=model.id,
        memory_mode="disabled",
        web_search_policy=search_policy.value,
        workspace_access=workspace_access,
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
            MemorySettings: MemorySettings(enabled=False),
            IntegrationSettings: IntegrationSettings(),
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
        AsyncMock(return_value=tool_result or _search_result()),
    )
    monkeypatch.setattr(run_events, "publish", publish)
    return db, run, requests, projections, events


def _assistant_messages(db) -> list[Message]:
    return [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "assistant"
    ]


async def test_runtime_persists_typed_file_actions_on_tool_results(monkeypatch) -> None:
    action = ToolFileAction(
        "write",
        "session",
        "9fbd5cf9-70fd-41ad-a88a-ff74be2c3a1a",
        "files/report.txt",
        "report.txt",
        7,
    )
    db, run, *_ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={
                            "root": "session",
                            "path": "files/report.txt",
                            "content": "report",
                        },
                    )
                ]
            ),
            ProviderResult(content="Fertig", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        workspace_access="read-write",
        tool_result=ToolResult.json(
            {"root": "session", "written": "files/report.txt"},
            file_actions=(action,),
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    tool_message = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    )
    assert tool_message.provider_data == {
        "_matoloop": {
            "fileActions": [
                {
                    "operation": "write",
                    "root": "session",
                    "rootIdentity": action.root_identity,
                    "path": "files/report.txt",
                    "filename": "report.txt",
                    "sizeBytes": 7,
                }
            ]
        }
    }
    assert "download" not in str(tool_message.provider_data).casefold()


def test_linked_research_sources_ignore_hidden_and_foreign_links() -> None:
    parenthesized_url = "https://example.test/wiki/Product_(series)"
    content = (
        f"[Beleg]({parenthesized_url})\n"
        "[Andere Quelle](https://other.example/source)\n"
        f"`[Versteckt]({SOURCE_URL})`"
    )

    assert _linked_research_source_ids(
        content,
        {"parenthesized": parenthesized_url, SOURCE_ID: SOURCE_URL},
    ) == ["parenthesized"]


def test_linked_research_sources_support_autolinks_but_not_images() -> None:
    image_url = "https://example.test/image"
    escaped_url = "https://example.test/escaped"
    hidden_url = "https://example.test/hidden"
    content = (
        f"<{SOURCE_URL}>\n"
        f"![Bild]({image_url})\n"
        f"\\[Kein Link]({escaped_url})\n"
        f"<!-- [Versteckt]({hidden_url})"
    )

    assert _linked_research_source_ids(
        content,
        {
            SOURCE_ID: SOURCE_URL,
            "image": image_url,
            "escaped": escaped_url,
            "hidden": hidden_url,
        },
    ) == [SOURCE_ID]


def test_linked_research_sources_match_rendered_destinations_not_link_labels() -> None:
    other_url = "https://other.example/source"
    content = (
        f"[{SOURCE_URL}]({other_url}) "
        f"[Quelle mit Titel]({SOURCE_URL} \"Offiziell\")"
    )

    assert _linked_research_source_ids(
        content,
        {SOURCE_ID: SOURCE_URL, "other": other_url},
    ) == ["other", SOURCE_ID]


async def test_available_can_answer_without_research(monkeypatch) -> None:
    answer = "Antwort aus stabilem Wissen"
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content=answer, finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 1
    assert requests[0]["tool_choice"] == "auto"
    assert any(event.get("delta") == answer for event in events)
    final = _assistant_messages(db)[0]
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "available",
        "used": False,
        "linkedSources": [],
    }


async def test_required_search_transitions_to_the_shared_streaming_loop(monkeypatch) -> None:
    answer = f"Belegte Antwort {SOURCE_CITATION}"
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Ich recherchiere.",
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                provider_data={"chat_reasoning_details": [{"text": "Intern"}]},
                finish_reason="tool_calls",
            ),
            ProviderResult(content=answer, finish_reason="stop"),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["required", "auto"]
    assert [tool["name"] for tool in requests[0]["tools"]] == ["web_search"]
    assert {tool["name"] for tool in requests[1]["tools"]} >= {
        "read_file",
        "web_search",
    }
    assert "Mandatory web research is active" in projections[0]["system_prompt"]
    assert "Mandatory web research is active" not in projections[1]["system_prompt"]
    assert "Web research contract:" in projections[0]["system_prompt"]
    assert "Web research contract:" in projections[1]["system_prompt"]
    persisted_contents = [message.content for message in _assistant_messages(db)]
    assert "Ich recherchiere." not in persisted_contents
    assert answer in persisted_contents
    assert all(event.get("delta") != "Ich recherchiere." for event in events)
    assert any(event.get("delta") == answer for event in events)
    final = next(message for message in _assistant_messages(db) if not message.tool_calls)
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "required",
        "used": True,
        "linkedSources": [{"title": SOURCE_TITLE, "url": SOURCE_URL}],
    }
    completed = next(
        event
        for event in events
        if event["type"] == "message.completed" and not event["message"]["toolCalls"]
    )
    assert completed["message"]["research"] == final.provider_data["_matoloop"][
        "research"
    ]


async def test_empty_required_search_satisfies_the_gate(monkeypatch) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-empty", "Sehr seltenes Thema")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Keine aktuellen Treffer gefunden.", finish_reason="stop"
            ),
        ],
    )
    tool_registry._execute_definition.return_value = _search_result(empty=True)

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["required", "auto"]
    assert any(
        event.get("delta") == "Keine aktuellen Treffer gefunden." for event in events
    )
    final = next(message for message in _assistant_messages(db) if not message.tool_calls)
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "required",
        "used": True,
        "linkedSources": [],
    }


async def test_required_search_retries_hidden_after_tool_failure(monkeypatch) -> None:
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Unbelegte Antwort",
                tool_calls=[_search_call("search-failed", "Fehlerhafte Suche")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Zweiter Versuch",
                tool_calls=[_search_call("search-ok", "Neu formulierte Suche")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Normale Antwort", finish_reason="stop"),
        ],
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("integration.unavailable"),
        _search_result(),
    ]

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "required",
        "required",
        "auto",
    ]
    assert "previous attempt did not complete" in projections[1]["system_prompt"]
    persisted_contents = [message.content for message in _assistant_messages(db)]
    assert "Unbelegte Antwort" not in persisted_contents
    assert "Zweiter Versuch" not in persisted_contents
    assert all(
        event.get("delta") not in {"Unbelegte Antwort", "Zweiter Versuch"}
        for event in events
    )


async def test_available_uses_the_shared_research_budget(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[_search_call("search-2", "Weitere Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Budgetierte Antwort", finish_reason="stop"),
        ],
        max_research_tool_calls=1,
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert tool_registry._execute_definition.await_count == 1
    assert "web_search" not in {tool["name"] for tool in requests[1]["tools"]}
    assert "webfetch" not in {tool["name"] for tool in requests[1]["tools"]}
    tool_messages = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert any("agent.researchBudgetReached" in message.content for message in tool_messages)


async def test_repeated_successful_research_call_does_not_hit_the_network(monkeypatch) -> None:
    db, run, requests, projections, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[_search_call("search-2", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Antwort nach Duplikat", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert tool_registry._execute_definition.await_count == 1
    tool_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert any('"alreadyCompleted": true' in content for content in tool_contents)
    assert "web_search" not in {tool["name"] for tool in requests[2]["tools"]}
    assert "Research tools are unavailable for the rest of this turn" in projections[2][
        "system_prompt"
    ]
    assert "7 remaining" not in projections[2]["system_prompt"]


async def test_tool_call_limit_applies_after_required_gate(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="read-1",
                        name="read_file",
                        arguments={"root": "session", "path": "files/info.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ],
        max_tool_calls=1,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.toolCallLimitReached"
    assert len(requests) == 2
    assert tool_registry._execute_definition.await_count == 1


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


@pytest.mark.parametrize(
    ("active_supports_image_input", "expected_model"),
    [(True, "local-model"), (False, "vision-model")],
)
async def test_pdf_vision_prefers_active_model_then_configured_fallback(
    monkeypatch, active_supports_image_input: bool, expected_model: str
) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="fetch-pdf",
                        name="webfetch",
                        arguments={"url": "https://example.test/scan.pdf"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content='[{"page":1,"text":"Scan text"}]'),
            ProviderResult(content="Fertige Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=active_supports_image_input,
        vision_fallback=True,
    )

    async def execute(_call, context, _tool):
        assert context.pdf_vision_handler is not None
        extracted = await context.pdf_vision_handler([(1, b"jpeg")], False)
        return ToolResult.json({"content": extracted[1]})

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert requests[1]["model"].model_id == expected_model
    assert requests[1]["tool_choice"] == "none"
    assert requests[1]["image_inputs"][0].message_index == 1
    assert requests[1]["image_inputs"][0].data == b"jpeg"


async def test_private_pdf_pages_skip_public_active_vision_model(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="fetch-pdf",
                        name="webfetch",
                        arguments={"url": "http://private.test/scan.pdf"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content='[{"page":1,"text":"Private scan text"}]'),
            ProviderResult(content="Fertige Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=True,
        vision_fallback=True,
        vision_fallback_private=True,
    )

    async def execute(_call, context, _tool):
        assert context.pdf_vision_handler is not None
        extracted = await context.pdf_vision_handler([(1, b"jpeg")], True)
        return ToolResult.json({"content": extracted[1]})

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert requests[1]["model"].model_id == "vision-model"
    assert requests[1]["target_trust_level"] == TrustLevel.PRIVATE
    assert requests[1]["image_inputs"][0].trust_level == TrustLevel.PRIVATE.value


async def test_cancel_does_not_capture_required_gate_text(monkeypatch) -> None:
    db, run, _, _, events = await _runtime_setup(monkeypatch, [])
    visible_response = _VisibleResponse()

    async def cancel_during_gate(_db, **kwargs):
        await kwargs["on_delta"]("Nicht freigegebener Entwurf")
        raise asyncio.CancelledError

    monkeypatch.setattr(
        ProviderService,
        "complete",
        staticmethod(cancel_during_gate),
    )

    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._execute_loop(db, run, visible_response)

    assert visible_response.content == ""
    assert all(event["type"] != "message.delta" for event in events)
