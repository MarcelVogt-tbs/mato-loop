import asyncio
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import websockets

from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.connection import ExecutionAgent, serialize_message
from mato_loop_execution_agent.protocol import Error, Request, Response
from mato_loop_execution_agent.workspace import AgentError

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
ROOT_ID = "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d"
OTHER_ROOT_ID = "721f9ef5-10c6-4c85-95cb-9612329d789e"
REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"


class ConnectionConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_config_does_not_require_connection_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            configuration = AgentConfig.model_validate(
                {
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": base}],
                    "dataDir": base / "data",
                }
            )

        self.assertIsNone(configuration.backend_url)
        self.assertIsNone(configuration.agent_id)
        self.assertTrue(configuration.allow_network)

    async def test_loads_json_paths_and_authenticates_outbound_socket(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config_path = base / "agent.json"
            received = {}

            async def handler(socket) -> None:
                received["authorization"] = socket.request.headers["Authorization"]
                received["hello"] = json.loads(await socket.recv())
                await socket.send(
                    json.dumps(
                        {
                            "type": "welcome",
                            "protocolVersion": 3,
                            "agentId": AGENT_ID,
                        }
                    )
                )
                await socket.close()

            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                config_path.write_text(
                    json.dumps(
                        {
                            "backendUrl": (f"ws://127.0.0.1:{port}/api/execution-agents/connect"),
                            "agentId": AGENT_ID,
                            "name": "Agent",
                            "roots": [{"id": ROOT_ID, "label": "Root", "path": str(base)}],
                            "dataDir": str(base / "data"),
                            "allowInsecure": True,
                        }
                    ),
                    encoding="utf-8",
                )
                with patch.dict(os.environ, {}, clear=True):
                    configuration = AgentConfig.load(config_path)
                await ExecutionAgent(configuration, "agent-secret")._connect_once()

            self.assertEqual(received["authorization"], "Bearer agent-secret")
            self.assertEqual(received["hello"]["type"], "hello")
            self.assertEqual(received["hello"]["protocolVersion"], 3)
            self.assertEqual(received["hello"]["roots"][0]["id"], ROOT_ID)

    async def test_mutation_cancellation_returns_before_actual_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            configuration = AgentConfig.model_validate(
                {
                    "backendUrl": "ws://localhost/api/execution-agents/connect",
                    "token": "secret",
                    "agentId": AGENT_ID,
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": base}],
                    "dataDir": base / "data",
                    "allowInsecure": True,
                }
            )
            agent = ExecutionAgent(configuration, "secret")
            started = threading.Event()
            release = threading.Event()

            def delayed_write(*_args) -> dict:
                started.set()
                release.wait(2)
                return {"path": "file.txt", "size": 1, "sha256": "a" * 64}

            agent.workspace.write = delayed_write
            request = Request.model_validate(
                {
                    "type": "request",
                    "id": REQUEST_ID,
                    "operation": "workspace.write",
                    "arguments": {
                        "rootId": ROOT_ID,
                        "rootRevision": agent.workspace.roots[ROOT_ID].revision,
                        "workingDirectory": ".",
                        "path": "file.txt",
                        "content": "x",
                    },
                }
            )
            task = asyncio.create_task(agent.dispatch(request))
            await asyncio.to_thread(started.wait, 1)
            task.cancel()
            try:
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                release.set()

    async def test_dispatches_advertised_model_file_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = AgentConfig.model_validate(
                {
                    "backendUrl": "ws://localhost/api/execution-agents/connect",
                    "agentId": AGENT_ID,
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
                    "dataDir": root / "data",
                    "allowInsecure": True,
                }
            )
            agent = ExecutionAgent(configuration, "secret")
            revision = agent.workspace.roots[ROOT_ID].revision
            self.assertIn("workspace.upsert", agent.hello().operations)
            self.assertIn("workspace.edit", agent.hello().operations)

            arguments = {
                "rootId": ROOT_ID,
                "rootRevision": revision,
                "workingDirectory": ".",
                "path": "app.txt",
            }
            await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "workspace.upsert",
                        "arguments": {**arguments, "content": "old"},
                    }
                )
            )
            result = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "workspace.edit",
                        "arguments": {
                            **arguments,
                            "oldText": "old",
                            "newText": "new",
                        },
                    }
                )
            )

            self.assertEqual((root / "app.txt").read_text(), "new")
            self.assertEqual(result["sha256"], hashlib.sha256(b"new").hexdigest())

    async def test_connection_retry_reloads_rotated_access_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            configuration = AgentConfig.model_validate(
                {
                    "backendUrl": "ws://localhost/api/execution-agents/connect",
                    "agentId": AGENT_ID,
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": base}],
                    "dataDir": base / "data",
                    "allowInsecure": True,
                }
            )
            agent = ExecutionAgent(configuration, "old-key")
            loader = AsyncMock(side_effect=["new-key", "rotated-key"])
            with (
                patch.object(
                    agent,
                    "_connect_once",
                    new=AsyncMock(side_effect=[RuntimeError(), asyncio.CancelledError()]),
                ),
                patch(
                    "mato_loop_execution_agent.connection.asyncio.sleep",
                    new=AsyncMock(),
                ),
                self.assertRaises(asyncio.CancelledError),
            ):
                await agent.run_forever(loader)

            self.assertEqual(agent._access_key, "rotated-key")
            self.assertEqual(agent.transfer._access_key, "rotated-key")

    def test_rejects_overlapping_roots_and_invalid_executables(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            child = base / "child"
            child.mkdir()
            values = {
                "backendUrl": "ws://localhost/api/execution-agents/connect",
                "token": "secret",
                "agentId": AGENT_ID,
                "name": "Agent",
                "roots": [
                    {"id": ROOT_ID, "label": "Root", "path": base},
                    {"id": OTHER_ROOT_ID, "label": "Child", "path": child},
                ],
                "dataDir": base / "data",
                "allowInsecure": True,
            }
            with self.assertRaises(ValueError):
                AgentConfig.model_validate(values)

            values["roots"] = [{"id": ROOT_ID, "label": "Root", "path": base}]
            values["processExecutables"] = {"missing": base / "missing"}
            with self.assertRaises(ValueError):
                ExecutionAgent(AgentConfig.model_validate(values), "secret")

            values["processExecutables"] = {"shell": Path("/bin/sh")}
            values["shellExecutable"] = Path("/bin/sh")
            agent = ExecutionAgent(AgentConfig.model_validate(values), "secret")
            self.assertTrue(agent.process.executables["shell"].is_file())
            self.assertEqual(agent.process.executables["shell"], Path("/bin/sh").resolve())
            self.assertIn("process.exec", agent.operations)
            self.assertIn("shell.run", agent.operations)

    async def test_home_resolution_system_info_and_directory_operation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            home = root / "home"
            home.mkdir(parents=True)
            configuration = AgentConfig.model_validate(
                {
                    "backendUrl": "ws://localhost/api/execution-agents/connect",
                    "agentId": AGENT_ID,
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
                    "homeDirectory": home / ".." / "home",
                    "dataDir": base / "data",
                    "allowInsecure": True,
                }
            )
            agent = ExecutionAgent(configuration, "secret")
            revision = agent.workspace.roots[ROOT_ID].revision
            hello = agent.hello()
            self.assertEqual(hello.home_directory, str(home))
            self.assertEqual(hello.home_root_id, ROOT_ID)
            self.assertEqual(hello.home_root_revision, revision)
            self.assertEqual(hello.home_relative_directory, "home")
            self.assertIn("shell.inspect", agent.operations)

            resolved = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "filesystem.resolve",
                        "arguments": {"path": "home", "currentDirectory": str(root)},
                    }
                )
            )
            self.assertEqual(
                resolved,
                {
                    "path": str(home),
                    "rootId": ROOT_ID,
                    "rootRevision": revision,
                    "relativePath": "home",
                },
            )

            workspace_resolved = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "workspace.resolve",
                        "arguments": {
                            "rootId": ROOT_ID,
                            "rootRevision": revision,
                            "workingDirectory": ".",
                            "path": "home",
                        },
                    }
                )
            )
            self.assertEqual(workspace_resolved, resolved)

            info = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": "3c907129-f296-456c-bd43-79daca5e61bd",
                        "operation": "system.info",
                        "arguments": {"currentDirectory": str(home)},
                    }
                )
            )
            self.assertGreater(info["logicalCpuCount"], 0)
            self.assertEqual(info["disk"]["rootId"], ROOT_ID)
            self.assertNotIn("environment", info)
            self.assertNotIn("network", info)
            self.assertNotIn("processes", info)

            outside = base / "outside"
            outside.mkdir()
            (root / "escape").symlink_to(outside, target_is_directory=True)
            with self.assertRaises(AgentError):
                agent.workspace.resolve_directory(str(root / "escape"))

    def test_home_directory_must_be_accessible_within_a_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            values = {
                "name": "Agent",
                "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
                "homeDirectory": outside,
                "dataDir": base / "data",
            }
            with self.assertRaises(ValueError):
                AgentConfig.model_validate(values)

    def test_complete_response_serialization_is_bounded(self) -> None:
        response = Response(id=REQUEST_ID, ok=True, result={"content": "x" * 1024 * 1024})
        with self.assertRaises(AgentError):
            serialize_message(response)

        error = Response(
            id=REQUEST_ID,
            ok=False,
            error=Error(
                code="openscad.renderFailed",
                params={"diagnostics": "safe output", "diagnosticsTruncated": False},
            ),
        )
        self.assertEqual(
            json.loads(serialize_message(error))["error"]["params"]["diagnostics"],
            "safe output",
        )


if __name__ == "__main__":
    unittest.main()
