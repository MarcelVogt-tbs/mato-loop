from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import HttpUrl, field_validator

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

# shortcut: remote operations share one write category; split them before per-tool category defaults.
SONOBIN_ACTION = ToolAction(
    "sonobin.call", "SonoBin-Funktion ausführen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, category="write"
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
    description="Sucht Musik und verwaltet Playlists, Favoriten, Importe und die Bibliothek.",
    setup_instructions=(
        "Verwende die SonoBin-Basis-URL. Der MCP-Endpunkt wird automatisch ergänzt.",
        "Verwende einen in SonoBin erstellten MCP-Token oder den konfigurierten statischen Bearer-Token.",
        "Für Download-Links muss SONOBIN_MCP_PUBLIC_BASE_URL in SonoBin gesetzt sein.",
    ),
    server_policy=ToolExecutionPolicy.ASK,
    tools={
        "list_recordings": _tool("Aufnahmen auflisten", ToolExecutionPolicy.AUTO),
        "list_recordings_without_tracks": _tool(
            "Aufnahmen ohne Bibliothekstitel auflisten", ToolExecutionPolicy.AUTO
        ),
        "get_recording": _tool("Aufnahme anzeigen", ToolExecutionPolicy.AUTO),
        "get_metadata_candidate": _tool(
            "Metadaten-Kandidaten anzeigen", ToolExecutionPolicy.AUTO
        ),
        "list_tracks": _tool("Bibliothekstitel auflisten", ToolExecutionPolicy.AUTO),
        "get_track": _tool("Bibliothekstitel anzeigen", ToolExecutionPolicy.AUTO),
        "get_mcp_context": _tool("Konto und Rechte anzeigen", ToolExecutionPolicy.AUTO),
        "list_providers": _tool("Musik-Provider auflisten", ToolExecutionPolicy.AUTO),
        "search_music": _tool("Musik durchsuchen", ToolExecutionPolicy.AUTO),
        "list_playlists": _tool("Playlists auflisten", ToolExecutionPolicy.AUTO),
        "get_playlist": _tool("Playlist anzeigen", ToolExecutionPolicy.AUTO),
        "list_playlist_tracks": _tool("Playlist-Einträge auflisten", ToolExecutionPolicy.AUTO),
        "create_playlist": _tool("Playlist erstellen", ToolExecutionPolicy.ASK),
        "update_playlist": _tool("Playlist bearbeiten", ToolExecutionPolicy.ASK),
        "delete_playlist": _tool("Playlist löschen", ToolExecutionPolicy.ASK),
        "add_playlist_tracks": _tool("Playlist-Titel hinzufügen", ToolExecutionPolicy.ASK),
        "remove_playlist_tracks": _tool("Playlist-Einträge entfernen", ToolExecutionPolicy.ASK),
        "reorder_playlist_tracks": _tool("Playlist-Einträge umsortieren", ToolExecutionPolicy.ASK),
        "list_likes": _tool("Favoriten auflisten", ToolExecutionPolicy.AUTO),
        "set_track_like": _tool("Titel-Favorit setzen oder entfernen", ToolExecutionPolicy.ASK),
        "import_remote_track": _tool("Remote-Titel importieren", ToolExecutionPolicy.ASK),
        "get_acquisition_status": _tool("Titel-Importstatus anzeigen", ToolExecutionPolicy.AUTO),
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
        "list_jobs": _tool("Hintergrundaufträge auflisten", ToolExecutionPolicy.AUTO),
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
        "get_runtime_diagnostics": _tool("Laufzeitdiagnose anzeigen", ToolExecutionPolicy.AUTO),
        "get_integration_diagnostics": _tool("Integrationsdiagnose anzeigen", ToolExecutionPolicy.AUTO),
        "get_player_diagnostics": _tool("Player-Diagnose anzeigen", ToolExecutionPolicy.AUTO),
        "get_playback_diagnostic_events": _tool(
            "Wiedergabe-Diagnoseereignisse anzeigen", ToolExecutionPolicy.AUTO
        ),
        "list_generator_runs": _tool("Generator-Ausführungen auflisten", ToolExecutionPolicy.AUTO),
        "get_generator_run": _tool("Generator-Ausführung anzeigen", ToolExecutionPolicy.AUTO),
    },
)


def sonobin_base_url(value: str | HttpUrl) -> str:
    url = HttpUrl(str(value))
    if (url.scheme != "https" or url.username is not None or url.password is not None
        or url.query is not None or url.fragment is not None):
        raise ValueError("SonoBin requires an HTTPS base URL without credentials, query, or fragment")
    return str(url).rstrip("/").removesuffix("/mcp")


class SonoBinConfig(McpConfig):
    @field_validator("endpoint", mode="before")
    @classmethod
    def normalize_endpoint(cls, value: Any) -> str:
        return sonobin_base_url(value) + "/mcp/"


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
