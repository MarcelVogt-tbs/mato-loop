"""Freeze the public model contract, including the unified personal settings tool."""
import hashlib
import json
from datetime import UTC, datetime

from mato_loop_backend.agent.prompts import general_system_prompt
from mato_loop_backend.models import ChatSession
from mato_loop_backend.settings import IntegrationSettings, ToolSettings, TrustLevel
from mato_loop_backend.tools.catalog import tool_registry


def test_model_contract_survives_module_extraction():
    contracts = []
    for trust in TrustLevel:
        for web in (False, True):
            tools = tool_registry.effective(
                IntegrationSettings(),
                webfetch_enabled=web,
                tool_settings=ToolSettings().select_trust(trust),
                memory_enabled=True,
                memory_write_enabled=True,
            )
            contracts.append({
                "schemas": tools.schemas(),
                "prompt": general_system_prompt(
                    ChatSession(title="Contract", memory_mode="full", web_search_policy="available"),
                    current_datetime=datetime(2026, 9, 24, 12, tzinfo=UTC),
                    offered_tool_names=frozenset(tools.tools),
                    tool_instructions=tools.prompt_fragments,
                ),
            })
    digest = hashlib.sha256(json.dumps(contracts, sort_keys=True).encode()).hexdigest()
    assert digest == "5de0f46e08b0c25355f7c7166ebaa2bb918bcf7cb9998c953616bfd4bc3747e4"
