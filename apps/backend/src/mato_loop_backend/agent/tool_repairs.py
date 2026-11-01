"""Presentation-only repair tracking; canonical tool content remains unchanged."""

import json
import posixpath
from typing import Any

from mato_loop_backend.execution.paths import is_absolute_path
from mato_loop_backend.files.roots import internal_file_roots
from mato_loop_backend.models import AgentRun
from mato_loop_backend.providers.base import ToolCall


def _computer_path(path: str, directory: str | None) -> str:
    return posixpath.normpath(path if is_absolute_path(path) else posixpath.join(directory or "/", path))


def tool_repair_key(call: ToolCall, run: AgentRun) -> str:
    arguments = internal_file_roots(call.arguments)
    target = {key: arguments[key] for key in (
        "path", "session_id", "integration_id", "resource_key", "url", "query",
        "automation_id", "memory_id", "tabId", "browserId", "source_path", "destination_path",
    ) if key in arguments}
    for prefix in ("", "source_", "destination_"):
        path_key, root_key = f"{prefix}path", f"{prefix}root"
        if path_key in target:
            root = arguments.get(root_key, "workspace")
            target[root_key] = root
            if isinstance(target[path_key], str):
                target[path_key] = (_computer_path(target[path_key], run.current_directory_snapshot)
                                    if root == "workspace" else posixpath.normpath(target[path_key]))
    if call.name == "apply_patch":
        patch = arguments.get("patchText", "")
        target["paths"] = sorted(
            _computer_path(line.split(": ", 1)[1], run.current_directory_snapshot)
            for line in patch.splitlines()
            if line.startswith(("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: "))
        ) if isinstance(patch, str) else []
    if not target or set(target) <= {"session_id", "integration_id", "resource_key"}:
        target = {key: value for key, value in arguments.items() if key != "_reason"}
    return json.dumps([str(run.execution_agent_id), run.current_directory_snapshot, call.name, target], sort_keys=True)


def repairable_tool_error(call: ToolCall, metadata: dict[str, Any], *, workflow: bool = False) -> bool:
    code = metadata.get("errorCode")
    if not isinstance(code, str):
        return False
    if code.startswith(("permission.", "question.", "executionAgent.")) and code not in {
        "question.exclusiveCallRequired", "executionAgent.exclusiveCallRequired",
    }:
        return False
    if code in {
        "file.patchIncomplete", "tool.executionFailed", "process.commandFailed", "process.timeout",
        "tool.skippedAfterUserDenial", "tool.superseded", "agent.toolCallLimitReached",
    } or code.endswith("outcomeUnknown"):
        return False
    return (workflow and code != "agent.repeatedToolCall") or metadata.get("toolRepair") == "pending" or metadata.get("argumentsRejected") is True or metadata.get("retryAfterFreshApproval") is True or code in {
        "question.exclusiveCallRequired", "skill.loadMustBeExclusive", "executionAgent.exclusiveCallRequired",
        "browser.observationUnstable", "webfetch.emptyResponse",
    } or (call.name in {"edit_file", "write_file", "apply_patch"} and code in {
        "file.changed", "file.editTextNotFound", "file.editTextNotUnique", "file.patchContextMismatch",
    })
