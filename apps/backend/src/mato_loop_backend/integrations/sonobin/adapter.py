from __future__ import annotations

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
from mato_loop_backend.tools.actions import ToolAction

# shortcut: remote operations share one write category; split them before per-tool category defaults.
SONOBIN_ACTION = ToolAction(
    "sonobin.call", "SonoBin-Funktion ausführen", category="write"
)

SONOBIN_TOOL_LABELS = {
    "list_recordings": "Aufnahmen auflisten",
    "list_recordings_without_tracks": "Aufnahmen ohne Bibliothekstitel auflisten",
    "get_recording": "Aufnahme anzeigen",
    "get_metadata_candidate": "Metadaten-Kandidaten anzeigen",
    "list_tracks": "Bibliothekstitel auflisten",
    "get_track": "Bibliothekstitel anzeigen",
    "get_mcp_context": "Konto und Rechte anzeigen",
    "list_providers": "Musik-Provider auflisten",
    "search_music": "Musik durchsuchen",
    "list_playlists": "Playlists auflisten",
    "get_playlist": "Playlist anzeigen",
    "list_playlist_tracks": "Playlist-Einträge auflisten",
    "create_playlist": "Playlist erstellen",
    "update_playlist": "Playlist bearbeiten",
    "delete_playlist": "Playlist löschen",
    "add_playlist_tracks": "Playlist-Titel hinzufügen",
    "remove_playlist_tracks": "Playlist-Einträge entfernen",
    "reorder_playlist_tracks": "Playlist-Einträge umsortieren",
    "list_likes": "Favoriten auflisten",
    "set_track_like": "Titel-Favorit setzen oder entfernen",
    "import_remote_track": "Remote-Titel importieren",
    "get_acquisition_status": "Titel-Importstatus anzeigen",
    "get_asset": "Audio-Asset anzeigen",
    "create_manual_candidate": "Manuellen Metadaten-Kandidaten erstellen",
    "update_manual_candidate": "Metadaten-Kandidaten bearbeiten",
    "delete_candidate": "Metadaten-Kandidaten löschen",
    "search_musicbrainz": "MusicBrainz durchsuchen",
    "add_musicbrainz_candidate": "MusicBrainz-Kandidaten hinzufügen",
    "identify_asset": "Audio-Asset erkennen",
    "create_track": "Bibliothekstitel erstellen",
    "set_track_candidate": "Metadaten-Kandidaten zuweisen",
    "delete_track": "Bibliothekstitel löschen",
    "set_preferred_asset": "Bevorzugtes Audio-Asset festlegen",
    "set_asset_source_candidate": "Quellenkandidaten für Audio-Asset festlegen",
    "move_asset": "Audio-Asset verschieben",
    "acquire_recording_asset": "Audio-Asset herunterladen",
    "get_asset_download_url": "Download-Link für Audio-Asset erstellen",
    "merge_recordings": "Aufnahmen zusammenführen",
    "delete_asset": "Audio-Asset löschen",
    "delete_recording": "Aufnahme löschen",
    "list_jobs": "Hintergrundaufträge auflisten",
    "get_job": "Hintergrundauftrag anzeigen",
    "cancel_job": "Hintergrundauftrag abbrechen",
    "get_diagnostics_overview": "Diagnoseübersicht anzeigen",
    "list_diagnostic_entries": "Diagnoseeinträge auflisten",
    "get_diagnostic_entry": "Diagnoseeintrag anzeigen",
    "get_runtime_diagnostics": "Laufzeitdiagnose anzeigen",
    "get_integration_diagnostics": "Integrationsdiagnose anzeigen",
    "get_player_diagnostics": "Player-Diagnose anzeigen",
    "get_playback_diagnostic_events": "Wiedergabe-Diagnoseereignisse anzeigen",
    "list_generator_runs": "Generator-Ausführungen auflisten",
    "get_generator_run": "Generator-Ausführung anzeigen",
}


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
        labels=SONOBIN_TOOL_LABELS,
        icon="library_music",
        approval_title="SonoBin-Funktion ausführen?",
        approval_summary="Der Agent möchte auf die verbundene SonoBin-Bibliothek zugreifen.",
    )


async def _resources(
    instance, credential: dict[str, Any] | None
) -> tuple[IntegrationResource, ...]:
    return await mcp_resources(instance, credential, labels=SONOBIN_TOOL_LABELS)


SONOBIN_ADAPTER = IntegrationAdapterDefinition(
    integration_type="music",
    adapter_type="sonobin",
    config_model=SonoBinConfig,
    contribution_factory=_contributions,
    resource_actions=(SONOBIN_ACTION,),
    resource_loader=_resources,
)
