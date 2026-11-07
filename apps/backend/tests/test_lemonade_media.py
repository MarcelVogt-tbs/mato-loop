import base64
import json
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.providers import lemonade, openai_images
from mato_loop_backend.providers import media as media_module
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.media import MediaService
from mato_loop_backend.settings import AiSettings
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    TrustLevel,
)


def _mock_client(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        lemonade.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.mark.parametrize("options", [{}, {"size": "512x512", "steps": 4, "cfg_scale": 1.5, "seed": 0}])
async def test_generate_image_sends_strict_payload_and_decodes_result(monkeypatch, options) -> None:
    image = b"\x89PNG\r\n\x1a\nimage"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://lemonade.test/v1/images/generations"
        assert request.headers["Authorization"] == "Bearer secret"
        assert json.loads(request.content) == {
            "model": "SD-Turbo",
            "prompt": "Bergsee",
            "n": 1,
            "response_format": "b64_json",
            **options,
        }
        return httpx.Response(
            200,
            json={
                "created": 123,
                "data": [{"b64_json": base64.b64encode(image).decode("ascii")}],
            },
        )

    _mock_client(monkeypatch, handler)

    data, metadata = await openai_images.generate_image(
        base_url="https://lemonade.test/v1",
        api_key="secret",
        model="SD-Turbo",
        prompt="Bergsee",
        **options,
    )

    assert data == image
    assert metadata == {"created": 123}


async def test_media_http_error_does_not_expose_remote_body(monkeypatch) -> None:
    _mock_client(
        monkeypatch,
        lambda _request: httpx.Response(500, json={"error": "secret backend detail"}),
    )

    with pytest.raises(ProviderError) as exc_info:
        await lemonade.generate_speech(
            base_url="https://lemonade.test/v1",
            api_key=None,
            model="kokoro",
            text="Hallo",
        )

    assert exc_info.value.public_code == "provider.unavailable"
    assert "secret backend detail" not in str(exc_info.value)


async def test_media_timeout_is_distinct_and_reports_safe_context(
    monkeypatch, caplog
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret remote detail", request=request)

    _mock_client(monkeypatch, handler)

    with pytest.raises(ProviderError) as exc_info:
        await openai_images.generate_image(
            base_url="https://lemonade.test/v1",
            api_key="secret",
            model="SD-Turbo",
            prompt="Test",
            timeout=420,
        )

    assert exc_info.value.public_code == "provider.timeout"
    assert exc_info.value.params == {"timeoutSeconds": 420, "retryable": True}
    assert "operation=image_generation" in caplog.text
    assert "exception=ReadTimeout" in caplog.text
    assert "secret remote detail" not in caplog.text
    assert "https://lemonade.test" not in caplog.text


async def test_audio_response_size_is_limited(monkeypatch) -> None:
    monkeypatch.setattr(lemonade, "MAX_AUDIO_BYTES", 4)
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, content=b"12345"))

    with pytest.raises(ProviderError) as exc_info:
        await lemonade.generate_audio(
            base_url="https://lemonade.test/v1",
            api_key=None,
            model="ThinkSound",
            prompt="Regen",
        )

    assert exc_info.value.public_code == "provider.responseTooLarge"


async def test_discovery_response_size_is_limited_while_streaming(monkeypatch) -> None:
    monkeypatch.setattr(lemonade, "_MAX_JSON_BYTES", 4)
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, content=b"12345"))

    with pytest.raises(ProviderError) as exc_info:
        await lemonade.discover_models(
            base_url="https://lemonade.test/v1", api_key=None
        )

    assert exc_info.value.public_code == "provider.invalidResponse"


async def test_transcription_sends_wav_multipart_and_reads_text(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://lemonade.test/v1/audio/transcriptions"
        assert b'name="model"' in request.content
        assert b"Whisper-Tiny" in request.content
        assert b'filename="audio.wav"' in request.content
        assert b"RIFFaudio" in request.content
        assert b'name="language"\r\n\r\nde\r\n' in request.content
        return httpx.Response(200, json={"text": "Guten Morgen"})

    _mock_client(monkeypatch, handler)

    text = await lemonade.transcribe_audio(
        base_url="https://lemonade.test/v1",
        api_key=None,
        model="Whisper-Tiny",
        audio=b"RIFFaudio",
        language="de-DE",
    )

    assert text == "Guten Morgen"


@pytest.mark.parametrize("payload", [
    {"data": [{"b64_json": "%%%"}]},
    {"data": [{"b64_json": ""}]},
    {"data": []},
])
async def test_image_rejects_invalid_response(monkeypatch, payload) -> None:
    _mock_client(
        monkeypatch,
        lambda _request: httpx.Response(200, json=payload),
    )

    with pytest.raises(ProviderError) as exc_info:
        await openai_images.generate_image(
            base_url="https://lemonade.test/v1",
            api_key=None,
            model="SD-Turbo",
            prompt="Test",
        )

    assert exc_info.value.public_code == "provider.invalidResponse"


@pytest.mark.parametrize("adapter", ["lemonade", "openai-compatible"])
async def test_media_service_resolves_host_default_without_model_argument(monkeypatch, adapter) -> None:
    provider = LlmProviderConfig(
        name="Lemonade",
        adapter_type=adapter,
        trust_level="private",
        config={"base_url": "https://lemonade.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="SD-Turbo",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(providers=[provider], models=[model], image_generation_model_id=model.id)
    monkeypatch.setattr(media_module, "load_effective_ai", AsyncMock(return_value=settings))
    image = _png()
    transport = AsyncMock(return_value=(image, {"created": 123}))
    monkeypatch.setattr(openai_images, "generate_image", transport)

    result = await MediaService.generate_image(
        AsyncMock(spec=AsyncSession), prompt="Ein Bergsee", user_id=uuid4()
    )

    assert result.data == image
    assert result.mime_type == "image/png"
    assert result.filename == "generated.png"
    assert result.metadata == {
        "created": 123,
        "providerAdapter": adapter,
        "providerName": "Lemonade",
        "modelId": "SD-Turbo",
        "modelName": "SD-Turbo",
        "targetTrustLevel": "private",
    }
    assert result.trust_level == TrustLevel.PRIVATE
    assert transport.await_args.kwargs["model"] == "SD-Turbo"
    assert transport.await_args.kwargs["size"] is None


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("source_format", ["PNG", "WEBP", "GIF"])
async def test_openai_edit_uses_shared_default_or_edit_override(monkeypatch, override, source_format) -> None:
    provider = LlmProviderConfig(
        name="Images", adapter_type="openai-compatible",
        config={"base_url": "https://images.test/v1"}, trust_level="private",
    )
    shared, editing = [
        ModelConfig(provider_id=provider.id, model_id=name, capabilities={
            ModelCapability.IMAGE_GENERATION, ModelCapability.IMAGE_EDITING,
        }) for name in ("shared", "editing")
    ]
    settings = AiSettings(
        providers=[provider], models=[shared, editing], image_generation_model_id=shared.id,
        image_editing_model_id=editing.id if override else None,
    )
    monkeypatch.setattr(media_module, "load_effective_ai", AsyncMock(return_value=settings))
    image = _png()
    transport = AsyncMock(return_value=(image, {}))
    monkeypatch.setattr(openai_images, "edit_image", transport)
    source_id = uuid4()
    source = BytesIO()
    Image.new("RGB", (2, 2), "red").save(source, format=source_format)

    result = await MediaService.edit_image(
        AsyncMock(spec=AsyncSession), prompt="Make it blue",
        images=((source.getvalue(), f"image/{source_format.lower()}"),),
        source_attachment_ids=(source_id,), user_id=uuid4(),
    )

    assert (transport.await_args.kwargs["model"], result.metadata["sourceAttachmentIds"],
            transport.await_args.kwargs["images"][0][1]) == (
        "editing" if override else "shared", [str(source_id)], "image/png",
    )


async def test_media_service_rejects_changed_target_trust_before_transport(
    monkeypatch,
) -> None:
    provider = LlmProviderConfig(
        name="Lemonade",
        adapter_type="lemonade",
        trust_level="private",
        config={"base_url": "https://lemonade.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="SD-Turbo",
        capabilities={ModelCapability.IMAGE_GENERATION},
    )
    settings = AiSettings(providers=[provider], models=[model], image_generation_model_id=model.id)
    monkeypatch.setattr(media_module, "load_effective_ai", AsyncMock(return_value=settings))
    transport = AsyncMock()
    monkeypatch.setattr(openai_images, "generate_image", transport)

    with pytest.raises(ProviderError) as exc_info:
        await MediaService.generate_image(
            AsyncMock(spec=AsyncSession),
            prompt="Ein Bergsee",
            expected_model_id=model.id,
            expected_trust_level=TrustLevel.PUBLIC,
            user_id=uuid4(),
        )

    assert exc_info.value.code == "provider.modelCapabilityUnavailable"
    transport.assert_not_awaited()
