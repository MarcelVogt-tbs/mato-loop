"""Project trusted, positional reasoning changes without rewriting the request prefix."""

from __future__ import annotations

from typing import Any

from mato_loop_backend.settings.models import ReasoningEffort


def reasoning_configuration(
    messages: list[dict[str, Any]], scope: dict[str, str | None], effort: ReasoningEffort,
) -> tuple[ReasoningEffort, dict[int, str], dict[str, Any]]:
    configurations = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        provider_data = message.get("provider_data") or {}
        saved = provider_data.get("reasoning_configuration")
        if saved is None and not message.get("source_message_id") and not provider_data.get("continuation_scope"):
            # Host Memory/checklist envelopes are not model responses or lineage changes.
            continue
        if not isinstance(saved, dict) or saved.get("scope") != scope:
            # Model/auth/privacy changes start a new lineage, never revive an older one.
            configurations.clear()
            continue
        try:
            base = ReasoningEffort(saved["baseEffort"])
            previous = ReasoningEffort(saved["effort"])
        except (KeyError, ValueError, TypeError):
            configurations.clear()
            continue
        configurations.append((index, base, previous))
    base = configurations[0][1] if configurations else effort
    current = base
    updates = {}
    for index, _, previous in configurations:
        if previous != current:
            updates[index] = previous.value
            current = previous
    if current != effort:
        updates[len(messages)] = effort.value
    return base, updates, {"scope": scope, "baseEffort": base.value, "effort": effort.value}
