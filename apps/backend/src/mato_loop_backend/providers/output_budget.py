"""Provider-neutral output allocation using estimates, not tokenizer guarantees."""

from __future__ import annotations

import json
import math
from typing import Any

from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.settings.models import ModelConfig

IMAGE_INPUT_RESERVE_TOKENS = 2_048
UNKNOWN_INPUT_RESERVE_TOKENS = 8_192
ESTIMATION_POLICY = "serialized-characters-media-reserves-v1"


def _validate_tokens(value: int, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ProviderError(
            "Invalid output budget token count", code="provider.configurationInvalid"
        )
    return value


def output_token_limit(model: ModelConfig, requested: int | None = None) -> int | None:
    """Return the minimum present manual, authoritative provider, and helper cap."""
    limits = [
        _validate_tokens(value, minimum=1)
        for value in (model.max_output_tokens, model.discovered_max_output_tokens, requested)
        if value is not None
    ]
    return min(limits) if limits else None


def generation_reserve(model: ModelConfig) -> int:
    """Reserve a useful generation turn, without treating it as the output ceiling."""
    cap = output_token_limit(model)
    return min(2_048, cap) if cap is not None else 2_048


def guarded_input_tokens(estimated_input_tokens: int) -> int:
    """Apply the shared ten-percent estimation slack exactly once (rounded up)."""
    return (_validate_tokens(estimated_input_tokens) * 11 + 9) // 10


def raw_input_budget(context_capacity: int) -> int:
    """Largest raw estimate whose guarded size fits a nonnegative input capacity.

    Subtract fixed safety and output reserves before calling. The returned budget
    is for raw estimates; only the provider allocation applies the actual slack.
    """
    return _validate_tokens(context_capacity) * 10 // 11


def allocate_output_tokens(
    model: ModelConfig,
    estimated_input_tokens: int,
    *,
    requested: int | None = None,
    reserved_output_tokens: int | None = None,
    safety_reserve_tokens: int = 4_096,
) -> int:
    """Allocate C - S - guarded I, capped only by present M/H/Q limits.

    Input estimates must not already include proportional slack or fixed safety.
    A requested reserve must fit both the remaining context and output caps.
    """
    cap = output_token_limit(model, requested)
    reserve = (
        min(2_048, cap) if cap is not None else 2_048
    ) if reserved_output_tokens is None else _validate_tokens(reserved_output_tokens)
    available = (
        _validate_tokens(model.context_window_tokens, minimum=1)
        - _validate_tokens(safety_reserve_tokens)
        - guarded_input_tokens(estimated_input_tokens)
    )
    allocated = min(available, cap) if cap is not None else available
    if allocated <= 0 or allocated < reserve:
        raise ProviderError(
            "Insufficient context for the required output reserve",
            code="agent.contextWindowExceeded",
        )
    return allocated


def estimate_request_input_tokens(
    body: dict[str, Any], characters_per_token: float = 4.0
) -> int:
    """Estimate serialized input framing, instructions, tools, and continuation.

    Media payloads are not text. Images reserve 2048 tokens each; audio/video
    and opaque encrypted continuation reserve 8192 each. These are heuristics,
    not upper bounds: duration, tokenization, and server templates are unknown.
    Transport metadata and generation options are deliberately excluded.
    """
    if (
        isinstance(characters_per_token, bool)
        or not isinstance(characters_per_token, (int, float))
        or not math.isfinite(characters_per_token)
        or characters_per_token <= 0
    ):
        raise ProviderError(
            "Invalid input token estimation ratio", code="provider.configurationInvalid"
        )
    media_reserve = 0

    def without_payloads(value: Any) -> Any:
        nonlocal media_reserve
        if isinstance(value, list):
            return [without_payloads(item) for item in value]
        if not isinstance(value, dict):
            return value
        kind = value.get("type")
        if kind in ("image_url", "input_image", "input_audio", "video_url"):
            media_reserve += (
                IMAGE_INPUT_RESERVE_TOKENS
                if kind in ("image_url", "input_image")
                else UNKNOWN_INPUT_RESERVE_TOKENS
            )
            return {"type": kind}
        opaque_keys = {"encrypted_content"}
        if kind == "reasoning.encrypted":
            opaque_keys.add("data")
        if any(value.get(key) for key in opaque_keys):
            media_reserve += UNKNOWN_INPUT_RESERVE_TOKENS
        return {
            key: without_payloads(item)
            for key, item in value.items()
            if key not in opaque_keys
        }

    inputs = {
        key: value if key == "tools" else without_payloads(value)
        for key, value in body.items()
        if key in {"messages", "input", "instructions", "tools"}
    }
    text = json.dumps(inputs, ensure_ascii=False, separators=(",", ":"))
    return math.ceil(len(text) / characters_per_token) + media_reserve
