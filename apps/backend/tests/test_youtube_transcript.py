import json
from unittest.mock import AsyncMock

import pytest

import mato_loop_backend.integrations.youtube_transcript as youtube_transcript
from mato_loop_backend.integrations.youtube_transcript import (
    TranscriptError,
    canonicalize_youtube_video_url,
    fetch_youtube_transcript,
)
from mato_loop_backend.web.fetch import WebFetchBytesResult


def _response(data: bytes, content_type: str) -> WebFetchBytesResult:
    return WebFetchBytesResult(
        final_url="https://www.youtube.com/",
        content_type=content_type,
        charset="utf-8",
        status_code=200,
        data=data,
    )


@pytest.mark.parametrize(
    "value",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=12",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?si=tracking",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ?feature=share",
    ],
)
def test_canonicalizes_supported_youtube_urls(value: str) -> None:
    assert canonicalize_youtube_video_url(value) == "https://youtu.be/dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "value",
    [
        "http://youtu.be/dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=abcdefghijk",
        "https://www.youtube.com/shorts/dQw4w9WgXcQ/extra",
        "https://youtu.be/not-an-id",
    ],
)
def test_rejects_unsupported_youtube_urls(value: str) -> None:
    with pytest.raises(TranscriptError, match="transcript.unsupportedUrl"):
        canonicalize_youtube_video_url(value)


async def test_fetches_existing_language_without_translation(monkeypatch) -> None:
    calls = []
    caption_url = "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=de"
    player = {
        "playabilityStatus": {"status": "OK"},
        "videoDetails": {"title": "  Beispiel   Video  "},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=en",
                        "languageCode": "en",
                        "kind": "asr",
                    },
                    {"baseUrl": caption_url, "languageCode": "de"},
                ]
            }
        },
    }

    async def request(url, **kwargs):
        await kwargs["authorize_url"](url)
        calls.append((url, kwargs))
        if url.endswith("watch?v=dQw4w9WgXcQ"):
            return _response(b'"INNERTUBE_API_KEY":"public_key_12345"', "text/html")
        if "/youtubei/v1/player" in url:
            assert json.loads(kwargs["body"])["videoId"] == "dQw4w9WgXcQ"
            return _response(json.dumps(player).encode(), "application/json")
        assert url == caption_url
        return _response(
            b'<transcript><text start="1.2">Hallo &amp;amp; &lt;b&gt;Welt&lt;/b&gt;</text></transcript>',
            "text/xml",
        )

    monkeypatch.setattr(youtube_transcript, "request_web_bytes", request)
    before_request = AsyncMock()

    result = await fetch_youtube_transcript(
        "https://youtu.be/dQw4w9WgXcQ",
        language="de-DE",
        before_request=before_request,
    )

    assert result.title == "Beispiel Video"
    assert result.language == "de"
    assert result.language_fallback is False
    assert result.track_type == "manual"
    assert result.content == "[00:00:01] Hallo & Welt"
    assert len(calls) == before_request.await_count == 3
    assert calls[1][1]["method"] == "POST"
    assert "tlang=" not in calls[2][0]


async def test_falls_back_to_original_track_and_rejects_foreign_caption_url(
    monkeypatch,
) -> None:
    player = {
        "playabilityStatus": {"status": "OK"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=en",
                        "languageCode": "en",
                        "kind": "asr",
                    }
                ]
            }
        },
    }

    async def request(url, **kwargs):
        await kwargs["authorize_url"](url)
        if url.endswith("watch?v=dQw4w9WgXcQ"):
            return _response(b'"INNERTUBE_API_KEY":"public_key_12345"', "text/html")
        if "/youtubei/v1/player" in url:
            return _response(json.dumps(player).encode(), "application/json")
        return _response(b'<transcript><text start="0">Hello</text></transcript>', "text/xml")

    monkeypatch.setattr(youtube_transcript, "request_web_bytes", request)
    result = await fetch_youtube_transcript(
        "https://youtu.be/dQw4w9WgXcQ", language="de"
    )
    assert (result.language, result.language_fallback, result.track_type) == (
        "en",
        True,
        "automatic",
    )

    player["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"][0][
        "baseUrl"
    ] = "https://evil.example/api/timedtext?v=dQw4w9WgXcQ"
    with pytest.raises(TranscriptError, match="transcript.responseInvalid"):
        await fetch_youtube_transcript("https://youtu.be/dQw4w9WgXcQ")


async def test_prefers_exact_regional_language_and_removes_caption_format(
    monkeypatch,
) -> None:
    selected_urls = []
    player = {
        "playabilityStatus": {"status": "OK"},
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=pt-PT&fmt=srv3",
                        "languageCode": "pt-PT",
                    },
                    {
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=pt-BR&fmt=srv3",
                        "languageCode": "pt-BR",
                    },
                ]
            }
        },
    }

    async def request(url, **kwargs):
        await kwargs["authorize_url"](url)
        if url.endswith("watch?v=dQw4w9WgXcQ"):
            return _response(b'"INNERTUBE_API_KEY":"public_key_12345"', "text/html")
        if "/youtubei/v1/player" in url:
            return _response(json.dumps(player).encode(), "application/json")
        selected_urls.append(url)
        return _response(b'<transcript><text start="0">Ola</text></transcript>', "text/xml")

    monkeypatch.setattr(youtube_transcript, "request_web_bytes", request)
    result = await fetch_youtube_transcript(
        "https://youtu.be/dQw4w9WgXcQ", language="pt-BR"
    )

    assert result.language == "pt-BR"
    assert selected_urls == [
        "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=pt-BR"
    ]


async def test_rejects_missing_playability_and_private_video(monkeypatch) -> None:
    player = {
        "captions": {
            "playerCaptionsTracklistRenderer": {
                "captionTracks": [
                    {
                        "baseUrl": "https://www.youtube.com/api/timedtext?v=dQw4w9WgXcQ&lang=en",
                        "languageCode": "en",
                    }
                ]
            }
        }
    }

    async def request(url, **kwargs):
        await kwargs["authorize_url"](url)
        if url.endswith("watch?v=dQw4w9WgXcQ"):
            return _response(b'"INNERTUBE_API_KEY":"public_key_12345"', "text/html")
        return _response(json.dumps(player).encode(), "application/json")

    monkeypatch.setattr(youtube_transcript, "request_web_bytes", request)
    with pytest.raises(TranscriptError, match="transcript.responseInvalid"):
        await fetch_youtube_transcript("https://youtu.be/dQw4w9WgXcQ")

    player["playabilityStatus"] = {"status": "OK"}
    player["videoDetails"] = {"isPrivate": True}
    with pytest.raises(TranscriptError, match="transcript.unavailable"):
        await fetch_youtube_transcript("https://youtu.be/dQw4w9WgXcQ")
