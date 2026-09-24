from __future__ import annotations

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.execution.client import ExecutionClientError


def browser_api_error(exc: ExecutionClientError) -> ApiError:
    code = exc.code
    status = 404 if code in {"integration.notFound", "executionAgent.notFound", "session.notFound"} else (
        409 if code in {"browser.busy", "browser.bindingChanged", "browser.snapshotChanged", "browser.observationUnstable", "browser.profileOwnerMismatch", "browser.profileInUse"}
        else 503 if code in {"browser.unavailable", "browser.notReady", "browser.notConnected"}
        or code.startswith("executionAgent.") else 502
    )
    return ApiError(status, code)
