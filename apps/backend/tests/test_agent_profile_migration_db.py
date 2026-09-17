import asyncio
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import mato_loop_backend.config as config_module
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    PermissionGrant,
    SettingRecord,
    UserAccount,
)
from mato_loop_backend.settings import GENERAL_PROFILE_ID


async def _create_database(url: str, name: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.execute(text(f'CREATE DATABASE "{name}"'))
    await engine.dispose()


async def _drop_database(url: str, name: str) -> None:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    async with engine.connect() as connection:
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :name AND pid <> pg_backend_pid()"
            ),
            {"name": name},
        )
        await connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
    await engine.dispose()


async def _seed_database(url: str):
    custom_profile_id = uuid4()
    integration_id = uuid4()
    integration_profile_id = uuid5(NAMESPACE_URL, f"matoloop:tbs-desk:{integration_id}")
    user_id = uuid4()
    session_id = uuid4()
    integration_session_id = uuid4()
    queued_run_id = uuid4()
    completed_run_id = uuid4()
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as db:
        db.add(UserAccount(id=user_id, username="migration-test"))
        await db.flush()
        global_settings = await db.scalar(
            select(SettingRecord).where(
                SettingRecord.key == "agent-profiles",
                SettingRecord.user_id.is_(None),
            )
        )
        assert global_settings is not None
        global_settings.data = {
            "profiles": [{"id": str(custom_profile_id), "name": "Global"}]
        }
        db.add_all(
            [
                SettingRecord(
                    key="agent-profiles",
                    user_id=user_id,
                    data={"profiles": [{"id": str(custom_profile_id), "name": "Personal"}]},
                ),
                SettingRecord(
                    key="integrations",
                    user_id=user_id,
                    data={
                        "instances": [
                            {"id": str(integration_id), "adapter_type": "tbs-desk"}
                        ]
                    },
                ),
                ChatSession(
                    id=session_id,
                    user_id=user_id,
                    agent_profile_id=custom_profile_id,
                ),
                ChatSession(
                    id=integration_session_id,
                    user_id=user_id,
                    agent_profile_id=integration_profile_id,
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                AgentRun(
                    id=queued_run_id,
                    session_id=session_id,
                    agent_profile_id=custom_profile_id,
                    status="queued",
                ),
                AgentRun(
                    id=completed_run_id,
                    session_id=session_id,
                    agent_profile_id=custom_profile_id,
                    status="completed",
                ),
                PermissionGrant(
                    session_id=session_id,
                    capability="filesystem.read",
                    resource_type="path",
                    resource_scope="/tmp",
                    agent_profile_id=custom_profile_id,
                ),
                PermissionGrant(
                    session_id=session_id,
                    capability="filesystem.read",
                    resource_type="path",
                    resource_scope="/data",
                    agent_profile_id=GENERAL_PROFILE_ID,
                ),
                PermissionGrant(
                    session_id=integration_session_id,
                    capability="support.read",
                    resource_type="network_service",
                    resource_scope=str(integration_id),
                    agent_profile_id=integration_profile_id,
                ),
            ]
        )
    await engine.dispose()
    return (
        custom_profile_id,
        integration_profile_id,
        user_id,
        session_id,
        integration_session_id,
        queued_run_id,
        completed_run_id,
    )


async def _read_database(url: str, ids):
    (
        custom_profile_id,
        integration_profile_id,
        user_id,
        session_id,
        integration_session_id,
        queued_run_id,
        completed_run_id,
    ) = ids
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        settings = (
            await db.execute(
                select(SettingRecord).where(SettingRecord.key == "agent-profiles")
            )
        ).scalars().all()
        session_profile_id = await db.scalar(
            select(ChatSession.agent_profile_id).where(ChatSession.id == session_id)
        )
        integration_session_profile_id = await db.scalar(
            select(ChatSession.agent_profile_id).where(
                ChatSession.id == integration_session_id
            )
        )
        run_profiles = dict(
            (await db.execute(select(AgentRun.id, AgentRun.agent_profile_id))).all()
        )
        grant_profiles = set(
            (await db.scalars(select(PermissionGrant.agent_profile_id))).all()
        )
    await engine.dispose()
    global_settings = next(record for record in settings if record.user_id is None)
    personal_settings = next(record for record in settings if record.user_id == user_id)
    return {
        "global_names": [profile["name"] for profile in global_settings.data["profiles"]],
        "personal_profiles": personal_settings.data["profiles"],
        "session_profile_id": session_profile_id,
        "integration_session_profile_id": integration_session_profile_id,
        "integration_profile_id": integration_profile_id,
        "queued_profile_id": run_profiles[queued_run_id],
        "completed_profile_id": run_profiles[completed_run_id],
        "grant_profiles": grant_profiles,
        "custom_profile_id": custom_profile_id,
    }


def test_agent_profile_migration_resets_representative_data(request, monkeypatch) -> None:
    if not request.config.getoption("--profile-migration-db"):
        pytest.skip("requires the development PostgreSQL database")

    backend_dir = Path(__file__).resolve().parents[1]
    database_name = f"profile_migration_{uuid4().hex}"
    base_url = get_runtime_config().database_url
    test_url = make_url(base_url).set(database=database_name).render_as_string(hide_password=False)
    asyncio.run(_create_database(base_url, database_name))
    monkeypatch.setattr(
        config_module,
        "get_runtime_config",
        lambda: SimpleNamespace(database_url=test_url),
    )
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_dir / "alembic"))

    try:
        command.upgrade(alembic_config, "012558511db3")
        ids = asyncio.run(_seed_database(test_url))
        command.upgrade(alembic_config, "head")
        result = asyncio.run(_read_database(test_url, ids))

        assert result["global_names"] == []
        assert result["personal_profiles"] == []
        assert result["session_profile_id"] == GENERAL_PROFILE_ID
        assert result["integration_session_profile_id"] == result["integration_profile_id"]
        assert result["queued_profile_id"] == GENERAL_PROFILE_ID
        assert result["completed_profile_id"] == result["custom_profile_id"]
        assert result["grant_profiles"] == {
            GENERAL_PROFILE_ID,
            result["integration_profile_id"],
        }

        command.downgrade(alembic_config, "012558511db3")
        result = asyncio.run(_read_database(test_url, ids))

        assert result["global_names"] == ["Allgemein", "Plan", "Build"]
        assert result["personal_profiles"] == []
        assert result["session_profile_id"] == GENERAL_PROFILE_ID
        assert result["integration_session_profile_id"] == result["integration_profile_id"]
        assert result["queued_profile_id"] == GENERAL_PROFILE_ID
        assert result["completed_profile_id"] == result["custom_profile_id"]
        assert result["grant_profiles"] == {
            GENERAL_PROFILE_ID,
            result["integration_profile_id"],
        }
    finally:
        asyncio.run(_drop_database(base_url, database_name))
