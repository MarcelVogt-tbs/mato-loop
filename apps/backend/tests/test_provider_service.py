import base64
import re
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.attachments import AttachmentStorage, validate_image
from mato_loop_backend.providers import service as service_module
from mato_loop_backend.providers.base import (
    ProviderError,
    ProviderResult,
    PublicReasoning,
    ToolCall,
)
from mato_loop_backend.providers.openai_compatible import _responses_body, discover_models
from mato_loop_backend.providers.service import ProviderService, _with_retries
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig, TrustLevel


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _attachment_reference(storage_key: str, data: bytes) -> dict:
    validated = validate_image(data, "image/png")
    return {
        "kind": "image",
        "mime_type": validated.mime_type,
        "size_bytes": validated.size_bytes,
        "sha256": validated.sha256,
        "storage_key": storage_key,
    }


async def test_complete_passes_model_capabilities_and_explicit_timeout(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        pseudonymization_enabled=False,
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="local-model",
        config={
            "max_output_tokens": 768,
            "supports_parallel_tool_calls": True,
            "supports_streaming": False,
            "temperature": 0.4,
        },
    )
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return ProviderResult(
            content="OK",
            provider_data={"chat_reasoning_details": [{"data": "opaque"}]},
        )

    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    result = await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": "Test"}],
        tools=[{"name": "read_file", "parameters": {}}],
        tool_choice="required",
        timeout=12,
        input_trust_level=TrustLevel.PUBLIC,
    )

    request = captured["request"]
    assert result.content == "OK"
    assert request.max_output_tokens == 768
    assert request.supports_parallel_tool_calls is True
    assert request.stream is False
    assert request.temperature == 0.4
    assert request.timeout == 12
    assert request.tool_choice == "required"
    assert result.provider_data["continuation_scope"]["providerId"] == str(provider.id)


async def test_public_complete_pseudonymizes_and_restores_request_scope(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(
        name="Public",
        adapter_type="openai-compatible",
        config={"base_url": "http://public.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="public-model")
    streamed: list[str] = []
    reasoned: list[tuple[str, str]] = []

    async def fake_chat_completions(**kwargs):
        request = kwargs["request"]
        protected = request.messages[0]["content"]
        assert "anna@example.com" not in protected
        assert "provider_data" not in request.messages[0]
        markers = re.findall(r"\[\[MLP_[A-F0-9]+_\d{4}\]\]", protected)
        assert markers
        pseudonym = markers[-1]
        await kwargs["on_delta"]("Kontakt: " + pseudonym[:7])
        await kwargs["on_delta"](pseudonym[7:])
        await kwargs["on_reasoning"]("Pruefe " + pseudonym, "summary")
        return ProviderResult(
            content="Antwort an " + pseudonym,
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="send_mail",
                    arguments={"address": pseudonym, "nested": [pseudonym]},
                )
            ],
            provider_data={
                "responses_output_items": [{"id": "opaque"}],
                "reasoning_encrypted_content": "opaque",
                "chat_reasoning_details": [{"data": "opaque"}],
            },
            reasoning=PublicReasoning(text="Gedanke zu " + pseudonym),
        )

    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    result = await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[
            {
                "role": "user",
                "content": "Schreibe an anna@example.com",
                "provider_data": {"responses_output_items": [{"id": "previous"}]},
            }
        ],
        tools=[],
        on_delta=lambda text: _append_async(streamed, text),
        on_reasoning=lambda text, kind: _append_async(reasoned, (text, kind)),
        input_trust_level=TrustLevel.PRIVATE,
    )

    assert "".join(streamed) == "Kontakt: anna@example.com"
    assert "".join(text for text, _kind in reasoned) == "Pruefe anna@example.com"
    assert result.content == "Antwort an anna@example.com"
    assert result.tool_calls[0].arguments == {
        "address": "anna@example.com",
        "nested": ["anna@example.com"],
    }
    assert result.reasoning is not None
    assert result.reasoning.text == "Gedanke zu anna@example.com"
    assert result.provider_data == {}


async def _append_async(items: list, value) -> None:
    items.append(value)


async def test_codex_complete_uses_subscription_compatible_responses_payload(monkeypatch) -> None:
    credential_id = uuid4()
    credential = object()
    provider = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=credential_id,
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="openai-codex/gpt-5.6-sol",
        supports_parallel_tool_calls=True,
        config={"temperature": 0.5},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = credential
    payload = {
        "access": "access",
        "refresh": "refresh",
        "account_id": "account",
    }
    captured = {}

    async def fake_responses_api(**kwargs):
        captured.update(kwargs)
        captured["body"] = _responses_body(
            kwargs["request"], kwargs.get("extra_body")
        )
        return ProviderResult(content="OK")

    monkeypatch.setattr(service_module, "read_credential", lambda _credential: payload)
    monkeypatch.setattr(
        service_module,
        "_refresh_codex_credential",
        AsyncMock(return_value=payload),
    )
    monkeypatch.setattr(service_module, "responses_api", fake_responses_api)

    result = await ProviderService.complete(
        db,
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": "Test"}],
        tools=[{"name": "read_file", "parameters": {}}],
        max_output_tokens=1_024,
        input_trust_level=TrustLevel.PUBLIC,
    )

    request = captured["request"]
    assert result.content == "OK"
    assert request.model == "gpt-5.6-sol"
    assert request.max_output_tokens is None
    assert request.temperature is None
    assert request.tools == [{"name": "read_file", "parameters": {}}]
    assert request.supports_parallel_tool_calls is True
    assert "extra_body" not in captured
    assert "max_output_tokens" not in captured["body"]
    assert "temperature" not in captured["body"]
    assert "text" not in captured["body"]


async def test_complete_does_not_touch_attachment_storage_without_image_capability(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="text-only", supports_image_input=False)
    captured = {}

    def fail_storage():
        raise AssertionError("AttachmentStorage darf nicht geoeffnet werden")

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return ProviderResult(content="OK")

    monkeypatch.setattr(service_module, "AttachmentStorage", fail_storage)
    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[
            {
                "role": "tool",
                "content": "Screenshot",
                "tool_call_id": "call-1",
                "attachments": [_attachment_reference("ab/missing", _png_bytes())],
            }
        ],
        tools=[],
        input_trust_level=TrustLevel.PUBLIC,
    )

    request = captured["request"]
    assert "attachments" not in request.messages[0]
    assert request.media == []


async def test_complete_materializes_valid_image_without_leaking_storage_key(
    monkeypatch, tmp_path: Path
) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="vision", supports_image_input=True)
    storage = AttachmentStorage(tmp_path / "attachments")
    data = _png_bytes()
    storage_key = storage.write(data)
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return ProviderResult(content="OK")

    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: storage)
    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[
            {
                "role": "tool",
                "content": "Screenshot",
                "tool_call_id": "call-1",
                "attachments": [_attachment_reference(storage_key, data)],
            }
        ],
        tools=[],
        input_trust_level=TrustLevel.PUBLIC,
    )

    request = captured["request"]
    assert request.media[0].status == "ready"
    assert request.media[0].data_url is not None
    assert base64.b64decode(request.media[0].data_url.split(",", 1)[1]) == data
    assert storage_key not in repr(request.messages)
    assert storage_key not in repr(request.media)


async def test_complete_uses_unavailable_marker_for_missing_attachment_file(
    monkeypatch, tmp_path: Path
) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="vision", supports_image_input=True)
    storage = AttachmentStorage(tmp_path / "attachments")
    data = _png_bytes()
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return ProviderResult(content="OK")

    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: storage)
    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[
            {
                "role": "tool",
                "content": "Screenshot",
                "tool_call_id": "call-1",
                "attachments": [_attachment_reference("ab/" + "0" * 32, data)],
            }
        ],
        tools=[],
        input_trust_level=TrustLevel.PUBLIC,
    )

    request = captured["request"]
    assert request.media[0].status == "unavailable"
    assert request.media[0].data_url is None


async def test_transient_errors_use_retry_after(monkeypatch) -> None:
    operation = AsyncMock(
        side_effect=[
            ProviderError("rate limited", status_code=429, retry_after=1.5),
            ProviderResult(content="OK"),
        ]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(service_module.asyncio, "sleep", sleep)

    result = await _with_retries(operation)

    assert result.content == "OK"
    assert operation.await_count == 2
    sleep.assert_awaited_once_with(1.5)


async def test_non_transient_error_is_not_retried() -> None:
    operation = AsyncMock(side_effect=ProviderError("bad request", status_code=400))

    with pytest.raises(ProviderError):
        await _with_retries(operation)

    operation.assert_awaited_once()


async def test_transient_error_is_not_retried_after_stream_output() -> None:
    operation = AsyncMock(side_effect=ProviderError("stream interrupted", status_code=503))

    with pytest.raises(ProviderError):
        await _with_retries(operation, can_retry=lambda: False)

    operation.assert_awaited_once()


async def test_discovery_gets_models_with_auth_and_preserves_opaque_ids(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://provider.test/v1/models"
        assert request.headers["Authorization"] == "Bearer secret-key"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "vendor/model:version@2026-07",
                        "object": "model",
                        "owned_by": "vendor",
                    }
                ],
                "extra": True,
            },
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    result = await discover_models(
        base_url="https://provider.test/v1/",
        api_key="secret-key",
    )

    assert result[0].id == "vendor/model:version@2026-07"
    assert result[0].metadata == {"object": "model", "owned_by": "vendor"}


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [(401, "provider.authenticationFailed"), (404, "provider.endpointNotFound")],
)
async def test_discovery_maps_http_errors(
    monkeypatch, status_code: int, expected_code: str
) -> None:
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _request: httpx.Response(status_code))
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(ProviderError) as exc_info:
        await discover_models(base_url="https://provider.test/v1", api_key=None)

    assert exc_info.value.public_code == expected_code


async def test_discovery_rejects_invalid_model_payload(monkeypatch) -> None:
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(200, json={"data": [{"name": "missing id"}]})
    )
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(ProviderError) as exc_info:
        await discover_models(base_url="https://provider.test/v1", api_key=None)

    assert exc_info.value.public_code == "provider.invalidResponse"


async def test_discovery_maps_network_errors(monkeypatch) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(fail)
    monkeypatch.setattr(
        "mato_loop_backend.providers.openai_compatible.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    with pytest.raises(ProviderError) as exc_info:
        await discover_models(base_url="https://provider.test/v1", api_key=None)

    assert exc_info.value.public_code == "provider.unavailable"


async def test_provider_discovery_uses_stored_api_key(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        credential_id=uuid4(),
        config={"base_url": "https://provider.test/v1"},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = object()
    monkeypatch.setattr(service_module, "read_credential", lambda _credential: {"api_key": "stored"})
    remote_discovery = AsyncMock(return_value=[])
    monkeypatch.setattr(service_module.openai_compatible, "discover_models", remote_discovery)

    await ProviderService.discover_models(db, provider=provider)

    remote_discovery.assert_awaited_once_with(
        base_url="https://provider.test/v1",
        api_key="stored",
        timeout=30,
    )


async def test_provider_discovery_uses_codex_credential(monkeypatch) -> None:
    credential_id = uuid4()
    credential = type(
        "CredentialStub",
        (),
        {"kind": "codex-oauth", "label": "Codex", "metadata_json": {}},
    )()
    provider = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=credential_id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = credential
    payload = {"access": "access", "refresh": "refresh", "account_id": "account"}
    monkeypatch.setattr(service_module, "read_credential", lambda _credential: payload)
    monkeypatch.setattr(service_module.codex_oauth, "refresh_token", AsyncMock(return_value=payload))
    remote_discovery = AsyncMock(return_value=[])
    monkeypatch.setattr(service_module.codex_oauth, "discover_models", remote_discovery)

    await ProviderService.discover_models(db, provider=provider)

    remote_discovery.assert_awaited_once_with(
        access_token="access",
        account_id="account",
        timeout=30,
    )


async def test_provider_discovery_requires_codex_connection() -> None:
    provider = LlmProviderConfig(name="Codex", adapter_type="openai-codex")

    with pytest.raises(ProviderError) as exc_info:
        await ProviderService.discover_models(AsyncMock(spec=AsyncSession), provider=provider)

    assert exc_info.value.public_code == "provider.notConnected"
