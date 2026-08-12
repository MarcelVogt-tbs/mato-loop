import base64
import json
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
from mato_loop_backend.providers import lemonade
from mato_loop_backend.providers import service as service_module
from mato_loop_backend.providers.base import (
    ProviderError,
    ProviderImageInput,
    ProviderMediaInput,
    ProviderResult,
    PublicReasoning,
    ToolCall,
)
from mato_loop_backend.providers.openai_compatible import (
    _chat_body,
    _responses_body,
    discover_models,
)
from mato_loop_backend.providers.service import ProviderService, _with_retries
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
    TrustLevel,
)


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _attachment_reference(
    session_id,
    storage_path: str,
    data: bytes,
    *,
    attachment_id=None,
    filename: str = "image.png",
    category: str = "generated",
    trust_level: TrustLevel = TrustLevel.PUBLIC,
) -> dict:
    validated = validate_image(data, "image/png")
    return {
        "id": str(attachment_id or uuid4()),
        "filename": filename,
        "kind": "image",
        "category": category,
        "mime_type": validated.mime_type,
        "size_bytes": validated.size_bytes,
        "sha256": validated.sha256,
        "trust_level": trust_level.value,
        "session_id": str(session_id),
        "storage_path": storage_path,
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


async def test_complete_validates_and_transports_direct_audio_and_video(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="vLLM",
        adapter_type="openai-compatible",
        pseudonymization_enabled=False,
        config={"base_url": "http://vllm.test/v1", "api_type": "chat_completions"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="multimodal",
        input_modalities={
            ModelInputModality.TEXT,
            ModelInputModality.AUDIO,
            ModelInputModality.VIDEO,
        },
    )
    captured = {}

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return ProviderResult(content="OK")

    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)
    inputs = [
        ProviderMediaInput(0, b"RIFFaudio", "audio/wav", "private", "audio"),
        ProviderMediaInput(0, b"video", "video/mp4", "private", "video"),
    ]

    await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": "Test"}],
        tools=[],
        media_inputs=inputs,
    )

    media = captured["request"].media
    assert base64.b64decode(media[0].data_base64) == b"RIFFaudio"
    assert base64.b64decode(media[1].data_url.split(",", 1)[1]) == b"video"
    assert "RIFFaudio" not in repr(inputs)
    assert "UklGRmF1ZGlv" not in repr(media)


@pytest.mark.parametrize(
    ("adapter_type", "api_type"),
    [
        ("openai-compatible", "responses"),
        ("openai-codex", "chat_completions"),
        ("lemonade", "chat_completions"),
    ],
)
async def test_complete_rejects_native_media_outside_openai_chat_completions(
    adapter_type: str, api_type: str
) -> None:
    provider = LlmProviderConfig(
        name="Unsupported",
        adapter_type=adapter_type,
        config={"base_url": "http://provider.test/v1", "api_type": api_type},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="audio",
        input_modalities={"text", "audio"},
    )

    with pytest.raises(ProviderError) as exc_info:
        await ProviderService.complete(
            AsyncMock(spec=AsyncSession),
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": "Test"}],
            tools=[],
            media_inputs=[
                ProviderMediaInput(0, b"audio", "audio/wav", "public", "audio")
            ],
        )

    assert exc_info.value.public_code == "provider.modelCapabilityUnavailable"


async def test_complete_rejects_unconfigured_or_oversized_native_media(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(
        name="Public",
        adapter_type="openai-compatible",
        config={"base_url": "http://provider.test/v1"},
    )
    text_model = ModelConfig(provider_id=provider.id, model_id="text")
    audio_model = ModelConfig(
        provider_id=provider.id,
        model_id="audio",
        input_modalities={"text", "audio"},
    )
    media = ProviderMediaInput(0, b"audio", "audio/wav", "public", "audio")

    with pytest.raises(ProviderError, match="does not support"):
        await ProviderService.complete(
            AsyncMock(spec=AsyncSession),
            provider=provider,
            model=text_model,
            messages=[{"role": "user", "content": "Test"}],
            tools=[],
            media_inputs=[media],
        )
    monkeypatch.setattr(service_module, "MAX_PROVIDER_AUDIO_BYTES", 4)
    with pytest.raises(ProviderError) as exc_info:
        await ProviderService.complete(
            AsyncMock(spec=AsyncSession),
            provider=provider,
            model=audio_model,
            messages=[{"role": "user", "content": "Test"}],
            tools=[],
            media_inputs=[media],
        )

    assert exc_info.value.public_code == "provider.responseTooLarge"


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
        protected = request.messages[-1]["content"]
        assert "anna@example.com" not in protected
        assert "provider_data" not in request.messages[-1]
        assert request.messages[0]["role"] == "system"
        assert "Host privacy placeholders" in request.messages[0]["content"]
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


async def test_public_complete_rejects_unknown_pseudonym_marker(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Public",
        adapter_type="openai-compatible",
        config={"base_url": "http://public.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="public-model")

    async def fake_chat_completions(**kwargs):
        protected = kwargs["request"].messages[-1]["content"]
        marker = re.search(r"\[\[MLP_[A-F0-9]+_\d{4}\]\]", protected)
        assert marker is not None
        unknown_marker = marker.group().replace("0001", "9999")
        return ProviderResult(
            content="",
            tool_calls=[
                ToolCall(
                    id="call-1",
                    name="send_mail",
                    arguments={"address": unknown_marker},
                )
            ],
        )

    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    with pytest.raises(ProviderError) as exc_info:
        await ProviderService.complete(
            AsyncMock(spec=AsyncSession),
            provider=provider,
            model=model,
            messages=[{"role": "user", "content": "Schreibe an anna@example.com"}],
            tools=[],
        )

    assert exc_info.value.code == "privacy.pseudonymizationFailed"


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

    monkeypatch.setattr(
        service_module,
        "refresh_codex_credential",
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
        raise AssertionError("AttachmentStorage must not be opened")

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
                "attachments": [
                    _attachment_reference(
                        uuid4(),
                        "generated/00000000-0000-4000-8000-000000000000/missing.png",
                        _png_bytes(),
                    )
                ],
            }
        ],
        tools=[],
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
    session_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id)
    storage = AttachmentStorage(sessions)
    data = _png_bytes()
    storage_path = storage.write(session_id, uuid4(), data, "image.png", "generated")
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
                "attachments": [_attachment_reference(session_id, storage_path, data)],
            }
        ],
        tools=[],
    )

    request = captured["request"]
    assert request.media[0].status == "ready"
    assert request.media[0].data_url is not None
    assert base64.b64decode(request.media[0].data_url.split(",", 1)[1]) == data
    assert storage_path not in repr(request.messages)
    assert storage_path not in repr(request.media)


async def test_complete_uses_unavailable_marker_for_missing_attachment_file(
    monkeypatch, tmp_path: Path
) -> None:
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "http://local.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="vision", supports_image_input=True)
    session_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id)
    storage = AttachmentStorage(sessions)
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
                "attachments": [
                    _attachment_reference(
                        session_id,
                        "generated/00000000-0000-4000-8000-000000000000/missing.png",
                        data,
                    )
                ],
            }
        ],
        tools=[],
    )

    request = captured["request"]
    assert request.media[0].status == "unavailable"
    assert request.media[0].data_url is None


async def test_projection_sends_current_user_images_but_not_unreferenced_history(
    monkeypatch, tmp_path: Path
) -> None:
    session_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id)
    storage = AttachmentStorage(sessions)
    old_data = _png_bytes()
    current_buffer = BytesIO()
    Image.new("RGB", (2, 2), (90, 80, 70)).save(current_buffer, format="PNG")
    current_data = current_buffer.getvalue()
    old_path = storage.write(session_id, uuid4(), old_data, "old.png", "user_upload")
    current_path = storage.write(
        session_id, uuid4(), current_data, "current.png", "user_upload"
    )
    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: storage)

    projected, media = await service_module._provider_projection(
        [
            {
                "role": "user",
                "content": "Alte Nachricht",
                "attachments": [
                    _attachment_reference(
                        session_id, old_path, old_data, filename="old.png"
                    )
                ],
            },
            {
                "role": "user",
                "content": "Aktuelles Bild",
                "attachments": [
                    _attachment_reference(
                        session_id, current_path, current_data, filename="current.png"
                    )
                ],
            },
        ],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
    )

    assert all("attachments" not in message for message in projected)
    assert len(media) == 1
    assert media[0].message_index == 1
    assert base64.b64decode(media[0].data_url.split(",", 1)[1]) == current_data


async def test_projection_accepts_bounded_transient_images() -> None:
    data = _png_bytes()

    projected, media = await service_module._provider_projection(
        [{"role": "user", "content": "PDF page 1"}],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
        image_inputs=[
            ProviderImageInput(
                0, data, trust_level=TrustLevel.PUBLIC.value, mime_type="image/png"
            )
        ],
    )

    assert projected == [{"role": "user", "content": "PDF page 1"}]
    assert len(media) == 1
    assert media[0].message_index == 0
    assert base64.b64decode(media[0].data_url.split(",", 1)[1]) == data

    _projected, private_media = await service_module._provider_projection(
        [{"role": "user", "content": "PDF page 1"}],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
        image_inputs=[
            ProviderImageInput(
                0, data, trust_level=TrustLevel.PRIVATE.value, mime_type="image/png"
            )
        ],
    )
    assert len(private_media) == 1

    with pytest.raises(ProviderError) as exc_info:
        await service_module._provider_projection(
            [{"role": "user", "content": "Frames"}],
            supports_image_input=True,
            target_trust_level=TrustLevel.PUBLIC,
            image_inputs=[
                ProviderImageInput(0, data, TrustLevel.PUBLIC.value)
                for _ in range(service_module.MAX_PROVIDER_IMAGES + 1)
            ],
        )
    assert exc_info.value.public_code == "provider.imageLimitExceeded"
    assert exc_info.value.params == {"maxImages": service_module.MAX_PROVIDER_IMAGES}


async def test_projection_accepts_and_downsizes_mpo_images_for_provider() -> None:
    buffer = BytesIO()
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(
        buffer,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", (3, 2), (30, 20, 10))],
    )

    _projected, media = await service_module._provider_projection(
        [{"role": "user", "content": "Großes Bild"}],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
        image_inputs=[
            ProviderImageInput(
                0,
                buffer.getvalue(),
                trust_level=TrustLevel.PUBLIC.value,
                mime_type="image/jpeg",
            )
        ],
    )

    normalized = base64.b64decode(media[0].data_url.split(",", 1)[1])
    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert image.size == (2048, 1365)
    assert len(normalized) <= service_module._MODEL_IMAGE_MAX_BYTES


async def test_session_tool_result_read_reactivates_historical_image(monkeypatch) -> None:
    attachment_id = uuid4()
    reference = _attachment_reference(
        uuid4(),
        f"attachments/{attachment_id}/scan.png",
        _png_bytes(),
        attachment_id=attachment_id,
        filename="scan.png",
    )
    reference["historical"] = True
    reference["source_order"] = 0
    monkeypatch.setattr(
        service_module,
        "AttachmentStorage",
        lambda: object(),
    )
    monkeypatch.setattr(
        service_module,
        "_read_image_data_url",
        lambda _storage, attachment: f"data:image/png;base64,{attachment['id']}",
    )

    _projected, media = await service_module._provider_projection(
        [
            {"role": "system", "content": "System", "attachments": [reference]},
            {"role": "user", "content": "Lies den alten Verlauf"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "read", "name": "session_read", "arguments": {}}],
            },
            {
                "role": "tool",
                "tool_call_id": "read",
                "content": json.dumps(
                    {
                        "messageId": str(uuid4()),
                        "offset": 0,
                        "content": "Image generated.",
                        "attachments": [
                            {
                                "id": str(attachment_id),
                                "filename": "scan.png",
                                "kind": "image",
                                "mimeType": "image/png",
                                "path": reference["storage_path"],
                            }
                        ],
                    }
                ),
            },
        ],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
    )

    assert len(media) == 1
    assert media[0].message_index == 3
    assert media[0].status == "ready"


async def test_historical_image_limit_prefers_newest_references(monkeypatch) -> None:
    references = []
    for order in range(5):
        attachment_id = uuid4()
        reference = _attachment_reference(
            uuid4(),
            f"attachments/{attachment_id}/image-{order}.png",
            _png_bytes(),
            attachment_id=attachment_id,
            filename=f"image-{order}.png",
        )
        reference.update({"historical": True, "source_order": order})
        references.append(reference)
    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: object())
    monkeypatch.setattr(
        service_module,
        "_read_image_data_url",
        lambda _storage, attachment: f"data:image/png;base64,{attachment['id']}",
    )

    _projected, media = await service_module._provider_projection(
        [
            {"role": "system", "content": "System", "attachments": references},
            {
                "role": "user",
                "content": " ".join(reference["id"] for reference in references),
            },
        ],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
    )

    assert len(media) == 4
    assert [item.data_url.rsplit(",", 1)[1] for item in media] == [
        reference["id"] for reference in references[1:]
    ]


async def test_public_target_blocks_private_image_without_reading_storage(monkeypatch) -> None:
    private_reference = _attachment_reference(
        uuid4(),
        f"attachments/{uuid4()}/private.png",
        _png_bytes(),
        trust_level=TrustLevel.PRIVATE,
    )
    private_reference["historical"] = True
    private_reference["source_order"] = 0

    def fail_storage():
        raise AssertionError("Private image data must not be read")

    monkeypatch.setattr(service_module, "AttachmentStorage", fail_storage)

    _projected, media = await service_module._provider_projection(
        [
            {"role": "system", "content": "System", "attachments": [private_reference]},
            {"role": "user", "content": private_reference["id"]},
        ],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
    )

    assert len(media) == 1
    assert media[0].status == "unavailable"
    assert media[0].data_url is None


async def test_public_target_blocks_image_without_provenance(monkeypatch) -> None:
    reference = _attachment_reference(
        uuid4(), f"attachments/{uuid4()}/unknown.png", _png_bytes()
    )
    reference.pop("trust_level")

    def fail_storage():
        raise AssertionError("Image data without provenance must not be read")

    monkeypatch.setattr(service_module, "AttachmentStorage", fail_storage)

    _projected, media = await service_module._provider_projection(
        [
            {"role": "system", "content": "System", "attachments": [reference]},
            {"role": "user", "content": reference["id"]},
        ],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
    )

    assert media[0].status == "unavailable"
    assert media[0].data_url is None


async def test_private_target_can_read_private_image(monkeypatch, tmp_path: Path) -> None:
    session_id = uuid4()
    sessions = SessionStorage(tmp_path / "sessions")
    sessions.provision(session_id)
    storage = AttachmentStorage(sessions)
    data = _png_bytes()
    storage_path = storage.write(session_id, uuid4(), data, "private.png", "user_upload")
    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: storage)

    _projected, media = await service_module._provider_projection(
        [
            {
                "role": "user",
                "content": "Privates Bild",
                "attachments": [
                    _attachment_reference(
                        session_id,
                        storage_path,
                        data,
                        trust_level=TrustLevel.PRIVATE,
                    )
                ],
            }
        ],
        supports_image_input=True,
        target_trust_level=TrustLevel.PRIVATE,
    )

    assert media[0].status == "ready"
    assert base64.b64decode(media[0].data_url.split(",", 1)[1]) == data


async def test_internal_storage_path_never_reaches_openai_payload(monkeypatch) -> None:
    storage_path = f"attachments/{uuid4()}/secret.png"
    reference = _attachment_reference(uuid4(), storage_path, _png_bytes())
    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: object())
    monkeypatch.setattr(
        service_module,
        "_read_image_data_url",
        lambda _storage, _attachment: "data:image/png;base64,aW1hZ2U=",
    )

    projected, media = await service_module._provider_projection(
        [{"role": "user", "content": "Bild", "attachments": [reference]}],
        supports_image_input=True,
        target_trust_level=TrustLevel.PUBLIC,
    )
    request = service_module.ProviderRequest(
        model="vision", messages=projected, media=media
    )
    payload = json.dumps(
        {"chat": _chat_body(request), "responses": _responses_body(request)}
    )

    assert storage_path not in payload
    assert "storage_path" not in payload
    assert "data:image/png" in payload


async def test_media_selection_precedes_pseudonymization(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Public",
        adapter_type="openai-compatible",
        config={"base_url": "http://public.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="vision",
        supports_image_input=True,
    )
    attachment_id = uuid4()
    reference = _attachment_reference(
        uuid4(),
        f"attachments/{attachment_id}/anna@example.com.png",
        _png_bytes(),
        attachment_id=attachment_id,
        filename="anna@example.com.png",
    )
    reference.update({"historical": True, "source_order": 0})
    captured = {}

    class ReplacingPseudonymizer:
        def protect_messages(self, messages):
            assert all("attachments" not in message for message in messages)
            return [
                {**message, "content": str(message.get("content") or "").replace("anna@example.com", "PERSON")}
                for message in messages
            ]

        def restore_text(self, text):
            return text

        def restore_value(self, value):
            return value

    async def fake_chat_completions(**kwargs):
        captured.update(kwargs)
        return ProviderResult(content="OK")

    monkeypatch.setattr(service_module, "RequestPseudonymizer", ReplacingPseudonymizer)
    monkeypatch.setattr(service_module, "AttachmentStorage", lambda: object())
    monkeypatch.setattr(
        service_module,
        "_read_image_data_url",
        lambda _storage, _attachment: "data:image/png;base64,aW1hZ2U=",
    )
    monkeypatch.setattr(service_module, "chat_completions", fake_chat_completions)

    await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[
            {"role": "system", "content": "System", "attachments": [reference]},
            {"role": "user", "content": "Zeige anna@example.com.png"},
        ],
        tools=[],
        target_trust_level=TrustLevel.PUBLIC,
        pseudonymization_enabled=True,
    )

    request = captured["request"]
    assert request.media[0].status == "ready"
    assert "anna@example.com" not in request.messages[1]["content"]


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


async def test_transient_errors_use_increasing_delays_and_report_attempts(
    monkeypatch,
) -> None:
    operation = AsyncMock(
        side_effect=[
            ProviderError("temporary", status_code=503),
            ProviderError("temporary", status_code=503),
            ProviderResult(content="OK"),
        ]
    )
    events = []
    sleep = AsyncMock(side_effect=lambda delay: events.append(("sleep", delay)))
    on_retry = AsyncMock(
        side_effect=lambda attempt, _total, _status, _code: events.append(
            ("retry", attempt)
        )
    )
    monkeypatch.setattr(service_module.asyncio, "sleep", sleep)

    result = await _with_retries(operation, on_retry=on_retry)

    assert result.content == "OK"
    assert operation.await_count == 3
    assert [item.args for item in sleep.await_args_list] == [(2.0,), (5.0,)]
    assert [item.args for item in on_retry.await_args_list] == [
        (2, 3, 503, "provider.unavailable"),
        (3, 3, 503, "provider.unavailable"),
    ]
    assert events == [("sleep", 2.0), ("retry", 2), ("sleep", 5.0), ("retry", 3)]


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
        {
            "id": credential_id,
            "kind": "codex-oauth",
            "label": "Codex",
            "metadata_json": {},
        },
    )()
    provider = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=credential_id,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = credential
    payload = {"access": "access", "refresh": "refresh", "account_id": "account"}
    monkeypatch.setattr(
        service_module,
        "refresh_codex_credential",
        AsyncMock(return_value=payload),
    )
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


async def test_lemonade_discovery_maps_capabilities_and_filters_missing(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://lemonade.test/v1/models"
        assert request.headers["Authorization"] == "Bearer secret"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "chat-vision",
                        "labels": ["Vision", "tool_calling"],
                        "recipe": " LlamaCpp ",
                        "downloaded": True,
                        "max_context_window": 8192,
                        "image_defaults": None,
                    },
                    {
                        "id": "image",
                        "labels": ["IMAGE"],
                        "recipe": "sd-cpp",
                        "downloaded": True,
                        "image_defaults": {"steps": 4, "cfg_scale": 1.0},
                    },
                    {
                        "id": "chat-parallel",
                        "labels": [],
                        "recipe": "llamacpp",
                        "downloaded": True,
                        "supports_parallel_tool_calls": True,
                    },
                    {
                        "id": "music",
                        "labels": [],
                        "recipe": "ACE_STEP",
                        "downloaded": True,
                    },
                    {
                        "id": "omni",
                        "labels": ["image", "tts"],
                        "recipe": "collection.omni",
                        "downloaded": True,
                    },
                    {
                        "id": "missing",
                        "labels": ["transcription"],
                        "recipe": "whispercpp",
                        "downloaded": False,
                    },
                ]
            },
        )

    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        lemonade.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )

    models = await lemonade.discover_models(
        base_url="https://lemonade.test/v1", api_key="secret"
    )

    assert [item.id for item in models] == [
        "chat-vision",
        "image",
        "chat-parallel",
        "music",
        "omni",
    ]
    assert models[0].capabilities == {ModelCapability.CHAT}
    assert models[0].metadata["supports_image_input"] is True
    assert models[0].metadata["supports_tools"] is True
    assert models[0].metadata["supports_parallel_tool_calls"] is False
    assert models[1].capabilities == {ModelCapability.IMAGE_GENERATION}
    assert models[2].metadata["supports_tools"] is True
    assert models[2].metadata["supports_parallel_tool_calls"] is True
    assert models[3].capabilities == {ModelCapability.AUDIO_GENERATION}
    assert ModelCapability.CHAT not in models[4].capabilities
    assert "capabilities" not in models[1].metadata


async def test_lemonade_complete_explicitly_uses_chat_completions(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Lemonade",
        adapter_type="lemonade",
        pseudonymization_enabled=False,
        config={"base_url": "http://lemonade.test/v1", "api_type": "responses"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="chat")
    transport = AsyncMock(return_value=ProviderResult(content="OK"))
    responses = AsyncMock(side_effect=AssertionError("Responses API must not be used"))
    monkeypatch.setattr(service_module, "chat_completions", transport)
    monkeypatch.setattr(service_module, "responses_api", responses)

    result = await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": "Test"}],
        tools=[],
    )

    assert result.content == "OK"
    transport.assert_awaited_once()
    responses.assert_not_awaited()
    assert transport.await_args.kwargs["request"].include_usage is True


async def test_openai_compatible_stream_does_not_request_usage(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="OpenAI-compatible",
        adapter_type="openai-compatible",
        pseudonymization_enabled=False,
        config={"base_url": "http://provider.test/v1"},
    )
    model = ModelConfig(provider_id=provider.id, model_id="chat")
    transport = AsyncMock(return_value=ProviderResult(content="OK"))
    monkeypatch.setattr(service_module, "chat_completions", transport)

    await ProviderService.complete(
        AsyncMock(spec=AsyncSession),
        provider=provider,
        model=model,
        messages=[{"role": "user", "content": "Test"}],
        tools=[],
    )

    assert transport.await_args.kwargs["request"].include_usage is False


async def test_complete_rejects_unknown_adapter_before_transport(monkeypatch) -> None:
    provider = LlmProviderConfig(
        name="Unknown", adapter_type="unknown", config={"base_url": "http://unknown.test"}
    )
    model = ModelConfig(provider_id=provider.id, model_id="chat")
    transport = AsyncMock()
    monkeypatch.setattr(service_module, "chat_completions", transport)

    with pytest.raises(ProviderError) as exc_info:
        await ProviderService.complete(
            AsyncMock(spec=AsyncSession),
            provider=provider,
            model=model,
            messages=[],
            tools=[],
        )

    assert exc_info.value.public_code == "provider.configurationInvalid"
    transport.assert_not_awaited()
