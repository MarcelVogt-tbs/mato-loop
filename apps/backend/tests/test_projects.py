from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent import AgentRuntime
from mato_loop_backend.api.errors import ApiError, register_error_handlers
from mato_loop_backend.api.routes.projects import (
    ProjectRequest,
    create_project,
    delete_project,
    list_projects,
    project_delete_impact,
    project_delete_impact_token,
    router,
    update_project,
)
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.models import Automation, ChatSession, Project, UserAccount
from mato_loop_backend.security import require_user


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def _project(user_id) -> Project:
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    return Project(
        id=uuid4(),
        user_id=user_id,
        name="Project",
        instructions="",
        created_at=now,
        updated_at=now,
    )


def _rows(*items):
    result = MagicMock()
    result.all.return_value = list(items)
    return result


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


def test_project_fields_are_trimmed_and_bounded() -> None:
    request = ProjectRequest(name="  Work  ", instructions="  Use Python.  ")
    assert request.name == "Work"
    assert request.instructions == "Use Python."
    with pytest.raises(ValidationError):
        ProjectRequest(name="   ")
    with pytest.raises(ValidationError):
        ProjectRequest(name="x" * 241)
    with pytest.raises(ValidationError):
        ProjectRequest(name="Work", instructions="x" * 4001)


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


async def test_create_and_update_project_commit_owner_scoped_changes() -> None:
    user = _user()
    now = datetime(2026, 8, 18, 12, tzinfo=UTC)
    db = AsyncMock(spec=AsyncSession)

    def persist(project: Project) -> None:
        project.id = uuid4()
        project.created_at = now
        project.updated_at = now

    db.add.side_effect = persist
    created = await create_project(
        ProjectRequest(name=" New ", instructions=" Use Python. "), user, db
    )

    project = _project(user.id)
    db.scalar.side_effect = [project, 2]
    updated = await update_project(
        project.id,
        ProjectRequest(name=" Renamed ", instructions=" Be concise. "),
        user,
        db,
    )

    assert created.name == "New"
    assert created.instructions == "Use Python."
    assert updated.name == "Renamed"
    assert updated.instructions == "Be concise."
    assert updated.session_count == 2
    owner_query = db.scalar.await_args_list[0].args[0]
    assert user.id in owner_query.compile().params.values()
    assert db.commit.await_count == 2


async def test_update_project_preserves_omitted_instructions() -> None:
    user = _user()
    project = _project(user.id)
    project.instructions = "Keep this."
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, 0]

    updated = await update_project(
        project.id, ProjectRequest(name="Renamed"), user, db
    )

    assert updated.instructions == "Keep this."


async def test_project_delete_impact_counts_sessions_and_linked_automations() -> None:
    user = _user()
    project = _project(user.id)
    automations = [
        Automation(
            id=uuid4(),
            user_id=user.id,
            execution_session_id=uuid4(),
            output_session_id=uuid4(),
            output_session_required=required,
            name="Automation",
            task="Task",
            revision=index,
        )
        for index, required in ((1, False), (3, True))
    ]
    session_ids = [uuid4(), uuid4(), uuid4(), uuid4()]
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = project
    db.scalars.side_effect = [_rows(*reversed(session_ids)), _rows(*automations)]

    result = await project_delete_impact(project.id, user, db)

    assert result == {
        "sessionCount": 4,
        "sessionIds": [str(item) for item in sorted(session_ids)],
        "automationCount": 2,
        "requiredAutomationCount": 1,
        "automationImpact": project_delete_impact_token(session_ids, automations),
    }
    linked_query = db.scalars.await_args_list[1].args[0]
    assert user.id in linked_query.compile().params.values()
    assert project.id in linked_query.compile().params.values()
    assert project_delete_impact_token(session_ids[:-1], automations) != result[
        "automationImpact"
    ]


async def test_delete_project_with_sessions_trashes_tree_and_database_rows(
    tmp_path, monkeypatch
) -> None:
    from mato_loop_backend.api.routes import projects as projects_route
    from mato_loop_backend.session_storage import SessionStorage

    user = _user()
    project = _project(user.id)
    storage = SessionStorage(tmp_path / "sessions")
    session = ChatSession(
        id=uuid4(), user_id=user.id, project_id=project.id, title="Session"
    )
    storage.provision(session.id, project.id)
    shared_file = storage.logical_root_path(session.id, project.id) / "files" / "shared.txt"
    shared_file.write_text("shared", encoding="utf-8")
    monkeypatch.setattr(projects_route, "SessionStorage", lambda: storage)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, project]
    db.scalars.side_effect = [
        _rows(session.id),
        _rows(),
        _rows(),
        _rows(session),
        _rows(),
        _rows(),
    ]

    await delete_project(
        project.id,
        user,
        db,
        automation_impact=project_delete_impact_token([session.id], []),
    )

    assert [call.args[0] for call in db.delete.await_args_list] == [session, project]
    db.commit.assert_awaited_once()
    assert not shared_file.exists()
    attachment_delete = db.execute.await_args.args[0]
    assert attachment_delete.is_delete
    assert project.id in attachment_delete.compile().params.values()
    assert list(storage.trash_dir.iterdir()) == []
    locked_automation_query = db.scalars.await_args_list[4].args[0]
    assert locked_automation_query.get_execution_options()["populate_existing"] is True
    assert "FOR UPDATE" in str(locked_automation_query)


async def test_foreign_project_is_not_found() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError) as exc_info:
        await update_project(
            uuid4(), ProjectRequest(name="Hidden"), _user(), db
        )

    assert exc_info.value.code == "project.notFound"
    assert describe_error("project.notFound").message == "The project was not found."
    assert describe_error("session.automationOutputLinked").message


async def test_delete_project_enforces_automation_mode_and_impact() -> None:
    user = _user()
    project = _project(user.id)
    session_id = uuid4()
    automation = Automation(
        id=uuid4(),
        user_id=user.id,
        execution_session_id=uuid4(),
        output_session_id=session_id,
        output_session_required=True,
        name="Required",
        task="Task",
        revision=2,
    )

    missing_mode_db = AsyncMock(spec=AsyncSession)
    missing_mode_db.scalar.return_value = project
    missing_mode_db.scalars.side_effect = [_rows(session_id), _rows(automation)]
    with pytest.raises(ApiError) as missing_mode:
        await delete_project(
            project.id,
            user,
            missing_mode_db,
            automation_impact=project_delete_impact_token([session_id], [automation]),
        )
    assert missing_mode.value.code == "session.automationOutputRequired"

    stale_db = AsyncMock(spec=AsyncSession)
    stale_db.scalar.return_value = project
    stale_db.scalars.side_effect = [_rows(session_id), _rows(automation)]
    with pytest.raises(ApiError) as stale:
        await delete_project(
            project.id,
            user,
            stale_db,
            automation_mode="deleteAutomations",
            automation_impact="0" * 64,
        )
    assert stale.value.code == "session.automationImpactChanged"

    unlink_db = AsyncMock(spec=AsyncSession)
    unlink_db.scalar.return_value = project
    unlink_db.scalars.side_effect = [_rows(session_id), _rows(automation)]
    with pytest.raises(ApiError) as unlink:
        await delete_project(
            project.id,
            user,
            unlink_db,
            automation_mode="unlink",
            automation_impact=project_delete_impact_token([session_id], [automation]),
        )
    assert unlink.value.code == "session.automationOutputRequired"


async def test_delete_project_revalidates_locked_session_membership() -> None:
    user = _user()
    project = _project(user.id)
    original_session = ChatSession(
        id=uuid4(), user_id=user.id, project_id=project.id, title="Original"
    )
    late_session = ChatSession(
        id=uuid4(), user_id=user.id, project_id=project.id, title="Late"
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, project]
    db.scalars.side_effect = [
        _rows(original_session.id),
        _rows(),
        _rows(),
        _rows(original_session, late_session),
        _rows(),
    ]

    with pytest.raises(ApiError) as exc_info:
        await delete_project(
            project.id,
            user,
            db,
            automation_impact=project_delete_impact_token(
                [original_session.id], []
            ),
        )

    assert exc_info.value.code == "session.automationImpactChanged"
    db.delete.assert_not_awaited()


async def test_delete_project_rejects_uncancellable_active_database_run(
    monkeypatch,
) -> None:
    user = _user()
    project = _project(user.id)
    session_id = uuid4()
    run_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, run_id]
    db.scalars.side_effect = [
        _rows(session_id),
        _rows(),
        _rows(run_id),
    ]
    cancel = AsyncMock(return_value=False)
    monkeypatch.setattr(AgentRuntime, "cancel_and_wait", cancel)

    with pytest.raises(ApiError) as exc_info:
        await delete_project(
            project.id,
            user,
            db,
            automation_impact=project_delete_impact_token([session_id], []),
        )

    assert exc_info.value.code == "session.runAlreadyActive"
    cancel.assert_awaited_once_with(run_id)
    db.rollback.assert_awaited_once()


def test_delete_project_rejects_unknown_automation_mode() -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)

    async def override_db():
        yield db

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_db_session] = override_db

    client = TestClient(app)
    missing_impact = client.delete(f"/projects/{uuid4()}")
    response = client.delete(
        f"/projects/{uuid4()}?automationMode=invalid&automationImpact={'0' * 64}"
    )

    assert missing_impact.status_code == 422
    assert missing_impact.json()["error"]["code"] == "validation.required"
    assert missing_impact.json()["error"]["field"] == "automationImpact"
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation.invalidChoice"
    assert response.json()["error"]["field"] == "automationMode"
    db.scalar.assert_not_awaited()


async def test_delete_project_restores_storage_when_commit_fails(
    tmp_path, monkeypatch
) -> None:
    from mato_loop_backend.api.routes import projects as projects_route
    from mato_loop_backend.session_storage import SessionStorage

    user = _user()
    project = _project(user.id)
    storage = SessionStorage(tmp_path / "sessions")
    session = ChatSession(
        id=uuid4(), user_id=user.id, project_id=project.id, title="Session"
    )
    execution_session = ChatSession(
        id=uuid4(), user_id=user.id, session_type="automation", title="Automation"
    )
    automation = Automation(
        id=uuid4(),
        user_id=user.id,
        execution_session_id=execution_session.id,
        output_session_id=session.id,
        output_session_required=True,
        name="Automation",
        task="Task",
        revision=1,
    )
    storage.provision(session.id, project.id)
    shared_file = storage.logical_root_path(session.id, project.id) / "files" / "shared.txt"
    shared_file.write_text("shared", encoding="utf-8")
    execution_file = storage.provision(execution_session.id) / "attachments" / "run.txt"
    execution_file.write_text("history", encoding="utf-8")
    monkeypatch.setattr(projects_route, "SessionStorage", lambda: storage)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, project]
    db.scalars.side_effect = [
        _rows(session.id),
        _rows(automation),
        _rows(),
        _rows(),
        _rows(session),
        _rows(automation),
        _rows(),
        _rows(),
        _rows(execution_session),
    ]
    db.commit.side_effect = RuntimeError("commit failed")

    async def rollback_after_restore() -> None:
        assert shared_file.read_text(encoding="utf-8") == "shared"
        assert execution_file.read_text(encoding="utf-8") == "history"

    db.rollback.side_effect = rollback_after_restore

    with pytest.raises(RuntimeError, match="commit failed"):
        await delete_project(
            project.id,
            user,
            db,
            automation_mode="deleteAutomations",
            automation_impact=project_delete_impact_token(
                [session.id], [automation]
            ),
        )

    db.rollback.assert_awaited_once()
    assert shared_file.read_text(encoding="utf-8") == "shared"
    assert execution_file.read_text(encoding="utf-8") == "history"
    assert list(storage.trash_dir.iterdir()) == []
    execution_session_query = db.scalars.await_args_list[8].args[0]
    assert user.id in execution_session_query.compile().params.values()
