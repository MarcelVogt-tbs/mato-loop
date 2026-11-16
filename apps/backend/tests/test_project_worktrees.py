import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import project_worktrees as context
from mato_loop_backend.agent.projection import _projection_messages
from mato_loop_backend.models import ChatSession, ExecutionAgent, Message, Project
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.providers import service as provider_module
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig, TrustLevel


@pytest.fixture
def setup(monkeypatch):
    owner, computer = uuid4(), uuid4()
    project = Project(id=uuid4(), user_id=owner, default_execution_agent_id=computer,
                      default_current_directory="/repo")
    session = ChatSession(id=uuid4(), user_id=owner, project_id=project.id,
                          preferred_execution_agent_id=computer, current_directory="/trees/a")
    source = ChatSession(id=uuid4(), user_id=owner, project_id=project.id, title="Original task",
                         current_directory="/trees/a/subdir", trust_level="private")
    agent = ExecutionAgent(id=computer, user_id=owner, home_directory="/", home_filesystem_policy="auto")
    current = Message(id=uuid4(), session_id=session.id, role="user", content="Task", attachments=[], provider_data={})
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = agent
    db.scalars.return_value = [source]
    inspect = AsyncMock(return_value=SimpleNamespace(
        repository="/repo", project_worktree_directory="/trees",
        worktrees=[SimpleNamespace(path="/trees/b", main=False), SimpleNamespace(path="/repo", main=True),
                   SimpleNamespace(path="/trees/a", main=False)],
    ))
    monkeypatch.setattr(context, "inspect_repository", inspect)
    return SimpleNamespace(db=db, project=project, session=session, source=source, current=current,
                           agent=agent, inspect=inspect, authorize=AsyncMock(return_value=PermissionDecision.ALLOW))


async def prepare(p, records=None, run_id=None, trust=TrustLevel.PRIVATE):
    return await context.prepare_project_worktrees(
        p.db, p.session, p.project, records or [p.current], run_id=run_id or uuid4(),
        trust_level=trust, authorize=p.authorize,
    )


async def test_topology_is_frozen_per_turn_and_permission_is_live(setup):
    p = setup
    run = uuid4()
    first = await prepare(p, run_id=run)
    p.current.provider_data = deepcopy(p.current.provider_data)
    p.source.title = "New title"
    second = await prepare(p, run_id=run)
    assert second == first and "Original task" in second[p.current.id]
    assert p.inspect.await_count == 1
    assert first[p.current.id].index('"path":"a"') < first[p.current.id].index('"path":"b"')
    assert p.authorize.await_count == 4
    p.authorize.return_value = PermissionDecision.ASK
    assert await prepare(p, run_id=run) == {}


async def test_historical_private_titles_do_not_become_public_and_deleted_sources_disappear(setup):
    p = setup
    await prepare(p)
    followup = Message(id=uuid4(), role="user", content="Next", provider_data={})
    p.source.trust_level = "public"
    result = await prepare(p, [p.current, followup], trust=TrustLevel.PUBLIC)
    assert "Original task" not in result[p.current.id]
    p.db.scalars.return_value = []
    assert all("Original task" not in text for text in (await prepare(p, [p.current, followup])).values())
    statement = str(p.db.scalars.call_args.args[0])
    assert all(field in statement for field in ("user_id", "project_id", "preferred_execution_agent_id", "delete_at", "parent_session_id"))


async def test_revoked_computer_and_other_project_snapshots_are_not_replayed(setup):
    p = setup
    await prepare(p)
    p.current.provider_data["_matoloop"]["projectWorktrees"]["project_id"] = str(uuid4())
    # A new current turn must not import an older snapshot from another project.
    followup = Message(id=uuid4(), role="user", content="Next", provider_data={})
    assert p.current.id not in await prepare(p, [p.current, followup])
    p.db.scalar.return_value = None
    assert await prepare(p) == {}


async def test_oversized_topology_is_bounded_and_unavailable_inspection_is_not_retried(setup, monkeypatch):
    p = setup
    monkeypatch.setattr(context, "MAX_CHARACTERS", 650)
    first = await prepare(p)
    assert len(first[p.current.id]) <= 650 and '"incomplete":true' in first[p.current.id]
    p.inspect.side_effect = context.SessionServiceError(409, "executionAgent.unavailable")
    run = uuid4()
    second = await prepare(p, run_id=run)
    calls = p.inspect.await_count
    assert '"unavailable":true' in second[p.current.id]
    await prepare(p, run_id=run)
    assert p.inspect.await_count == calls


@pytest.mark.parametrize("serialize,key", [(provider_module._chat_body, "messages"), (provider_module._responses_body, "input")])
@pytest.mark.parametrize("changed", [False, True])
async def test_serialized_prefix_survives_tool_round_reload_followup_and_compaction(setup, serialize, key, changed):
    p = setup
    run = uuid4()
    contexts = await prepare(p, run_id=run)
    model = ModelConfig(provider_id=uuid4(), model_id="test")
    provider = LlmProviderConfig(id=model.provider_id, name="Test", adapter_type="openai-compatible")

    async def body(records, snapshots, live_calls=None):
        messages = _projection_messages("Stable system", None, records, TrustLevel.PRIVATE,
                                       session_contexts={key: {"worktrees": value, "session": "Saved note"}
                                                         for key, value in snapshots.items()},
                                       live_tool_calls=live_calls)
        prepared = await ProviderService.prepare_request(provider=provider, model=model, tools=[], messages=messages,
                                                        pseudonymization_enabled=False)
        return serialize(prepared.request)

    first = await body([p.current], contexts)
    calls = [{"id": "pwd", "name": "shell_run", "arguments": {"command": "pwd"}}]
    assistant = Message(id=uuid4(), session_id=p.session.id, role="assistant", content="", tool_calls=calls,
                        attachments=[], provider_data={})
    result = Message(id=uuid4(), session_id=p.session.id, role="tool", content='{"stdout":"/trees/a"}',
                     tool_call_id="pwd", trust_level="private", attachments=[], provider_data={})
    records = [p.current, assistant, result]
    tool_round = await body(records, await prepare(p, records, run_id=run), {assistant.id: calls})
    assert tool_round[key][:len(first[key])] == first[key]
    p.current.provider_data = json.loads(json.dumps(p.current.provider_data, sort_keys=True))
    assistant.tool_calls = json.loads(json.dumps(calls, sort_keys=True))
    if changed:
        p.source.title = "Updated task"
    followup = Message(id=uuid4(), session_id=p.session.id, role="user", content="Next", attachments=[], provider_data={})
    records.append(followup)
    later_contexts = await prepare(p, records)
    later = await body(records, later_contexts)
    assert later[key][:len(tool_round[key])] == tool_round[key]
    assert str(later[key]).count("Project worktree snapshot:") == (2 if changed else 1)
    assert str(later[key]).count("Saved note") == 2
    # If compaction removes the originating turn, the latest saved snapshot becomes visible.
    assert "Project worktree snapshot:" in str(await body([followup], later_contexts))
