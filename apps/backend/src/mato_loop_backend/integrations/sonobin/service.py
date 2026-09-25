from __future__ import annotations

from mato_loop_backend.integrations.mcp.service import McpIntegrationService
from mato_loop_backend.integrations.sonobin.adapter import SONOBIN, SonoBinConfig


class SonoBinIntegrationService(McpIntegrationService):
    integration_type = "music"
    adapter_type = "sonobin"
    config_model = SonoBinConfig
    capability = "sonobin.call"
    defaults = SONOBIN
    token_required = True
