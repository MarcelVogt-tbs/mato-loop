import re

from mato_loop_backend.agent.tool_display import TOOL_ACTIVITY_LABELS

PHASE_ACTIVITY_LABELS = {
    "submitting": "Verarbeite Anfrage",
    "queued": "Verarbeite Anfrage",
    "preparing_context": "Verarbeite Anfrage",
    "compacting_context": "Kontext verdichten",
    "waiting_approval": "Freigabe erforderlich",
    "waiting_input": "Antwort erforderlich",
    "finalizing": "Abschließen",
    "reconnecting": "Neu verbinden",
}

def run_activity_label(
    phase: str | None,
    *,
    model_request: int | None = None,
    provider_attempt: int | None = None,
    provider_attempt_total: int | None = None,
    tool: str | None = None,
) -> str | None:
    if phase == "waiting_model":
        return None
    if phase == "preparing_context":
        return "Verarbeite Anfrage"
    if phase == "processing_tool":
        return TOOL_ACTIVITY_LABELS.get(tool, "Tool ausführen")
    return PHASE_ACTIVITY_LABELS.get(phase, "Agent arbeitet")


def reasoning_activity_label(reasoning: dict[str, object] | None) -> str:
    if reasoning is None or reasoning.get("kind") != "summary":
        return "Nachdenken"
    text = reasoning.get("text")
    if not isinstance(text, str) or not text:
        return "Nachdenken"

    blocks = [
        block.strip()
        for block in re.split(r"\n{2,}", text.replace("****", "**\n\n**"))
        if block.strip()
    ]
    for block in reversed(blocks):
        segments: list[tuple[str, bool]] = []
        cursor = 0
        for match in re.finditer(r"\*\*([^*\n]+)\*\*", block):
            if match.start() > cursor:
                segments.append((block[cursor : match.start()], False))
            segments.append((match.group(1), True))
            cursor = match.end()
        if cursor < len(block):
            segments.append((block[cursor:], False))
        if not segments:
            segments.append((block, False))

        first = next((segment for segment in segments if segment[0].strip()), None)
        source = (
            " ".join(segment for segment, strong in segments if strong)
            if first is not None and first[1]
            else "".join(segment for segment, _strong in segments)
        )
        label = re.sub(r"^\*{1,2}|\*{1,2}$", "", source)
        label = re.sub(r"\s+", " ", label).strip()
        if label:
            return f"{label[:160]}…" if len(label) > 160 else label
    return "Nachdenken"
