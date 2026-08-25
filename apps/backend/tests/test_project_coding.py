import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.tool_arguments import CodingAnalyzeArguments
from mato_loop_backend.agent.tools import _coding_analyze
from mato_loop_backend.integrations.opencode_client import (
    OpenCodeFileStatus,
    OpenCodeMessageResult,
    OpenCodeModel,
    OpenCodeSession,
)
from mato_loop_backend.models import Project, ProjectPlan, ProjectPlanExecution
from mato_loop_backend.project_coding import (
    CodingAnalysisResult,
    CodingFileStatus,
    CodingScheduler,
    ProjectCodingError,
    ProjectCodingService,
    _BoundCodingProject,
    _redact_permission_pattern,
)
from mato_loop_backend.settings import SettingsStore


def _project(user_id, **values) -> Project:
    defaults = {
        "id": uuid4(),
        "user_id": user_id,
        "name": "Project",
        "coding_integration_id": uuid4(),
        "coding_project_key": "project-1",
        "coding_workspace_key": "repo",
        "coding_analysis_model_key": "provider/analyze",
        "coding_execution_model_key": "provider/build",
    }
    defaults.update(values)
    return Project(**defaults)


def _bound(client) -> _BoundCodingProject:
    return _BoundCodingProject(
        client=client,
        config=SimpleNamespace(server_root="/srv"),
        directory="/srv/repo",
        project_key="project-1",
        workspace_key="repo",
    )


def _scalars(*items):
    result = AsyncMock()
    result.all = lambda: list(items)
    return result


async def test_resolve_rejects_foreign_and_incomplete_projects() -> None:
    user_id = uuid4()
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ProjectCodingError) as foreign:
        await ProjectCodingService.resolve(db, user_id, _project(uuid4()))
    with pytest.raises(ProjectCodingError) as incomplete:
        await ProjectCodingService.resolve(
            db, user_id, _project(user_id, coding_integration_id=None)
        )

    assert foreign.value.code == "project.notFound"
    assert incomplete.value.code == "project.codingNotConfigured"
    db.assert_not_awaited()


async def test_set_models_validates_live_catalog_before_locking(monkeypatch) -> None:
    user_id = uuid4()
    project = _project(user_id)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.list_models.return_value = (
        OpenCodeModel("provider/analyze", "provider", "analyze", "Provider", "Analyze"),
    )
    monkeypatch.setattr(
        ProjectCodingService, "resolve", AsyncMock(return_value=_bound(client))
    )
    db = AsyncMock(spec=AsyncSession)

    with pytest.raises(ProjectCodingError) as error:
        await ProjectCodingService.set_models(
            db, user_id, project, "provider/analyze", "provider/missing"
        )

    assert error.value.code == "project.codingModelUnavailable"
    assert error.value.params == {"keys": ["provider/missing"]}
    db.scalar.assert_not_awaited()


async def test_create_plan_hashes_exact_markdown(monkeypatch) -> None:
    user_id = uuid4()
    project = _project(user_id)
    client = AsyncMock()
    monkeypatch.setattr(
        ProjectCodingService, "resolve", AsyncMock(return_value=_bound(client))
    )
    db = AsyncMock(spec=AsyncSession)
    markdown = "# Plan\n\nKeep trailing space. "

    plan = await ProjectCodingService.create_plan(
        db, user_id, project, None, "Plan", markdown
    )

    assert plan.markdown == markdown
    assert plan.revision == __import__("hashlib").sha256(markdown.encode()).hexdigest()
    assert plan.working_directory == "/srv/repo"
    db.add.assert_called_once_with(plan)
    db.commit.assert_awaited_once()


async def test_analysis_enables_only_discovered_read_tools(monkeypatch) -> None:
    user_id = uuid4()
    project = _project(user_id)
    client = AsyncMock()
    client.__aenter__.return_value = client
    client.__aexit__.return_value = None
    client.list_models.return_value = (
        OpenCodeModel("provider/analyze", "provider", "analyze", "Provider", "Analyze"),
    )
    client.create_session.return_value = OpenCodeSession(
        "session-1", "Analysis", "/srv/repo"
    )
    client.send_message.return_value = OpenCodeMessageResult("Findings", None)
    monkeypatch.setattr(
        ProjectCodingService, "resolve", AsyncMock(return_value=_bound(client))
    )

    result = await ProjectCodingService.analyze(
        AsyncMock(spec=AsyncSession), user_id, project, "Brief", "Title", "", ""
    )

    assert result.text == "Findings"
    assert client.send_message.await_args.args[-1] == {
        "*": False,
        "read": True,
        "grep": True,
        "glob": True,
        "list": True,
    }


def test_permission_patterns_hide_absolute_paths() -> None:
    assert _redact_permission_pattern(
        "write /srv/repo/src/app.py and /etc/passwd", "/srv/repo"
    ) == "write ./src/app.py and <absoluter Pfad>"


async def test_enqueue_preserves_order_and_snapshots(monkeypatch) -> None:
    user_id = uuid4()
    project = _project(user_id)
    plan_ids = [uuid4(), uuid4()]
    plans = [
        ProjectPlan(
            id=plan_id,
            project_id=project.id,
            title=str(index),
            markdown=f"plan {index}",
            revision=f"revision-{index}",
            working_directory="/old",
            analysis_model_key="provider/analyze",
        )
        for index, plan_id in enumerate(reversed(plan_ids), start=1)
    ]
    client = AsyncMock()
    monkeypatch.setattr(
        ProjectCodingService, "resolve", AsyncMock(return_value=_bound(client))
    )
    wake = MagicMock()
    monkeypatch.setattr(CodingScheduler, "wake", wake)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [project, 7]
    db.scalars.return_value = _scalars(*plans)

    executions = await ProjectCodingService.enqueue(
        db, user_id, project, plan_ids
    )

    by_id = {plan.id: plan for plan in plans}
    assert [item.plan_id for item in executions] == plan_ids
    assert [item.queue_position for item in executions] == [8, 9]
    assert [item.plan_snapshot for item in executions] == [
        by_id[plan_id].markdown for plan_id in plan_ids
    ]
    assert all(item.working_directory == "/srv/repo" for item in executions)
    assert all(item.model_key == "provider/build" for item in executions)
    db.commit.assert_awaited_once()
    wake.assert_called_once()


async def test_recover_marks_active_unknown_and_pauses_projects() -> None:
    project_id = uuid4()
    executions = [
        ProjectPlanExecution(
            project_id=project_id,
            plan_id=uuid4(),
            queue_position=index,
            status=status,
            plan_revision="r",
            plan_snapshot="plan",
            working_directory="/srv/repo",
            model_key="provider/build",
            pending_permission={"id": "permission"},
        )
        for index, status in enumerate(("running", "waiting_permission"), start=1)
    ]
    project = _project(uuid4(), id=project_id)
    db = AsyncMock(spec=AsyncSession)
    db.scalars.side_effect = [_scalars(*executions), _scalars(project)]

    changed = await CodingScheduler.recover(db)

    assert changed == 2
    assert all(item.status == "outcome_unknown" for item in executions)
    assert all(item.pending_permission is None for item in executions)
    assert project.coding_queue_pause_reason == "outcome_unknown"
    db.commit.assert_awaited_once()


def test_event_and_file_status_mapping(monkeypatch) -> None:
    assert CodingScheduler._event_session_status(
        {
            "type": "session.status",
            "properties": {"sessionID": "other", "status": {"type": "busy"}},
        }
    ) == ("other", "busy")
    assert CodingScheduler._event_session_status(
        {"type": "permission.updated", "properties": {}}
    ) is None

    async def exercise() -> tuple[CodingFileStatus, ...]:
        user_id = uuid4()
        project = _project(user_id)
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.__aexit__.return_value = None
        client.file_status.return_value = (
            OpenCodeFileStatus("src/app.py", 2, 1, "modified"),
        )
        monkeypatch.setattr(
            ProjectCodingService, "resolve", AsyncMock(return_value=_bound(client))
        )
        return await ProjectCodingService.file_status(AsyncMock(), user_id, project)

    result = __import__("asyncio").run(exercise())
    assert result == (CodingFileStatus("src/app.py", 2, 1, "modified"),)


async def test_coding_analyze_tool_uses_bound_project_and_permission(monkeypatch) -> None:
    user_id = uuid4()
    project = _project(user_id, instructions="Project rules")
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = project
    ask = AsyncMock(return_value=PermissionDecision.ALLOW)
    context = SimpleNamespace(
        db=db,
        session=SimpleNamespace(id=uuid4(), user_id=user_id, project_id=project.id),
        ask=ask,
    )
    monkeypatch.setattr(
        SettingsStore,
        "load",
        AsyncMock(return_value=SimpleNamespace(instructions="User rules")),
    )
    analyze = AsyncMock(return_value=CodingAnalysisResult("Findings", "remote-1"))
    monkeypatch.setattr(ProjectCodingService, "analyze", analyze)

    result = await _coding_analyze(
        CodingAnalyzeArguments(title="Feature", brief="Inspect it"), context
    )

    assert json.loads(result.content) == {
        "analysis": "Findings",
        "externalSessionId": "remote-1",
    }
    permission = ask.await_args.args[0]
    assert permission.capability == "coding.analyze"
    assert permission.resource_type == "project"
    analyze.assert_awaited_once_with(
        db,
        user_id,
        project,
        "Inspect it",
        "Feature",
        "User rules",
        "Project rules",
    )
