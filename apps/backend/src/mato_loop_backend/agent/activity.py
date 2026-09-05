import re

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

TOOL_ACTIVITY_LABELS = {
    "question": "Rückfrage stellen",
    "read": "Datei lesen",
    "glob": "Dateien suchen",
    "grep": "Dateiinhalte durchsuchen",
    "list_directory": "Ordner auflisten",
    "read_file": "Datei lesen",
    "task": "Codebasis erkunden",
    "provide_file": "Datei bereitstellen",
    "write_file": "Datei schreiben",
    "edit_file": "Datei bearbeiten",
    "apply_patch": "Patch anwenden",
    "create_directory": "Ordner erstellen",
    "delete_path": "Pfad löschen",
    "copy_path": "Pfad kopieren",
    "move_path": "Pfad verschieben",
    "web_search": "Websuche",
    "webfetch": "Webseite abrufen",
    "fetch_transcript": "Untertitel abrufen",
    "generate_qr_code": "QR-Code erzeugen",
    "read_qr_code": "QR-Code lesen",
    "generate_image": "Bild erzeugen",
    "edit_image": "Bild bearbeiten",
    "generate_speech": "Sprache erzeugen",
    "generate_audio": "Audio erzeugen",
    "transcribe_audio": "Audio transkribieren",
    "analyze_video": "Video analysieren",
    "calendar_list_accounts": "Kalenderkonten auflisten",
    "calendar_list_calendars": "Kalender auflisten",
    "calendar_query_events": "Termine lesen",
    "calendar_create_event": "Termin erstellen",
    "calendar_update_event": "Termin ändern",
    "calendar_delete_event": "Termin löschen",
    "tasks_list_accounts": "Aufgabenkonten auflisten",
    "tasks_list_lists": "Aufgabenlisten auflisten",
    "tasks_query": "Aufgaben lesen",
    "tasks_create": "Aufgabe erstellen",
    "tasks_create_many": "Fehlende Aufgaben erstellen",
    "tasks_update": "Aufgabe ändern",
    "tasks_set_completed": "Aufgabenstatus ändern",
    "tasks_delete": "Aufgabe löschen",
    "mail_list_accounts": "Mail-Konten auflisten",
    "mail_list_folders": "Mail-Ordner auflisten",
    "mail_search": "E-Mails suchen",
    "mail_get_message": "E-Mail lesen",
    "mail_create_draft": "Entwurf erstellen",
    "mail_update_draft": "Entwurf ändern",
    "mail_delete_draft": "Entwurf löschen",
    "mail_send_draft": "Entwurf senden",
    "mail_send_message": "E-Mail senden",
    "mail_delete_message": "E-Mail löschen",
    "mealie_list_accounts": "Mealie-Konten auflisten",
    "recipes_search": "Rezepte suchen",
    "recipe_get": "Rezept lesen",
    "meal_plan_get": "Essensplan lesen",
    "meal_plan_add": "Essensplan-Eintrag erstellen",
    "meal_plan_update": "Essensplan-Eintrag ändern",
    "meal_plan_delete": "Essensplan-Eintrag löschen",
    "session_read": "Session lesen",
    "session_tool_result_read": "Tool-Ergebnis lesen",
    "session_browse": "Letzte Sessions anzeigen",
    "session_search": "Sessions durchsuchen",
    "memory_retrieve": "Memory abrufen",
    "memory_write": "Memory speichern",
    "update_agent_identity": "Persona ändern",
    "update_user_profile": "Persönliches Profil ändern",
    "process_exec": "Prozess ausführen",
    "process.exec": "Prozess ausführen",
    "shell_run": "Shell-Befehl ausführen",
    "shell.run": "Shell-Befehl ausführen",
    "git_clone": "Git-Repository klonen",
    "git_status": "Git-Status anzeigen",
    "git_diff": "Git-Änderungen anzeigen",
    "git_log": "Git-Verlauf anzeigen",
    "git_add": "Git-Änderungen vormerken",
    "git_commit": "Git-Commit erstellen",
    "list_automations": "Automationen anzeigen",
    "get_automation": "Automation anzeigen",
    "create_automation": "Automation erstellen",
    "edit_automation": "Automation ändern",
    "run_automation": "Automation starten",
    "delete_automation": "Automation löschen",
}


def run_activity_label(
    phase: str | None,
    *,
    model_request: int | None = None,
    provider_attempt: int | None = None,
    provider_attempt_total: int | None = None,
    tool: str | None = None,
) -> str:
    if phase in {"preparing_context", "waiting_model"}:
        return (
            "Verarbeite Tool-Ergebnisse"
            if type(model_request) is int and model_request > 1
            else "Verarbeite Anfrage"
        )
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
