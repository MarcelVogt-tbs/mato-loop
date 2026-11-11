import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import ChatSession, Message
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.settings import TrustLevel
from mato_loop_backend.tools import message_metadata as metadata


def tool_context(monkeypatch):
    context = SimpleNamespace(
        db=AsyncMock(spec=AsyncSession), session=ChatSession(id=uuid4(), user_id=uuid4()),
        run_id=uuid4(), assistant_message_id=uuid4(), model_trust_level=TrustLevel.PRIVATE,
        ask_handler=AsyncMock(return_value=PermissionDecision.ALLOW),
    )
    monkeypatch.setattr(metadata.tools_access, "allow", AsyncMock(return_value=True))
    return context


def messages(context, *items):
    context.db.scalars.return_value = Mock(all=Mock(return_value=list(items)))


def assistant(context, **data):
    return Message(id=context.assistant_message_id, role="assistant", session_id=context.session.id,
                   trust_level="public", provider_data={"_matoloop": {"runId": str(context.run_id)}, **data})


@pytest.mark.parametrize("payload", [
    {"operation": "write", "namespace": "_matoloop", "data": {}},
    {"operation": "write", "namespace": "provider_data", "data": {}},
    {"operation": "write", "namespace": "items", "data": {}, "message_id": str(uuid4())},
    {"operation": "write", "namespace": "items", "data": {"a": "ü" * 4096}},
    {"operation": "write", "namespace": "items", "data": {str(i): i for i in range(33)}},
    {"operation": "write", "namespace": "items", "data": {"a": float("nan")}},
    {"operation": "read", "data": {}},
    {"session_id": str(uuid4())},
])
def test_rejects_host_targets_unbounded_or_ambiguous_input(payload):
    with pytest.raises(ValidationError):
        metadata.MessageMetadataArguments.model_validate(payload)


async def test_write_persists_user_annotation_and_preserves_host_data(monkeypatch):
    context = tool_context(monkeypatch)
    message = assistant(context, usage={"tokens": 1})
    messages(context, message)
    context.db.scalar.return_value = message
    result = await metadata._message_metadata(metadata.MessageMetadataArguments(
        operation="write", namespace="processed", data={"items": [{"id": "user-defined", "version": 2}]},
    ), context)
    context.db.commit.assert_awaited_once()
    assert json.loads(json.dumps(message.provider_data)) == {
        "_matoloop": {"runId": str(context.run_id)}, "usage": {"tokens": 1},
        "agent_metadata": {"processed": {"data": {"items": [{"id": "user-defined", "version": 2}]},
                                           "trustLevel": "private"}},
    }
    assert json.loads(result.content)["messageId"] == str(message.id)
    sql = str(context.db.scalars.call_args_list[0].args[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert all(fragment in sql for fragment in [str(context.session.id), str(context.session.user_id),
                                              str(context.assistant_message_id), "messages.role = 'assistant'"])


@pytest.mark.parametrize("failure", ["permission", "run", "namespace_limit"])
async def test_rejected_write_does_not_persist(monkeypatch, failure):
    context = tool_context(monkeypatch)
    message = assistant(context)
    if failure == "permission":
        metadata.tools_access.allow.return_value = False
    elif failure == "run":
        message.provider_data["_matoloop"]["runId"] = str(uuid4())
    else:
        message.provider_data["agent_metadata"] = {str(i): {} for i in range(metadata.MAX_NAMESPACES)}
    messages(context, message)
    context.db.scalar.return_value = message
    result = await metadata._message_metadata(metadata.MessageMetadataArguments(operation="write", namespace="new", data={}), context)
    assert result.is_error
    context.db.commit.assert_not_awaited()


async def test_read_paginates_and_filters_namespace(monkeypatch):
    context = tool_context(monkeypatch)
    page = [assistant(context, agent_metadata={"processed": {"data": {"id": i}}, "other": {}}) for i in range(3)]
    for item in page:
        item.id = uuid4()
    messages(context, *page)
    result = await metadata._message_metadata(metadata.MessageMetadataArguments(namespace="processed", limit=2), context)
    body = json.loads(result.content)
    assert (len(body["messages"]), body["nextBeforeMessageId"], list(body["messages"][0]["namespaces"])) == (2, str(page[1].id), ["processed"])


async def test_public_model_sensitive_read_revalidates_after_consent(monkeypatch):
    context = tool_context(monkeypatch)
    context.model_trust_level = TrustLevel.PUBLIC
    public = assistant(context, agent_metadata={"note": {"data": {}, "trustLevel": "public"}})
    private = assistant(context, agent_metadata={"note": {"data": {}, "trustLevel": "private"}})
    context.db.scalars.side_effect = [Mock(all=Mock(return_value=[item])) for item in [public, private]]
    metadata.tools_access.allow.side_effect = [True, False]
    result = await metadata._message_metadata(metadata.MessageMetadataArguments(), context)
    assert result.is_error and metadata.tools_access.allow.call_args.args[1].capability == "session.read_sensitive"


async def test_tool_registration_and_permission_preparation(monkeypatch):
    from mato_loop_backend.tools.catalog import tool_registry
    from mato_loop_backend.tools.permissions import builtin_permissions

    context = tool_context(monkeypatch)
    requests = await builtin_permissions(metadata.MessageMetadataArguments(operation="write", namespace="note", data={}), context, "message_metadata")
    assert requests[0].capability == "session.state.write"
    assert tool_registry._definitions["message_metadata"].actions == metadata.MESSAGE_METADATA.actions
    context.db.commit.assert_not_awaited()
async def test_sensitive_metadata_read_marks_result_private(monkeypatch):
    context = tool_context(monkeypatch)
    context.model_trust_level = TrustLevel.PUBLIC
    messages(context, assistant(context, agent_metadata={"note": {"data": {"text": "private evidence"}, "trustLevel": "private"}}))
    result = await metadata._message_metadata(metadata.MessageMetadataArguments(), context)
    assert result.trust_level == TrustLevel.PRIVATE and "private evidence" in result.content


async def test_metadata_write_revoked_during_read_approval_does_not_lock_or_persist(monkeypatch):
    context = tool_context(monkeypatch)
    messages(context, assistant(context))
    context.ask_handler.return_value = PermissionDecision.DENY
    result = await metadata._message_metadata(metadata.MessageMetadataArguments(operation="write", namespace="note", data={}), context)
    assert result.is_error
    context.db.scalar.assert_not_awaited()
    context.db.commit.assert_not_awaited()
