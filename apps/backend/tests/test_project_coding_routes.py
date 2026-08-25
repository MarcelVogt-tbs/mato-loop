from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.api.routes import project_coding as routes
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import Project, ProjectPlan, ProjectPlanExecution, UserAccount
from mato_loop_backend.project_coding import ProjectCodingError, ProjectCodingService
from mato_loop_backend.security import require_user


def _user() -> UserAccount:
    return UserAccount(
        id=uuid4(),
        username="coding-test",
        password_hash="hash",
        is_active=True,
    )


def _project(user_id) -> Project:
    return Project(
        id=uuid4(),
        user_id=user_id,
        name="Project",
        instructions="Project rules",
        coding_integration_id=uuid4(),
        coding_project_key="remote-project",
        coding_workspace_key="repo",
        coding_analysis_model_key="provider/analyze",
        coding_analysis_model_variant="high",
        coding_execution_model_key="provider/build",
        coding_execution_model_variant="max",
        coding_queue_pause_reason="manual",
    )


def _client(user: UserAccount, db: AsyncMock) -> TestClient:
    async def override_db():
        yield db

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(routes.router)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


def test_project_endpoint_checks_ownership_before_service(monkeypatch) -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    service = AsyncMock()
    monkeypatch.setattr(ProjectCodingService, "list_models", service)

    response = _client(user, db).get(f"/projects/{uuid4()}/coding/models")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "project.notFound"
    service.assert_not_awaited()


def test_project_binding_can_be_set_and_cleared(monkeypatch) -> None:
    user = _user()
    project = _project(user.id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = project
    set_service = AsyncMock(return_value=project)
    clear_service = AsyncMock(return_value=project)
    monkeypatch.setattr(ProjectCodingService, "set_binding", set_service)
    monkeypatch.setattr(ProjectCodingService, "clear_binding", clear_service)
    client = _client(user, db)

    response = client.put(
        f"/projects/{project.id}/coding/binding",
        json={
            "integrationId": str(project.coding_integration_id),
            "projectKey": project.coding_project_key,
            "workspaceKey": project.coding_workspace_key,
        },
    )
    cleared = client.delete(f"/projects/{project.id}/coding/binding")

    assert response.status_code == 200
    assert response.json() == {
        "adapterType": "opencode",
        "integrationId": str(project.coding_integration_id),
        "projectKey": "remote-project",
        "workspaceKey": "repo",
        "analysisModelKey": "provider/analyze",
        "analysisModelVariant": "high",
        "executionModelKey": "provider/build",
        "executionModelVariant": "max",
        "queuePauseReason": "manual",
    }
    assert cleared.status_code == 204
    set_service.assert_awaited_once()
    clear_service.assert_awaited_once()


def test_model_catalog_and_selection_include_variants(monkeypatch) -> None:
    user = _user()
    project = _project(user.id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = project
    list_service = AsyncMock(
        return_value=(
            routes.CodingModelOption(
                "provider/analyze", "Analyze", "Provider", ("low", "high")
            ),
        )
    )
    async def set_models(
        _db,
        _user_id,
        item,
        analysis_model_key,
        analysis_model_variant,
        execution_model_key,
        execution_model_variant,
    ):
        item.coding_analysis_model_key = analysis_model_key
        item.coding_analysis_model_variant = analysis_model_variant
        item.coding_execution_model_key = execution_model_key
        item.coding_execution_model_variant = execution_model_variant
        return item

    set_service = AsyncMock(side_effect=set_models)
    monkeypatch.setattr(ProjectCodingService, "list_models", list_service)
    monkeypatch.setattr(ProjectCodingService, "set_models", set_service)
    client = _client(user, db)

    catalog = client.get(f"/projects/{project.id}/coding/models")
    selection = client.put(
        f"/projects/{project.id}/coding/models",
        json={
            "analysisModelKey": "provider/analyze",
            "analysisModelVariant": "high",
            "executionModelKey": "provider/build",
            "executionModelVariant": None,
        },
    )

    assert catalog.status_code == 200
    assert catalog.json()["items"][0]["variants"] == ["low", "high"]
    assert catalog.json()["analysisModelVariant"] == "high"
    assert selection.status_code == 200
    assert selection.json()["executionModelVariant"] is None
    set_service.assert_awaited_once_with(
        db,
        user.id,
        project,
        "provider/analyze",
        "high",
        "provider/build",
        None,
    )


@pytest.mark.parametrize(
    ("code", "expected_status"),
    [
        ("validation.invalidChoice", 422),
        ("project.planNotFound", 404),
        ("project.executionNotCancellable", 409),
        ("opencode.malformedResponse", 502),
        ("opencode.unavailable", 503),
        ("unexpected.secret", 409),
    ],
)
def test_project_coding_error_mapping_is_stable_and_safe(
    code: str, expected_status: int
) -> None:
    error = routes._mapped_error(
        ProjectCodingError(
            code,
            {"field": "response", "remoteError": "credential=secret"},
        )
    )

    assert error.status_code == expected_status
    assert error.code == ("http.conflict" if code == "unexpected.secret" else code)
    assert error.field == "response"
    assert not error.params or "remoteError" not in error.params


def test_plan_and_execution_payloads_do_not_expose_internal_fields(monkeypatch) -> None:
    user = _user()
    project = _project(user.id)
    now = datetime.now(UTC)
    plan = ProjectPlan(
        id=uuid4(),
        project_id=project.id,
        title="Plan",
        markdown="# Safe plan",
        revision="secret-revision",
        working_directory="/srv/private/repo",
        analysis_model_key="provider/analyze",
        analysis_model_variant="high",
        created_at=now,
        updated_at=now,
    )
    execution = ProjectPlanExecution(
        id=uuid4(),
        project_id=project.id,
        plan_id=plan.id,
        queue_position=1,
        status="waiting_permission",
        plan_revision="secret-revision",
        plan_snapshot="secret snapshot",
        working_directory="/srv/private/repo",
        model_key="provider/build",
        model_variant="max",
        pending_permission={
            "id": "permission-1",
            "title": "Shell",
            "type": "bash",
            "patterns": ["git status"],
            "metadata": {"token": "secret"},
        },
        error_params={"upstream": "secret"},
        created_at=now,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = project
    monkeypatch.setattr(
        ProjectCodingService, "list_plans", AsyncMock(return_value=(plan,))
    )
    monkeypatch.setattr(
        ProjectCodingService,
        "list_executions",
        AsyncMock(return_value=(execution,)),
    )
    client = _client(user, db)

    plan_payload = client.get(f"/projects/{project.id}/coding/plans").json()
    execution_payload = client.get(
        f"/projects/{project.id}/coding/executions"
    ).json()
    serialized = str((plan_payload, execution_payload))

    assert plan_payload["items"][0]["workspaceKey"] == "repo"
    assert plan_payload["items"][0]["analysisModelVariant"] == "high"
    assert execution_payload["queuePauseReason"] == "manual"
    assert execution_payload["items"][0]["modelVariant"] == "max"
    assert execution_payload["items"][0]["pendingPermission"] == {
        "id": "permission-1",
        "title": "Shell",
        "type": "bash",
        "patterns": ["git status"],
    }
    for forbidden in (
        "/srv/private/repo",
        "planSnapshot",
        "secret snapshot",
        "secret-revision",
        "metadata",
        "errorParams",
    ):
        assert forbidden not in serialized


def test_binary_content_replaces_unsafe_mime(monkeypatch) -> None:
    user = _user()
    project = _project(user.id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = project
    monkeypatch.setattr(
        ProjectCodingService,
        "read_file",
        AsyncMock(
            return_value=routes.CodingFileContent(
                type="binary",
                content="PHNjcmlwdD4=",
                encoding="base64",
                mime_type="text/html",
            )
        ),
    )

    response = _client(user, db).get(
        f"/projects/{project.id}/coding/files/content?path=asset.bin"
    )

    assert response.status_code == 200
    assert response.json()["mimeType"] == "application/octet-stream"
    assert "text/html" not in response.text
