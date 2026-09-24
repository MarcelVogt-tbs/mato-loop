from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import run_support
from mato_loop_backend.agent.context import ContextManager
from mato_loop_backend.agent.memory_context import MemoryTurn
from mato_loop_backend.agent.projection import (
    ContextError,
    ContextProjection,
    SummaryResult,
    model_browser_observations,
)
from mato_loop_backend.agent.prompts import memory_context_prompt
from mato_loop_backend.events import run_events
from mato_loop_backend.media.attachments import AttachmentService
from mato_loop_backend.models import AgentRun, ChatSession, Message
from mato_loop_backend.providers.base import ProviderImageInput
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import AgentRuntimeSettings, AiSettings, InstanceLimits
from mato_loop_backend.settings.models import ModelConfig, TrustLevel


async def build_turn_context(
    runtime,
    db: AsyncSession,
    run: AgentRun,
    session: ChatSession,
    *,
    ai_settings: AiSettings,
    model: ModelConfig,
    trust_level: TrustLevel,
    settings: AgentRuntimeSettings,
    instance_limits: InstanceLimits,
    records: list[Message],
    system_prompt: str,
    runtime_instructions: str,
    schemas: list[dict[str, Any]],
    offered_tool_names: frozenset[str],
    memory_turn: MemoryTurn,
    memory_read_enabled: bool,
    request_options: dict[str, Any],
    live_tool_calls: dict[UUID, list[dict[str, Any]]],
    ephemeral_images: dict[tuple[UUID, int, str, int], ProviderImageInput],
    attachment_service: type[AttachmentService],
) -> tuple[ContextProjection, int, int]:
    async def summarize(source: str, output_tokens: int) -> SummaryResult:
        await run_support._publish_run_phase(run.id, "compacting_context")
        last_summary_publish_at = 0.0

        async def on_update(text: str, is_fallback: bool) -> None:
            nonlocal last_summary_publish_at
            now = time.monotonic()
            if text and not is_fallback and now - last_summary_publish_at < 0.1:
                return
            if text:
                last_summary_publish_at = now
            await run_events.publish(run.id, {
                "type": "context.summary.updated",
                "summary": {"content": text, "isFallback": is_fallback, "status": "streaming"},
            })

        return await runtime._summarize(
            source, output_tokens,
            ai_settings=ai_settings, active_model=model,
            runtime_settings=settings, on_update=on_update,
        )

    async def estimate_request(messages: list[dict[str, Any]]) -> int:
        messages, images = run_support._project_ephemeral_images(
            model_browser_observations(messages), ephemeral_images,
            model=model, target_trust_level=trust_level,
        )
        prepared = await ProviderService.prepare_request(
            messages=messages, image_inputs=images, **request_options
        )
        return prepared.estimated_input_tokens

    scope_payload = (
        {"total": memory_turn.scope.total, "pinned": memory_turn.scope.pinned}
        if memory_turn.scope is not None else None
    )
    projection = None
    memory_count = 0
    episode_count = 0
    for candidate_index, (candidate_text, candidate_memory_count, candidate_episode_count) in enumerate(memory_turn.candidates):
        memory_context = memory_context_prompt(
            memory_text=candidate_text,
            memory_read_enabled=memory_read_enabled,
            memory_read_scope=trust_level,
            memory_scope=scope_payload,
            injected_memory_count=candidate_memory_count,
            injected_episode_count=candidate_episode_count,
            memory_overview_prefetched=memory_turn.overview_prefetched,
            memory_overview_has_more=memory_turn.overview_has_more,
            offered_tool_names=offered_tool_names,
        )
        try:
            projection = await ContextManager.build(
                db,
                chat_session=session,
                records=records,
                system_prompt=system_prompt,
                runtime_instructions=runtime_instructions,
                memory_context=memory_context,
                tool_schemas=schemas,
                model=model,
                target_trust_level=trust_level,
                settings=settings,
                attachment_service=attachment_service(settings=instance_limits, run_id=run.id),
                summarize=summarize,
                estimate_request=estimate_request,
                live_tool_calls=live_tool_calls,
            )
            memory_count = candidate_memory_count
            episode_count = candidate_episode_count
            break
        except ContextError as exc:
            if exc.code != "agent.contextWindowExceeded" or candidate_index == len(memory_turn.candidates) - 1:
                raise
    if projection is None:
        raise ContextError("agent.contextWindowExceeded")
    if projection.compacted:
        summary = projection.context_summary
        assert summary is not None
        await run_events.publish(run.id, {
            "type": "context.compacted",
            "estimatedInputTokens": projection.estimated_input_tokens,
            "summary": {
                "id": str(summary.id),
                "coveredThroughMessageId": str(summary.covered_through_message_id),
                "content": summary.content,
                "isFallback": summary.is_fallback,
                "createdAt": summary.created_at.isoformat() if summary.created_at else None,
            },
        })
    return projection, memory_count, episode_count
