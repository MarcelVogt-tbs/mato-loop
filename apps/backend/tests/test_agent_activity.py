import pytest

from mato_loop_backend.agent.activity import (
    TOOL_ACTIVITY_LABELS,
    reasoning_activity_label,
    run_activity_label,
)


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("submitting", "Anfrage wird übergeben"),
        ("queued", "Ausführung wartet auf den Start"),
        ("preparing_context", "Kontext und Tools werden vorbereitet"),
        ("compacting_context", "Gesprächskontext wird verdichtet"),
        ("streaming", "Antwort wird erstellt"),
        ("preparing_tools", "Tool-Aufrufe werden vorbereitet"),
        ("waiting_approval", "Wartet auf Freigabe"),
        ("waiting_input", "Wartet auf deine Antwort"),
        ("finalizing", "Antwort wird abgeschlossen"),
        ("reconnecting", "Verbindung wird wiederhergestellt"),
        ("unknown", "Agent arbeitet"),
    ],
)
def test_run_activity_labels_all_static_phases(phase: str, expected: str) -> None:
    assert run_activity_label(phase) == expected


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({}, "Modell analysiert die Anfrage"),
        ({"model_request": 1}, "Modell analysiert die Anfrage"),
        ({"model_request": 2}, "Modell wertet Tool-Ergebnisse aus"),
        (
            {
                "model_request": 2,
                "provider_attempt": 2,
                "provider_attempt_total": 3,
            },
            "Provider-Anfrage: Versuch 2 von 3",
        ),
        (
            {"provider_attempt": 4, "provider_attempt_total": 3},
            "Modell analysiert die Anfrage",
        ),
    ],
)
def test_run_activity_labels_model_requests(details: dict, expected: str) -> None:
    assert run_activity_label("waiting_model", **details) == expected


def test_tool_activity_labels_match_the_frontend_compatibility_table() -> None:
    assert TOOL_ACTIVITY_LABELS == {
        "question": "Rückfrage wird vorbereitet",
        "read": "Datei wird gelesen",
        "glob": "Dateien werden gesucht",
        "grep": "Dateiinhalte werden durchsucht",
        "list_directory": "Ordner wird aufgelistet",
        "read_file": "Datei wird gelesen",
        "provide_file": "Datei wird bereitgestellt",
        "write_file": "Datei wird geschrieben",
        "edit_file": "Datei wird bearbeitet",
        "create_directory": "Ordner wird erstellt",
        "delete_path": "Pfad wird gelöscht",
        "copy_path": "Pfad wird kopiert",
        "move_path": "Pfad wird verschoben",
        "web_search": "Web wird durchsucht",
        "webfetch": "Webseite wird abgerufen",
        "fetch_transcript": "Untertitel werden abgerufen",
        "generate_qr_code": "QR-Code wird erzeugt",
        "read_qr_code": "QR-Code wird gelesen",
        "generate_image": "Bild wird erzeugt",
        "edit_image": "Bild wird bearbeitet",
        "generate_speech": "Sprachaufnahme wird erzeugt",
        "generate_audio": "Audio wird erzeugt",
        "transcribe_audio": "Audio wird transkribiert",
        "analyze_video": "Video wird analysiert",
        "calendar_list_accounts": "Kalenderkonten werden geladen",
        "calendar_list_calendars": "Kalender werden geladen",
        "calendar_query_events": "Termine werden gelesen",
        "calendar_create_event": "Termin wird erstellt",
        "calendar_update_event": "Termin wird geändert",
        "calendar_delete_event": "Termin wird gelöscht",
        "tasks_list_accounts": "Aufgabenkonten werden geladen",
        "tasks_list_lists": "Aufgabenlisten werden geladen",
        "tasks_query": "Aufgaben werden gelesen",
        "tasks_create": "Aufgabe wird erstellt",
        "tasks_create_many": "Aufgaben werden erstellt",
        "tasks_update": "Aufgabe wird geändert",
        "tasks_set_completed": "Aufgabenstatus wird geändert",
        "tasks_delete": "Aufgabe wird gelöscht",
        "mail_list_accounts": "Mail-Konten werden geladen",
        "mail_list_folders": "Mail-Ordner werden geladen",
        "mail_search": "E-Mails werden gesucht",
        "mail_get_message": "E-Mail wird gelesen",
        "mail_create_draft": "Entwurf wird erstellt",
        "mail_update_draft": "Entwurf wird geändert",
        "mail_delete_draft": "Entwurf wird gelöscht",
        "mail_send_draft": "Entwurf wird gesendet",
        "mail_send_message": "E-Mail wird gesendet",
        "mail_delete_message": "E-Mail wird gelöscht",
        "mealie_list_accounts": "Mealie-Konten werden geladen",
        "recipes_search": "Rezepte werden gesucht",
        "recipe_get": "Rezept wird gelesen",
        "meal_plan_get": "Essensplan wird gelesen",
        "meal_plan_add": "Essensplan-Eintrag wird erstellt",
        "meal_plan_update": "Essensplan-Eintrag wird geändert",
        "meal_plan_delete": "Essensplan-Eintrag wird gelöscht",
        "session_read": "Session wird gelesen",
        "session_tool_result_read": "Tool-Ergebnis wird gelesen",
        "session_browse": "Letzte Sessions werden geladen",
        "session_search": "Sessions werden durchsucht",
        "memory_retrieve": "Memory wird abgerufen",
        "memory_write": "Memory wird gespeichert",
        "update_agent_identity": "Persona wird aktualisiert",
        "update_user_profile": "Persönliches Profil wird aktualisiert",
        "process_exec": "Prozess wird ausgeführt",
        "process.exec": "Prozess wird ausgeführt",
        "shell_run": "Befehl wird ausgeführt",
        "shell.run": "Befehl wird ausgeführt",
        "git_clone": "Git-Repository wird geklont",
        "git_status": "Git-Status wird gelesen",
        "git_diff": "Git-Änderungen werden gelesen",
        "git_log": "Git-Verlauf wird gelesen",
        "git_add": "Git-Änderungen werden vorgemerkt",
        "git_commit": "Git-Commit wird erstellt",
        "list_automations": "Automationen werden geladen",
        "get_automation": "Automation wird geladen",
        "create_automation": "Automation wird erstellt",
        "edit_automation": "Automation wird geändert",
        "run_automation": "Automation wird gestartet",
        "delete_automation": "Automation wird gelöscht",
    }
    assert run_activity_label("processing_tool", tool="unknown") == "Tool wird ausgeführt"


@pytest.mark.parametrize(
    ("reasoning", "expected"),
    [
        (
            {
                "kind": "summary",
                "text": "**Planung**\nDetails\n\n**Ergebnisse prüfen**\nWeitere Details",
            },
            "Ergebnisse prüfen",
        ),
        ({"kind": "summary", "text": "**Wochenmenü planen"}, "Wochenmenü planen"),
        (
            {"kind": "summary", "text": "**Planung****Prüfung**\nDetails"},
            "Prüfung",
        ),
        ({"kind": "content", "text": "Öffentliche Überlegung"}, "Nachdenken"),
        ({"kind": "summary", "text": ""}, "Nachdenken"),
        (None, "Nachdenken"),
    ],
)
def test_reasoning_activity_labels(reasoning: dict | None, expected: str) -> None:
    assert reasoning_activity_label(reasoning) == expected


def test_reasoning_activity_label_uses_a_unicode_ellipsis_after_160_characters() -> None:
    assert reasoning_activity_label({"kind": "summary", "text": "x" * 161}) == (
        "x" * 160 + "…"
    )
