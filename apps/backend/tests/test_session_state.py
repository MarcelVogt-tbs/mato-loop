import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ChatSession
from mato_loop_backend.permissions.policy import PermissionDecision, session_permission
from mato_loop_backend.settings import TrustLevel
from mato_loop_backend.tools import session_state as state


def tool_context():
    session = ChatSession(id=uuid4(), user_id=uuid4(), session_profile={"message_size": "small"})
    return SimpleNamespace(
        db=AsyncMock(spec=AsyncSession), session=session, run_id=uuid4(),
        assistant_message_id=uuid4(),
        model_trust_level=TrustLevel.PRIVATE,
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
    )


@pytest.mark.parametrize("payload", [
    {"operation": "update_context", "blocks": []},
    {"operation": "update_context", "expected_revision": 0, "blocks": [{"key": "a", "text": "x"}] * 2},
    {"operation": "update_context", "expected_revision": 0, "blocks": [{"key": "a", "text": "x" * 12_000}]},
    {"operation": "update_context", "expected_revision": 0, "blocks": [], "session_id": str(uuid4())},
    {"operation": "record_receipts"},
    {"operation": "read_receipts"},
    {"operation": "read", "_matoloop": {"runId": "invented"}},
])
def test_state_rejects_unbounded_ambiguous_or_authority_input(payload):
    with pytest.raises(ValidationError):
        state.SessionStateArguments.model_validate(payload)


async def test_context_roundtrip_preserves_profile_and_rejects_stale_revision(monkeypatch):
    context = tool_context()
    context.db.scalar.return_value = context.session
    monkeypatch.setattr(state.tools_access, "allow", AsyncMock(return_value=True))
    arguments = state.SessionStateArguments(operation="update_context", expected_revision=0, blocks=[state.ContextBlock(key="handoff", text="Untrusted notes")])
    await state._session_state(arguments, context)
    context.db.commit.assert_awaited_once()
    reloaded = ChatSession(session_profile=json.loads(json.dumps(context.session.session_profile)))
    assert (state.session_context_state(reloaded).revision, reloaded.session_profile["message_size"]) == (1, "small")
    result = await state._session_state(arguments, context)
    assert json.loads(result.content)["error"]["code"] == "http.conflict"


@pytest.mark.parametrize("replacement", [[], [{"key": "keep", "text": "still here"}]])
async def test_context_clear_checks_independent_delete_permission(monkeypatch, replacement):
    context = tool_context()
    context.session.session_profile[state.CONTEXT_STATE_KEY] = {"revision": 0, "blocks": [{"key": "remove", "text": "old"}]}
    context.db.scalar.return_value = context.session
    allow = AsyncMock(side_effect=[True, True, False])
    monkeypatch.setattr(state.tools_access, "allow", allow)
    result = await state._session_state(state.SessionStateArguments(operation="update_context", expected_revision=0, blocks=replacement), context)
    assert result.is_error and allow.call_args.args[1].capability == "session.state.delete"
    context.db.flush.assert_not_awaited()


async def test_denied_write_does_not_mutate_session(monkeypatch):
    context = tool_context()
    monkeypatch.setattr(state.tools_access, "allow", AsyncMock(return_value=False))
    result = await state._session_state(state.SessionStateArguments(operation="update_context", expected_revision=0, blocks=[state.ContextBlock(key="note", text="text")]), context)
    assert result.is_error
    context.db.scalar.assert_not_awaited()


async def test_context_read_revalidates_source_trust_after_approval(monkeypatch):
    context = tool_context()
    target_id = uuid4()
    permission = AsyncMock(side_effect=[
        (context.session, session_permission(target_id)),
        (context.session, session_permission(target_id, private_access=True)),
    ])
    monkeypatch.setattr(state, "session_read_permission", permission)
    allow = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(state.tools_access, "allow", allow)
    assert await state.authorized_context_session(context, target_id) is None
    assert allow.call_args.args[1].capability == "session.read_sensitive"


def test_state_tool_registered_with_independent_action_categories():
    from mato_loop_backend.tools.catalog import tool_registry

    assert {key: tool_registry.actions_by_capability[key].category for key in ("session.state.write", "session.state.delete")} == {"session.state.write": "write", "session.state.delete": "delete"}


async def test_state_preparation_declares_write_and_delete_without_mutating():
    from mato_loop_backend.tools.permissions import builtin_permissions

    context = tool_context()
    context.db.scalar.return_value = context.session
    requests = await builtin_permissions(state.SessionStateArguments(operation="update_context", expected_revision=0, blocks=[]), context, "session_state")
    assert {request.capability for request in requests} == {"session.state.write", "session.state.delete", "session.read"}
    context.db.commit.assert_not_awaited()
async def test_public_replacement_requires_sensitive_read(monkeypatch):
    context = tool_context()
    context.model_trust_level = TrustLevel.PUBLIC
    context.session.session_profile[state.CONTEXT_STATE_KEY] = {
        "revision": 0, "trust_level": "private", "blocks": [{"key": "note", "text": "private evidence"}],
    }
    context.db.scalar.return_value = context.session
    allow = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(state.tools_access, "allow", allow)
    result = await state._session_state(state.SessionStateArguments(
        operation="update_context", expected_revision=0, blocks=[state.ContextBlock(key="note", text="replacement")],
    ), context)
    assert result.is_error and allow.call_args.args[1].capability == "session.read_sensitive"
    context.db.commit.assert_not_awaited()


async def test_context_write_revoked_during_delete_approval_does_not_lock_or_persist(monkeypatch):
    context = tool_context()
    context.db.scalar.return_value = context.session
    monkeypatch.setattr(state.tools_access, "allow", AsyncMock(return_value=True))
    context.ask_handler.return_value = PermissionDecision.DENY
    result = await state._session_state(state.SessionStateArguments(operation="update_context", expected_revision=0, blocks=[]), context)
    assert result.is_error and not any(call.args[0]._for_update_arg is not None for call in context.db.scalar.call_args_list)
    context.db.commit.assert_not_awaited()
