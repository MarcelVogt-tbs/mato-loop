from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from mato_loop_backend.agent import run_support
from mato_loop_backend.agent.run_support import (
    OPENSCAD_VISION_REQUEST_MAX_CHARACTERS,
    OPENSCAD_VISION_SYSTEM_PROMPT,
    OpenScadVisionResult,
    logger,
)
from mato_loop_backend.media.attachments import (
    AttachmentError,
    AttachmentService,
    model_attachments,
)
from mato_loop_backend.models import AgentRun, ChatSession, Message
from mato_loop_backend.providers.base import (
    MAX_PROVIDER_IMAGE_BYTES,
    select_provider_images,
)
from mato_loop_backend.settings import AiSettings, effective_model_trust
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelConfig,
    TrustLevel,
)


async def verify_preview(
    content: str,
    attachments: tuple,
    *,
    run: AgentRun,
    session: ChatSession,
    records: list[Message],
    projected_messages: list[dict[str, Any]],
    model: ModelConfig,
    provider: LlmProviderConfig,
    ai_settings: AiSettings,
    fallback_vision_model: ModelConfig | None,
    model_trust_level: TrustLevel,
    tool_trust_level: TrustLevel,
    analyze_image_batch: Callable[..., Awaitable[str]],
) -> str:
    try:
        render_payload = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        render_payload = None
    contact_sheet = next(
        (attachment.data for attachment in attachments if attachment.kind == "image"), None
    )
    if isinstance(render_payload, dict):
        verification = {"status": "unavailable"}
        latest_user = next(
            (item for item in reversed(records) if item.role == "user"), None
        )
        source_trust = (
            TrustLevel.PRIVATE
            if tool_trust_level == TrustLevel.PRIVATE
            or (latest_user is not None and latest_user.trust_level == TrustLevel.PRIVATE.value)
            else TrustLevel.PUBLIC
        )
        verification_target = None
        if model.supports_image_input:
            verification_target = (model, provider, model_trust_level)
        elif fallback_vision_model is not None and fallback_vision_model.supports_image_input:
            fallback_provider = run_support._provider_for_model(ai_settings, fallback_vision_model)
            if fallback_provider is not None:
                verification_target = (
                    fallback_vision_model,
                    fallback_provider,
                    effective_model_trust(fallback_provider, fallback_vision_model),
                )
        references = []
        reference_inputs = []
        if contact_sheet is not None and verification_target is not None:
            attachment_service = AttachmentService(run_id=run.id)
            image_bytes = len(contact_sheet)
            attachment_records = {
                str(attachment.id): (attachment, record.trust_level)
                for record in records
                for attachment in model_attachments(record)
            }
            selected_references = select_provider_images(
                projected_messages,
                target_trust_level=verification_target[2].value,
                allowed_categories=frozenset({"user_upload", "downloaded"}),
                max_images=7,
                max_bytes=max(0, MAX_PROVIDER_IMAGE_BYTES - image_bytes),
            )
            for selected_reference in selected_references:
                record = attachment_records.get(
                    str(selected_reference.attachment.get("id") or "")
                )
                if record is None:
                    continue
                attachment, attachment_trust = record
                try:
                    reference = await attachment_service.read_image(
                        attachment, session.id, current_only=True
                    )
                except AttachmentError:
                    continue
                references.append(reference)
                reference_inputs.append(
                    {
                        "image": len(references) + 1,
                        "filename": attachment.filename,
                        **(
                            {"pageNumber": attachment.metadata_json["pageNumber"]}
                            if isinstance((attachment.metadata_json or {}).get("pageNumber"), int)
                            else {}
                        ),
                    }
                )
                image_bytes += len(reference)
                if attachment_trust == TrustLevel.PRIVATE.value:
                    source_trust = TrustLevel.PRIVATE
            try:
                remaining_request_characters = OPENSCAD_VISION_REQUEST_MAX_CHARACTERS
                recent_requests = []
                for request_message in reversed(
                    [item for item in records if item.role == "user"][-3:]
                ):
                    if remaining_request_characters <= 0:
                        break
                    request_content = request_message.content[:remaining_request_characters]
                    recent_requests.append(
                        {"messageId": str(request_message.id), "content": request_content}
                    )
                    remaining_request_characters -= len(request_content)
                recent_requests.reverse()
                analysis = await analyze_image_batch(
                    [contact_sheet, *references],
                    [
                        json.dumps(
                            {
                                "imageOrder": {
                                    "contactSheet": 1,
                                    "referenceImages": list(range(2, len(references) + 2)),
                                },
                                "recentRequests": recent_requests,
                                "previewLayout": render_payload.get("previewLayout"),
                                "geometryMetadata": render_payload.get("geometryMetadata"),
                                "referenceImages": reference_inputs,
                            },
                            ensure_ascii=False,
                        )
                    ],
                    source_trust,
                    OPENSCAD_VISION_SYSTEM_PROMPT,
                    max_output_tokens=512,
                    candidates=(verification_target,),
                )
                parsed_analysis = OpenScadVisionResult.model_validate_json(analysis)
                verdict = parsed_analysis.verdict
                if verdict == "pass" and parsed_analysis.uncertainty:
                    verdict = "uncertain"
                verification = {
                    "status": "completed",
                    "scope": "visible_geometry",
                    "verdict": verdict,
                    "findings": parsed_analysis.findings,
                    "uncertainty": parsed_analysis.uncertainty,
                    "referenceImageCount": len(references),
                }
            except Exception:
                logger.warning(
                    "OpenSCAD visual verification for run %s failed", run.id, exc_info=True
                )
        render_payload["visualVerification"] = verification
    render_payload = render_payload if isinstance(render_payload, dict) else {}
    return json.dumps(
        {
            **{
                key: render_payload[key]
                for key in (
                    "output", "diagnostics", "diagnosticsTruncated", "geometryMetadata",
                    "previewLayout", "visualVerification",
                )
                if key in render_payload
            },
            "previewInternal": True,
            "note": "Preview files are internal and were not saved. Use the SCAD source and verification diagnostics.",
        },
        ensure_ascii=False,
    )
