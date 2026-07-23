from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from mato_loop_backend.app import _static_path, app


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_validation_errors_use_stable_codes() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/auth/setup",
            json={"username": "test", "password": "too-short"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation.tooShort",
            "field": "password",
            "params": {"minLength": 10},
        }
    }


def test_domain_errors_use_stable_codes() -> None:
    with TestClient(app) as client:
        response = client.get("/api/sessions")

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "auth.notAuthenticated"}}


def test_attachment_download_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.get(
            f"/api/sessions/{uuid4()}/messages/{uuid4()}/attachments/{uuid4()}"
        )

    assert response.status_code == 401
    assert response.json() == {"error": {"code": "auth.notAuthenticated"}}


def test_static_paths_cannot_escape_static_root(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()

    assert _static_path(static_root, "assets/app.js") == static_root / "assets/app.js"
    assert _static_path(static_root, "../secret.txt") is None
