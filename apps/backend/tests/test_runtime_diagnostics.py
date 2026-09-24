from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from test_agent_runtime_events import _SessionContext
from test_required_web_search_runtime import _assistant_messages, _runtime_setup, _search_call

import mato_loop_backend.agent.runtime as mock_agent_runtime
import mato_loop_backend.agent.summarization as mock_agent_summarization
import mato_loop_backend.agent.titles as mock_agent_titles
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.models import AgentRun, Message, WebSearchPolicy
from mato_loop_backend.providers.base import ProviderResult
from mato_loop_backend.tools.catalog import tool_registry


@pytest.mark.parametrize("failure", ["length", "max_tokens", "max_output_tokens", "MAX_TOKENS", "after_generation", "after_commit", "next_request"])
async def test_canonical_error_retains_only_current_safe_generation_diagnostics(monkeypatch, failure):
    result = ProviderResult(
        content="Partial answer",
        finish_reason=failure if failure not in {"after_generation", "after_commit", "next_request"} else "stop",
        usage={"input_tokens": 100, "output_tokens": 2048, "reasoning_tokens": 2000,
               "secret": "private", "total_tokens": -1, "cached_input_tokens": True},
        provider_data={
            "secret": "private",
            "responses_output_items": [{"encrypted_content": "private"}],
            "_matoloop": {"secret": "private"},
            "output_budget": {"allocatedOutputTokens": 2048, "transmittedMaxOutputTokens": 2048,
                              "secret": "private"},
        },
    )
    if failure in {"after_commit", "next_request"}:
        result.tool_calls = [_search_call("search", "question")]
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch, [result], search_policy=WebSearchPolicy.AVAILABLE,
    )
    session = db.get.return_value
    user = (await AgentRuntime._records(db, session.id))[0]
    messages = {user.id: user}

    def add(message):
        message.id = uuid4()
        messages[message.id] = message

    db.add.side_effect = add
    db.get.side_effect = lambda entity, key, **kw: (
        run if entity is AgentRun else messages.get(key) if entity is Message else session
    )
    db.scalar.return_value = None
    def _mock_value():
        return _SessionContext(db)
    for _mock_target in (mock_agent_runtime, mock_agent_summarization, mock_agent_titles,):
        monkeypatch.setattr(_mock_target, 'AsyncSessionFactory', _mock_value)
    monkeypatch.setattr(AgentRuntime, "_ensure_user_active", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_terminalize_incomplete_tool_calls", AsyncMock())
    monkeypatch.setattr(AgentRuntime, "_deliver_run_push", AsyncMock())
    if failure == "after_generation":
        monkeypatch.setattr(mock_agent_runtime.EpisodeIndexService, "enqueue", AsyncMock(side_effect=RuntimeError("private")))
    if failure == "after_commit":
        async def fail_after_commit(_session):
            if any(event["type"] == "message.completed" for event in events):
                db.commit.assert_awaited()
                raise RuntimeError("private")

        db.refresh.side_effect = fail_after_commit

    await AgentRuntime._run(run.id)

    errors = [message for message in _assistant_messages(db) if message.error_code]
    assert len(errors) == 1
    error = errors[0]
    assert run.status == "failed"
    assert run.result_message_id == error.id
    assert error.error_run_id == run.id
    assert error.error_code == ("agent.failed" if failure in {"after_generation", "after_commit", "next_request"} else "agent.outputLimitReached")
    assert error.content == ""
    assert error.tool_calls is None
    assert len(requests) == (2 if failure == "next_request" else 1)
    if failure not in {"after_commit", "next_request"}:
        tool_registry._execute_definition.assert_not_awaited()
        assert set(error.provider_data) == {"usage", "finish_reason", "_matoloop"}
        assert error.provider_data["usage"] == {"input_tokens": 100, "output_tokens": 2048, "reasoning_tokens": 2000}
        assert error.provider_data["finish_reason"] == result.finish_reason
        performance = error.provider_data["_matoloop"]["performance"]
        assert performance["modelRequest"] == 1
        assert performance["allocatedOutputTokens"] == performance["transmittedMaxOutputTokens"] == 2048
        assert performance["providerDurationMs"] >= 0
        assert performance["firstTextMs"] >= 0
        assert performance["activeDurationMs"] >= 0
    else:
        assert set(error.provider_data["_matoloop"]["performance"]) == {"activeDurationMs"}
        assert "usage" not in error.provider_data
        assert "finish_reason" not in error.provider_data
        assert _assistant_messages(db)[0].provider_data["_matoloop"]["performance"]["modelRequest"] == 1
        if failure == "after_commit":
            tool_registry._execute_definition.assert_not_awaited()
    assert "private" not in str(error.provider_data)
    assert "private" not in str(events)
    assert sum(event["type"] == "message.completed" and event["message"].get("kind") == "runtime_error" for event in events) == 1
    assert sum(event["type"] == "run.failed" for event in events) == 1
    assert next(event for event in events if event["type"] == "run.failed")["runId"] == str(run.id)
