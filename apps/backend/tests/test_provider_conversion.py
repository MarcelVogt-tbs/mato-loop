import json

import httpx
import pytest

from mato_loop_backend.providers.base import ProviderError, ProviderMedia, ProviderRequest
from mato_loop_backend.providers.openai_compatible import (
    _chat_body,
    _chat_messages,
    _normalize_finish_reason,
    _responses_body,
    _responses_finish_reason,
    _responses_input,
    _responses_result,
    chat_completions,
    responses_api,
)


def test_chat_messages_convert_internal_tool_calls() -> None:
    messages = _chat_messages(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "name": "read_file", "arguments": {"path": "README.md"}}
                ],
            },
            {"role": "tool", "content": "content", "tool_call_id": "call-1"},
        ]
    )

    assert messages[0]["tool_calls"][0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "README.md"}',
    }
    assert messages[1]["tool_call_id"] == "call-1"


def test_responses_input_keeps_function_call_and_output() -> None:
    instructions, items = _responses_input(
        [
            {"role": "system", "content": "Be precise."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "call-1", "name": "list_directory", "arguments": {"path": "."}}
                ],
            },
            {"role": "tool", "content": "[]", "tool_call_id": "call-1"},
        ]
    )

    assert instructions == "Be precise."
    assert items[0]["type"] == "function_call"
    assert items[1] == {"type": "function_call_output", "call_id": "call-1", "output": "[]"}


def test_media_follows_complete_parallel_tool_result_block_for_both_protocols() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call-1", "name": "capture", "arguments": {}},
                {"id": "call-2", "name": "inspect", "arguments": {}},
            ],
        },
        {"role": "tool", "content": "captured", "tool_call_id": "call-1"},
        {"role": "tool", "content": "inspected", "tool_call_id": "call-2"},
    ]
    media = [
        ProviderMedia(
            message_index=1,
            mime_type="image/png",
            data_url="data:image/png;base64,aW1hZ2U=",
            status="ready",
        )
    ]

    chat = _chat_messages(messages, media=media)
    _, responses = _responses_input(messages, media=media)

    assert [item["role"] for item in chat] == ["assistant", "tool", "tool", "user"]
    assert chat[-1]["content"] == [
        {"type": "text", "text": "[Session image]"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aW1hZ2U="},
        },
    ]
    assert [item.get("type") for item in responses] == [
        "function_call",
        "function_call",
        "function_call_output",
        "function_call_output",
        None,
    ]
    assert responses[-1]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,aW1hZ2U=",
    }
    assert responses[-1]["content"][0] == {
        "type": "input_text",
        "text": "[Session image]",
    }


def test_unavailable_media_becomes_text_marker_without_image_payload() -> None:
    messages = [{"role": "tool", "content": "captured", "tool_call_id": "call-1"}]
    media = [ProviderMedia(message_index=0, mime_type="image/png")]

    chat = _chat_messages(messages, media=media)
    _, responses = _responses_input(messages, media=media)

    assert [part["type"] for part in chat[1]["content"]] == ["text", "text"]
    assert [part["type"] for part in responses[1]["content"]] == [
        "input_text",
        "input_text",
    ]
    assert "data:" not in json.dumps({"chat": chat, "responses": responses})


def test_chat_messages_serialize_vllm_audio_and_video_parts() -> None:
    request = ProviderRequest(
        model="multimodal",
        messages=[{"role": "user", "content": "Beschreibe die Medien"}],
        media=[
            ProviderMedia(
                message_index=0,
                mime_type="video/mp4",
                data_url="data:video/mp4;base64,dmlkZW8=",
                status="ready",
                modality="video",
            ),
            ProviderMedia(
                message_index=0,
                mime_type="audio/wav",
                data_base64="YXVkaW8=",
                format="wav",
                status="ready",
                modality="audio",
            ),
        ],
    )

    content = _chat_body(request)["messages"][-1]["content"]

    assert content[1] == {
        "type": "video_url",
        "video_url": {"url": "data:video/mp4;base64,dmlkZW8="},
    }
    assert content[2] == {
        "type": "input_audio",
        "input_audio": {"data": "YXVkaW8=", "format": "wav"},
    }
    with pytest.raises(ProviderError, match="Responses API"):
        _responses_body(request)


def test_responses_input_replays_reasoning_before_assistant_output() -> None:
    scope = {
        "adapterType": "openai-compatible",
        "providerId": "provider-1",
        "modelConfigId": "model-1",
        "credentialId": None,
        "endpoint": "https://provider.test/v1",
    }
    _, items = _responses_input(
        [
            {
                "role": "assistant",
                "content": "Ergebnis",
                "tool_calls": [{"id": "call-1", "name": "read_file", "arguments": {}}],
                "provider_data": {
                    "reasoning_encrypted_content": "encrypted",
                    "continuation_scope": scope,
                },
            },
            {"role": "tool", "content": "Inhalt", "tool_call_id": "call-1"},
        ],
        scope,
    )

    assert [item.get("type") for item in items] == [
        "reasoning",
        None,
        "function_call",
        "function_call_output",
    ]


def test_payloads_convert_model_options_for_each_protocol() -> None:
    request = ProviderRequest(
        model="local-model",
        messages=[{"role": "user", "content": "Hallo"}],
        tools=[{"name": "read_file", "description": "Datei lesen", "parameters": {}}],
        stream=False,
        supports_parallel_tool_calls=True,
        max_output_tokens=512,
        temperature=0.25,
    )

    chat_body = _chat_body(request)
    responses_body = _responses_body(request)

    assert chat_body["max_tokens"] == 512
    assert "max_output_tokens" not in chat_body
    assert responses_body["max_output_tokens"] == 512
    assert "max_tokens" not in responses_body
    assert chat_body["parallel_tool_calls"] is True
    assert responses_body["parallel_tool_calls"] is True
    assert chat_body["tool_choice"] == responses_body["tool_choice"] == "auto"
    assert chat_body["temperature"] == responses_body["temperature"] == 0.25
    assert chat_body["stream"] is responses_body["stream"] is False


def test_responses_payload_forwards_optional_prompt_cache_key() -> None:
    request = ProviderRequest(
        model="gpt-5.6",
        messages=[],
        prompt_cache_key="session-1",
    )

    assert _responses_body(request)["prompt_cache_key"] == "session-1"
    request.prompt_cache_key = None
    assert "prompt_cache_key" not in _responses_body(request)


def test_chat_stream_can_request_usage() -> None:
    request = ProviderRequest(
        model="local-model",
        messages=[],
        stream=True,
        include_usage=True,
    )

    assert _chat_body(request)["stream_options"] == {"include_usage": True}

    request.stream = False

    assert "stream_options" not in _chat_body(request)


def test_payloads_omit_tool_options_without_tools_or_capability() -> None:
    request = ProviderRequest(model="local-model", messages=[], tools=[])

    assert "tool_choice" not in _chat_body(request)
    assert "parallel_tool_calls" not in _chat_body(request)
    assert "tool_choice" not in _responses_body(request)
    assert "parallel_tool_calls" not in _responses_body(request)


def test_payloads_forward_required_tool_choice_for_each_protocol() -> None:
    request = ProviderRequest(
        model="local-model",
        messages=[],
        tools=[{"name": "web_search", "parameters": {}}],
        tool_choice="required",
    )

    assert _chat_body(request)["tool_choice"] == "required"
    assert _responses_body(request)["tool_choice"] == "required"

    request.tools = [{"name": "read_file", "parameters": {}}]

    assert "parallel_tool_calls" not in _chat_body(request)
    assert "parallel_tool_calls" not in _responses_body(request)


def test_responses_finish_reason_normalizes_output_limit() -> None:
    assert (
        _responses_finish_reason(
            {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}
        )
        == "length"
    )


def test_chat_finish_reason_normalizes_output_limit() -> None:
    assert _normalize_finish_reason("max_tokens") == "length"
    assert _normalize_finish_reason("max_output_tokens") == "length"
    assert _normalize_finish_reason("stop") == "stop"
    assert _normalize_finish_reason(None) is None


def test_failed_responses_result_raises_safe_provider_error() -> None:
    with pytest.raises(ProviderError) as exc_info:
        _responses_result({"status": "failed", "error": {"message": "secret upstream"}})

    assert exc_info.value.public_code == "provider.requestFailed"
    assert exc_info.value.status_code == 503
    assert "secret upstream" not in str(exc_info.value)


def test_rate_limited_responses_result_preserves_safe_error_code() -> None:
    with pytest.raises(ProviderError) as exc_info:
        _responses_result(
            {
                "status": "failed",
                "error": {
                    "code": "rate_limit_exceeded",
                    "message": "secret upstream",
                },
            }
        )

    assert exc_info.value.public_code == "provider.rateLimited"
    assert exc_info.value.status_code == 429
    assert "secret upstream" not in str(exc_info.value)


async def test_chat_completions_supports_non_streaming(monkeypatch) -> None:
    sent_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent_body.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Fertig",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {"name": "read_file", "arguments": '{"path":"a"}'},
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
            },
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    deltas = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    result = await chat_completions(
        base_url="http://local.test/v1",
        api_key=None,
        request=ProviderRequest(
            model="local-model",
            messages=[{"role": "user", "content": "Los"}],
            stream=False,
        ),
        on_delta=on_delta,
    )

    assert sent_body["stream"] is False
    assert result.content == "Fertig"
    assert result.tool_calls[0].arguments == {"path": "a"}
    assert result.usage == {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
    assert result.finish_reason == "tool_calls"
    assert deltas == ["Fertig"]


async def test_responses_supports_non_streaming(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is False
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Antwort"}],
                    },
                    {
                        "type": "function_call",
                        "call_id": "call-2",
                        "name": "list_directory",
                        "arguments": "{}",
                    },
                ],
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
            },
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    result = await responses_api(
        url="http://local.test/v1/responses",
        headers={"Content-Type": "application/json"},
        request=ProviderRequest(model="local-model", messages=[], stream=False),
        on_delta=None,
    )

    assert result.content == "Antwort"
    assert result.tool_calls[0].id == "call-2"
    assert result.usage == {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4}
    assert result.finish_reason == "stop"
