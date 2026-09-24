from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import mato_loop_backend.agent.run_support as agent_run_support
from mato_loop_backend.agent.projection import (
    SummaryResult,
    compaction_messages,
    valid_compaction_summary,
)
from mato_loop_backend.agent.run_support import logger
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.providers.output_budget import output_token_limit
from mato_loop_backend.providers.reasoning import reasoning_profile
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import AgentRuntimeSettings, AiSettings
from mato_loop_backend.settings.models import (
    ModelAvailability,
    ModelCapability,
    ModelConfig,
    ReasoningEffort,
)


async def summarize(
    cls,
    source: str,
    output_tokens: int,
    *,
    ai_settings: AiSettings,
    active_model: ModelConfig,
    runtime_settings: AgentRuntimeSettings,
    on_update: Callable[[str, bool], Awaitable[None]] | None = None,
) -> SummaryResult:
    helper = next(
        (
            item
            for item in ai_settings.models
            if item.id == ai_settings.compaction_model_id
            and item.enabled
            and item.availability != ModelAvailability.MISSING
            and ModelCapability.CHAT in item.capabilities
        ),
        None,
    )
    candidates = [item for item in (helper, active_model) if item is not None]
    unique_candidates = list({item.id: item for item in candidates}.values())
    attempts = 0
    messages = compaction_messages(source)
    try:
        # Share one monotonic deadline across provider retries and model fallback.
        # Isolate credential transactions; close outside the deadline before fallback.
        async with (
            AsyncSessionFactory() as db,
            asyncio.timeout(runtime_settings.request_timeout_seconds),
        ):
            for candidate in unique_candidates:
                if attempts >= runtime_settings.max_compaction_requests:
                    break
                candidate_provider = agent_run_support._provider_for_model(ai_settings, candidate)
                if candidate_provider is None:
                    continue
                attempts += 1
                effective_output_tokens = output_token_limit(candidate, output_tokens)
                assert effective_output_tokens is not None
                streamed_parts: list[str] = []

                async def on_delta(text: str, parts: list[str] = streamed_parts) -> None:
                    parts.append(text)
                    if on_update is not None:
                        await on_update("".join(parts), False)

                async def on_retry(*_args: Any, parts: list[str] = streamed_parts) -> None:
                    parts.clear()
                    if on_update is not None:
                        await on_update("", False)

                try:
                    if on_update is not None:
                        await on_update("", False)
                    profile = reasoning_profile(
                        candidate.model_id, candidate.discovery_metadata
                    )
                    result = await ProviderService.complete(
                        db,
                        provider=candidate_provider,
                        model=candidate,
                        messages=messages,
                        tools=[],
                        timeout=runtime_settings.request_timeout_seconds,
                        max_output_tokens=effective_output_tokens,
                        reserved_output_tokens=effective_output_tokens,
                        safety_reserve_tokens=runtime_settings.safety_reserve_tokens,
                        on_delta=on_delta,
                        on_retry=on_retry,
                        reasoning_effort=(
                            ReasoningEffort.NONE
                            if profile is not None
                            and ReasoningEffort.NONE in profile.options
                            else None
                        ),
                    )
                except Exception:
                    logger.warning("Context compaction with model %s failed", candidate.id, exc_info=True)
                    await db.rollback()
                    continue
                content = result.content.strip()
                if valid_compaction_summary(content):
                    if on_update is not None:
                        await on_update(content, False)
                    return SummaryResult(content, candidate.id)
                logger.warning(
                    "Context compaction with model %s returned invalid visible output "
                    "(content=%s, reasoning=%s, finish_reason=%s)",
                    candidate.id,
                    bool(content),
                    bool(getattr(result, "reasoning", None)),
                    getattr(result, "finish_reason", None),
                )
    except TimeoutError:
        logger.warning("Context compaction deadline expired; using deterministic summary")
    source_excerpt = source
    if len(source) > 6_000:
        source_excerpt = (
            source[:3_000]
            + "\n[Middle shortened; use session_read for original messages.]\n"
            + source[-3_000:]
        )
    fallback = (
        "Goal\nContinue the conversation using the retained recent messages.\n\n"
        "Facts and earlier context\n"
        + source_excerpt
    )
    if on_update is not None:
        await on_update(fallback, True)
    return SummaryResult(fallback, None, is_fallback=True)
