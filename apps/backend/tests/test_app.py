import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mato_loop_backend.app import _mount_documentation, _static_headers, _static_path, app


def test_browser_latency_logging_under_default_uvicorn_configuration() -> None:
    result = subprocess.run(
        [sys.executable, "-c", """
import logging.config
from uuid import UUID
from uvicorn.config import LOGGING_CONFIG
logging.config.dictConfig(LOGGING_CONFIG)
import mato_loop_backend.app
logging.getLogger('httpx').info('private-request-url')
from mato_loop_backend.execution.browsers import _browser_latency
with _browser_latency('snapshot', UUID(int=1)):
    pass
"""],
        env={**os.environ, "MATO_LOOP_LOG_LEVEL": "INFO"},
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert "Browser coordinator latency operation=snapshot" in result.stderr
    assert "outcome=ok" in result.stderr and "total_ms" in result.stderr
    assert "private-request-url" not in result.stderr


def test_liveness() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health/live")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_agent_installer_is_publicly_available() -> None:
    with TestClient(app) as client:
        response = client.get("/api/execution-agents/install.sh")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/x-shellscript")
    assert response.headers["cache-control"] == "no-cache"
    assert response.text.startswith("#!/usr/bin/env sh\n")


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
            "message": "The request contains invalid data.",
            "field": "password",
            "params": {"minLength": 10},
        }
    }


def test_domain_errors_use_stable_codes() -> None:
    with TestClient(app) as client:
        response = client.get("/api/sessions")

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "auth.notAuthenticated",
            "message": "Authentication failed.",
        }
    }


def test_attachment_download_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.get(
            f"/api/sessions/{uuid4()}/messages/{uuid4()}/attachments/{uuid4()}"
        )

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "auth.notAuthenticated",
            "message": "Authentication failed.",
        }
    }


def test_workspace_file_update_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.put(
            "/api/workspace/files",
            json={
                "path": "notes.txt",
                "content": "new",
                "expected_revision": "0" * 64,
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.notAuthenticated"


def test_workspace_usage_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/workspace/usage", params={"session_id": uuid4()})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.notAuthenticated"


def test_notifications_require_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/notifications")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.notAuthenticated"


def test_web_push_config_requires_authentication() -> None:
    with TestClient(app) as client:
        response = client.get("/api/notifications/push/config")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth.notAuthenticated"


def test_general_attachment_routes_are_registered() -> None:
    paths = app.openapi()["paths"]

    assert "/api/sessions/with-attachments" in paths
    assert "/api/sessions/{session_id}/messages/with-attachments" in paths


def test_static_paths_cannot_escape_static_root(tmp_path: Path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()

    assert _static_path(static_root, "assets/app.js") == static_root / "assets/app.js"
    assert _static_path(static_root, "../secret.txt") is None


def test_pwa_entry_files_are_not_cached() -> None:
    assert _static_headers("sw.js") == {
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Service-Worker-Allowed": "/",
    }
    assert _static_headers("manifest.webmanifest")["Cache-Control"] == (
        "no-cache, no-store, must-revalidate"
    )
    assert _static_headers("assets/app.js") == {}


def test_documentation_serves_public_directory_urls(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    guide_dir = docs_dir / "guide"
    guide_dir.mkdir(parents=True)
    (docs_dir / "index.html").write_text("Documentation", encoding="utf-8")
    (guide_dir / "index.html").write_text("Guide", encoding="utf-8")

    documentation_app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    _mount_documentation(documentation_app, docs_dir)

    @documentation_app.get("/{full_path:path}")
    async def spa_fallback(full_path: str) -> str:
        return f"SPA: {full_path}"

    with TestClient(documentation_app) as client:
        redirect = client.get("/docs")
        index = client.get("/docs/")
        guide = client.get("/docs/guide/")
        missing = client.get("/docs/missing/")

    assert [response.status_code for response in redirect.history] == [308]
    assert redirect.history[0].headers["location"] == "/docs/"
    assert redirect.text == "Documentation"
    assert index.status_code == 200
    assert index.text == "Documentation"
    assert guide.status_code == 200
    assert guide.text == "Guide"
    assert missing.status_code == 404
    assert "SPA" not in missing.text
