from dataclasses import replace
from uuid import uuid4

import pytest
from test_required_web_search_runtime import _runtime_setup

from mato_loop_backend.agent.context import estimate_tokens, general_system_prompt
from mato_loop_backend.agent.model_prompt_profiles import build_model_instructions
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.models import ChatSession, WebSearchPolicy
from mato_loop_backend.prompt_eval import CASES, SOURCE_FILES, build_case
from mato_loop_backend.providers.base import ProviderResult
from mato_loop_backend.settings import SettingsStore
from mato_loop_backend.settings.models import (
    BUILD_AGENT_PROFILE_ID,
    AgentProfile,
    AgentProfilesSettings,
    build_agent_profile,
    default_agent_profile,
    plan_agent_profile,
)


@pytest.mark.parametrize("model_id", [
    "Qwen3.8-27B", "Qwen/Qwen3.8-27B", "qwen3.8_27b",
    "Qwen3.8-27B-GGUF-UD-Q4_K_XL", "Qwen3.8-27B:latest",
])
def test_base_model_and_named_variants_share_one_instruction(model_id):
    instruction = build_model_instructions("Qwen3.8-27B")
    assert instruction and build_model_instructions(model_id) == instruction
    assert "Do not draft complete files" in instruction
    assert "For a follow-up explanation" not in instruction
    assert estimate_tokens(instruction, 4.0) < 160


@pytest.mark.parametrize("model_id", [
    "", "gpt-6-astra", "Qwen3.6-27B", "Qwen3.8-270B",
    "Qwen3.8-27Billion", "custom-Qwen3.8-27B", "Qwen3.8-27B/other-model",
])
def test_other_models_keep_exactly_the_same_prompt(model_id):
    session = ChatSession(id=uuid4())
    options = dict(agent_profile=build_agent_profile(), include_local_time=False)
    assert build_model_instructions(model_id) == ""
    assert general_system_prompt(session, model_id=model_id, **options) == general_system_prompt(session, **options)


@pytest.mark.parametrize("profile,delegated,expected", [
    (build_agent_profile(), False, True), (build_agent_profile(), True, False),
    (plan_agent_profile(), False, False), (default_agent_profile(), False, False),
    (AgentProfile(name="Build", instructions="Custom profile."), False, False),
    (None, False, False),
])
def test_prompt_scope_and_model_switch(profile, delegated, expected):
    session = ChatSession(id=uuid4())
    options = dict(agent_profile=profile, delegated=delegated, include_local_time=False)
    original = general_system_prompt(session, **options)
    instruction = build_model_instructions("Qwen3.8-27B")
    prompt = general_system_prompt(session, model_id="Qwen3.8-27B", **options)
    assert prompt.count(instruction) == int(expected)
    assert general_system_prompt(session, model_id="gpt-6-astra", **options) == original
    if not expected:
        assert prompt == original
    else:
        assert "When the user requests file changes and tools are available" in instruction
        assert "within runtime limits" in instruction
        assert 0 < estimate_tokens(prompt, 4.0) - estimate_tokens(original, 4.0) < 180
        exhausted = general_system_prompt(session, model_id="Qwen3.8-27B", tool_call_limit=25,
                                          tool_budget_exhausted=True, **options)
        assert "No tools are available" in exhausted
        assert exhausted.index(instruction) < exhausted.index("Tool budget exhausted")


@pytest.mark.parametrize("model_id,expected", [("Qwen3.8-27B-GGUF-UD-Q4_K_XL", True), ("other-model", False)])
async def test_runtime_uses_resolved_model_for_build_prompt(monkeypatch, model_id, expected):
    db, run, requests, projections, _events = await _runtime_setup(
        monkeypatch, [ProviderResult(content="Finished", finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE, model_id=model_id,
    )
    run.agent_profile_id = BUILD_AGENT_PROFILE_ID
    original_load = SettingsStore.load

    async def load_settings(db, settings_type, **kwargs):
        if settings_type is AgentProfilesSettings:
            return AgentProfilesSettings()
        return await original_load(db, settings_type, **kwargs)

    monkeypatch.setattr(SettingsStore, "load", load_settings)
    await AgentRuntime._execute_loop(db, run)
    instruction = build_model_instructions("Qwen3.8-27B")
    assert (instruction in requests[0]["messages"][0]["content"]) is expected
    assert requests[0]["messages"][0]["content"] == projections[0]["system_prompt"]


def test_evaluator_includes_selected_model_in_shared_prompt_and_fingerprints():
    case = replace(next(case for case in CASES if case.kind == "general"),
                   system_options={"agent_profile": build_agent_profile()})
    qwen, qwen_tools = build_case(case, model_id="Qwen3.8-27B")
    other, other_tools = build_case(case, model_id="other-model")
    instruction = build_model_instructions("Qwen3.8-27B")
    assert instruction in qwen[0]["content"]
    assert instruction not in other[0]["content"]
    assert qwen_tools == other_tools
    assert "model_id" not in case.system_options
    assert "src/mato_loop_backend/agent/model_prompt_profiles.py" in SOURCE_FILES
