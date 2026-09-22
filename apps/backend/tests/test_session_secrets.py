from uuid import UUID, uuid4

from mato_loop_backend.agent.session_secrets import SessionSecretStore


def test_session_secrets_are_reusable_isolated_and_clearable() -> None:
    store = SessionSecretStore()
    session_id = uuid4()
    other_session_id = uuid4()

    references = store.put(
        session_id,
        {"TOKEN": "exact-secret"},
        {"TOKEN": "Token for deployments"},
    )
    reference = references["TOKEN"]
    secret_id = reference.removeprefix("session-secret:")

    assert references == store.references(session_id)
    assert store.prompt_entries(session_id) == {
        "TOKEN": {
            "valueRef": reference,
            "description": "Token for deployments",
        }
    }
    assert "exact-secret" not in repr(store)
    assert store.resolve(session_id, UUID(secret_id)) == "exact-secret"
    assert store.resolve(other_session_id, UUID(secret_id)) is None

    store.clear_session(session_id)

    assert store.references(session_id) == {}
