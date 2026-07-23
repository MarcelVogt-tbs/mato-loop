import pytest

from mato_loop_backend.privacy import (
    PseudonymizationError,
    RequestPseudonymizer,
    StreamingPseudonymRestorer,
)
from mato_loop_backend.privacy import pseudonymization as pseudonymization_module


def test_pseudonymizer_reuses_markers_and_restores_nested_values() -> None:
    pseudonymizer = RequestPseudonymizer()
    protected = pseudonymizer.protect_value(
        {
            "recipient": "anna@example.com",
            "items": ["anna@example.com", "password=supersecret"],
        }
    )

    assert "anna@example.com" not in str(protected)
    assert "supersecret" not in str(protected)
    assert protected["recipient"] == protected["items"][0]
    assert pseudonymizer.restore_value(protected) == {
        "recipient": "anna@example.com",
        "items": ["anna@example.com", "password=supersecret"],
    }


def test_pseudonymizer_uses_local_german_named_entity_recognition() -> None:
    pseudonymizer = RequestPseudonymizer()
    source = "Anna Müller wohnt in Berlin."

    protected = pseudonymizer.protect_text(source)

    assert "Anna Müller" not in protected
    assert "Berlin" not in protected
    assert pseudonymizer.restore_text(protected) == source


def test_pseudonymizer_preserves_unavailable_error_code(monkeypatch) -> None:
    def unavailable():
        raise PseudonymizationError("privacy.pseudonymizationUnavailable")

    monkeypatch.setattr(pseudonymization_module, "_analyzer", unavailable)

    with pytest.raises(
        PseudonymizationError, match="privacy.pseudonymizationUnavailable"
    ):
        RequestPseudonymizer().protect_text("anna@example.com")


def test_streaming_restorer_handles_marker_boundaries() -> None:
    pseudonymizer = RequestPseudonymizer()
    protected = pseudonymizer.protect_text("anna@example.com")
    restorer = StreamingPseudonymRestorer(pseudonymizer)

    chunks = [restorer.feed(character) for character in "Kontakt: " + protected]
    chunks.append(restorer.flush())

    assert "".join(chunks) == "Kontakt: anna@example.com"
