import asyncio
import json
from email.parser import BytesParser
from email.policy import default
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
from xml.etree import ElementTree

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.providers import azure_speech, media
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import AiSettings
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    ModelInputModality,
)

ENDPOINT = "https://speech.cognitiveservices.azure.com"
VOICES = [
    {"ShortName": azure_speech.DEFAULT_VOICE, "Locale": "en-US"},
    {"ShortName": "de-DE-KatjaNeural", "Locale": "de-DE"},
    {"ShortName": "de-DE-Seraphina:DragonHDLatestNeural", "Locale": "de-DE"},
]


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


async def test_discovery_checks_access_and_declares_media_models(monkeypatch):
    def handler(request):
        assert request.method == "GET"
        if request.url.path == "/tts/cognitiveservices/voices/list":
            return httpx.Response(200, json=VOICES)
        assert request.url.path == "/speechtotext/transcriptions/locales"
        return httpx.Response(200, json={"Transcribe": ["de-DE"]})

    _mock_client(monkeypatch, handler)
    models = await azure_speech.discover_models(base_url=ENDPOINT, api_key="test-key")
    assert len(models) == 2
    assert models[0].id == azure_speech.MODEL_ID
    assert models[0].capabilities == {ModelCapability.AUDIO_TRANSCRIPTION}
    assert models[0].input_modalities == {ModelInputModality.AUDIO}
    assert models[0].metadata["capabilities_authoritative"] is True
    assert models[0].metadata["supports_tools"] is False
    assert models[1].id == azure_speech.SPEECH_MODEL_ID
    assert models[1].capabilities == {ModelCapability.SPEECH_GENERATION}
    assert models[1].input_modalities == {ModelInputModality.TEXT}
    assert models[1].metadata["speech_voices"] == sorted(voice["ShortName"] for voice in VOICES)


@pytest.mark.parametrize(
    ("endpoint", "host", "prefix"),
    [(ENDPOINT, "speech.cognitiveservices.azure.com", "/tts"),
     ("https://westeurope.api.cognitive.microsoft.com",
      "westeurope.tts.speech.microsoft.com", "")],
)
@pytest.mark.parametrize("hd", [False, True])
async def test_synthesis_uses_escaped_ssml_and_wav_contract(monkeypatch, endpoint, host, prefix, hd):
    text = 'Hallo <audio src="https://other.test"/> & guten Tag!'
    voice = VOICES[2 if hd else 1]["ShortName"]
    speed = 1.0 if hd else 1.25

    def handler(request):
        assert request.url.host == host
        assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
        assert not request.url.query
        if request.method == "GET":
            assert request.url.path == f"{prefix}/cognitiveservices/voices/list"
            return httpx.Response(200, json=VOICES)
        assert request.url.path == f"{prefix}/cognitiveservices/v1"
        assert request.headers["Content-Type"] == "application/ssml+xml"
        assert request.headers["X-Microsoft-OutputFormat"] == "riff-24khz-16bit-mono-pcm"
        root = ElementTree.fromstring(request.content)
        ns = "{http://www.w3.org/2001/10/synthesis}"
        assert root.attrib["{http://www.w3.org/XML/1998/namespace}lang"] == "de-DE"
        speaker = root.find(f"{ns}voice")
        assert speaker.attrib["name"] == voice
        if hd:
            assert (speaker.text, list(speaker)) == (text, [])
        else:
            prosody = speaker.find(f"{ns}prosody")
            assert prosody.attrib["rate"] == "1.25"
            assert prosody.text == text
            assert list(prosody) == []
        return httpx.Response(200, content=b"RIFFaudio")

    _mock_client(monkeypatch, handler)
    assert await azure_speech.generate_speech(
        base_url=endpoint, api_key="test-key", model=azure_speech.SPEECH_MODEL_ID,
        text=text, voice=voice, speed=speed,
    ) == b"RIFFaudio"


@pytest.mark.parametrize(
    ("payload", "voice", "speed", "audio", "code"),
    [([], azure_speech.DEFAULT_VOICE, 1, b"audio", "provider.invalidResponse"),
     ([{"ShortName": "invalid"}], azure_speech.DEFAULT_VOICE, 1, b"audio", "provider.invalidResponse"),
     (VOICES, "unknown", 1, b"audio", "provider.configurationInvalid"),
     (VOICES, azure_speech.DEFAULT_VOICE, 0.25, b"audio", "provider.configurationInvalid"),
     (VOICES, azure_speech.DEFAULT_VOICE, 4, b"audio", "provider.configurationInvalid"),
     (VOICES, VOICES[2]["ShortName"], 1.25, b"audio", "provider.configurationInvalid"),
     (VOICES, azure_speech.DEFAULT_VOICE, 1, b"", "provider.invalidResponse")],
)
async def test_invalid_synthesis_is_not_a_success(monkeypatch, payload, voice, speed, audio, code):
    _mock_client(monkeypatch, lambda request: httpx.Response(
        200, json=payload,
    ) if request.method == "GET" else httpx.Response(200, content=audio))
    with pytest.raises(ProviderError) as error:
        await azure_speech.generate_speech(
            base_url=ENDPOINT, api_key="test-key", model=azure_speech.SPEECH_MODEL_ID,
            text="Hallo", voice=voice, speed=speed,
        )
    assert error.value.public_code == code


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "provider.requestFailed"), (401, "provider.authenticationFailed"),
     (403, "provider.authenticationFailed"), (404, "provider.endpointNotFound"),
     (429, "provider.rateLimited"), (503, "provider.unavailable"), (302, "provider.requestFailed")],
)
@pytest.mark.parametrize("speech", [False, True])
async def test_http_errors_are_safe_and_redirects_are_not_followed(monkeypatch, caplog, status, code, speech):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, text="secret-remote-detail",
            headers={"location": "https://other.test/secret"},
        )

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        await azure_speech._request(base_url=ENDPOINT, api_key="test-key", timeout=30, speech=speech)
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


@pytest.mark.parametrize("speech", [False, True])
async def test_timeout_covers_lookup_and_media_request(monkeypatch, speech):
    async def wait_forever(**_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(azure_speech, "_supported_voices" if speech else "_supported_locales", wait_forever)
    with pytest.raises(ProviderError) as error:
        operation = azure_speech.generate_speech if speech else azure_speech.transcribe_audio
        await operation(
            base_url=ENDPOINT,
            api_key="test-key",
            model=azure_speech.SPEECH_MODEL_ID if speech else azure_speech.MODEL_ID,
            timeout=0.01,
            **({"text": "Hallo"} if speech else {"audio": b"audio", "language": "de"}),
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


@pytest.mark.parametrize("speech", [False, True])
async def test_response_limit_is_enforced(monkeypatch, speech):
    monkeypatch.setattr(azure_speech, "_MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(azure_speech, "MAX_AUDIO_BYTES", 4)
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, content=b"12345"))
    with pytest.raises(ProviderError) as error:
        await azure_speech._request(
            base_url=ENDPOINT, api_key="test-key", timeout=30,
            speech=speech, ssml=b"<speak/>" if speech else None,
        )
    assert error.value.public_code == "provider.responseTooLarge"


async def test_exact_audio_and_response_limits_are_accepted(monkeypatch):
    content = b'{"combinedPhrases":[{"text":"ok"}]}'
    monkeypatch.setattr(azure_speech, "MAX_AUDIO_BYTES", 4)
    monkeypatch.setattr(azure_speech, "_MAX_RESPONSE_BYTES", len(content))
    _mock_client(monkeypatch, lambda _request: httpx.Response(200, content=content))
    assert await azure_speech.transcribe_audio(
        base_url=ENDPOINT, api_key="test-key", model=azure_speech.MODEL_ID, audio=b"1234"
    ) == "ok"


@pytest.mark.parametrize("speech", [False, True])
async def test_unknown_model_is_rejected_before_network(speech):
    with pytest.raises(ProviderError) as error:
        operation = azure_speech.generate_speech if speech else azure_speech.transcribe_audio
        await operation(
            base_url=ENDPOINT, api_key="test-key", model="unknown",
            **({"text": "Hallo"} if speech else {"audio": b"audio"}),
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


@pytest.mark.parametrize("speech", [False, True])
@pytest.mark.parametrize("adapter_type", ["azure-speech", "lemonade"])
async def test_media_service_resolves_default_and_dispatches(monkeypatch, speech, adapter_type):
    from mato_loop_backend.providers import lemonade

    adapter = azure_speech if adapter_type == "azure-speech" else lemonade
    capability = ModelCapability.SPEECH_GENERATION if speech else ModelCapability.AUDIO_TRANSCRIPTION
    model_id = azure_speech.SPEECH_MODEL_ID if speech else azure_speech.MODEL_ID
    provider = LlmProviderConfig(
        name="Media", adapter_type=adapter_type, credential_id=uuid4(),
        config={"base_url": ENDPOINT}, owner_id=uuid4(),
    )
    model = ModelConfig(
        provider_id=provider.id, model_id=model_id,
        capabilities={capability}, supports_tools=False,
    )
    defaults = {"speech_generation_model_id" if speech else "audio_transcription_model_id": model.id}
    settings = AiSettings(providers=[provider], models=[model], **defaults)
    monkeypatch.setattr(media, "load_effective_ai", AsyncMock(return_value=settings))
    load = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(media, "load_credential", load)
    monkeypatch.setattr(media, "read_credential", lambda _credential: {"api_key": "test-key"})
    operation = AsyncMock(return_value=b"RIFFaudio" if speech else "Hallo")
    monkeypatch.setattr(adapter, "generate_speech" if speech else "transcribe_audio", operation)
    db = AsyncMock(spec=AsyncSession)
    if speech:
        result = await media.MediaService.generate_speech(
            db, text="Hallo", timeout=15, user_id=uuid4()
        )
        assert (result.data, result.mime_type, result.filename) == (
            b"RIFFaudio", "audio/wav", "speech.wav",
        )
        arguments = {
            "text": "Hallo",
            "voice": azure_speech.DEFAULT_VOICE if adapter is azure_speech else "shimmer",
            "speed": 1.0,
        }
    else:
        result = await media.MediaService.transcribe_audio(
            db, audio=b"audio", language="de", timeout=15, user_id=uuid4()
        )
        assert result.text == "Hallo"
        arguments = {"audio": b"audio", "language": "de"}
    load.assert_awaited_once_with(
        db, provider.credential_id, owner_id=provider.owner_id, populate_existing=True
    )
    operation.assert_awaited_once_with(
        base_url=ENDPOINT, api_key="test-key", model=model_id,
        timeout=15, **arguments,
    )
    if speech:
        settings.speech_generation_voices[model.id] = "de-DE-KatjaNeural"
        await media.MediaService.generate_speech(db, text="Hallo", user_id=uuid4())
        assert operation.await_args.kwargs["voice"] == "de-DE-KatjaNeural"
        await media.MediaService.generate_speech(
            db, text="Hallo", voice="explicit", user_id=uuid4()
        )
        assert operation.await_args.kwargs["voice"] == "explicit"
    assert media.media_model_available(settings, capability, model.id)
    assert not media.media_model_available(settings, ModelCapability.CHAT, model.id)
    provider.credential_id = None
    assert media.media_model_available(settings, capability, model.id) is (
        adapter_type == "lemonade"
    )


async def test_ssml_limit_rejects_oversized_text_before_synthesis(monkeypatch):
    monkeypatch.setattr(azure_speech, "MAX_SSML_BYTES", 256)

    def handler(request):
        assert request.method == "GET"
        return httpx.Response(200, json=VOICES)

    _mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        await azure_speech.generate_speech(
            base_url=ENDPOINT, api_key="test-key", model=azure_speech.SPEECH_MODEL_ID,
            text="&" * 100,
        )
    assert error.value.public_code == "provider.configurationInvalid"
