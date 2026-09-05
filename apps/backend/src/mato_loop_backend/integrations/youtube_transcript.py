from __future__ import annotations

import asyncio
import html
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from defusedxml import ElementTree
from yarl import URL

from mato_loop_backend.integrations.webfetch import (
    WebFetchBytesResult,
    WebFetchError,
    request_web_bytes,
)
from mato_loop_backend.settings.models import DEFAULT_WEBFETCH_USER_AGENT

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_API_KEY = re.compile(rb'"INNERTUBE_API_KEY"\s*:\s*"([A-Za-z0-9_-]{10,100})"')
_HTML_TAG = re.compile(r"<[^>]*>")
_WATCH_MAX_BYTES = 4 * 1024 * 1024
_PLAYER_MAX_BYTES = 2 * 1024 * 1024
_CAPTION_MAX_BYTES = 8 * 1024 * 1024
_MAX_TRACKS = 100
_MAX_SEGMENTS = 20_000
_MAX_CONTENT_CHARACTERS = 1_000_000
_INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "ANDROID",
        "clientVersion": "20.10.38",
        "hl": "en",
    }
}


class TranscriptError(RuntimeError):
    def __init__(self, code: str, *, params: dict[str, object] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


@dataclass(frozen=True, slots=True)
class TranscriptResult:
    source_url: str
    title: str | None
    language: str
    language_fallback: bool
    track_type: str
    content: str


def _error(code: str, **params: object) -> TranscriptError:
    return TranscriptError(f"transcript.{code}", params=params or None)


def canonicalize_youtube_video_url(value: str) -> str:
    try:
        url = URL(value)
        host = (url.host or "").rstrip(".").lower()
        port = url.port
    except (TypeError, ValueError) as exc:
        raise _error("unsupportedUrl") from exc
    if (
        url.scheme.lower() != "https"
        or url.user is not None
        or url.password is not None
        or port != 443
    ):
        raise _error("unsupportedUrl")
    video_id: str | None = None
    if host in {"youtube.com", "www.youtube.com"} and url.path == "/watch":
        values = url.query.getall("v", [])
        if len(values) == 1:
            video_id = values[0]
    elif host in {"youtube.com", "www.youtube.com"} and url.path.startswith("/shorts/"):
        parts = [part for part in url.path.split("/") if part]
        if len(parts) == 2 and parts[0] == "shorts":
            video_id = parts[1]
    elif host == "youtu.be":
        parts = [part for part in url.path.split("/") if part]
        if len(parts) == 1:
            video_id = parts[0]
    if video_id is None or _VIDEO_ID.fullmatch(video_id) is None:
        raise _error("unsupportedUrl")
    return f"https://youtu.be/{video_id}"


def _video_id(source_url: str) -> str:
    return source_url.rsplit("/", 1)[-1]


def _internal_url(value: str, *, path: str, video_id: str) -> URL:
    try:
        url = URL(value)
        host = (url.host or "").rstrip(".").lower()
        port = url.port
    except (TypeError, ValueError) as exc:
        raise _error("responseInvalid") from exc
    if (
        url.scheme.lower() != "https"
        or host != "www.youtube.com"
        or port != 443
        or url.user is not None
        or url.password is not None
        or url.path != path
        or url.fragment
    ):
        raise _error("responseInvalid")
    if path in {"/watch", "/api/timedtext"}:
        values = url.query.getall("v", [])
        if len(values) != 1 or values[0] != video_id:
            raise _error("responseInvalid")
    if path == "/api/timedtext" and url.query.getall("tlang", []):
        raise _error("responseInvalid")
    return url


def _stage_authorizer(
    *,
    path: str,
    video_id: str,
    before_request: Callable[[str], Awaitable[None]] | None,
) -> Callable[[str], Awaitable[None]]:
    async def authorize(value: str) -> None:
        _internal_url(value, path=path, video_id=video_id)
        if before_request is not None:
            await before_request(value)

    return authorize


def _require_content_type(response: WebFetchBytesResult, allowed: set[str]) -> None:
    if response.content_type not in allowed:
        raise _error("responseInvalid")


def _decode_json(response: WebFetchBytesResult) -> dict[str, object]:
    _require_content_type(response, {"application/json"})
    try:
        value = json.loads(response.data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _error("responseInvalid") from exc
    if not isinstance(value, dict):
        raise _error("responseInvalid")
    return value


def _track_language(track: object) -> str | None:
    if not isinstance(track, dict):
        return None
    language = track.get("languageCode")
    return language if isinstance(language, str) and language else None


def _language_matches(requested: str, available: str) -> bool:
    requested = requested.lower()
    available = available.lower()
    return requested == available or requested.split("-", 1)[0] == available.split("-", 1)[0]


def _select_track(
    tracks: list[object], language: str | None
) -> tuple[dict[str, object], bool]:
    valid = [
        track
        for track in tracks
        if isinstance(track, dict)
        and _track_language(track) is not None
        and isinstance(track.get("baseUrl"), str)
    ]
    if not valid or len(valid) > _MAX_TRACKS:
        raise _error("unavailable")
    if language is not None:
        exact_matches = [
            track
            for track in valid
            if language.lower() == str(track["languageCode"]).lower()
        ]
        matches = exact_matches or [
            track
            for track in valid
            if _language_matches(language, str(track["languageCode"]))
        ]
        if matches:
            matches.sort(key=lambda track: track.get("kind") == "asr")
            return matches[0], False
    return valid[0], language is not None


def _title(player: dict[str, object]) -> str | None:
    details = player.get("videoDetails")
    if not isinstance(details, dict) or not isinstance(details.get("title"), str):
        return None
    title = " ".join(str(details["title"]).split())
    return title[:300] or None


def _is_active_live(player: dict[str, object]) -> bool:
    details = player.get("videoDetails")
    if isinstance(details, dict) and details.get("isLive") is True:
        return True
    microformat = player.get("microformat")
    if not isinstance(microformat, dict):
        return False
    renderer = microformat.get("playerMicroformatRenderer")
    if not isinstance(renderer, dict):
        return False
    live = renderer.get("liveBroadcastDetails")
    return isinstance(live, dict) and live.get("isLiveNow") is True


def _is_private(player: dict[str, object]) -> bool:
    details = player.get("videoDetails")
    return isinstance(details, dict) and details.get("isPrivate") is True


def _caption_tracks(player: dict[str, object]) -> list[object]:
    captions = player.get("captions")
    renderer = (
        captions.get("playerCaptionsTracklistRenderer")
        if isinstance(captions, dict)
        else None
    )
    tracks = renderer.get("captionTracks") if isinstance(renderer, dict) else None
    if not isinstance(tracks, list):
        raise _error("unavailable")
    return tracks


def _timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"[{hours:02d}:{minutes:02d}:{seconds:02d}]"


def _parse_captions(response: WebFetchBytesResult) -> str:
    _require_content_type(response, {"application/xml", "text/xml"})
    try:
        root = ElementTree.fromstring(response.data)
    except Exception as exc:
        raise _error("responseInvalid") from exc
    if root.tag.rsplit("}", 1)[-1] != "transcript":
        raise _error("responseInvalid")
    lines: list[str] = []
    characters = 0
    segments = 0
    for element in root:
        if element.tag.rsplit("}", 1)[-1] != "text":
            continue
        segments += 1
        if segments > _MAX_SEGMENTS:
            raise _error("responseTooLarge")
        try:
            start = float(element.attrib["start"])
        except (KeyError, TypeError, ValueError) as exc:
            raise _error("responseInvalid") from exc
        if not math.isfinite(start) or start < 0:
            raise _error("responseInvalid")
        text = " ".join(
            _HTML_TAG.sub("", html.unescape("".join(element.itertext()))).split()
        )
        if not text:
            continue
        line = f"{_timestamp(start)} {text}"
        characters += len(line) + 1
        if characters > _MAX_CONTENT_CHARACTERS:
            raise _error("responseTooLarge")
        lines.append(line)
    if not lines:
        raise _error("unavailable")
    return "\n".join(lines)


def _mapped_webfetch_error(exc: WebFetchError) -> TranscriptError:
    code = {
        "webfetch.timeout": "timeout",
        "webfetch.responseTooLarge": "responseTooLarge",
    }.get(exc.code, "requestFailed")
    return _error(code, **(exc.params or {}))


async def fetch_youtube_transcript(
    url: str,
    *,
    language: str | None = None,
    timeout: float = 30,
    max_response_bytes: int = _CAPTION_MAX_BYTES,
    user_agent: str = DEFAULT_WEBFETCH_USER_AGENT,
    public_addresses_only: bool = True,
    before_request: Callable[[str], Awaitable[None]] | None = None,
) -> TranscriptResult:
    source_url = canonicalize_youtube_video_url(url)
    video_id = _video_id(source_url)
    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        async with asyncio.timeout(timeout):
            watch = await request_web_bytes(
                watch_url,
                accept="text/html",
                timeout=timeout,
                public_addresses_only=public_addresses_only,
                authorize_url=_stage_authorizer(
                    path="/watch", video_id=video_id, before_request=before_request
                ),
                max_response_bytes=min(max_response_bytes, _WATCH_MAX_BYTES),
                user_agent=user_agent,
            )
            _require_content_type(watch, {"text/html"})
            match = _API_KEY.search(watch.data)
            if match is None:
                raise _error("unavailable")
            api_key = match.group(1).decode("ascii")
            body = json.dumps(
                {"context": _INNERTUBE_CONTEXT, "videoId": video_id},
                separators=(",", ":"),
            ).encode()
            player = _decode_json(
                await request_web_bytes(
                    f"https://www.youtube.com/youtubei/v1/player?key={api_key}",
                    method="POST",
                    body=body,
                    request_content_type="application/json",
                    accept="application/json",
                    timeout=timeout,
                    public_addresses_only=public_addresses_only,
                    authorize_url=_stage_authorizer(
                        path="/youtubei/v1/player",
                        video_id=video_id,
                        before_request=before_request,
                    ),
                    max_response_bytes=min(max_response_bytes, _PLAYER_MAX_BYTES),
                    user_agent=user_agent,
                )
            )
            playability = player.get("playabilityStatus")
            if not isinstance(playability, dict) or not isinstance(
                playability.get("status"), str
            ):
                raise _error("responseInvalid")
            if playability["status"] != "OK":
                raise _error("unavailable")
            if _is_private(player) or _is_active_live(player):
                raise _error("unavailable")
            track, language_fallback = _select_track(
                _caption_tracks(player), language
            )
            caption_target = _internal_url(
                str(track["baseUrl"]), path="/api/timedtext", video_id=video_id
            )
            caption_url = str(
                caption_target.with_query(
                    [(key, value) for key, value in caption_target.query.items() if key != "fmt"]
                )
            )
            captions = await request_web_bytes(
                caption_url,
                accept="application/xml, text/xml",
                timeout=timeout,
                public_addresses_only=public_addresses_only,
                authorize_url=_stage_authorizer(
                    path="/api/timedtext",
                    video_id=video_id,
                    before_request=before_request,
                ),
                max_response_bytes=min(max_response_bytes, _CAPTION_MAX_BYTES),
                user_agent=user_agent,
            )
            content = await asyncio.to_thread(_parse_captions, captions)
            return TranscriptResult(
                source_url=source_url,
                title=_title(player),
                language=str(track["languageCode"]),
                language_fallback=language_fallback,
                track_type="automatic" if track.get("kind") == "asr" else "manual",
                content=content,
            )
    except TranscriptError:
        raise
    except WebFetchError as exc:
        raise _mapped_webfetch_error(exc) from exc
    except TimeoutError as exc:
        raise _error("timeout") from exc
