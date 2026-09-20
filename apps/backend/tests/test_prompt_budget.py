from datetime import UTC, datetime

from mato_loop_backend.agent.context import (
    estimate_tokens,
    general_system_prompt,
    memory_context_prompt,
)
from mato_loop_backend.agent.media_prompt_profiles import Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS
from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.integrations.caldav import CalDavConfig
from mato_loop_backend.models import ChatSession
from mato_loop_backend.settings import (
    DefaultCalendarSettings,
    DefaultTaskListSettings,
    IntegrationInstance,
    IntegrationSettings,
    SkillDefinition,
    ToolExecutionPolicy,
    ToolSettings,
)


def test_full_initial_tool_contract_stays_within_conservative_budget() -> None:
    account = IntegrationInstance(
        name="Calendar",
        integration_type="calendar",
        adapter_type="caldav",
        config=CalDavConfig(
            base_url="https://calendar.example.com",
            username="user",
        ).model_dump(mode="json"),
    )
    integrations = IntegrationSettings(instances=[account])
    settings = ToolSettings(
        default_calendar=DefaultCalendarSettings(
            integration_id=account.id,
            calendar_key=f"calendar:{'a' * 32}",
        ),
        default_task_list=DefaultTaskListSettings(
            integration_id=account.id,
            task_list_key=f"tasklist:{'a' * 32}",
        ),
        generate_image={"policy": ToolExecutionPolicy.AUTO},
        transcribe_audio={"policy": ToolExecutionPolicy.AUTO},
        analyze_video={"policy": ToolExecutionPolicy.ASK},
        public_permissions={
            "policies": {
                "media.generate.image": "auto",
                "media.transcribe.audio": "auto",
            }
        },
    )
    capabilities = {
        "media.generate.image",
        "media.transcribe.audio",
        "media.analyze.video",
        "settings.agent_identity.update",
    }
    effective = tool_registry.effective(
        integrations,
        webfetch_enabled=True,
        memory_enabled=True,
        memory_write_enabled=True,
        tool_capabilities=capabilities,
        tool_settings=settings,
    )
    inactive = tool_registry.inactive_capability_groups(
        integrations,
        frozenset(effective.tools),
        webfetch_enabled=True,
        web_search_enabled=True,
        memory_enabled=True,
        memory_write_enabled=True,
        tool_capabilities=capabilities,
        tool_settings=settings,
    )
    prompt = general_system_prompt(
        ChatSession(
            title="Test",
            memory_mode="full",
            web_search_policy="available",
        ),
        memory_context_enabled=True,
        current_datetime=datetime(2026, 8, 7, 17, 49, tzinfo=UTC),
        research_tool_call_limit=8,
        offered_tool_names=frozenset(effective.tools),
        tool_instructions=(
            *effective.prompt_fragments,
            Z_IMAGE_TURBO_GENERATE_INSTRUCTIONS,
        ),
        inactive_capability_groups=inactive,
        skill_catalog=tuple(
            SkillDefinition(
                name=f"{index:02d}-" + "n" * 61,
                description="d" * 240,
                instructions="inactive instructions",
            )
            for index in range(20)
        ),
        active_skill=SkillDefinition(
            name="a" * 64,
            description="active",
            instructions="i" * 8_000,
        ),
    )
    memory_prompt = memory_context_prompt(
        memory_read_enabled=True,
        memory_scope={"total": 3, "pinned": 0},
        offered_tool_names=frozenset(effective.tools),
    )
    schemas = effective.schemas()
    prompt_tokens = estimate_tokens(prompt, 4.0) + estimate_tokens(memory_prompt, 4.0)
    schema_tokens = estimate_tokens(schemas, 4.0)

    assert len(schemas) == 53
    assert {"load_skill", "build_skill", "edit_skill"} <= {
        schema["name"] for schema in schemas
    }
    build_skill = next(schema for schema in schemas if schema["name"] == "build_skill")
    assert set(build_skill["parameters"]["properties"]) == {
        "_reason",
        "brief",
        "name_hint",
    }
    assert prompt_tokens <= 7_300, prompt_tokens
    # Permission reasons add one small host field to each permission-capable tool.
    assert schema_tokens <= 9_250, schema_tokens
    assert prompt_tokens + schema_tokens <= 16_550, prompt_tokens + schema_tokens
