from types import SimpleNamespace

import pytest

from mato_loop_backend.agent.activity import (
    TOOL_ACTIVITY_LABELS,
    reasoning_activity_label,
    run_activity_label,
)
from mato_loop_backend.agent.tool_display import tool_display


@pytest.mark.parametrize(
    ("phase", "expected"),
    [
        ("submitting", "Verarbeite Anfrage"),
        ("queued", "Verarbeite Anfrage"),
        ("preparing_context", "Verarbeite Anfrage"),
        ("compacting_context", "Kontext verdichten"),
        ("waiting_approval", "Freigabe erforderlich"),
        ("waiting_input", "Antwort erforderlich"),
        ("finalizing", "Abschließen"),
        ("reconnecting", "Neu verbinden"),
        ("unknown", "Agent arbeitet"),
    ],
)
def test_run_activity_labels_all_static_phases(phase: str, expected: str) -> None:
    assert run_activity_label(phase) == expected


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({}, None),
        ({"model_request": 1}, None),
        ({"model_request": 2}, None),
        (
            {
                "model_request": 2,
                "provider_attempt": 2,
                "provider_attempt_total": 3,
            },
            None,
        ),
        (
            {"provider_attempt": 4, "provider_attempt_total": 3},
            None,
        ),
    ],
)
def test_run_activity_labels_model_requests(details: dict, expected: str) -> None:
    assert run_activity_label("waiting_model", **details) == expected


def test_context_preparation_uses_the_upcoming_model_request() -> None:
    assert run_activity_label("preparing_context", model_request=2) == "Verarbeite Anfrage"


def test_tool_activity_labels_are_short_and_specific() -> None:
    assert TOOL_ACTIVITY_LABELS["web_search"] == "Im Web suchen"
    assert TOOL_ACTIVITY_LABELS["read_file"] == "Datei lesen"
    assert TOOL_ACTIVITY_LABELS["shell_run"] == "Shell-Befehl ausführen"
    assert TOOL_ACTIVITY_LABELS["calendar_create_event"] == "Termin erstellen"
    assert run_activity_label("processing_tool", tool="unknown") == "Tool ausführen"


def test_tool_display_groups_labels_summaries_and_redacts_unlisted_arguments() -> None:
    definition = SimpleNamespace(group="integrations", label="Browser-Navigation")
    navigate = tool_display(
        "browser_navigate",
        {"url": "https://example.org/path?token=secret", "content": "secret"},
        definition,
    )
    tabs = tool_display("browser_tabs", {"action": "close"}, definition)
    search = tool_display("web_search", {"query": "x" * 200, "prompt": "secret"})

    assert navigate == {
        "group": "web", "groupLabel": "Web", "icon": "language", "emoji": "🌐",
        "label": "Website aufrufen", "summary": "https://example.org/path?REDACTED",
    }
    assert tabs["icon"] == navigate["icon"]
    assert tabs["label"] == "Browser-Tab schließen"
    assert len(search["summary"]) == 140
    assert "secret" not in str((navigate, search))
    assert tool_display("private_custom_action", {})["label"] == "Tool ausführen"


def test_tool_display_supports_persisted_copy_and_move_arguments() -> None:
    assert tool_display("copy_path", {
        "source_path": "a.txt", "destination_path": "archive/a.txt",
    })["summary"] == "a.txt → archive/a.txt"
    assert tool_display("move_path", {
        "sourcePath": "old.txt", "destinationPath": "new.txt",
    })["summary"] == "old.txt → new.txt"


@pytest.mark.parametrize(
    ("name", "label", "group"),
    [
        ("coding_analyze", "Projekt analysieren", "coding"),
        ("coding_create_plan", "Plan speichern", "coding"),
        ("coding_list_plans", "Pläne anzeigen", "coding"),
        ("coding_enqueue", "Pläne ausführen", "coding"),
        ("load_skill", "Skill laden", "agent"),
        ("build_skill", "Skill speichern", "agent"),
        ("edit_skill", "Skill bearbeiten", "agent"),
        ("get_system_info", "Systeminformationen", "process"),
        ("change_directory", "Verzeichnis wechseln", "process"),
        ("render_openscad", "OpenSCAD-Modell rendern", "integrations"),
        ("knowledge_list_accounts", "Wissensdatenbanken auflisten", "integrations"),
        ("knowledge_list_books", "Bücher auflisten", "integrations"),
        ("knowledge_search", "Wissensdatenbank durchsuchen", "integrations"),
        ("knowledge_read_book", "Buchinhalt lesen", "integrations"),
        ("knowledge_read_page", "Seite lesen", "integrations"),
        ("knowledge_create_page", "Seite erstellen", "integrations"),
        ("knowledge_update_page", "Seite bearbeiten", "integrations"),
        ("knowledge_delete_page", "Seite löschen", "integrations"),
        ("knowledge_list_attachments", "Anhänge auflisten", "integrations"),
        ("knowledge_create_attachment", "Anhang hinzufügen", "integrations"),
        ("knowledge_delete_attachment", "Anhang löschen", "integrations"),
        ("knowledge_create_book", "Buch erstellen", "integrations"),
        ("knowledge_update_book", "Buch ändern", "integrations"),
        ("knowledge_delete_book", "Buch löschen", "integrations"),
        ("knowledge_create_chapter", "Kapitel erstellen", "integrations"),
        ("knowledge_update_chapter", "Kapitel ändern", "integrations"),
        ("knowledge_delete_chapter", "Kapitel löschen", "integrations"),
        ("knowledge_list_shelves", "Regale auflisten", "integrations"),
        ("knowledge_read_shelf", "Regalinhalt lesen", "integrations"),
        ("knowledge_create_shelf", "Regal erstellen", "integrations"),
        ("knowledge_update_shelf", "Regal bearbeiten", "integrations"),
        ("knowledge_delete_shelf", "Regal löschen", "integrations"),
    ],
)
def test_tool_display_legacy_fallback_covers_registered_tools(
    name: str, label: str, group: str,
) -> None:
    display = tool_display(name)
    assert display["label"] == label
    assert display["group"] == group


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
