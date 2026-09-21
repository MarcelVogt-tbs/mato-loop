from uuid import uuid4

from mato_loop_backend.sessions import presence


def test_presence_is_user_scoped_renewable_and_expires(monkeypatch) -> None:
    now = 100.0
    monkeypatch.setattr(presence.time, "monotonic", lambda: now)
    presence._active_users.clear()
    user_id = uuid4()

    presence.mark_user_present(user_id)

    assert presence.user_is_present(user_id)
    assert not presence.user_is_present(uuid4())
    now += presence.PRESENCE_TTL_SECONDS - 1
    presence.mark_user_present(user_id)
    now += 1
    assert presence.user_is_present(user_id)
    now += presence.PRESENCE_TTL_SECONDS - 1
    assert not presence.user_is_present(user_id)
