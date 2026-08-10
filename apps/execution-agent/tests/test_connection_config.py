import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import websockets

from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.connection import ExecutionAgent, serialize_message
from mato_loop_execution_agent.protocol import Request, Response
from mato_loop_execution_agent.workspace import AgentError

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
ROOT_ID = "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d"
OTHER_ROOT_ID = "721f9ef5-10c6-4c85-95cb-9612329d789e"
REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"


class ConnectionConfigTests(unittest.IsolatedAsyncioTestCase):
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
                            "protocolVersion": 1,
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
                            "token": "agent-secret",
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
                await ExecutionAgent(configuration)._connect_once()

            self.assertEqual(received["authorization"], "Bearer agent-secret")
            self.assertEqual(received["hello"]["type"], "hello")
            self.assertEqual(received["hello"]["roots"][0]["id"], ROOT_ID)

    async def test_mutation_cancellation_waits_for_actual_outcome(self) -> None:
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
            agent = ExecutionAgent(configuration)
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
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            release.set()
            self.assertEqual((await task)["path"], "file.txt")

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
                ExecutionAgent(AgentConfig.model_validate(values))

            values["processExecutables"] = {"shell": Path("/bin/sh")}
            values["shellExecutable"] = Path("/bin/sh")
            agent = ExecutionAgent(AgentConfig.model_validate(values))
            self.assertTrue(agent.process.executables["shell"].is_file())
            self.assertEqual(agent.process.executables["shell"], Path("/bin/sh").resolve())
            self.assertIn("process.exec", agent.operations)
            self.assertIn("shell.run", agent.operations)

    def test_complete_response_serialization_is_bounded(self) -> None:
        response = Response(id=REQUEST_ID, ok=True, result={"content": "x" * 1024 * 1024})
        with self.assertRaises(AgentError):
            serialize_message(response)


if __name__ == "__main__":
    unittest.main()
