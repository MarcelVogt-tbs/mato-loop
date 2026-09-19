from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import delete, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.applications import Starlette
from starlette.routing import Mount

from mato_loop_backend import debug_mcp
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.diagnostics import record_diagnostic_event
from mato_loop_backend.models import ChatSession, DiagnosticEvent, UserAccount
from mato_loop_backend.security import hash_token


async def test_debug_token_verifier_binds_active_admin(monkeypatch) -> None:
    user = UserAccount(
        id=uuid4(),
        username="admin",
        is_active=True,
        is_admin=True,
        debug_token_hash=hash_token("secret"),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = user

    @asynccontextmanager
    async def session_factory():
        yield db

    monkeypatch.setattr(debug_mcp, "AsyncSessionFactory", session_factory)

    access = await debug_mcp.DebugTokenVerifier().verify_token("secret")

    assert access is not None
    assert access.subject == str(user.id)
    assert access.scopes == ["debug:read"]
    statement = db.scalar.await_args.args[0].compile(dialect=postgresql.dialect())
    sql = str(statement)
    assert "user_accounts.debug_token_hash" in sql
    assert "user_accounts.is_active IS true" in sql
    assert "user_accounts.is_admin IS true" in sql
    assert hash_token("secret") in statement.params.values()


async def test_debug_token_verifier_rejects_unknown_token(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    @asynccontextmanager
    async def session_factory():
        yield db

    monkeypatch.setattr(debug_mcp, "AsyncSessionFactory", session_factory)

    assert await debug_mcp.DebugTokenVerifier().verify_token("unknown") is None


async def test_debug_tool_errors_do_not_expose_exception_text() -> None:
    async def broken() -> dict:
        raise RuntimeError("database password leaked")

    result = await debug_mcp._safe_tool(broken)()

    assert result == {
        "error": {"code": "debug.queryFailed", "message": "The debug query failed."}
    }


def test_debug_list_content_is_bounded() -> None:
    payload = debug_mcp._compact_message(
        {"content": "x" * 3_000, "toolCalls": [{"arguments": "y" * 3_000}]}
    )

    assert len(payload["content"]) == 2_000
    assert payload["contentLength"] == 3_000
    assert payload["toolCalls"] is None
    assert payload["detailFields"]["toolCalls"] > 3_000


async def test_unavailable_debug_mount_returns_controlled_error() -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=debug_mcp.DebugMcpMount()),
        base_url="https://matoloop.test",
    ) as client:
        response = await client.get("/mcp")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "debug.unavailable",
            "message": "The debug service is unavailable.",
        }
    }


async def test_debug_mcp_http_authenticates_and_filters_sessions(monkeypatch) -> None:
    user = UserAccount(id=uuid4(), username="admin", is_active=True, is_admin=True)
    session = ChatSession(
        id=uuid4(),
        user_id=user.id,
        title="Broken chat",
        session_type="chat",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = user
    scalar_result = MagicMock()
    scalar_result.all.return_value = [session]
    db.scalars.return_value = scalar_result

    @asynccontextmanager
    async def session_factory():
        yield db

    monkeypatch.setattr(debug_mcp, "AsyncSessionFactory", session_factory)
    server, mcp_app = debug_mcp.create_debug_mcp()
    app = Starlette(routes=[Mount("/api/debug", app=mcp_app)])
    transport = httpx.ASGITransport(app=app)

    async with server.session_manager.run():
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://matoloop.test",
            headers={"Authorization": "Bearer secret"},
        ) as client:
            async with streamable_http_client(
                "https://matoloop.test/api/debug/mcp", http_client=client
            ) as (read, write, _session_id):
                async with ClientSession(read, write) as mcp_session:
                    await mcp_session.initialize()
                    result = await mcp_session.call_tool("list_sessions", {"limit": 1})

    assert result.isError is False
    assert result.structuredContent == {
        "items": [
            {
                "id": str(session.id),
                "title": "Broken chat",
                "type": "chat",
                "projectId": None,
                "parentSessionId": None,
                "currentDirectory": None,
                "createdAt": session.created_at.isoformat(),
                "updatedAt": session.updated_at.isoformat(),
            }
        ]
    }
    statement = db.scalars.await_args.args[0].compile(dialect=postgresql.dialect())
    assert user.id in statement.params.values()


async def test_debug_mcp_postgres_isolates_two_admins(request) -> None:
    if not request.config.getoption("--debug-db"):
        pytest.skip("requires --debug-db")
    first = UserAccount(
        username=f"debug-{uuid4()}",
        is_active=True,
        is_admin=True,
        debug_token_hash=hash_token("first-secret"),
    )
    second = UserAccount(
        username=f"debug-{uuid4()}",
        is_active=True,
        is_admin=True,
    )
    async with AsyncSessionFactory() as db:
        db.add_all([first, second])
        await db.flush()
        own = ChatSession(user_id=first.id, title="Own diagnostic session")
        foreign = ChatSession(user_id=second.id, title="Foreign diagnostic session")
        db.add_all([own, foreign])
        await db.commit()

    await record_diagnostic_event(
        first.id, "test.recorded", component="test", session_id=own.id
    )
    await record_diagnostic_event(
        second.id, "test.not-recorded", component="test", session_id=foreign.id
    )
    await record_diagnostic_event(
        first.id, "test.cross-owner", component="test", session_id=foreign.id
    )
    async with AsyncSessionFactory() as db:
        event_types = set(
            (
                await db.scalars(
                    select(DiagnosticEvent.event_type).where(
                        DiagnosticEvent.user_id.in_([first.id, second.id])
                    )
                )
            ).all()
        )
    assert event_types == {"test.recorded"}

    server, mcp_app = debug_mcp.create_debug_mcp()
    app = Starlette(routes=[Mount("/api/debug", app=mcp_app)])
    transport = httpx.ASGITransport(app=app)
    try:
        async with server.session_manager.run():
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://matoloop.test",
                headers={"Authorization": "Bearer first-secret"},
            ) as client:
                async with streamable_http_client(
                    "https://matoloop.test/api/debug/mcp", http_client=client
                ) as (read, write, _session_id):
                    async with ClientSession(read, write) as mcp_session:
                        await mcp_session.initialize()
                        result = await mcp_session.call_tool("list_sessions", {"limit": 100})
        titles = {item["title"] for item in result.structuredContent["items"]}
        assert "Own diagnostic session" in titles
        assert "Foreign diagnostic session" not in titles
    finally:
        async with AsyncSessionFactory() as db:
            await db.execute(delete(UserAccount).where(UserAccount.id.in_([first.id, second.id])))
            await db.commit()
