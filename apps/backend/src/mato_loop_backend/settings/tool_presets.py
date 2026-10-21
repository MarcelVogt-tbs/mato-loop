from collections.abc import Mapping
from dataclasses import dataclass

from mato_loop_backend.settings.models import ToolExecutionPolicy, ToolPermissionPreset
from mato_loop_backend.tools.actions import ToolAction, ToolActionCategory


@dataclass(frozen=True)
class PermissionPreset:
    label: str
    automatic_categories: frozenset[ToolActionCategory]


PRESETS: dict[ToolPermissionPreset, PermissionPreset] = {
    "ask_all": PermissionPreset("Alles bestätigen", frozenset()),
    "balanced": PermissionPreset("Ausgewogen", frozenset()),
    "ask_changes": PermissionPreset("Komfortabel", frozenset({"read", "read_sensitive"})),
    "ask_delete": PermissionPreset("Automatisch außer Löschen", frozenset({"read", "read_sensitive", "write"})),
    "auto_all": PermissionPreset("Vollautomatisch", frozenset({"read", "read_sensitive", "write", "delete"})),
}

CATEGORY_LABELS: dict[ToolActionCategory, str] = {
    "read": "Lesen", "read_sensitive": "Sensible Zugriffe", "write": "Schreiben", "delete": "Löschen",
}

AUTOMATIC_ACTION_GROUPS = {
    "Öffentliche Recherche und Downloads in die Session": ("network.search", "network.fetch"),
    "Session-Dateien lesen, bearbeiten und organisieren": ("session.files.read", "session.files.write"),
    "Dateien bereitstellen": ("file.provide",),
    "Bilder erzeugen und bearbeiten": ("media.generate.image", "media.edit.image"),
    "Sprache und Audio erzeugen und verarbeiten": ("media.generate.speech", "media.generate.audio", "media.transcribe.audio", "media.analyze.audio"),
    "Videos analysieren": ("media.analyze.video",),
    "Skills laden": ("skills.load",),
    "Tool-Ergebnisse dieser Session nachlesen": ("session.tool_result.read",),
}
AUTOMATIC_CAPABILITIES = frozenset(capability for group in AUTOMATIC_ACTION_GROUPS.values() for capability in group)

CONFIRMATION_ACTION_GROUPS = {
    "Variablen und Secrets verwenden": ("secrets.access",),
}
CONFIRMATION_CAPABILITIES = frozenset(capability for group in CONFIRMATION_ACTION_GROUPS.values() for capability in group)


def preset_policy(preset: ToolPermissionPreset, category: ToolActionCategory, capability: str | None = None) -> ToolExecutionPolicy:
    if capability in CONFIRMATION_CAPABILITIES:
        return ToolExecutionPolicy.ASK
    automatic = category in PRESETS[preset].automatic_categories or (
        preset != "ask_all" and capability in AUTOMATIC_CAPABILITIES
    )
    return ToolExecutionPolicy.AUTO if automatic else ToolExecutionPolicy.ASK


def preset_catalog(actions: Mapping[str, ToolAction]) -> list[dict]:
    return [
        {
            "value": key, "label": preset.label,
            "baseline": [
                {"category": category, "label": label, "policy": preset_policy(key, category)}
                for category, label in CATEGORY_LABELS.items()
            ],
            "additionalPermissions": [
                label for label, capabilities in AUTOMATIC_ACTION_GROUPS.items()
                if any(
                    preset_policy(key, actions[capability].category) != preset_policy(key, actions[capability].category, capability)
                    for capability in capabilities
                )
            ],
            "additionalConfirmations": [
                label for label, capabilities in CONFIRMATION_ACTION_GROUPS.items()
                if any(
                    preset_policy(key, actions[capability].category) != preset_policy(key, actions[capability].category, capability)
                    for capability in capabilities
                )
            ],
        }
        for key, preset in PRESETS.items()
    ]
