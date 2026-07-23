from uuid import uuid4

from mato_loop_backend.models import ContextSummary


def test_context_summary_has_stable_message_boundary() -> None:
    session_id = uuid4()
    message_id = uuid4()
    previous_summary_id = uuid4()
    model_config_id = uuid4()

    summary = ContextSummary(
        session_id=session_id,
        covered_through_message_id=message_id,
        previous_summary_id=previous_summary_id,
        content="Bisheriger Verlauf",
        estimated_tokens=120,
        model_config_id=model_config_id,
        is_fallback=True,
    )

    assert summary.session_id == session_id
    assert summary.covered_through_message_id == message_id
    assert summary.previous_summary_id == previous_summary_id
    assert summary.model_config_id == model_config_id
    assert summary.is_fallback is True
