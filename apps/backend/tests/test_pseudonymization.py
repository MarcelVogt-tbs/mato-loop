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


def test_pseudonymizer_preserves_names_locations_and_technical_terms() -> None:
    pseudonymizer = RequestPseudonymizer()
    source = (
        "Anna Müller nutzt docker compose in Berlin mit lemonade, llama und modelmanager."
    )

    protected = pseudonymizer.protect_text(source)

    assert protected == source
    assert pseudonymizer.restore_text(protected) == source


@pytest.mark.parametrize(
    "sensitive_value",
    [
        "anna@example.com",
        "192.0.2.1",
        "DE89370400440532013000",
        "4111 1111 1111 1111",
        "+49 30 123456",
    ],
)
def test_pseudonymizer_protects_valid_identifiers(sensitive_value: str) -> None:
    pseudonymizer = RequestPseudonymizer()

    protected = pseudonymizer.protect_text(f"Wert: {sensitive_value}")

    assert sensitive_value not in protected
    assert pseudonymizer.restore_text(protected) == f"Wert: {sensitive_value}"


def test_pseudonymizer_protects_structured_secret_and_complete_private_key() -> None:
    pseudonymizer = RequestPseudonymizer()
    key_label = "PRIVATE KEY"
    private_key = "\n".join(
        (
            f"-----BEGIN {key_label}-----",
            "ZmFrZS1wcml2YXRlLWtleS1tYXRlcmlhbA==",
            f"-----END {key_label}-----",
        )
    )
    github_token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    source = f'github_token: "{github_token}"\n{private_key}'

    protected = pseudonymizer.protect_text(source)

    assert "ghp_" not in protected
    assert "BEGIN PRIVATE KEY" not in protected
    assert "ZmFrZS" not in protected
    assert pseudonymizer.restore_text(protected) == source


def test_protect_messages_adds_marker_instruction_only_when_needed() -> None:
    pseudonymizer = RequestPseudonymizer()

    protected = pseudonymizer.protect_messages(
        [
            {"role": "system", "content": "Systemvertrag"},
            {"role": "user", "content": "Kontakt: anna@example.com"},
        ]
    )

    assert protected[0]["content"].startswith("Systemvertrag\n\nHost privacy placeholders")
    assert "anna@example.com" not in protected[1]["content"]

    untouched = RequestPseudonymizer().protect_messages(
        [{"role": "system", "content": "Systemvertrag"}]
    )
    assert untouched == [{"role": "system", "content": "Systemvertrag"}]


def test_pseudonymizer_preserves_unavailable_error_code(monkeypatch) -> None:
    def unavailable():
        raise PseudonymizationError("privacy.pseudonymizationUnavailable")

    monkeypatch.setattr(pseudonymization_module, "_recognizers", unavailable)

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


def test_streaming_restorer_rejects_unknown_request_marker() -> None:
    pseudonymizer = RequestPseudonymizer()
    protected = pseudonymizer.protect_text("anna@example.com")
    unknown = protected.replace("0001", "9999")
    restorer = StreamingPseudonymRestorer(pseudonymizer)

    with pytest.raises(PseudonymizationError, match="privacy.pseudonymizationFailed"):
        restorer.feed(unknown)
