from uuid import uuid4

from mato_loop_backend.sessions import presence


def test_session_presence_is_user_scoped_and_expires(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(presence.time, "monotonic", lambda: now)
    presence._active_sessions.clear()
    user_id = uuid4()
    session_id = uuid4()

    presence.mark_session_present(user_id, session_id)

    assert presence.session_is_present(user_id, session_id)
    assert not presence.session_is_present(uuid4(), session_id)
    now += presence.SESSION_PRESENCE_TTL_SECONDS
    assert not presence.session_is_present(user_id, session_id)
