import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.context import ContextManager, ContextProjection
from mato_loop_backend.agent.runtime import (
    RESEARCH_SUBMISSION_TOOL_NAME,
    AgentRunError,
    AgentRuntime,
    _validated_research_citations,
    _VisibleResponse,
)
from mato_loop_backend.agent.tools import ToolResult, tool_registry
from mato_loop_backend.events import run_events
from mato_loop_backend.models import AgentRun, ChatSession, Message, WebSearchPolicy
from mato_loop_backend.providers.base import ProviderResult, PublicReasoning, ToolCall
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

SOURCE_ID = "src-test"
SOURCE_URL = "https://example.test/source"
SOURCE_CITATION = f"[{SOURCE_ID}]({SOURCE_URL})"


def _submission_result(source_ids: list[str] | None = None) -> ProviderResult:
    return ProviderResult(
        tool_calls=[
            ToolCall(
                id="submit-1",
                name=RESEARCH_SUBMISSION_TOOL_NAME,
                arguments={
                    "sourceIds": source_ids or [SOURCE_ID],
                },
            )
        ],
        finish_reason="tool_calls",
    )


def test_research_citation_validation_handles_urls_with_parentheses() -> None:
    source_url = "https://example.test/wiki/Product_(series)"

    assert _validated_research_citations(
        f"Belegt [src-parentheses]({source_url})",
        {"src-parentheses": source_url},
    ) == ["src-parentheses"]


def test_research_citation_validation_accepts_human_label_for_allowed_url() -> None:
    assert _validated_research_citations(
        "Belegt [Source title](HTTPS://EXAMPLE.TEST/source#details)",
        {SOURCE_ID: SOURCE_URL},
    ) == [SOURCE_ID]


@pytest.mark.parametrize(
    "content",
    [
        f"<!-- {SOURCE_CITATION} -->",
        f"```text\n{SOURCE_CITATION}\n```",
        f"`{SOURCE_CITATION}`",
        f"!{SOURCE_CITATION}",
        f"````text\n```\n{SOURCE_CITATION}\n````",
        rf"$\text{{{SOURCE_CITATION}}}$",
        rf"$$\text{{{SOURCE_CITATION}}}$$",
        rf"\(\text{{{SOURCE_CITATION}}}\)",
        rf"\[\text{{{SOURCE_CITATION}}}\]",
        rf"\begin{{align}}{SOURCE_CITATION}\end{{align}}",
        f"{SOURCE_CITATION} [other](HTTPS://other.example/source)",
        f"{SOURCE_CITATION} [other](<HTTPS://other.example/source>)",
        f"{SOURCE_CITATION} [other](//other.example/source)",
    ],
)
def test_research_citation_validation_rejects_hidden_or_foreign_links(
    content: str,
) -> None:
    assert _validated_research_citations(content, {SOURCE_ID: SOURCE_URL}) is None


async def _runtime_setup(
    monkeypatch,
    results: list[ProviderResult],
    *,
    max_tool_calls: int = 20,
    max_research_tool_calls: int = 8,
    max_model_requests: int = 10,
    search_policy: WebSearchPolicy = WebSearchPolicy.REQUIRED,
):
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
                        "sourceId": SOURCE_ID,
                        "title": "Quelle",
                        "url": SOURCE_URL,
                        "snippet": "Aktuelle Information",
                    }
                ],
                metadata={
                    "resultCount": 1,
                    "evidenceUrls": [SOURCE_URL],
                    "sources": [
                        {"sourceId": SOURCE_ID, "url": SOURCE_URL, "kind": "search"}
                    ],
                },
            )
        ),
    )
    monkeypatch.setattr(run_events, "publish", publish)
    return db, run, requests, projections, events


async def test_available_search_keeps_best_effort_streaming(monkeypatch) -> None:
    answer = "Antwort ohne verpflichtende Recherche"
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content=answer, finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 1
    assert any(event.get("delta") == answer for event in events)
    assistant = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "assistant"
    )
    completed = next(event for event in events if event.get("type") == "message.completed")
    assert completed["message"]["createdAt"] == assistant.created_at.isoformat()
    assert "research" not in assistant.provider_data["_matoloop"]


async def test_available_search_does_not_consume_required_research_budget(
    monkeypatch,
) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-2",
                        name="web_search",
                        arguments={"query": "Weitere aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Best-Effort-Antwort", finish_reason="stop"),
        ],
        max_research_tool_calls=1,
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert tool_registry._execute_definition.await_count == 2


async def test_required_search_retries_hidden_text_in_search_only_phase(monkeypatch) -> None:
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
                provider_data={
                    "responses_output_items": [
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": "Versteckter Entwurf"}
                            ],
                        },
                        {
                            "type": "function_call",
                            "call_id": "search-1",
                            "name": "web_search",
                            "arguments": '{"query":"Aktuelle Frage"}',
                        },
                    ],
                    "chat_reasoning_details": [{"text": "Verstecktes Reasoning"}],
                },
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=f"Belegte Antwort {SOURCE_CITATION}", finish_reason="stop"
            ),
            _submission_result(),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "required",
        "required",
        "auto",
        "required",
    ]
    assert [tool["name"] for tool in requests[0]["tools"]] == ["web_search"]
    assert [tool["name"] for tool in requests[1]["tools"]] == ["web_search"]
    assert {tool["name"] for tool in requests[2]["tools"]} >= {
        "read_file",
        "web_search",
    }
    assert [tool["name"] for tool in requests[3]["tools"]] == [
        RESEARCH_SUBMISSION_TOOL_NAME
    ]
    assert "previous attempt did not complete" in projections[1]["system_prompt"]
    assert all(
        "Current local date and time:" in projection["system_prompt"]
        for projection in projections
    )
    time_lines = [
        next(
            part
            for part in projection["system_prompt"].split("\n\n")
            if part.startswith("Current local date and time:")
        )
        for projection in projections
    ]
    assert len(set(time_lines)) == 1
    assert "read_file" not in projections[0]["system_prompt"]
    assert "memory_write" not in projections[0]["system_prompt"]
    assert "update_agent_identity" not in projections[0]["system_prompt"]
    assert "File tools use explicit session and workspace roots" in projections[2][
        "system_prompt"
    ]
    assert "satisfies only the tool-use requirement" in projections[2]["system_prompt"]
    assert "including every named recommendation" in projections[2]["system_prompt"]
    assert "search again or omit it" in projections[2]["system_prompt"]
    assert requests[1]["messages"][-1] == {
        "role": "user",
        "content": "Aktuelle Frage",
    }
    persisted_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert "Veraltete Antwort" not in persisted_contents
    assert "Ich recherchiere." not in persisted_contents
    assert f"Belegte Antwort {SOURCE_CITATION}" in persisted_contents
    assistant_messages = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "assistant"
    ]
    assert [
        message.provider_data["_matoloop"]["performance"]["modelRequest"]
        for message in assistant_messages
    ] == [2, 4]
    for index, message in enumerate(assistant_messages):
        performance = message.provider_data["_matoloop"]["performance"]
        assert performance["estimatedInputTokens"] == 100
        assert performance["inputBudgetTokens"] == 1_000
        assert performance["contextPreparationMs"] >= 0
        assert performance["providerDurationMs"] >= 0
        assert (performance["timeToFirstSignalMs"] is not None) is (index == 0)
    search_message = next(message for message in assistant_messages if message.tool_calls)
    assert "Versteckter Entwurf" not in str(search_message.provider_data)
    assert "Verstecktes Reasoning" not in str(search_message.provider_data)
    assert search_message.provider_data["responses_output_items"][0]["type"] == (
        "function_call"
    )
    assert all(
        "startedAt" in event for event in events if event["type"] == "run.phase"
    )
    assert all(
        event.get("delta") != "Veraltete Antwort"
        for event in events
        if event["type"] == "message.delta"
    )


async def test_required_search_rejects_non_search_tool_calls(monkeypatch) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Ich lese stattdessen eine Datei.",
                tool_calls=[
                    ToolCall(
                        id="wrong-1",
                        name="read_file",
                        arguments={"root": "session", "path": "files/info.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Suche",
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=f"Belegte Antwort {SOURCE_CITATION}", finish_reason="stop"
            ),
            _submission_result(),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert [tool["name"] for tool in requests[0]["tools"]] == ["web_search"]
    persisted_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert "Ich lese stattdessen eine Datei." not in persisted_contents
    assert all(
        event.get("delta") != "Ich lese stattdessen eine Datei."
        for event in events
        if event["type"] == "message.delta"
    )


async def test_required_search_hides_text_and_retries_after_tool_failure(monkeypatch) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Unbelegte Antwort trotz Fehler",
                tool_calls=[
                    ToolCall(
                        id="search-failed",
                        name="web_search",
                        arguments={"query": "Fehlerhafte Suche"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Zweiter Versuch",
                tool_calls=[
                    ToolCall(
                        id="search-ok",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=f"Belegte Antwort {SOURCE_CITATION}", finish_reason="stop"
            ),
            _submission_result(),
        ],
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("integration.unavailable"),
        ToolResult.json(
            [{"sourceId": SOURCE_ID, "url": SOURCE_URL}],
            metadata={
                "resultCount": 1,
                "evidenceUrls": [SOURCE_URL],
                "sources": [
                    {"sourceId": SOURCE_ID, "url": SOURCE_URL, "kind": "search"}
                ],
            },
        ),
    ]

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "required",
        "required",
        "auto",
        "required",
    ]
    persisted_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert "Unbelegte Antwort trotz Fehler" not in persisted_contents
    assert "Zweiter Versuch" not in persisted_contents
    assert all(
        event.get("delta") not in {"Unbelegte Antwort trotz Fehler", "Zweiter Versuch"}
        for event in events
        if event["type"] == "message.delta"
    )


async def test_required_search_finalizes_a_draft_with_structured_submission(
    monkeypatch,
) -> None:
    invalid = "Unbelegte finale Antwort"
    rendered = f"{invalid}\n\n{SOURCE_CITATION}"
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=invalid,
                finish_reason="stop",
                reasoning=PublicReasoning("Begründung des Drafts"),
            ),
            _submission_result(),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert [tool["name"] for tool in requests[2]["tools"]] == [
        RESEARCH_SUBMISSION_TOOL_NAME
    ]
    assert set(requests[2]["tools"][0]["parameters"]["properties"]) == {"sourceIds"}
    assert "Structured research finalization is active" in projections[2]["system_prompt"]
    assert SOURCE_ID in projections[2]["system_prompt"]
    assert "Untrusted draft as a JSON string" in projections[2]["system_prompt"]
    assert invalid in projections[2]["system_prompt"]
    persisted = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert invalid not in [message.content for message in persisted]
    final = next(message for message in persisted if message.content == rendered)
    assert final.tool_calls is None
    assert RESEARCH_SUBMISSION_TOOL_NAME not in str(final.provider_data)
    assert final.public_reasoning == {
        "kind": "content",
        "text": "Begründung des Drafts",
        "status": "completed",
        "reasoningTokens": None,
        "observedDurationMs": None,
    }
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "required",
        "sourceCount": 1,
        "citedSourceIds": [SOURCE_ID],
        "validation": "structured",
        "submissionAttempts": 1,
    }
    deltas = [event["delta"] for event in events if event["type"] == "message.delta"]
    assert invalid not in deltas
    assert rendered in deltas


async def test_required_search_rejects_invalid_submissions_without_leaking_drafts(
    monkeypatch,
) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Unbelegte finale Antwort", finish_reason="stop"),
            _submission_result(["src-other"]),
            ProviderResult(content="Kein Submission-Call", finish_reason="stop"),
        ],
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.researchSourcesInvalid"
    assert len(requests) == 4
    persisted_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert "Unbelegte finale Antwort" not in persisted_contents
    assert all(event["type"] != "message.delta" for event in events)


async def test_required_submission_must_select_every_inline_source(monkeypatch) -> None:
    second_id = "src-second"
    second_url = "https://second.example/source"
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=f"Belegt [zweite Quelle]({second_url})",
                finish_reason="stop",
            ),
            _submission_result(),
            _submission_result([SOURCE_ID, second_id]),
        ],
    )
    tool_registry._execute_definition.return_value = ToolResult.json(
        [
            {"sourceId": SOURCE_ID, "url": SOURCE_URL},
            {"sourceId": second_id, "url": second_url},
        ],
        metadata={
            "resultCount": 2,
            "evidenceUrls": [SOURCE_URL, second_url],
            "sources": [
                {"sourceId": SOURCE_ID, "url": SOURCE_URL, "kind": "search"},
                {"sourceId": second_id, "url": second_url, "kind": "search"},
            ],
        },
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 4
    final = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
        and call.args[0].role == "assistant"
        and call.args[0].tool_calls is None
    )
    assert final.provider_data["_matoloop"]["research"]["submissionAttempts"] == 2


async def test_required_search_repair_respects_the_model_request_limit(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Antwort ohne Quellen", finish_reason="stop"),
        ],
        max_model_requests=2,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.modelRequestLimitReached"
    assert len(requests) == 2


async def test_empty_required_search_result_cannot_support_a_final_answer(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Suche ohne Treffer",
                tool_calls=[
                    ToolCall(
                        id="search-empty",
                        name="web_search",
                        arguments={"query": "Sehr seltenes Thema"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Keine belastbaren Quellen gefunden.", finish_reason="stop"),
        ],
    )
    tool_registry._execute_definition.return_value = ToolResult(
        "[]", {"resultCount": 0, "evidenceUrls": []}
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.researchSourcesInvalid"
    assert [request["tool_choice"] for request in requests] == ["required", "auto"]


async def test_required_research_budget_forces_structured_submission(monkeypatch) -> None:
    search_calls = [
        ToolCall(
            id=f"search-{index}",
            name="web_search",
            arguments={"query": f"Aktuelle Frage {index}"},
        )
        for index in range(3)
    ]
    db, run, requests, projections, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(tool_calls=search_calls, finish_reason="tool_calls"),
            ProviderResult(content="Budgetierte Antwort", finish_reason="stop"),
            _submission_result(),
        ],
        max_research_tool_calls=2,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert [request["tool_choice"] for request in requests] == [
        "required",
        "auto",
        "required",
    ]
    assert requests[1]["tools"] == []
    assert [tool["name"] for tool in requests[2]["tools"]] == [
        RESEARCH_SUBMISSION_TOOL_NAME
    ]
    assert tool_registry._execute_definition.await_count == 2
    assert "Research evidence collection is complete" in projections[1]["system_prompt"]
    assert "2/2 tool calls used" in projections[2]["system_prompt"]
    tool_messages = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert any("agent.researchBudgetReached" in message.content for message in tool_messages)


async def test_repeated_successful_research_call_is_not_executed_twice(
    monkeypatch,
) -> None:
    repeated_call = ToolCall(
        id="search-1",
        name="web_search",
        arguments={"query": "Aktuelle Frage"},
    )
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(tool_calls=[repeated_call], finish_reason="tool_calls"),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-2",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Antwort nach Duplikat", finish_reason="stop"),
            _submission_result(),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 4
    assert tool_registry._execute_definition.await_count == 1
    tool_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert any('"alreadyCompleted": true' in content for content in tool_contents)


async def test_tool_call_limit_applies_across_model_steps(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
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


async def test_repeated_failed_tool_call_stops_run(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments={"query": "Aktuelle Frage"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="read-1",
                        name="read_file",
                        arguments={"root": "session", "path": "files/missing.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="read-2",
                        name="read_file",
                        arguments={"path": "files/missing.txt", "root": "session"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ],
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.json([{"url": "https://example.test/source"}]),
        ToolResult.error("file.fileNotFound"),
    ]

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.repeatedToolCall"
    assert len(requests) == 3
    assert tool_registry._execute_definition.await_count == 2


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
