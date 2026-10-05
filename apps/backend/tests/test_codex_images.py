import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.providers import codex_images
from mato_loop_backend.providers import media as media_module
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.codex_oauth import (
    CODEX_IMAGES_EDITS_URL,
    CODEX_IMAGES_GENERATIONS_URL,
)
from mato_loop_backend.providers.media import MediaService
from mato_loop_backend.settings import AiSettings
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    TrustLevel,
)


def _client_with(handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: real_client(transport=transport, **kwargs)


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (1, 1)).save(buffer, "PNG")
    return buffer.getvalue()


async def test_generate_image_uses_codex_contract(monkeypatch) -> None:
    image = b"\x89PNG\r\n\x1a\ncontent"

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CODEX_IMAGES_GENERATIONS_URL
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["ChatGPT-Account-ID"] == "account"
        assert request.headers["x-codex-image-turn-id"] == "turn"
        assert json.loads(request.content) == {
            "prompt": "A lake",
            "background": "auto",
            "model": "gpt-image-2",
            "quality": "auto",
            "size": "auto",
        }
        return httpx.Response(
            200,
            json={
                "created": 123,
                "data": [{"b64_json": base64.b64encode(image).decode()}],
                "quality": "high",
                "size": "1024x1024",
            },
        )

    monkeypatch.setattr(codex_images.httpx, "AsyncClient", _client_with(handler))

    data, metadata = await codex_images.generate_image(
        credential={"access": "access", "account_id": "account"},
        prompt="A lake",
        size=None,
        turn_id="turn",
        timeout=30,
    )

    assert data == image
    assert metadata == {"created": 123, "quality": "high", "size": "1024x1024"}


async def test_edit_image_sends_ordered_data_urls(monkeypatch) -> None:
    inputs = ((b"first", "image/png"), (b"second", "image/jpeg"))

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == CODEX_IMAGES_EDITS_URL
        assert request.headers["x-codex-image-turn-id"] == "turn"
        assert json.loads(request.content) == {
            "images": [
                {
                    "image_url": "data:image/png;base64,"
                    + base64.b64encode(b"first").decode()
                },
                {
                    "image_url": "data:image/jpeg;base64,"
                    + base64.b64encode(b"second").decode()
                },
            ],
            "prompt": "Change only the background",
            "background": "auto",
            "model": "gpt-image-2",
            "quality": "auto",
            "size": "auto",
        }
        return httpx.Response(
            200,
            json={
                "created": 123,
                "data": [{"b64_json": base64.b64encode(b"result").decode()}],
            },
        )

    monkeypatch.setattr(codex_images.httpx, "AsyncClient", _client_with(handler))

    data, metadata = await codex_images.edit_image(
        credential={"access": "access"},
        images=inputs,
        prompt="Change only the background",
        turn_id="turn",
        timeout=30,
    )

    assert data == b"result"
    assert metadata == {"created": 123}


@pytest.mark.parametrize("encoded", ["not base64", ""])
async def test_generate_image_rejects_invalid_base64(monkeypatch, encoded) -> None:
    monkeypatch.setattr(
        codex_images.httpx,
        "AsyncClient",
        _client_with(
            lambda _request: httpx.Response(
                200,
                json={"created": 123, "data": [{"b64_json": encoded}]},
            )
        ),
    )

    with pytest.raises(ProviderError) as exc_info:
        await codex_images.generate_image(
            credential={"access": "access"},
            prompt="A lake",
            size=None,
            turn_id="turn",
            timeout=30,
        )

    assert exc_info.value.code == "provider.invalidResponse"


async def test_media_service_edits_with_native_codex_model(monkeypatch) -> None:
    credential_id = uuid4()
    provider = LlmProviderConfig(
        name="Codex",
        adapter_type="openai-codex",
        credential_id=credential_id,
        trust_level="private",
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="openai-codex/gpt-image-2",
        capabilities={ModelCapability.IMAGE_EDITING},
    )
    settings = AiSettings(providers=[provider], models=[model], image_generation_model_id=model.id)
    monkeypatch.setattr(media_module, "load_effective_ai", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        media_module,
        "refresh_codex_credential",
        AsyncMock(return_value={"access": "access"}),
    )
    output = _png()
    transport = AsyncMock(return_value=(output, {"created": 123}))
    monkeypatch.setattr(codex_images, "edit_image", transport)
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = SimpleNamespace(user_id=None)
    source_id = uuid4()
    source = BytesIO()
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(
        source,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", (3, 2), (30, 20, 10))],
    )

    result = await MediaService.edit_image(
        db,
        prompt="Change only the background",
        images=((source.getvalue(), "image/jpeg"),),
        source_attachment_ids=(source_id,),
        user_id=uuid4(),
    )

    assert result.data == output
    assert result.trust_level == TrustLevel.PRIVATE
    assert result.metadata["sourceAttachmentIds"] == [str(source_id)]
    assert transport.await_args.kwargs["prompt"] == "Change only the background"
    normalized, mime_type = transport.await_args.kwargs["images"][0]
    with Image.open(BytesIO(normalized)) as image:
        assert image.format == "JPEG"
        assert image.size == (2048, 1365)
    assert mime_type == "image/jpeg"
