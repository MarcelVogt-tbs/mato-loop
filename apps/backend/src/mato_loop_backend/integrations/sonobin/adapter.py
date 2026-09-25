from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import model_validator

from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
)
from mato_loop_backend.integrations.mcp.adapter import (
    McpConfig,
    mcp_contributions,
    mcp_resources,
)
from mato_loop_backend.settings import ToolExecutionPolicy
from mato_loop_backend.tools.actions import ToolAction

SONOBIN_ACTION = ToolAction(
    "sonobin.call", "SonoBin-Funktion ausführen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK
)


@dataclass(frozen=True, slots=True)
class SonoBinTool:
    label: str
    policy: ToolExecutionPolicy


@dataclass(frozen=True, slots=True)
class SonoBinMetadata:
    label: str
    description: str
    setup_instructions: tuple[str, ...]
    server_policy: ToolExecutionPolicy
    tools: dict[str, SonoBinTool]


def _tool(label: str, policy: ToolExecutionPolicy) -> SonoBinTool:
    return SonoBinTool(label, policy)


SONOBIN = SonoBinMetadata(
    label="SonoBin",
    description="Verwaltet Aufnahmen, Audio-Assets, Metadaten und Bibliothekstitel.",
    setup_instructions=(
        "Verwende die SonoBin-Backend-Adresse mit /mcp/ als Endpunkt.",
        "Der Bearer-Token muss SONOBIN_MCP_BEARER_TOKEN entsprechen.",
        "SONOBIN_MCP_USER muss ein aktiver Administrator sein und SONOBIN_MCP_PUBLIC_BASE_URL muss gesetzt sein.",
    ),
    server_policy=ToolExecutionPolicy.ASK,
    tools={
        "list_recordings": _tool("Aufnahmen auflisten", ToolExecutionPolicy.AUTO),
        "list_recordings_without_tracks": _tool(
            "Aufnahmen ohne Bibliothekstitel auflisten", ToolExecutionPolicy.AUTO
        ),
        "get_recording": _tool("Aufnahme anzeigen", ToolExecutionPolicy.AUTO),
        "list_tracks": _tool("Bibliothekstitel auflisten", ToolExecutionPolicy.AUTO),
        "get_track": _tool("Bibliothekstitel anzeigen", ToolExecutionPolicy.AUTO),
        "get_asset": _tool("Audio-Asset anzeigen", ToolExecutionPolicy.AUTO),
        "create_manual_candidate": _tool(
            "Manuellen Metadaten-Kandidaten erstellen", ToolExecutionPolicy.ASK
        ),
        "update_manual_candidate": _tool(
            "Manuellen Metadaten-Kandidaten bearbeiten", ToolExecutionPolicy.ASK
        ),
        "delete_candidate": _tool(
            "Metadaten-Kandidaten löschen", ToolExecutionPolicy.ASK
        ),
        "search_musicbrainz": _tool(
            "MusicBrainz durchsuchen", ToolExecutionPolicy.AUTO
        ),
        "add_musicbrainz_candidate": _tool(
            "MusicBrainz-Kandidaten hinzufügen", ToolExecutionPolicy.ASK
        ),
        "identify_asset": _tool("Audio-Asset erkennen", ToolExecutionPolicy.ASK),
        "create_track": _tool("Bibliothekstitel erstellen", ToolExecutionPolicy.ASK),
        "set_track_candidate": _tool(
            "Metadaten-Kandidaten zuweisen", ToolExecutionPolicy.ASK
        ),
        "delete_track": _tool("Bibliothekstitel löschen", ToolExecutionPolicy.ASK),
        "set_preferred_asset": _tool(
            "Bevorzugtes Audio-Asset festlegen", ToolExecutionPolicy.ASK
        ),
        "set_asset_source_candidate": _tool(
            "Quellenkandidaten für Audio-Asset festlegen", ToolExecutionPolicy.ASK
        ),
        "move_asset": _tool("Audio-Asset verschieben", ToolExecutionPolicy.ASK),
        "acquire_recording_asset": _tool(
            "Audio-Asset herunterladen", ToolExecutionPolicy.ASK
        ),
        "get_asset_download_url": _tool(
            "Download-Link für Audio-Asset erstellen", ToolExecutionPolicy.AUTO
        ),
        "merge_recordings": _tool("Aufnahmen zusammenführen", ToolExecutionPolicy.ASK),
        "delete_asset": _tool("Audio-Asset löschen", ToolExecutionPolicy.ASK),
        "delete_recording": _tool("Aufnahme löschen", ToolExecutionPolicy.ASK),
        "get_job": _tool("Hintergrundauftrag anzeigen", ToolExecutionPolicy.AUTO),
        "cancel_job": _tool("Hintergrundauftrag abbrechen", ToolExecutionPolicy.ASK),
        "get_diagnostics_overview": _tool(
            "Diagnoseübersicht anzeigen", ToolExecutionPolicy.AUTO
        ),
        "list_diagnostic_entries": _tool(
            "Diagnoseeinträge auflisten", ToolExecutionPolicy.AUTO
        ),
        "get_diagnostic_entry": _tool(
            "Diagnoseeintrag anzeigen", ToolExecutionPolicy.AUTO
        ),
    },
)


class SonoBinConfig(McpConfig):
    @model_validator(mode="after")
    def require_mcp_endpoint(self) -> SonoBinConfig:
        if self.endpoint.scheme != "https" or not self.endpoint.path.endswith("/mcp/"):
            raise ValueError("SonoBin requires an HTTPS /mcp/ endpoint")
        return self


def _contributions(instance) -> IntegrationContributions:
    return mcp_contributions(
        instance,
        action=SONOBIN_ACTION,
        labels={name: tool.label for name, tool in SONOBIN.tools.items()},
        icon="library_music",
        approval_title="SonoBin-Funktion ausführen?",
        approval_summary="Der Agent möchte auf die verbundene SonoBin-Bibliothek zugreifen.",
    )


async def _resources(
    instance, credential: dict[str, Any] | None
) -> tuple[IntegrationResource, ...]:
    return await mcp_resources(
        instance,
        credential,
        labels={name: tool.label for name, tool in SONOBIN.tools.items()},
    )


SONOBIN_ADAPTER = IntegrationAdapterDefinition(
    integration_type="music",
    adapter_type="sonobin",
    config_model=SonoBinConfig,
    contribution_factory=_contributions,
    resource_actions=(SONOBIN_ACTION,),
    resource_loader=_resources,
)
