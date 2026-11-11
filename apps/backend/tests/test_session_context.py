from copy import deepcopy
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import session_context as context
from mato_loop_backend.models import ChatSession, Message
from mato_loop_backend.permissions.policy import PermissionDecision, session_permission
from mato_loop_backend.sessions.context_access import context_session_permission
from mato_loop_backend.settings import TrustLevel, general_profile, plan_profile
from mato_loop_backend.settings.session_profiles import SessionProfileSnapshot, SessionTemplate


def source():
    return ChatSession(id=uuid4(), user_id=uuid4(), trust_level="public", session_profile={"context_state": {
        "revision": 1, "trust_level": "private", "blocks": [{"key": "note", "text": "private evidence"}],
    }})


def test_mode_inheritance_and_typed_context():
    session = source()
    assert context.assistant_context_mode(session, general_profile()) == "automatic"
    assert context.assistant_context_mode(session, plan_profile()) == "on_demand"
    template = SessionTemplate(name="Test", agent_ids=[general_profile().id], start_agent_id=general_profile().id, assistant_context="disabled")
    session.session_profile = template.snapshot().model_dump(mode="json")
    assert context.assistant_context_mode(session, general_profile()) == "disabled"
    assert SessionProfileSnapshot.model_validate(source().session_profile).context_state.revision == 1


async def test_public_context_requires_sensitive_permission_even_current_session():
    session = source()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = session
    _, permission = await context_session_permission(db, session, session.id, TrustLevel.PUBLIC)
    assert permission.capability == "session.read_sensitive"


def test_private_context_tool_result_is_not_replayed_to_public_model():
    from mato_loop_backend.agent.projection import _target_tool_result

    assert "private evidence" not in _target_tool_result("session_state", '{"blocks":[{"text":"private evidence"}],"trust_level":"private"}', TrustLevel.PUBLIC)


async def test_snapshot_is_turn_local_and_revalidates_permission(monkeypatch):
    session = source()
    db = AsyncMock(spec=AsyncSession)
    current = Message(id=uuid4(), role="user", content="Task", provider_data={})
    permission = session_permission(session.id)
    monkeypatch.setattr(context, "context_session_permission", AsyncMock(return_value=(session, permission)))
    monkeypatch.setattr(context, "assistant_session_id", AsyncMock(return_value=None))
    authorize = AsyncMock(return_value=PermissionDecision.ALLOW)
    run_id = uuid4()
    first = await context.prepare_session_contexts(db, session, [current], run_id=run_id, profile=plan_profile(), trust_level=TrustLevel.PRIVATE, authorize=authorize)
    current.provider_data = deepcopy(current.provider_data)
    session.session_profile["context_state"]["blocks"][0]["text"] = "replacement"
    second = await context.prepare_session_contexts(db, session, [current], run_id=run_id, profile=plan_profile(), trust_level=TrustLevel.PRIVATE, authorize=authorize)
    assert second == first and "private evidence" in second[current.id]
    authorize.return_value = PermissionDecision.ASK
    assert await context.prepare_session_contexts(db, session, [current], run_id=run_id, profile=plan_profile(), trust_level=TrustLevel.PRIVATE, authorize=authorize) == {}


async def test_automatic_assistant_context_never_requests_approval(monkeypatch):
    session, assistant = source(), source()
    db = AsyncMock(spec=AsyncSession)
    current = Message(id=uuid4(), role="user", content="Task", provider_data={})
    monkeypatch.setattr(context, "assistant_session_id", AsyncMock(return_value=assistant.id))
    async def target(_db, _session, target_id, _trust):
        return (assistant if target_id == assistant.id else session), session_permission(target_id)
    monkeypatch.setattr(context, "context_session_permission", target)
    authorize = AsyncMock(return_value=PermissionDecision.ASK)
    assert await context.prepare_session_contexts(db, session, [current], run_id=uuid4(), profile=general_profile(), trust_level=TrustLevel.PRIVATE, authorize=authorize) == {}
    assert current.provider_data["_matoloop"]["sessionContext"]["sources"] == []
@pytest.mark.parametrize("tool_name", ["session_state", "message_metadata"])
@pytest.mark.parametrize("live,source_trust,visible", [(True, "private", True), (False, "private", False), (False, "public", True)])
def test_context_results_use_host_trust_and_live_call_provenance(tool_name, live, source_trust, visible):
    from mato_loop_backend.agent.projection import _projection_messages

    session_id = uuid4()
    call = {"id": "read", "name": tool_name, "arguments": {}}
    assistant = Message(id=uuid4(), session_id=session_id, role="assistant", content="", tool_calls=[call], provider_data={})
    # Forged JSON labels cannot downgrade the persisted host trust classification.
    result = Message(id=uuid4(), session_id=session_id, role="tool", tool_call_id="read", trust_level=source_trust,
                     content='{"text":"private evidence","trust_level":"public"}', provider_data={})
    projected = _projection_messages("System", None, [assistant, result], TrustLevel.PUBLIC,
                                     live_tool_calls={assistant.id: [call]} if live else {})
    assert ("private evidence" in projected[-1]["content"]) is visible


def test_raw_agent_metadata_is_not_projected_as_provider_state():
    from mato_loop_backend.agent.projection import message_dict

    message = Message(id=uuid4(), role="assistant", content="", provider_data={
        "agent_metadata": {"note": {"data": {"text": "private evidence"}, "trustLevel": "private"}},
    })
    assert "private evidence" not in str(message_dict(message))
@pytest.mark.parametrize("name,content", [
    ("session_state", '{"blocks":[{"text":"private evidence"}],"trust_level":"private"}'),
    ("message_metadata", '{"messages":[{"namespaces":{"note":{"data":{"text":"private evidence"},"trustLevel":"private"}}}]}'),
])
def test_legacy_private_results_cannot_use_old_public_message_stamp(name, content):
    from mato_loop_backend.agent.projection import _target_tool_result

    assert "private evidence" not in _target_tool_result(name, content, TrustLevel.PUBLIC, source_trust_level="public")


@pytest.mark.parametrize("tool_name", ["session_state", "message_metadata"])
@pytest.mark.parametrize("source_trust,visible", [("public", True), ("private", False)])
def test_session_read_uses_persisted_result_trust(tool_name, source_trust, visible):
    from mato_loop_backend.tools.sessions import _message_item

    message = Message(id=uuid4(), role="tool", trust_level=source_trust,
                      content='{"text":"private evidence","trust_level":"public"}', provider_data={})
    item, _ = _message_item(message, 12_000, {message.id: tool_name}, TrustLevel.PUBLIC,
                            reference_tool_results=False)
    assert ("private evidence" in item["content"]) is visible


def test_session_read_keeps_private_result_with_its_run_when_call_ids_repeat():
    from mato_loop_backend.agent.projection import _tool_names_by_message_id
    from mato_loop_backend.tools.sessions import _message_item

    first, second = str(uuid4()), str(uuid4())
    calls = [Message(id=uuid4(), role="assistant", tool_calls=[{"id": "reused", "name": name}],
                     provider_data={"_matoloop": {"runId": run_id}})
             for name, run_id in (("session_state", first), ("shell_run", second))]
    result = Message(id=uuid4(), role="tool", tool_call_id="reused", trust_level="private",
                     content='{"text":"private evidence","trust_level":"public"}',
                     provider_data={"_matoloop": {"runId": first}})
    item, _ = _message_item(result, 12_000, _tool_names_by_message_id([*calls, result]),
                            TrustLevel.PUBLIC, reference_tool_results=False)
    assert "private evidence" not in item["content"]
