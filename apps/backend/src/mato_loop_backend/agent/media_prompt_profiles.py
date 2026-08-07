from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from mato_loop_backend.agent.tools import EffectiveToolSet
from mato_loop_backend.providers.codex_oauth import CODEX_IMAGE_MODEL_ID
from mato_loop_backend.providers.media import resolve_media_target
from mato_loop_backend.settings import AiSettings
from mato_loop_backend.settings.models import ModelCapability

GPT_IMAGE_2_GENERATE_INSTRUCTIONS = (
    "GPT Image 2 generation prompt contract: Write prompt in clear English unless the user "
    "explicitly provides an exact prompt in another language. Copy exact prompts verbatim without "
    "translation or expansion. Preserve quoted visible text and proper names exactly. Otherwise "
    "translate the intent faithfully into a complete standalone visual brief with relevant subject, "
    "setting, composition, viewpoint, lighting, color, style, and visible-text details. Do not add "
    "unrelated subjects, text, branding, or story elements."
)
GPT_IMAGE_2_EDIT_INSTRUCTIONS = (
    "GPT Image 2 editing prompt contract: Write prompt in clear English unless the user explicitly "
    "provides an exact prompt in another language. Copy exact prompts verbatim without translation "
    "or expansion, and preserve quoted visible text and proper names exactly. Refer to inputs as "
    "Image 1, Image 2, and so on in attachment order and state each input's role. Describe only the "
    "requested changes. Preserve all other content, especially identity, geometry, proportions, "
    "perspective, composition, and crop, unless the user explicitly changes it."
)
Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS = (
    "Z-Image Turbo generation prompt contract: Write prompt as a fluent, detailed natural-language "
    "scene description in English, not a keyword or tag list, unless the user explicitly provides "
    "an exact prompt in another language. Copy exact prompts verbatim without translation or "
    "expansion. Preserve quoted visible text and proper names exactly. Otherwise include only "
    "relevant subject appearance and action, environment, spatial relationships and counts, "
    "composition, viewpoint, lighting, color, style, and visible-text details. Do not add unrelated "
    "elements, negative-prompt syntax, or generation parameters."
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
