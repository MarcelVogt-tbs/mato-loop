import asyncio
import json
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.providers import azure_speech, media
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import AiSettings
from mato_loop_backend.settings.models import (
    AgentProfile,
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
)

ENDPOINT = "https://speech.cognitiveservices.azure.com"


def _mock_client(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        azure_speech.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def _form(request):
    message = BytesParser(policy=default).parsebytes(
        f"Content-Type: {request.headers['content-type']}\r\n\r\n".encode()
        + request.content
    )
    return {
        part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
        for part in message.iter_parts()
    }


@pytest.mark.parametrize(
    ("language", "locales"),
    [(None, []), ("de", ["de-DE"]), ("de-DE", ["de-DE"]), ("DE-de", ["de-DE"]),
     ("de-AT", ["de-AT"])],
)
async def test_transcription_uses_documented_multipart_contract(monkeypatch, language, locales):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.params["api-version"] == "2025-10-15"
        assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
        assert "authorization" not in request.headers
        if request.method == "GET":
            assert request.url.path == "/speechtotext/transcriptions/locales"
            return httpx.Response(200, json={"Transcribe": ["en-US", "de-DE", "de-AT"]})
        assert request.url.path == "/speechtotext/transcriptions:transcribe"
        form = _form(request)
        assert form["audio"] == b"RIFFaudio"
        assert json.loads(form["definition"]) == {
            "locales": locales, "profanityFilterMode": "None"
        }
        assert b'filename="audio.wav"' in request.content
        assert b"Content-Type: audio/wav" in request.content
        return httpx.Response(200, json={"combinedPhrases": [{"text": "Guten Morgen"}]})

    _mock_client(monkeypatch, handler)
    result = await azure_speech.transcribe_audio(
        base_url=ENDPOINT,
        api_key="test-key",
        model=azure_speech.MODEL_ID,
        audio=b"RIFFaudio",
        language=language,
    )
    assert result == "Guten Morgen"
    assert len(calls) == (1 if language is None else 2)


async def test_discovery_checks_access_and_declares_only_transcription(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/speechtotext/transcriptions/locales"
        return httpx.Response(200, json={"Transcribe": ["de-DE"]})

    _mock_client(monkeypatch, handler)
    models = await azure_speech.discover_models(base_url=ENDPOINT, api_key="test-key")
    assert len(models) == 1
    assert models[0].id == azure_speech.MODEL_ID
    assert models[0].capabilities == {ModelCapability.AUDIO_TRANSCRIPTION}
    assert models[0].input_modalities == {ModelInputModality.AUDIO}
    assert models[0].metadata["capabilities_authoritative"] is True
    assert models[0].metadata["supports_tools"] is False


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "provider.requestFailed"), (401, "provider.authenticationFailed"),
     (403, "provider.authenticationFailed"), (404, "provider.endpointNotFound"),
     (429, "provider.rateLimited"), (503, "provider.unavailable"), (302, "provider.requestFailed")],
)
async def test_http_errors_are_safe_and_redirects_are_not_followed(monkeypatch, caplog, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="secret-remote-detail",
            headers={"location": "https://other.test/secret"},
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        await azure_speech.discover_models(base_url=ENDPOINT, api_key="test-key")
    assert error.value.public_code == code
    assert len(calls) == 1
    assert "secret-remote-detail" not in str(error.value) + caplog.text
    assert ENDPOINT not in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.parametrize("exception", [httpx.ReadTimeout, httpx.ConnectError])
async def test_transport_errors_are_safe(monkeypatch, caplog, exception):
    def handler(request):
        raise exception("secret-remote-detail", request=request)

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        await azure_speech.discover_models(base_url=ENDPOINT, api_key="test-key", timeout=12)
    assert error.value.public_code == (
        "provider.timeout" if exception is httpx.ReadTimeout else "provider.unavailable"
    )
    if exception is httpx.ReadTimeout:
        assert error.value.params == {"timeoutSeconds": 12, "retryable": True}
    assert "secret-remote-detail" not in str(error.value) + caplog.text


async def test_transcription_timeout_covers_locale_lookup_and_upload(monkeypatch):
    async def wait_forever(**_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(azure_speech, "_supported_locales", wait_forever)
    with pytest.raises(ProviderError) as error:
        await azure_speech.transcribe_audio(
            base_url=ENDPOINT,
            api_key="test-key",
            model=azure_speech.MODEL_ID,
            audio=b"audio",
            language="de",
            timeout=0.01,
        )
    assert error.value.public_code == "provider.timeout"
    assert error.value.params == {"timeoutSeconds": 0.01, "retryable": True}


@pytest.mark.parametrize(
    "payload",
    [{}, {"combinedPhrases": None}, {"combinedPhrases": [{}]},
     {"combinedPhrases": [{"text": 42}]}, {"combinedPhrases": [{"text": "x"}] * 3}],
)
async def test_malformed_transcript_is_not_a_success(monkeypatch, payload):
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError) as error:
        await azure_speech.transcribe_audio(
            base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID, audio=b"audio"
        )
    assert error.value.public_code == "provider.invalidResponse"


@pytest.mark.parametrize("payload", [{}, {"Transcribe": []}, {"Transcribe": ["invalid"]}])
async def test_malformed_discovery_is_rejected(monkeypatch, payload):
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, json=payload))
    with pytest.raises(ProviderError) as error:
        await azure_speech.discover_models(base_url=ENDPOINT, api_key="test-key")
    assert error.value.public_code == "provider.invalidResponse"


async def test_silence_returns_empty_text(monkeypatch):
    _mock_client(
        monkeypatch, lambda _request: httpx.Response(200, json={"combinedPhrases": []})
    )
    assert await azure_speech.transcribe_audio(
        base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID, audio=b"audio"
    ) == ""


async def test_response_limit_is_enforced(monkeypatch):
    monkeypatch.setattr(azure_speech, "_MAX_RESPONSE_BYTES", 4)
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, content=b"12345"))
    with pytest.raises(ProviderError) as error:
        await azure_speech.discover_models(base_url=ENDPOINT, api_key="test-key")
    assert error.value.public_code == "provider.responseTooLarge"


async def test_exact_audio_and_response_limits_are_accepted(monkeypatch):
    content = b'{"combinedPhrases":[{"text":"ok"}]}'
    monkeypatch.setattr(azure_speech, "MAX_AUDIO_BYTES", 4)
    monkeypatch.setattr(azure_speech, "_MAX_RESPONSE_BYTES", len(content))
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, content=content))
    assert await azure_speech.transcribe_audio(
        base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID, audio=b"1234"
    ) == "ok"


async def test_unknown_model_is_rejected_before_network():
    with pytest.raises(ProviderError) as error:
        await azure_speech.transcribe_audio(
            base_url=ENDPOINT, api_key="test-key", model="unknown", audio=b"audio"
        )
    assert error.value.public_code == "provider.modelCapabilityUnavailable"


@pytest.mark.parametrize("audio", [b"", b"12345"])
async def test_invalid_audio_is_rejected_before_network(monkeypatch, audio):
    monkeypatch.setattr(azure_speech, "MAX_AUDIO_BYTES", 4)
    with pytest.raises(ProviderError) as error:
        await azure_speech.transcribe_audio(
            base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID, audio=audio
        )
    assert error.value.public_code == "provider.configurationInvalid"


async def test_unsupported_language_does_not_upload_audio(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"Transcribe": ["de-DE"]})

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        await azure_speech.transcribe_audio(
            base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID,
            audio=b"audio", language="xx",
        )
    assert error.value.public_code == "provider.configurationInvalid"


@pytest.mark.parametrize("api_key", [None, "", " "])
async def test_missing_key_is_not_connected(api_key):
    with pytest.raises(ProviderError) as error:
        await azure_speech.discover_models(base_url=ENDPOINT, api_key=api_key)
    assert error.value.public_code == "provider.notConnected"


async def test_service_discovery_uses_owner_bound_credential(monkeypatch):
    from mato_loop_backend.providers import service

    owner_id = uuid4()
    provider = LlmProviderConfig(
        name="Azure", adapter_type="azure-speech", owner_id=owner_id,
        credential_id=uuid4(), config={"base_url": ENDPOINT},
    )
    credential = SimpleNamespace()
    load = AsyncMock(return_value=credential)
    monkeypatch.setattr(service, "load_credential", load)
    monkeypatch.setattr(service, "read_credential", lambda _credential: {"api_key": "test-key"})
    discover = AsyncMock(return_value=[])
    monkeypatch.setattr(azure_speech, "discover_models", discover)
    db = AsyncMock(spec=AsyncSession)
    await ProviderService.discover_models(db, provider=provider)
    load.assert_awaited_once_with(
        db, provider.credential_id, owner_id=owner_id, populate_existing=True
    )
    discover.assert_awaited_once_with(base_url=ENDPOINT, api_key="test-key", timeout=30)


async def test_media_service_resolves_azure_default_and_dispatches(monkeypatch):
    provider = LlmProviderConfig(
        name="Azure", adapter_type="azure-speech", credential_id=uuid4(),
        config={"base_url": ENDPOINT}, owner_id=uuid4(),
    )
    model = ModelConfig(
        provider_id=provider.id, model_id=azure_speech.MODEL_ID,
        capabilities={ModelCapability.AUDIO_TRANSCRIPTION}, supports_tools=False,
    )
    settings = AiSettings(providers=[provider], models=[model])
    monkeypatch.setattr(media, "load_effective_ai", AsyncMock(return_value=settings))
    monkeypatch.setattr(
        media,
        "load_agent_profile",
        AsyncMock(return_value=AgentProfile(
            name="Test", instructions="Test", audio_transcription_model_id=model.id
        )),
    )
    load = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(media, "load_credential", load)
    monkeypatch.setattr(media, "read_credential", lambda _credential: {"api_key": "test-key"})
    transcribe = AsyncMock(return_value="Hallo")
    monkeypatch.setattr(azure_speech, "transcribe_audio", transcribe)
    db = AsyncMock(spec=AsyncSession)
    result = await media.MediaService.transcribe_audio(
        db, audio=b"audio", language="de", timeout=15, user_id=uuid4()
    )
    assert result.text == "Hallo"
    load.assert_awaited_once_with(
        db, provider.credential_id, owner_id=provider.owner_id, populate_existing=True
    )
    transcribe.assert_awaited_once_with(
        base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID,
        audio=b"audio", language="de", timeout=15,
    )
    assert media.media_model_available(settings, ModelCapability.AUDIO_TRANSCRIPTION, model.id)
    assert not media.media_model_available(settings, ModelCapability.CHAT, model.id)
    provider.credential_id = None
    assert not media.media_model_available(settings, ModelCapability.AUDIO_TRANSCRIPTION, model.id)
