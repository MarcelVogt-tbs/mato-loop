from __future__ import annotations

import copy
import secrets
from functools import lru_cache
from typing import Any

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    EmailRecognizer,
    IbanRecognizer,
    IpRecognizer,
    PhoneRecognizer,
    SpacyRecognizer,
)


class PseudonymizationError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def _analyzer() -> AnalyzerEngine:
    try:
        nlp_engine = NlpEngineProvider(
            nlp_configuration={
                "nlp_engine_name": "spacy",
                "models": [{"lang_code": "de", "model_name": "de_core_news_sm"}],
                "ner_model_configuration": {"labels_to_ignore": ["MISC", "ORG"]},
            }
        ).create_engine()
        registry = RecognizerRegistry(supported_languages=["de"])
        for recognizer_type in (
            CreditCardRecognizer,
            EmailRecognizer,
            IbanRecognizer,
            IpRecognizer,
            PhoneRecognizer,
            SpacyRecognizer,
        ):
            registry.add_recognizer(recognizer_type(supported_language="de"))
        analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=registry,
            supported_languages=["de"],
        )
        analyzer.registry.add_recognizer(
            PatternRecognizer(
                supported_entity="SECRET",
                supported_language="de",
                patterns=[
                    Pattern(
                        "private-key",
                        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z0-9 ]*PRIVATE KEY-----",
                        0.95,
                    ),
                    Pattern("openai-key", r"\bsk-[A-Za-z0-9_-]{20,}\b", 0.9),
                    Pattern(
                        "assigned-secret",
                        r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b\s*[:=]\s*[\"']?[^\s\"']{8,}",
                        0.8,
                    ),
                ],
            )
        )
        return analyzer
    except Exception as exc:
        raise PseudonymizationError("privacy.pseudonymizationUnavailable") from exc


class RequestPseudonymizer:
    _entities = [
        "PERSON",
        "LOCATION",
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "IBAN_CODE",
        "CREDIT_CARD",
        "IP_ADDRESS",
        "SECRET",
    ]

    def __init__(self) -> None:
        self._nonce = secrets.token_hex(4).upper()
        self._markers: dict[tuple[str, str], str] = {}
        self._originals: dict[str, str] = {}

    @property
    def max_marker_length(self) -> int:
        return max((len(marker) for marker in self._originals), default=0)

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
            results = _analyzer().analyze(
                text=text,
                language="de",
                entities=self._entities,
                score_threshold=0.45,
            )
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
                marker = f"[[MLP_{self._nonce}_{len(self._markers) + 1:04d}]]"
                self._markers[key] = marker
                self._originals[marker] = original
            protected = protected[: result.start] + marker + protected[result.end :]
        return protected

    def restore_text(self, text: str) -> str:
        restored = text
        for marker, original in self._originals.items():
            restored = restored.replace(marker, original)
        return restored


class StreamingPseudonymRestorer:
    _prefix = "[[MLP_"

    def __init__(self, pseudonymizer: RequestPseudonymizer) -> None:
        self._pseudonymizer = pseudonymizer
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
                    restored.append(self._buffer[0])
                    self._buffer = self._buffer[1:]
                    continue
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
