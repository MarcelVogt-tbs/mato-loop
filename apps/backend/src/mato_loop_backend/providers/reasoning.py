from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_OPEN = re.compile(r"^\s*<(?:think(?:ing)?|thought|mm:think)(?:\s+[^>]*)?>", re.I)
_TAG_CLOSE = re.compile(r"</(?:think(?:ing)?|thought|mm:think)>", re.I)
_CHANNEL_OPEN = re.compile(r"^\s*<\|channel>thought\s*", re.I)
_CHANNEL_CLOSE = re.compile(r"<channel\|>", re.I)
_CHANNEL_RESPONSE = re.compile(r"^\s*<\|channel>response\s*", re.I)
_CHANNEL_RESPONSE_PREFIX = "<|channel>response"
_OPEN_PREFIXES = ("<think", "<thinking", "<thought", "<mm:think", "<|channel>thought")
_CLOSE_PREFIXES = ("</think>", "</thinking>", "</thought>", "</mm:think>", "<channel|>")


def _without_partial_marker(value: str) -> str:
    marker = value.rfind("<")
    if marker < 0:
        return value
    suffix = value[marker:].lower()
    if any(item.startswith(suffix) for item in _CLOSE_PREFIXES):
        return value[:marker]
    return value


def split_tagged_reasoning(value: str, *, final: bool = False) -> tuple[str, str]:
    stripped = value.lstrip()
    open_match = _TAG_OPEN.match(value)
    channel_match = _CHANNEL_OPEN.match(value)
    if open_match is None and channel_match is None:
        lowered = stripped.lower()
        if not final and lowered.startswith("<") and any(
            item.startswith(lowered) or lowered.startswith(item) for item in _OPEN_PREFIXES
        ):
            return "", ""
        return value, ""

    if open_match is not None:
        remainder = value[open_match.end() :]
        close_match = _TAG_CLOSE.search(remainder)
    else:
        remainder = value[channel_match.end() :]
        close_match = _CHANNEL_CLOSE.search(remainder)

    if close_match is None:
        reasoning = remainder if final else _without_partial_marker(remainder)
        return "", reasoning.strip()

    reasoning = remainder[: close_match.start()].strip()
    answer = remainder[close_match.end() :]
    stripped_answer = answer.lstrip().lower()
    if (
        not final
        and stripped_answer.startswith("<")
        and _CHANNEL_RESPONSE_PREFIX.startswith(stripped_answer)
    ):
        return "", reasoning
    answer = _CHANNEL_RESPONSE.sub("", answer, count=1)
    return answer.lstrip(), reasoning


@dataclass(slots=True)
class TaggedReasoningStream:
    raw: str = ""
    emitted_answer: int = 0
    emitted_reasoning: int = 0

    def add(self, delta: str) -> tuple[str, str]:
        self.raw += delta
        answer, reasoning = split_tagged_reasoning(self.raw)
        answer_delta = answer[self.emitted_answer :]
        reasoning_delta = reasoning[self.emitted_reasoning :]
        self.emitted_answer = len(answer)
        self.emitted_reasoning = len(reasoning)
        return answer_delta, reasoning_delta

    def finish(self) -> tuple[str, str]:
        return split_tagged_reasoning(self.raw, final=True)
