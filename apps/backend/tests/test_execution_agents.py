from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes.execution_agents import (
    ExecutionAgentRequest,
    create_execution_agent,
)
from mato_loop_backend.models import ExecutionAgent, ExecutionAgentRoot, UserAccount
from mato_loop_backend.security import hash_token


def test_execution_agent_models_store_only_token_hash_and_relational_roots() -> None:
    agent = ExecutionAgent(
        id=uuid4(),
        user_id=uuid4(),
        name="Desktop",
        platform="linux",
        token_hash=hash_token("strong one-time token"),
    )
    root = ExecutionAgentRoot(
        execution_agent_id=agent.id,
        id=uuid4(),
        label="Projects",
        revision="a" * 64,
    )

    assert agent.token_hash != "strong one-time token"
    assert len(agent.token_hash) == 64
    assert root.execution_agent_id == agent.id
    assert ExecutionAgent.__table__.c.operations.type.compile(
        dialect=postgresql.dialect()
    ) == "JSONB"


async def test_create_returns_one_time_token_and_frontend_envelope() -> None:
    user = UserAccount(id=uuid4(), username="test", password_hash="hash")
    db = AsyncMock(spec=AsyncSession)

    async def assign_server_values() -> None:
        agent = db.add.call_args.args[0]
        agent.id = uuid4()
        agent.revision = 1
        agent.operations = []
        agent.roots = []
        agent.created_at = datetime.now(UTC)
        agent.updated_at = datetime.now(UTC)

    db.flush.side_effect = assign_server_values

    response = await create_execution_agent(
        ExecutionAgentRequest(name="Desktop"), user, db
    )

    assert response["agent"]["name"] == "Desktop"
    assert response["agent"]["platform"] is None
    assert response["token"]
    assert response["websocketUrl"] == "/api/execution-agents/connect"
    assert db.add.call_args.args[0].token_hash == hash_token(response["token"])
    db.commit.assert_awaited_once()
