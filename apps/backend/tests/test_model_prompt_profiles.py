from uuid import uuid4

import pytest

from mato_loop_backend.agent.model_prompt_profiles import build_model_instructions
from mato_loop_backend.agent.prompts import general_system_prompt
from mato_loop_backend.models import ChatSession
from mato_loop_backend.settings import (
    AgentProfileOverrides,
    CustomAgentProfile,
    build_profile,
    general_profile,
    plan_profile,
    resolve_agent_profile,
)


@pytest.mark.parametrize(
    "model_id",
    [
        "Qwen3.8-27B",
        "Qwen/Qwen3.8-27B",
        "qwen3.8_27b",
        "Qwen3.8-27B-GGUF-UD-Q4_K_XL",
        "Qwen3.8-27B:latest",
    ],
)
def test_base_model_and_named_variants_share_one_instruction(model_id):
    instruction = build_model_instructions("Qwen3.8-27B")

    assert instruction and build_model_instructions(model_id) == instruction


@pytest.mark.parametrize(
    "profile,delegated,expected",
    [
        (build_profile(), False, True),
        (build_profile(), True, False),
        (plan_profile(), False, False),
        (general_profile(), False, False),
        (None, False, False),
    ],
)
def test_prompt_scope_and_model_switch(profile, delegated, expected):
    prompt = general_system_prompt(
        ChatSession(id=uuid4()),
        model_id="Qwen3.8-27B",
        agent_profile=profile,
        delegated=delegated,
        include_local_time=False,
    )

    assert (build_model_instructions("Qwen3.8-27B") in prompt) is expected


def test_custom_profile_inherits_build_model_instructions():
    profile = resolve_agent_profile(
        CustomAgentProfile(
            name="Custom Build",
            description="",
            base_profile_id=build_profile().id,
            overrides=AgentProfileOverrides(),
        ),
        build_profile(),
        None,
    )

    prompt = general_system_prompt(
        ChatSession(id=uuid4()),
        model_id="Qwen3.8-27B",
        agent_profile=profile,
        include_local_time=False,
    )

    assert build_model_instructions("Qwen3.8-27B") in prompt
