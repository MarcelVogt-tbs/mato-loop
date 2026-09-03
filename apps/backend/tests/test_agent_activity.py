import pytest

from mato_loop_backend.agent.activity import (
    TOOL_ACTIVITY_LABELS,
    reasoning_activity_label,
    run_activity_label,
)


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
        ({}, "Verarbeite Anfrage"),
        ({"model_request": 1}, "Verarbeite Anfrage"),
        ({"model_request": 2}, "Verarbeite Tool-Ergebnisse"),
        (
            {
                "model_request": 2,
                "provider_attempt": 2,
                "provider_attempt_total": 3,
            },
            "Verarbeite Tool-Ergebnisse",
        ),
        (
            {"provider_attempt": 4, "provider_attempt_total": 3},
            "Verarbeite Anfrage",
        ),
    ],
)
def test_run_activity_labels_model_requests(details: dict, expected: str) -> None:
    assert run_activity_label("waiting_model", **details) == expected


def test_context_preparation_uses_the_upcoming_model_request() -> None:
    assert (
        run_activity_label("preparing_context", model_request=2)
        == "Verarbeite Tool-Ergebnisse"
    )


def test_tool_activity_labels_are_short_and_specific() -> None:
    assert TOOL_ACTIVITY_LABELS["web_search"] == "Websuche"
    assert TOOL_ACTIVITY_LABELS["read_file"] == "Datei lesen"
    assert TOOL_ACTIVITY_LABELS["shell_run"] == "Shell-Befehl ausführen"
    assert TOOL_ACTIVITY_LABELS["calendar_create_event"] == "Termin erstellen"
    assert run_activity_label("processing_tool", tool="unknown") == "Tool ausführen"


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
