"""Runtime coding regression with scripted provider and execution transport responses.

This checks orchestration, not filesystem behavior, executable modes, process execution,
or WebSocket transport. Standalone primitive tests cover those independently.
"""

import json
from unittest.mock import AsyncMock
from uuid import uuid4

from test_required_web_search_runtime import (
    _assistant_messages,
    _runtime_setup,
    _tool_messages,
)

from mato_loop_backend.agent.permissions import PermissionDecision, PermissionPolicy
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.agent.tools import tool_registry
from mato_loop_backend.execution.protocol import EventMessage, ResponseMessage
from mato_loop_backend.models import Message, WebSearchPolicy
from mato_loop_backend.providers.base import ProviderResult, ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.workspace import RootFileService, SessionStorage


async def test_native_coding_replays_revision_errors_checks_and_diff(tmp_path, monkeypatch):
    agent_id = uuid4()
    directory = "/home/test/project"
    revision = "a" * 64
    operations = (
        "filesystem.stat", "filesystem.read", "filesystem.patch", "shell.inspect", "shell.run",
    )
    execute_definition = tool_registry._execute_definition
    db, run, _, projections, events = await _runtime_setup(
        monkeypatch, [],
        search_policy=WebSearchPolicy.AVAILABLE,
        execution_agent_id=agent_id,
        execution_operations=operations,
        user_content="Fix check.sh, preserve my changes, run it and review git diff.",
    )
    run.current_directory_snapshot = directory
    db.get.return_value.current_directory = directory
    records = await AgentRuntime._records(db, run.session_id)

    def add(record):
        if isinstance(record, Message):
            record.id = uuid4()
            records.append(record)

    db.add.side_effect = add
    monkeypatch.setattr(tool_registry, "_execute_definition", execute_definition)
    monkeypatch.setattr(
        PermissionPolicy, "evaluate", AsyncMock(return_value=PermissionDecision.ALLOW),
    )
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(run.session_id)
    monkeypatch.setattr(
        "mato_loop_backend.agent.runtime.RootFileService",
        lambda session_id, **kwargs: RootFileService(
            session_id, session_storage=storage, **kwargs,
        ),
    )

    patch = '''*** Begin Patch
*** Update File: check.sh
@@
-test "old" = "fixed"
+test "fixed" = "fixed"
*** End Patch
'''
    diff = '+# existing user change\n-test "old" = "fixed"\n+test "fixed" = "fixed"\n'
    calls = [
        ToolCall("read", "read", {
            "root": "workspace", "path": "check.sh",
            "_reason": "Read the file before editing it.",
        }),
        ToolCall("failing-check", "shell_run", {
            "command": "./check.sh",
            "_reason": "Run the existing check before changing the file.",
        }),
        ToolCall("stale-patch", "apply_patch", {
            "patch": patch, "expectedSha256": {"check.sh": "0" * 64},
            "_reason": "Apply the fix without overwriting newer changes.",
        }),
        ToolCall("patch", "apply_patch", {
            "patch": patch, "expectedSha256": {"check.sh": revision},
            "_reason": "Apply the fix to the reviewed file revision.",
        }),
        ToolCall("check", "shell_run", {
            "command": "./check.sh",
            "_reason": "Verify that the corrected check passes.",
        }),
        ToolCall("diff", "shell_run", {
            "command": "git diff -- check.sh",
            "_reason": "Review the final changes for unintended edits.",
        }),
    ]
    dispatched = []
    step = 0

    async def request(selected_agent_id, operation, arguments, *, on_event=None, **_kwargs):
        assert selected_agent_id == agent_id
        dispatched.append((operation, arguments))
        request_id = uuid4()
        if operation == "filesystem.stat":
            result = {"path": directory + "/check.sh", "kind": "file", "size": 70, "modifiedAt": 0.0}
        elif operation == "filesystem.read":
            result = {
                "content": '1: #!/bin/sh\n2: # existing user change\n3: test "old" = "fixed"',
                "linesRead": 3, "hasMore": False, "truncatedLines": [], "sha256": revision,
            }
        elif operation == "filesystem.patch":
            assert arguments["patch"] == patch.replace("check.sh", directory + "/check.sh")
            expected = arguments["expectedSha256"]
            assert expected == {directory + "/check.sh": "0" * 64 if step == 3 else revision}
            if step == 3:
                return ResponseMessage(
                    type="response", id=request_id, ok=False, error={"code": "file.changed"},
                )
            result = {"files": [{
                "operation": "update", "path": directory + "/check.sh", "sha256": "b" * 64, "size": 72,
            }]}
        elif operation == "shell.inspect":
            assert arguments["currentDirectory"] == directory
            result = {
                "targets": [], "analysisComplete": False, "classification": "write",
                "readOnly": False, "commands": [], "inspectionRevision": "c" * 64,
            }
        else:
            assert operation == "shell.run"
            assert arguments["currentDirectory"] == directory
            assert arguments["expectedInspectionRevision"] == "c" * 64
            output = diff if step == 6 else ""
            if output:
                await on_event(EventMessage(type="event", id=request_id, event="stdout", data=output))
            result = {
                "exitCode": 1 if step == 2 else 0, "timedOut": False, "durationMs": 1,
                "stdoutBytes": len(output), "stderrBytes": 0,
                "stdoutTruncated": False, "stderrTruncated": False,
            }
        return ResponseMessage(type="response", id=request_id, ok=True, result=result)

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request", request,
    )
    final = "The initial check failed; the stale patch was rejected. The corrected check passes; the diff includes your comment."

    async def complete(_db, **kwargs):
        nonlocal step
        messages = _tool_messages(db)
        assert len(messages) == step
        if step:
            assert any(
                message.get("tool_call_id") == messages[-1].tool_call_id
                and messages[-1].content in message["content"]
                for message in kwargs["messages"] if message["role"] == "tool"
            )
            previous = json.loads(messages[-1].content) if step > 1 else None
            assert messages[-1].tool_call_id == calls[step - 1].id
            if step == 1:
                assert "# existing user change" in messages[-1].content
                read_event = next(event for event in events if event.get("type") == "tool.completed")
                assert read_event["metadata"]["sha256"] == revision
            elif step in {2, 5, 6}:
                assert previous["exitCode"] == (1 if step == 2 else 0)
                assert previous["timedOut"] is False
                assert messages[-1].provider_data["_matoloop"].get("errorCode") == (
                    "process.commandFailed" if step == 2 else None
                )
            elif step == 3:
                assert previous["error"]["code"] == "file.changed"
            elif step == 4:
                assert "error" not in previous
            if step == 6:
                assert previous["output"]["stdout"] == diff
                return ProviderResult(content=final, finish_reason="stop")
        call = calls[step]
        assert call.name in {schema["name"] for schema in kwargs["tools"]}
        step += 1
        return ProviderResult(tool_calls=[call], finish_reason="tool_calls")

    monkeypatch.setattr(ProviderService, "complete", staticmethod(complete))
    await AgentRuntime._execute_loop(db, run)

    assert step == len(calls)
    assert _assistant_messages(db)[-1].content == final
    assert any(
        event.get("type") == "message.completed" and event["message"]["content"] == final
        for event in events
    )
    assert [operation for operation, _ in dispatched] == [
        "filesystem.stat", "filesystem.read", "shell.inspect", "shell.run",
        "filesystem.patch", "filesystem.patch", "shell.inspect", "shell.run",
        "shell.inspect", "shell.run",
    ]
    assert any(
        call["id"] == "patch" and call["arguments"] == calls[3].arguments
        for batch in projections[-1]["live_tool_calls"].values()
        for call in batch
    )
