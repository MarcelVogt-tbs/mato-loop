from __future__ import annotations

import re
from typing import Any

from mato_loop_backend.integrations import WebFetchError, sanitize_url_for_display

TOOL_GROUPS = (
    {"id": "filesystem", "label": "Dateisystem", "icon": "folder", "emoji": "📁"},
    {"id": "process", "label": "Prozesse", "icon": "terminal", "emoji": "💻"},
    {"id": "web", "label": "Web", "icon": "language", "emoji": "🌐"},
    {"id": "calendar", "label": "Kalender", "icon": "calendar_month", "emoji": "📅"},
    {"id": "tasks", "label": "Aufgaben", "icon": "checklist", "emoji": "✅"},
    {"id": "mail", "label": "E-Mail", "icon": "mail", "emoji": "✉️"},
    {"id": "media", "label": "Medien", "icon": "perm_media", "emoji": "🎨"},
    {"id": "sessions", "label": "Sessions", "icon": "forum", "emoji": "💬"},
    {"id": "automations", "label": "Automationen", "icon": "schedule", "emoji": "⏱️"},
    {"id": "memory", "label": "Memory", "icon": "psychology", "emoji": "🧠"},
    {"id": "agent", "label": "Agent", "icon": "smart_toy", "emoji": "🤖"},
    {"id": "coding", "label": "Coding", "icon": "code", "emoji": "⌨️"},
    {"id": "integrations", "label": "Integrationen", "icon": "extension", "emoji": "🔌"},
)
_GROUPS = {item["id"]: item for item in TOOL_GROUPS}

TOOL_ACTIVITY_LABELS = {
    "coding_analyze": "Projekt analysieren", "coding_create_plan": "Plan speichern",
    "coding_list_plans": "Pläne anzeigen", "coding_enqueue": "Pläne ausführen",
    "question": "Rückfrage stellen", "todo_write": "Aufgaben aktualisieren", "read": "Pfad lesen", "glob": "Dateien suchen",
    "grep": "Dateiinhalte durchsuchen", "list_directory": "Ordner auflisten",
    "read_file": "Datei lesen", "task": "Aufgaben delegieren", "provide_file": "Datei bereitstellen",
    "write_file": "Datei schreiben", "edit_file": "Datei bearbeiten", "apply_patch": "Patch anwenden",
    "create_directory": "Ordner erstellen", "delete_path": "Pfad löschen", "copy_path": "Pfad kopieren",
    "move_path": "Pfad verschieben", "web_search": "Im Web suchen", "webfetch": "Webseite abrufen",
    "fetch_transcript": "Untertitel abrufen", "generate_qr_code": "QR-Code erzeugen",
    "read_qr_code": "QR-Code lesen", "generate_image": "Bild erzeugen", "edit_image": "Bild bearbeiten",
    "generate_speech": "Sprache erzeugen", "generate_audio": "Audio erzeugen",
    "transcribe_audio": "Audio transkribieren", "analyze_video": "Video analysieren",
    "calendar_list_accounts": "Kalenderkonten auflisten", "calendar_list_calendars": "Kalender auflisten",
    "calendar_query_events": "Termine lesen", "calendar_create_event": "Termin erstellen",
    "calendar_update_event": "Termin ändern", "calendar_delete_event": "Termin löschen",
    "tasks_list_accounts": "Aufgabenkonten auflisten", "tasks_list_lists": "Aufgabenlisten auflisten",
    "tasks_query": "Aufgaben lesen", "tasks_create": "Aufgabe erstellen",
    "tasks_create_many": "Fehlende Aufgaben gesammelt erstellen", "tasks_update": "Aufgabe ändern",
    "tasks_set_completed": "Aufgabenstatus ändern", "tasks_delete": "Aufgabe löschen",
    "mail_list_accounts": "Mail-Konten auflisten", "mail_list_folders": "Mail-Ordner auflisten",
    "mail_search": "E-Mails suchen", "mail_get_message": "E-Mail lesen",
    "mail_create_draft": "Entwurf erstellen", "mail_update_draft": "Entwurf ändern",
    "mail_delete_draft": "Entwurf löschen", "mail_send_draft": "Entwurf senden",
    "mail_send_message": "E-Mail senden", "mail_delete_message": "E-Mail löschen",
    "mealie_list_accounts": "Mealie-Konten auflisten", "recipes_search": "Rezepte suchen",
    "recipe_get": "Rezept lesen", "meal_plan_get": "Essensplan lesen",
    "meal_plan_add": "Essensplan-Eintrag erstellen", "meal_plan_update": "Essensplan-Eintrag ändern",
    "meal_plan_delete": "Essensplan-Eintrag löschen", "session_read": "Session lesen",
    "knowledge_list_accounts": "Wissensdatenbanken auflisten", "knowledge_list_books": "Bücher auflisten",
    "knowledge_search": "Wissensdatenbank durchsuchen", "knowledge_read_book": "Buchinhalt lesen",
    "knowledge_read_page": "Seite lesen", "knowledge_create_page": "Seite erstellen",
    "knowledge_update_page": "Seite bearbeiten", "knowledge_delete_page": "Seite löschen",
    "knowledge_list_attachments": "Anhänge auflisten", "knowledge_create_attachment": "Anhang hinzufügen",
    "knowledge_delete_attachment": "Anhang löschen", "knowledge_create_book": "Buch erstellen",
    "knowledge_update_book": "Buch ändern", "knowledge_delete_book": "Buch löschen",
    "knowledge_create_chapter": "Kapitel erstellen", "knowledge_update_chapter": "Kapitel ändern",
    "knowledge_delete_chapter": "Kapitel löschen", "knowledge_list_shelves": "Regale auflisten",
    "knowledge_read_shelf": "Regalinhalt lesen", "knowledge_create_shelf": "Regal erstellen",
    "knowledge_update_shelf": "Regal bearbeiten", "knowledge_delete_shelf": "Regal löschen",
    "session_tool_result_read": "Tool-Ergebnis lesen", "session_browse": "Letzte Sessions anzeigen",
    "session_search": "Sessions durchsuchen", "memory_retrieve": "Memory abrufen",
    "memory_write": "Memory speichern", "update_agent_identity": "Persona ändern",
    "update_user_profile": "Persönliches Profil ändern", "variable_read": "Variable lesen",
    "variable_write": "Variable speichern", "secret_input": "Secret einmalig abfragen",
    "process_exec": "Prozess ausführen",
    "process.exec": "Prozess ausführen", "shell_run": "Shell-Befehl ausführen",
    "shell.run": "Shell-Befehl ausführen", "list_automations": "Automationen anzeigen",
    "get_automation": "Automation anzeigen", "create_automation": "Automation erstellen",
    "edit_automation": "Automation ändern", "run_automation": "Automation starten",
    "delete_automation": "Automation löschen",
    "load_skill": "Skill laden", "build_skill": "Skill speichern", "edit_skill": "Skill bearbeiten",
    "get_system_info": "Systeminformationen", "change_directory": "Verzeichnis wechseln",
    "render_openscad": "OpenSCAD-Modell rendern",
    "browser_tabs": "Browser-Tabs", "browser_navigate": "Website aufrufen",
    "browser_snapshot": "Browser-Snapshot", "browser_take_screenshot": "Browser-Screenshot",
    "browser_click": "Browser-Klick", "browser_type": "Browser-Texteingabe",
    "browser_fill_form": "Browser-Formular", "browser_press_key": "Browser-Tastendruck",
    "browser_scroll": "Browser-Scrollen", "browser_diagnostics": "Browser-Diagnose",
    "browser_evaluate": "Browser-JavaScript", "browser_run_code": "Browser-Python",
    "browser_request_control": "Browser-Steuerung anfordern",
    "browser_release_control": "Browser-Steuerung freigeben",
}

_FAMILY_GROUPS = {
    "browser": "web", "calendar": "calendar", "tasks": "tasks", "mail": "mail",
    "memory": "memory", "session": "sessions", "coding": "coding", "process": "process",
    "shell": "process", "recipe": "integrations", "recipes": "integrations", "meal": "integrations",
}
_TOOL_GROUPS = {
    "web_search": "web", "webfetch": "web", "fetch_transcript": "web",
    "list_automations": "automations", "get_automation": "automations",
    "create_automation": "automations", "edit_automation": "automations",
    "run_automation": "automations", "delete_automation": "automations",
    "get_system_info": "process", "change_directory": "process",
    **{
        name: "filesystem"
        for name in (
            "read", "glob", "grep", "list_directory", "read_file", "provide_file",
            "write_file", "edit_file", "apply_patch", "create_directory", "delete_path",
            "copy_path", "move_path",
        )
    },
    **{
        name: "media"
        for name in (
            "generate_qr_code", "read_qr_code", "generate_image", "edit_image",
            "generate_speech", "generate_audio", "transcribe_audio", "analyze_video",
        )
    },
    **{
        name: "agent"
        for name in (
            "question", "task", "todo_write", "load_skill", "build_skill", "edit_skill",
            "update_agent_identity", "update_user_profile",
        )
    },
}


def _group(tool_name: str, declared_group: str | None) -> dict[str, str]:
    family = re.split(r"[_.]", tool_name, maxsplit=1)[0]
    group_id = _TOOL_GROUPS.get(tool_name) or _FAMILY_GROUPS.get(family)
    if group_id is None and declared_group in _GROUPS:
        group_id = declared_group
    return _GROUPS.get(group_id or "integrations", _GROUPS["integrations"])


def _label(tool_name: str, arguments: dict[str, Any], declared_label: str | None) -> str:
    if tool_name == "browser_navigate":
        return "Website aufrufen"
    if tool_name == "browser_tabs":
        return {
            "list": "Browser-Tabs anzeigen", "new": "Browser-Tab öffnen",
            "close": "Browser-Tab schließen", "select": "Browser-Tab auswählen",
        }.get(arguments.get("action"), declared_label or "Browser-Tabs")
    return declared_label or TOOL_ACTIVITY_LABELS.get(tool_name, "Tool ausführen")


def _summary(tool_name: str, arguments: dict[str, Any]) -> str | None:
    value: object | None = None
    if tool_name in {"webfetch", "fetch_transcript", "browser_navigate"} or (
        tool_name == "browser_tabs" and arguments.get("action") == "new"
    ):
        url = arguments.get("url")
        if isinstance(url, str):
            try:
                value = sanitize_url_for_display(url)
            except (ValueError, WebFetchError):
                pass
    elif tool_name in {"web_search", "session_search", "mail_search", "recipes_search"}:
        value = arguments.get("query")
    elif tool_name in {"read", "read_file", "write_file", "edit_file", "create_directory", "delete_path", "provide_file"}:
        value = arguments.get("path")
    elif tool_name in {"glob", "grep"}:
        value = arguments.get("pattern")
    elif tool_name in {"copy_path", "move_path"}:
        source = arguments.get("source_path", arguments.get("sourcePath"))
        destination = arguments.get("destination_path", arguments.get("destinationPath"))
        if isinstance(source, str) and isinstance(destination, str):
            value = f"{source} → {destination}"
    if not isinstance(value, str):
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value[:137] + "..." if len(value) > 140 else value or None


def tool_display(tool_name: str, arguments: dict[str, Any] | None = None, definition: Any | None = None) -> dict[str, str]:
    arguments = arguments or {}
    group = _group(tool_name, getattr(definition, "group", None))
    display = {
        "group": group["id"], "groupLabel": group["label"], "icon": group["icon"],
        "emoji": group["emoji"], "label": _label(tool_name, arguments, getattr(definition, "label", None)),
    }
    if summary := _summary(tool_name, arguments):
        display["summary"] = summary
    return display


def valid_tool_display(value: object) -> bool:
    if not isinstance(value, dict) or set(value) - {"group", "groupLabel", "icon", "emoji", "label", "summary"}:
        return False
    group = _GROUPS.get(value.get("group"))
    return bool(
        group
        and value.get("groupLabel") == group["label"]
        and all(value.get(key) == group[key] for key in ("icon", "emoji"))
        and isinstance(value.get("label"), str) and 0 < len(value["label"]) <= 160
        and ("summary" not in value or isinstance(value["summary"], str) and len(value["summary"]) <= 140)
    )


def enrich_tool_calls(calls: object) -> object:
    if not isinstance(calls, list):
        return calls
    enriched = []
    for raw in calls:
        if not isinstance(raw, dict):
            enriched.append(raw)
            continue
        call = dict(raw)
        if not valid_tool_display(call.get("toolDisplay")) and isinstance(call.get("name"), str):
            arguments = call.get("arguments")
            call["toolDisplay"] = tool_display(call["name"], arguments if isinstance(arguments, dict) else {})
        enriched.append(call)
    return enriched
