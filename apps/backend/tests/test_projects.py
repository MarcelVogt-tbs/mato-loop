from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.projects import (
    ProjectRequest,
    create_project,
    delete_project,
    list_projects,
    rename_project,
)
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.models import ChatSession, Project, UserAccount


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def _project(user_id) -> Project:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    return Project(id=uuid4(), user_id=user_id, name="Project", created_at=now, updated_at=now)


def test_project_model_enforces_owner_binding_and_chat_sessions() -> None:
    project_unique = next(
        constraint
        for constraint in Project.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name == "uq_projects_id_user_id"
    )
    project_fk = next(
        constraint
        for constraint in ChatSession.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
        and constraint.name == "fk_chat_sessions_project_owner"
    )
    project_check = next(
        constraint
        for constraint in ChatSession.__table__.constraints
        if isinstance(constraint, CheckConstraint)
        and constraint.name == "ck_chat_sessions_project_chat_sessions_only"
    )

    assert [column.name for column in project_unique.columns] == ["id", "user_id"]
    assert [column.name for column in project_fk.columns] == ["project_id", "user_id"]
    assert str(project_check.sqltext) == "project_id IS NULL OR session_type = 'chat'"
    assert ChatSession.project_id.nullable is True


def test_project_name_is_trimmed_and_bounded() -> None:
    assert ProjectRequest(name="  Work  ").name == "Work"
    with pytest.raises(ValidationError):
        ProjectRequest(name="   ")
    with pytest.raises(ValidationError):
        ProjectRequest(name="x" * 241)


async def test_project_list_is_owner_scoped_and_counts_sessions() -> None:
    user = _user()
    project = _project(user.id)
    result = MagicMock()
    result.all.return_value = [(project, 3)]
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = result

    response = await list_projects(user, db)

    assert response.items[0].session_count == 3
    statement = db.execute.await_args.args[0]
    assert user.id in statement.compile().params.values()
    assert "LEFT OUTER JOIN chat_sessions" in str(statement)


async def test_create_and_rename_project_commit_owner_scoped_changes() -> None:
    user = _user()
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    db = AsyncMock(spec=AsyncSession)

    def persist(project: Project) -> None:
        project.id = uuid4()
        project.created_at = now
        project.updated_at = now

    db.add.side_effect = persist
    created = await create_project(ProjectRequest(name=" New "), user, db)

    project = _project(user.id)
    db.scalar.side_effect = [project, 2]
    renamed = await rename_project(
        project.id, ProjectRequest(name=" Renamed "), user, db
    )

    assert created.name == "New"
    assert renamed.name == "Renamed"
    assert renamed.session_count == 2
    owner_query = db.scalar.await_args_list[0].args[0]
    assert user.id in owner_query.compile().params.values()
    assert db.commit.await_count == 2


async def test_delete_project_requires_owned_empty_project() -> None:
    user = _user()
    project = _project(user.id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, 1]

    with pytest.raises(ApiError) as exc_info:
        await delete_project(project.id, user, db)

    assert exc_info.value.code == "project.notEmpty"
    assert "FOR UPDATE" in str(db.scalar.await_args_list[0].args[0])
    db.delete.assert_not_awaited()

    db.scalar.side_effect = [project, 0]
    await delete_project(project.id, user, db)
    db.delete.assert_awaited_once_with(project)
    db.commit.assert_awaited_once()


async def test_foreign_project_is_not_found() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError) as exc_info:
        await rename_project(
            uuid4(), ProjectRequest(name="Hidden"), _user(), db
        )

    assert exc_info.value.code == "project.notFound"
    assert describe_error("project.notFound").message == "The project was not found."
    assert (
        describe_error("project.notEmpty").message
        == "The project still contains chat sessions."
    )
