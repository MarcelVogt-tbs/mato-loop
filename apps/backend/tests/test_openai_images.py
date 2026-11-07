from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from mato_loop_backend.providers import openai_images
from mato_loop_backend.providers.base import ProviderError


def _mock_client(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        openai_images.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )


@pytest.mark.parametrize("mime_types", [("image/png", "image/jpeg"), ("image/jpeg", "image/png")])
async def test_edit_image_sends_ordered_multipart(monkeypatch, mime_types) -> None:
    images = tuple((f"source-{index}".encode(), mime) for index, mime in enumerate(mime_types))

    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://images.test/v1/images/edits"
        assert "Authorization" not in request.headers
        assert request.extensions["timeout"]["read"] == 420
        message = BytesParser(policy=default).parsebytes(
            f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode() + request.content
        )
        parts = list(message.iter_parts())
        assert {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True).decode()
            for part in parts
            if part.get_filename() is None
        } == {"model": "image-model", "prompt": "Bergsee", "n": "1", "response_format": "b64_json"}
        assert [
            (
                part.get_param("name", header="content-disposition"),
                part.get_filename(),
                part.get_content_type(),
                part.get_payload(decode=True),
            )
            for part in parts
            if part.get_filename() is not None
        ] == [
            ("image[]", f"image-{index + 1}.{'png' if mime == 'image/png' else 'jpg'}", mime, data)
            for index, (data, mime) in enumerate(images)
        ]
        return httpx.Response(200, json={"data": [{"b64_json": "aW1hZ2U="}]})

    _mock_client(monkeypatch, handler)
    result = await openai_images.edit_image(
        base_url="https://images.test/v1",
        model="image-model",
        prompt="Bergsee",
        images=images,
        timeout=420,
    )

    assert result == (b"image", {})


@pytest.mark.parametrize("limit", ["header", "stream", "decoded"])
async def test_image_response_size_is_limited(monkeypatch, limit) -> None:
    monkeypatch.setattr(openai_images, "MAX_IMAGE_BYTES" if limit == "decoded" else "_MAX_JSON_BYTES", 4)

    def handler(_request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, json={"data": [{"b64_json": "aW1hZ2U="}]})
        if limit == "stream":
            del response.headers["content-length"]
        return response

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc_info:
        await openai_images.generate_image(
            base_url="https://images.test/v1", model="image-model", prompt="Test"
        )

    assert exc_info.value.public_code == "provider.responseTooLarge"


@pytest.mark.parametrize("operation", ["generate_image", "edit_image"])
@pytest.mark.parametrize(
    "failure,code",
    [
        ("http", "provider.unavailable"),
        ("network", "provider.unavailable"),
        ("timeout", "provider.timeout"),
    ],
)
async def test_image_transport_errors_are_safe(monkeypatch, caplog, operation, failure, code) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("secret remote detail", request=request)
        if failure == "network":
            raise httpx.ConnectError("secret remote detail", request=request)
        return httpx.Response(500, text="secret remote detail")

    _mock_client(monkeypatch, handler)
    kwargs = {"images": ((b"source", "image/png"),)} if operation == "edit_image" else {}
    with pytest.raises(ProviderError) as exc_info:
        await getattr(openai_images, operation)(
            base_url="https://images.test/v1", model="image-model", prompt="Test", timeout=420, **kwargs
        )

    assert exc_info.value.public_code == code
    assert "secret remote detail" not in str(exc_info.value) + caplog.text
    assert "https://images.test" not in caplog.text
    if failure == "timeout":
        assert exc_info.value.params == {"timeoutSeconds": 420, "retryable": True}
