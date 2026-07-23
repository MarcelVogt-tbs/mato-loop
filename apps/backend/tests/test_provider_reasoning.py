import json

import httpx

from mato_loop_backend.providers.base import ProviderRequest
from mato_loop_backend.providers.openai_compatible import (
    _chat_messages,
    _chat_result,
    _responses_body,
    _responses_input,
    _responses_result,
    chat_completions,
    responses_api,
)
from mato_loop_backend.providers.reasoning import (
    TaggedReasoningStream,
    split_tagged_reasoning,
)


def test_tagged_reasoning_is_separated_from_answer() -> None:
    assert split_tagged_reasoning("<think>Prüfen und vergleichen.</think>Fertige Antwort", final=True) == (
        "Fertige Antwort",
        "Prüfen und vergleichen.",
    )
    assert split_tagged_reasoning("Normale Antwort", final=True) == ("Normale Antwort", "")
    assert split_tagged_reasoning(
        "<|channel>thought\nInterne Ausgabe<channel|><|channel>response\nAntwort",
        final=True,
    ) == ("Antwort", "Interne Ausgabe")


def test_tagged_reasoning_stream_handles_marker_chunk_boundaries() -> None:
    stream = TaggedReasoningStream()

    assert stream.add("<thi") == ("", "")
    assert stream.add("nk>Plan") == ("", "Plan")
    assert stream.add(" ausarbeiten</think>Antwort") == ("Antwort", " ausarbeiten")
    assert stream.finish() == ("Antwort", "Plan ausarbeiten")


def test_channel_response_marker_can_span_chunks() -> None:
    stream = TaggedReasoningStream()

    assert stream.add("<|channel>thought\nPlan<channel|><|chan") == ("", "Plan")
    assert stream.add("nel>response\nAntwort") == ("Antwort", "")
    assert stream.finish() == ("Antwort", "Plan")


def test_chat_result_exposes_explicit_reasoning_and_usage() -> None:
    result = _chat_result(
        {
            "choices": [
                {
                    "message": {
                        "reasoning_content": "Optionen verglichen.",
                        "content": "Ergebnis",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 8,
                "completion_tokens_details": {"reasoning_tokens": 5},
            },
        }
    )

    assert result.content == "Ergebnis"
    assert result.reasoning is not None
    assert result.reasoning.text == "Optionen verglichen."
    assert result.reasoning.reasoning_tokens == 5


def test_chat_result_preserves_private_reasoning_details_separately() -> None:
    details = [{"type": "reasoning.encrypted", "data": "opaque"}]
    result = _chat_result(
        {
            "choices": [
                {
                    "message": {
                        "reasoning": "Öffentlich begründet.",
                        "reasoning_details": details,
                        "content": "Antwort",
                    }
                }
            ]
        }
    )

    assert result.reasoning is not None
    assert result.reasoning.text == "Öffentlich begründet."
    assert result.provider_data["chat_reasoning_details"] == details
    assert "opaque" not in result.reasoning.text


def test_chat_reasoning_details_replay_only_in_matching_scope() -> None:
    scope = {
        "adapterType": "openai-compatible",
        "providerId": "provider-1",
        "modelConfigId": "model-1",
        "credentialId": None,
        "endpoint": "https://provider.test/v1",
    }
    message = {
        "role": "assistant",
        "content": "Antwort",
        "provider_data": {
            "chat_reasoning_details": [{"data": "opaque"}],
            "continuation_scope": scope,
        },
    }

    assert "reasoning_details" in _chat_messages([message], scope)[0]
    assert "reasoning_details" not in _chat_messages(
        [message], {**scope, "modelConfigId": "model-2"}
    )[0]


def test_responses_result_exposes_summary_but_not_encrypted_state() -> None:
    result = _responses_result(
        {
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "encrypted_content": "opaque-secret-state",
                    "summary": [{"type": "summary_text", "text": "Kurz begründet."}],
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Antwort"}],
                },
            ],
        }
    )

    assert result.reasoning is not None
    assert result.reasoning.text == "Kurz begründet."
    assert "opaque-secret-state" not in result.reasoning.text
    assert result.provider_data["reasoning_encrypted_content"] == "opaque-secret-state"


def test_tagged_responses_reasoning_keeps_content_kind() -> None:
    result = _responses_result(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "<think>Plan</think>Antwort"}
                    ],
                }
            ],
        }
    )

    assert result.content == "Antwort"
    assert result.reasoning is not None
    assert result.reasoning.kind == "content"


def test_responses_summary_request_and_scoped_replay() -> None:
    scope = {
        "adapterType": "openai-compatible",
        "providerId": "provider-1",
        "modelConfigId": "model-1",
        "credentialId": None,
        "endpoint": "https://provider.test/v1",
    }
    request = ProviderRequest(
        model="reasoning-model",
        messages=[],
        reasoning_summary=True,
        continuation_scope=scope,
    )
    assert _responses_body(request)["reasoning"] == {"summary": "auto"}
    assert _responses_body(request)["include"] == ["reasoning.encrypted_content"]

    message = {
        "role": "assistant",
        "content": "Antwort",
        "provider_data": {
            "reasoning_encrypted_content": "encrypted",
            "continuation_scope": scope,
        },
    }
    _, matching = _responses_input([message], scope)
    _, foreign = _responses_input([message], {**scope, "providerId": "provider-2"})

    assert matching[0]["type"] == "reasoning"
    assert all(item.get("type") != "reasoning" for item in foreign)


async def test_chat_stream_routes_tagged_reasoning_separately(monkeypatch) -> None:
    events = [
        {"choices": [{"delta": {"content": "<thi"}}]},
        {"choices": [{"delta": {"content": "nk>Plan"}}]},
        {"choices": [{"delta": {"content": "en</think>Antwort"}, "finish_reason": "stop"}]},
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text=body))
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    answers = []
    reasoning = []

    async def on_delta(text: str) -> None:
        answers.append(text)

    async def on_reasoning(text: str, kind: str) -> None:
        reasoning.append((text, kind))

    result = await chat_completions(
        base_url="https://provider.test/v1",
        api_key=None,
        request=ProviderRequest(model="local", messages=[]),
        on_delta=on_delta,
        on_reasoning=on_reasoning,
    )

    assert answers == ["Antwort"]
    assert reasoning == [("Plan", "content"), ("en", "content")]
    assert result.content == "Antwort"
    assert result.reasoning is not None
    assert result.reasoning.text == "Planen"


async def test_responses_stream_routes_summary_separately(monkeypatch) -> None:
    events = [
        {"type": "response.reasoning_summary_text.delta", "delta": "Kurz "},
        {"type": "response.reasoning_summary_text.delta", "delta": "begründet."},
        {"type": "response.output_text.delta", "delta": "Antwort"},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "output": [
                    {
                        "type": "reasoning",
                        "summary": [{"type": "summary_text", "text": "Kurz begründet."}],
                    }
                ],
            },
        },
    ]
    body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, text=body))
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    reasoning = []

    async def on_reasoning(text: str, kind: str) -> None:
        reasoning.append((text, kind))

    result = await responses_api(
        url="https://provider.test/v1/responses",
        headers={},
        request=ProviderRequest(model="reasoning", messages=[]),
        on_delta=None,
        on_reasoning=on_reasoning,
    )

    assert reasoning == [("Kurz ", "summary"), ("begründet.", "summary")]
    assert result.content == "Antwort"
    assert result.reasoning is not None
    assert result.reasoning.text == "Kurz begründet."
