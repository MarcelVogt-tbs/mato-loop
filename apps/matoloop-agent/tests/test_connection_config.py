import asyncio
import hashlib
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import websockets
from pydantic import ValidationError

from matoloop_agent.config import AgentConfig
from matoloop_agent.connection import MatoLoopAgent, serialize_message
from matoloop_agent.filesystem import AgentError
from matoloop_agent.protocol import Error, Request, Response

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
REQUEST_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"


def configuration(home: Path, **values) -> AgentConfig:
    config = AgentConfig.model_validate(
        {
            "homeDirectory": home,
            "backendUrl": "ws://localhost/api/execution-agents/connect",
            "agentId": AGENT_ID,
            "allowInsecure": True,
            **values,
        }
    )
    config._data_directory = home
    return config


class ConnectionConfigTests(unittest.IsolatedAsyncioTestCase):
    def test_config_parent_is_data_directory_and_removed_fields_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            path = base / "config.json"
            path.write_text(json.dumps({"homeDirectory": os.fspath(base)}))
            config = AgentConfig.load(path)
            self.assertEqual(config.data_dir, base)

            path.write_text(
                json.dumps(
                    {
                        "homeDirectory": os.fspath(base),
                        "processExecutables": {"python": "/usr/bin/python3"},
                    }
                )
            )
            self.assertEqual(AgentConfig.load(path).home_directory, base)

            for removed in ("name", "roots", "dataDir", "gitExecutable", "allowNetwork"):
                path.write_text(json.dumps({"homeDirectory": os.fspath(base), removed: "old"}))
                with self.subTest(removed=removed):
                    with self.assertRaises(ValidationError) as rejected:
                        AgentConfig.load(path)
                    self.assertEqual(rejected.exception.errors()[0]["type"], "extra_forbidden")

    async def test_hello_uses_protocol_one_public_agent_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            config_path = base / "config.json"
            received = {}

            async def handler(websocket) -> None:
                received["authorization"] = websocket.request.headers["Authorization"]
                received["hello"] = json.loads(await websocket.recv())
                await websocket.send(
                    json.dumps({"type": "welcome", "protocolVersion": 1, "agentId": AGENT_ID})
                )
                await websocket.close()

            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                config_path.write_text(
                    json.dumps(
                        {
                            "backendUrl": f"ws://127.0.0.1:{port}/api/execution-agents/connect",
                            "agentId": AGENT_ID,
                            "homeDirectory": os.fspath(base),
                            "allowInsecure": True,
                        }
                    )
                )
                config = AgentConfig.load(config_path)
                await MatoLoopAgent(config, "agent-secret")._connect_once(asyncio.Event())

            hello = received["hello"]
            self.assertEqual(received["authorization"], "Bearer agent-secret")
            self.assertEqual(hello["protocolVersion"], 1)
            self.assertEqual(hello["computerName"], socket.gethostname())
            self.assertEqual(hello["homeDirectory"], os.fspath(base))
            self.assertIn("agentVersion", hello)
            self.assertNotIn("name", hello)
            self.assertNotIn("roots", hello)
            self.assertNotIn("dataDir", hello)

    async def test_mutation_cancellation_waits_for_truthful_thread_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            started = threading.Event()
            release = threading.Event()
            received = {}

            def delayed_write(*_args) -> dict:
                started.set()
                release.wait(2)
                return {
                    "path": os.fspath(root / "file.txt"),
                    "size": 1,
                    "sha256": "a" * 64,
                }

            async def handler(websocket) -> None:
                await websocket.recv()
                await websocket.send(
                    json.dumps({"type": "welcome", "protocolVersion": 1, "agentId": AGENT_ID})
                )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "request",
                            "id": REQUEST_ID,
                            "operation": "filesystem.write",
                            "arguments": {
                                "path": os.fspath(root / "file.txt"),
                                "content": "x",
                            },
                        }
                    )
                )
                await asyncio.to_thread(started.wait, 1)
                await websocket.send(json.dumps({"type": "cancel", "id": REQUEST_ID}))
                try:
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(websocket.recv(), 0.05)
                finally:
                    release.set()
                received.update(json.loads(await websocket.recv()))
                await websocket.close()

            async with websockets.serve(handler, "127.0.0.1", 0) as server:
                port = server.sockets[0].getsockname()[1]
                agent = MatoLoopAgent(
                    configuration(
                        root,
                        backendUrl=f"ws://127.0.0.1:{port}/api/execution-agents/connect",
                    ),
                    "secret",
                )
                agent.filesystem.write = delayed_write
                await agent._connect_once(asyncio.Event())

            self.assertTrue(received["ok"])
            self.assertEqual(received["result"]["sha256"], "a" * 64)

    async def test_cancelled_mutation_reports_its_actual_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = MatoLoopAgent(configuration(root), "secret")
            started = threading.Event()
            release = threading.Event()

            def delayed_failure(*_args) -> dict:
                started.set()
                release.wait(2)
                raise AgentError("file.changed", "changed")

            agent.filesystem.write = delayed_failure
            request = Request.model_validate(
                {
                    "type": "request",
                    "id": REQUEST_ID,
                    "operation": "filesystem.write",
                    "arguments": {"path": os.fspath(root / "file.txt"), "content": "x"},
                }
            )
            task = asyncio.create_task(agent.dispatch(request))
            await asyncio.to_thread(started.wait, 1)
            task.cancel()
            release.set()
            with self.assertRaisesRegex(AgentError, "changed"):
                await task

    async def test_dispatches_absolute_filesystem_operations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = MatoLoopAgent(configuration(root), "secret")
            path = os.fspath(root / "app.txt")
            self.assertIn("filesystem.upsert", agent.operations)
            await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "filesystem.upsert",
                        "arguments": {"path": path, "content": "old"},
                    }
                )
            )
            result = await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": REQUEST_ID,
                        "operation": "filesystem.edit",
                        "arguments": {"path": path, "oldText": "old", "newText": "new"},
                    }
                )
            )
            self.assertEqual((root / "app.txt").read_text(), "new")
            self.assertEqual(result["sha256"], hashlib.sha256(b"new").hexdigest())

    async def test_connection_retry_reloads_rotated_access_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent = MatoLoopAgent(configuration(Path(temporary)), "old-key")
            loader = AsyncMock(side_effect=["new-key", "rotated-key"])
            with (
                patch.object(
                    agent,
                    "_connect_once",
                    new=AsyncMock(side_effect=[RuntimeError(), asyncio.CancelledError()]),
                ),
                patch("matoloop_agent.connection.asyncio.sleep", new=AsyncMock()),
                self.assertRaises(asyncio.CancelledError),
            ):
                await agent.run_forever(loader)
            self.assertEqual(agent._access_key, "rotated-key")
            self.assertEqual(agent.transfer._access_key, "rotated-key")

    async def test_connection_backoff_resets_after_successful_handshake(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent = MatoLoopAgent(configuration(Path(temporary)), "access-key")
            attempts = 0

            async def connect(connected: asyncio.Event) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 3:
                    connected.set()
                if attempts == 4:
                    raise asyncio.CancelledError
                raise RuntimeError

            with (
                patch.object(agent, "_connect_once", side_effect=connect),
                patch("matoloop_agent.connection.asyncio.sleep", new=AsyncMock()) as sleep,
                self.assertRaises(asyncio.CancelledError),
            ):
                await agent.run_forever()

            self.assertEqual([call.args[0] for call in sleep.await_args_list], [1.0, 2.0, 1.0])

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
            json.loads(serialize_message(error))["error"]["code"], "openscad.renderFailed"
        )


if __name__ == "__main__":
    unittest.main()
