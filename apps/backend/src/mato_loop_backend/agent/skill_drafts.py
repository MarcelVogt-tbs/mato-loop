from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import run_support
from mato_loop_backend.agent.run_support import (
    _OUTPUT_LIMIT_REASONS,
    SKILL_CREATION_OUTPUT_TOKENS,
    SKILL_CREATION_SYSTEM_PROMPT,
    logger,
)
from mato_loop_backend.models import AgentRun, Message
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings import (
    AgentRuntimeSettings,
    AiSettings,
    SkillDefinition,
    effective_model_pseudonymization,
    effective_model_trust,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig
from mato_loop_backend.skills import SkillDraft, SkillEditPatch, SkillError
from mato_loop_backend.tools.arguments import QuestionArguments


async def generate_skill_draft(
    brief: str,
    name_hint: str | None,
    current_skill: SkillDefinition | None,
    *,
    db: AsyncSession,
    run: AgentRun,
    ai_settings: AiSettings,
    runtime_settings: AgentRuntimeSettings,
    schemas: list[dict[str, Any]],
    records: list[Message],
    model: ModelConfig,
    provider: LlmProviderConfig,
    question: Callable[[QuestionArguments], Awaitable[dict[str, Any]]],
    tool_phase: dict[str, Any],
    model_request_number: Callable[[], int],
    count_model_request: Callable[[], None],
) -> SkillDraft:
    repair_available = True
    current_request = next(
        (item.content for item in reversed(records) if item.role == "user"), ""
    )
    payload = {
        "currentRequest": current_request,
        "brief": brief,
        "nameHint": name_hint,
        "effectiveTools": schemas,
    }
    if current_skill is not None:
        payload["currentSkill"] = current_skill.model_dump(
            mode="json", include={"name", "description", "instructions"}
        )
    user_payload = json.dumps(payload, ensure_ascii=False)
    editing = current_skill is not None
    output_tool = run_support._skill_draft_output_tool(editing=editing)
    output_tool_name = output_tool["name"]

    async def request_draft(
        target_model: ModelConfig,
        target_provider: LlmProviderConfig,
    ) -> SkillDraft | None:
        nonlocal repair_available
        messages = [
            {"role": "system", "content": SKILL_CREATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]
        while True:
            await run_support._publish_run_phase(
                run.id, "processing_tool", **tool_phase, modelRequest=model_request_number()
            )
            count_model_request()
            try:
                result = await ProviderService.complete(
                    db,
                    provider=target_provider,
                    model=target_model,
                    messages=list(messages),
                    tools=[output_tool],
                    tool_choice="required",
                    timeout=runtime_settings.request_timeout_seconds,
                    max_output_tokens=SKILL_CREATION_OUTPUT_TOKENS,
                    safety_reserve_tokens=runtime_settings.safety_reserve_tokens,
                    target_trust_level=effective_model_trust(
                        target_provider, target_model
                    ),
                    pseudonymization_enabled=effective_model_pseudonymization(
                        target_provider, target_model
                    ),
                )
            except Exception:
                logger.warning("Skill draft model %s failed", target_model.id, exc_info=True)
                return None
            try:
                if (
                    result.finish_reason in _OUTPUT_LIMIT_REASONS
                    or len(result.tool_calls) != 1
                    or result.tool_calls[0].name != output_tool_name
                ):
                    raise ValueError("invalid skill draft tool response")
                if current_skill is None:
                    return SkillDraft.model_validate(result.tool_calls[0].arguments)
                patch = SkillEditPatch.model_validate(result.tool_calls[0].arguments)
                return SkillDraft.model_validate(
                    {
                        **current_skill.model_dump(
                            include={"name", "description", "instructions"}
                        ),
                        **patch.model_dump(exclude_none=True),
                    }
                )
            except (ValidationError, ValueError) as exc:
                attempt = 1 if repair_available else 2
                if isinstance(exc, ValidationError):
                    issues = []
                    for error in exc.errors(include_url=False, include_input=False):
                        field = next(
                            (
                                str(item)
                                for item in error.get("loc", ())
                                if item in {"name", "description", "instructions"}
                            ),
                            "arguments",
                        )
                        context = error.get("ctx") or {}
                        bounds = ", ".join(
                            f"{key}={context[key]}"
                            for key in ("min_length", "max_length")
                            if isinstance(context.get(key), int)
                        )
                        issue = f"{field}: {error.get('type', 'invalid')}"
                        issues.append(f"{issue} ({bounds})" if bounds else issue)
                    failure = "; ".join(issues) or "invalid arguments"
                else:
                    failure = "expected exactly one matching output tool call"
                logger.warning(
                    "Skill draft output invalid: model_id=%s editing=%s "
                    "attempt=%s tool_call_count=%s output_tool_call_count=%s "
                    "finish_reason=%s visible_characters=%s reasoning=%s "
                    "output_tokens=%s validation=%s",
                    target_model.id,
                    editing,
                    attempt,
                    len(result.tool_calls),
                    sum(call.name == output_tool_name for call in result.tool_calls),
                    result.finish_reason,
                    len(result.content.strip()),
                    bool(result.reasoning),
                    result.usage.get("output_tokens"),
                    failure,
                )
                if not repair_available:
                    return None
                repair_available = False
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            f"The output was invalid: {failure}. Return exactly one "
                            f"{output_tool_name} tool call matching the schema. "
                            "Limits: name 1-64 characters, description 1-240 "
                            "characters, instructions 1-8000 characters."
                        ),
                    }
                )

    configured_model_id = ai_settings.skill_creation_model_id
    target_model = (
        run_support._model_for_id(ai_settings, configured_model_id)
        if configured_model_id is not None else model
    )
    target_provider = (
        run_support._provider_for_model(ai_settings, target_model)
        if target_model is not None else None
    )
    if target_model is not None and target_provider is not None and target_model.supports_tools:
        if draft := await request_draft(target_model, target_provider):
            return draft
    if configured_model_id is None or configured_model_id == model.id:
        raise SkillError("skill.creationModelFailed")
    answers = await question(
        QuestionArguments.model_validate(
            {
                "questions": [
                    {
                        "type": "single_choice",
                        "id": "skillModelFallback",
                        "header": "Skill-Modell",
                        "question": (
                            "Das konfigurierte Modell konnte keinen Skill-Entwurf "
                            "erzeugen. Soll das Sessionmodell verwendet werden?"
                        ),
                        "options": [
                            {
                                "label": "Sessionmodell verwenden",
                                "description": "Erzeugt den Entwurf mit dem aktuellen Sessionmodell.",
                            },
                            {"label": "Abbrechen", "description": "Erstellt keinen Skill."},
                        ],
                        "allow_custom": False,
                    }
                ]
            }
        )
    )
    if not isinstance(answers, dict) or answers.get("skillModelFallback") != ["Sessionmodell verwenden"]:
        raise SkillError("skill.creationModelFailed")
    if draft := await request_draft(model, provider):
        return draft
    raise SkillError("skill.creationModelFailed")
