from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from mato_loop_backend.providers.codex_oauth import CODEX_IMAGE_MODEL_ID
from mato_loop_backend.providers.media import resolve_media_target
from mato_loop_backend.settings import AiSettings
from mato_loop_backend.settings.models import ModelCapability
from mato_loop_backend.tools.registry import EffectiveToolSet

GPT_IMAGE_2_GENERATE_INSTRUCTIONS = (
    "GPT Image 2 generation prompt contract: Write prompt in clear English unless the user "
    "explicitly provides an exact prompt in another language. Copy exact prompts verbatim without "
    "translation or expansion. Preserve quoted visible text and proper names exactly. Otherwise write "
    "a clean standalone visual brief in this order when relevant: intended output and use; scene and "
    "background; subject and action; key details; composition and viewpoint; lighting, color, and "
    "style; exact visible text; constraints. Use short labeled lines for complex requests. Add only "
    "practical detail that supports the user's intent; do not invent subjects, branding, slogans, "
    "story elements, or arbitrary placement."
)
GPT_IMAGE_2_EDIT_INSTRUCTIONS = (
    "GPT Image 2 editing prompt contract: Write prompt in clear English unless the user explicitly "
    "provides an exact prompt in another language. Copy exact prompts verbatim without translation "
    "or expansion, and preserve quoted visible text and proper names exactly. For every other edit, "
    "put the base edit target first in attachment_ids and write a short labeled contract with "
    "Operation, Inputs, Requested change, Preserve, and Avoid. For a localized edit, Operation must "
    "begin: 'EDIT Image 1. Image 1 is the base canvas and must remain the same scene.' Name every "
    "input by attachment order and role. Treat additional images only as their stated references; do "
    "not copy their background, framing, or unrelated content. Describe the requested delta and its "
    "object or region before anything else; do not redescribe the whole scene. Preserve the original "
    "canvas, aspect ratio, crop, framing, camera position and angle, lens perspective, horizon, depth "
    "of field, subject scale, object positions, lighting, color treatment, identity, pose, expression, "
    "existing text, and unrelated objects unless explicitly changed. Do not zoom, pan, rotate, "
    "reframe, extend, recrop, redesign, restyle, add an alternate scene, or add unrelated people, "
    "props, text, logos, or watermarks. Allow only minimal nearby shadow, reflection, and occlusion "
    "changes needed to integrate the requested edit. Use only applicable invariants and never "
    "contradict an explicitly requested change. For an intentional global transformation, state that "
    "change explicitly and preserve all unrequested structure and identity."
)
Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS = (
    "Z-Image Turbo: write a fluent English scene description, not tags. Copy an explicit prompt "
    "verbatim; preserve quoted text and proper names. Do not add negative-prompt syntax or generation "
    "parameters."
)

_TOOL_CAPABILITIES = {
    "generate_image": ModelCapability.IMAGE_GENERATION,
    "edit_image": ModelCapability.IMAGE_EDITING,
}
_PROFILES = {
    ("openai-codex", CODEX_IMAGE_MODEL_ID, "generate_image"): (
        GPT_IMAGE_2_GENERATE_INSTRUCTIONS
    ),
    ("openai-codex", CODEX_IMAGE_MODEL_ID, "edit_image"): (
        GPT_IMAGE_2_EDIT_INSTRUCTIONS
    ),
    ("lemonade", "Z-Image-Turbo", "generate_image"): (
        Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS
    ),
}


def with_media_prompt_profiles(
    toolset: EffectiveToolSet,
    settings: AiSettings,
    *,
    active_model_id: UUID,
) -> EffectiveToolSet:
    tools = dict(toolset.tools)
    instructions = []
    for tool_name, capability in _TOOL_CAPABILITIES.items():
        tool = tools.get(tool_name)
        if tool is None:
            continue
        target = resolve_media_target(
            settings, capability, active_model_id=active_model_id
        )
        if target is None:
            continue
        provider, model = target
        tools[tool_name] = replace(
            tool,
            selected_options={
                **tool.selected_options,
                "media_provider_id": str(provider.id),
                "media_model_id": str(model.id),
                "media_adapter_type": provider.adapter_type,
                "media_provider_model_id": model.model_id,
            },
        )
        profile = _PROFILES.get(
            (provider.adapter_type, model.model_id, tool_name)
        )
        if profile is not None:
            instructions.append(profile)
    return EffectiveToolSet(
        toolset.registry,
        tools,
        toolset.prompt_fragments + tuple(instructions),
    )
