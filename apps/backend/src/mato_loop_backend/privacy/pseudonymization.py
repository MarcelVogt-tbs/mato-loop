from __future__ import annotations

import copy
import secrets
from functools import lru_cache
from threading import Lock
from typing import Any

from detect_secrets.core.scan import scan_line
from detect_secrets.plugins.keyword import KeywordDetector
from detect_secrets.settings import transient_settings
from presidio_analyzer import EntityRecognizer, RecognizerResult
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    IpRecognizer,
    PhoneRecognizer,
)

_SECRET_SCAN_LOCK = Lock()
_SECRET_SCAN_SETTINGS = {
    "plugins_used": [
        {"name": name}
        for name in (
            "ArtifactoryDetector",
            "AWSKeyDetector",
            "AzureStorageKeyDetector",
            "BasicAuthDetector",
            "CloudantDetector",
            "DiscordBotTokenDetector",
            "GitHubTokenDetector",
            "GitLabTokenDetector",
            "IbmCloudIamDetector",
            "IbmCosHmacDetector",
            "JwtTokenDetector",
            "MailchimpDetector",
            "NpmDetector",
            "OpenAIDetector",
            "PrivateKeyDetector",
            "PypiTokenDetector",
            "SendGridDetector",
            "SlackDetector",
            "SoftlayerDetector",
            "SquareOAuthDetector",
            "StripeDetector",
            "TelegramBotTokenDetector",
            "TwilioKeyDetector",
        )
    ],
    "filters_used": [],
}


class PseudonymizationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _recognizers() -> tuple[EntityRecognizer, ...]:
    try:
        return (
            CreditCardRecognizer(supported_language="de"),
            EmailRecognizer(supported_language="de"),
            IbanRecognizer(supported_language="de"),
            IpRecognizer(supported_language="de"),
            PhoneRecognizer(supported_language="de", leniency=3),
        )
    except Exception as exc:
        raise PseudonymizationError("privacy.pseudonymizationUnavailable") from exc


def _expand_private_key_span(text: str, start: int, end: int) -> tuple[int, int]:
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    header = text[line_start:line_end].strip()

    if header.startswith("-----BEGIN ") and header.endswith("-----"):
        label = header[len("-----BEGIN ") : -len("-----")]
        end_marker = f"-----END {label}-----"
        block_end = text.find(end_marker, line_end)
        if block_end >= 0:
            return line_start, block_end + len(end_marker)

    if header.startswith("PuTTY-User-Key-File-"):
        block_end = text.find("\nPrivate-MAC:", line_end)
        if block_end >= 0:
            mac_end = text.find("\n", block_end + 1)
            return line_start, len(text) if mac_end < 0 else mac_end

    return start, end


def _secret_results(text: str) -> list[RecognizerResult]:
    results: list[RecognizerResult] = []
    offset = 0
    keyword_detector = KeywordDetector()
    with _SECRET_SCAN_LOCK, transient_settings(_SECRET_SCAN_SETTINGS):
        for line_with_ending in text.splitlines(keepends=True):
            line = line_with_ending.rstrip("\r\n")
            potentials = list(scan_line(line))
            potentials.extend(
                keyword_detector.analyze_line(
                    filename="message.yaml",
                    line=line,
                )
            )
            for potential in potentials:
                secret = potential.secret_value
                if not secret:
                    continue
                position = 0
                while (position := line.find(secret, position)) >= 0:
                    start = offset + position
                    end = start + len(secret)
                    if potential.type == "Private Key":
                        start, end = _expand_private_key_span(text, start, end)
                    results.append(
                        RecognizerResult(
                            entity_type="SECRET",
                            start=start,
                            end=end,
                            score=1.0,
                        )
                    )
                    position += len(secret)
            offset += len(line_with_ending)
    return results


def _analyze(text: str) -> list[RecognizerResult]:
    results = _secret_results(text)
    for recognizer in _recognizers():
        current = recognizer.analyze(
            text=text,
            entities=recognizer.supported_entities,
            nlp_artifacts=None,
        )
        minimum_score = 0.4 if isinstance(recognizer, PhoneRecognizer) else 0.45
        results.extend(result for result in current if result.score >= minimum_score)
    return results


class RequestPseudonymizer:
    _marker_instruction = (
        "Host privacy placeholders are opaque values. Copy every placeholder exactly and "
        "unchanged in answers and tool arguments; never interpret, abbreviate, or reformat it. "
        "Never create a new privacy placeholder. Only use placeholders that already appear "
        "in the input."
    )

    def __init__(self) -> None:
        self._nonce = secrets.token_hex(4).upper()
        self._markers: dict[tuple[str, str], str] = {}
        self._originals: dict[str, str] = {}

    @property
    def max_marker_length(self) -> int:
        return max((len(marker) for marker in self._originals), default=0)

    @property
    def marker_prefix(self) -> str:
        return f"[[MLP_{self._nonce}_"

    def protect_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        try:
            protected = copy.deepcopy(messages)
            for message in protected:
                content = message.get("content")
                if isinstance(content, str):
                    message["content"] = self.protect_text(content)
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if isinstance(call, dict) and "arguments" in call:
                            call["arguments"] = self.protect_value(call["arguments"])
                # Opaque continuation data cannot be inspected or safely pseudonymized.
                message.pop("provider_data", None)
            if self._originals:
                system_message = next(
                    (
                        message
                        for message in protected
                        if message.get("role") == "system"
                        and isinstance(message.get("content"), str)
                    ),
                    None,
                )
                if system_message is None:
                    protected.insert(
                        0, {"role": "system", "content": self._marker_instruction}
                    )
                else:
                    system_message["content"] += f"\n\n{self._marker_instruction}"
            return protected
        except PseudonymizationError:
            raise
        except Exception as exc:
            raise PseudonymizationError("privacy.pseudonymizationFailed") from exc

    def protect_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.protect_text(value)
        if isinstance(value, list):
            return [self.protect_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.protect_value(item) for key, item in value.items()}
        return value

    def restore_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.restore_text(value)
        if isinstance(value, list):
            return [self.restore_value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.restore_value(item) for key, item in value.items()}
        return value

    def protect_text(self, text: str) -> str:
        if not text:
            return text
        try:
            results = _analyze(text)
        except PseudonymizationError:
            raise
        except Exception as exc:
            raise PseudonymizationError("privacy.pseudonymizationFailed") from exc
        selected = []
        occupied: list[tuple[int, int]] = []
        for result in sorted(
            results,
            key=lambda item: (-item.score, -(item.end - item.start), item.start),
        ):
            if any(result.start < end and result.end > start for start, end in occupied):
                continue
            selected.append(result)
            occupied.append((result.start, result.end))
        protected = text
        for result in sorted(selected, key=lambda item: item.start, reverse=True):
            original = text[result.start : result.end]
            key = (result.entity_type, original.casefold())
            marker = self._markers.get(key)
            if marker is None:
                marker = f"{self.marker_prefix}{len(self._markers) + 1:04d}]]"
                self._markers[key] = marker
                self._originals[marker] = original
            protected = protected[: result.start] + marker + protected[result.end :]
        return protected

    def restore_text(self, text: str) -> str:
        restored = text
        for marker, original in self._originals.items():
            restored = restored.replace(marker, original)
        if self.marker_prefix in restored:
            raise PseudonymizationError("privacy.pseudonymizationFailed")
        return restored


class StreamingPseudonymRestorer:
    def __init__(self, pseudonymizer: RequestPseudonymizer) -> None:
        self._pseudonymizer = pseudonymizer
        self._prefix = pseudonymizer.marker_prefix
        self._buffer = ""

    def feed(self, text: str) -> str:
        self._buffer += text
        restored: list[str] = []
        while self._buffer:
            marker_start = self._buffer.find(self._prefix)
            if marker_start < 0:
                retained = 0
                for length in range(1, min(len(self._buffer), len(self._prefix) - 1) + 1):
                    if self._prefix.startswith(self._buffer[-length:]):
                        retained = length
                safe_end = len(self._buffer) - retained
                restored.append(self._buffer[:safe_end])
                self._buffer = self._buffer[safe_end:]
                break
            if marker_start > 0:
                restored.append(self._buffer[:marker_start])
                self._buffer = self._buffer[marker_start:]
                continue
            marker_end = self._buffer.find("]]", len(self._prefix))
            if marker_end < 0:
                if (
                    self._pseudonymizer.max_marker_length
                    and len(self._buffer) > self._pseudonymizer.max_marker_length
                ):
                    raise PseudonymizationError("privacy.pseudonymizationFailed")
                break
            marker_end += 2
            restored.append(
                self._pseudonymizer.restore_text(self._buffer[:marker_end])
            )
            self._buffer = self._buffer[marker_end:]
        return "".join(restored)

    def flush(self) -> str:
        remaining = self._pseudonymizer.restore_text(self._buffer)
        self._buffer = ""
        return remaining
