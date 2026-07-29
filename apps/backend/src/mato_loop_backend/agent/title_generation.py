from __future__ import annotations

import re

from mato_loop_backend.providers.base import ProviderResult

TITLE_MAX_CHARACTERS = 80
TITLE_INITIAL_OUTPUT_TOKENS = 2_048
TITLE_MAX_OUTPUT_TOKENS = 4_096
TITLE_REPAIR_RESERVE_TOKENS = 512
TITLE_SYSTEM_PROMPT = (
    "Create a short title for this conversation. Reply in the language "
    "of the user message with exactly one line and at most 80 characters. "
    "Return only the title without quotation marks, explanation, or reasoning."
)


def initial_session_title(content: str) -> str:
    title = re.sub(r"\s+", " ", content).strip()
    return title[:TITLE_MAX_CHARACTERS].rstrip() or "Neue Session"


def generated_session_title(content: str) -> str | None:
    cleaned = re.sub(
        r"<think(?:ing)?>[\s\S]*?</think(?:ing)?>\s*",
        "",
        content,
        flags=re.IGNORECASE,
    )
    title = next(
        (line.strip().strip("\"'") for line in cleaned.splitlines() if line.strip()),
        "",
    )
    title = re.sub(r"\s+", " ", title).strip()
    return title[:TITLE_MAX_CHARACTERS].rstrip() or None


def initial_title_output_budget(model_limit: int) -> int:
    return min(model_limit, TITLE_INITIAL_OUTPUT_TOKENS)


def repair_title_output_budget(
    current_budget: int,
    model_limit: int,
    result: ProviderResult,
) -> int:
    hard_limit = min(model_limit, TITLE_MAX_OUTPUT_TOKENS)
    observed_tokens = max(
        result.usage.get("output_tokens", 0),
        result.usage.get("reasoning_tokens", 0),
    )
    reasoning_only = result.reasoning is not None or result.usage.get(
        "reasoning_tokens", 0
    ) > 0
    if reasoning_only or result.finish_reason == "length":
        return hard_limit
    return min(
        hard_limit,
        max(current_budget * 2, observed_tokens + TITLE_REPAIR_RESERVE_TOKENS),
    )
