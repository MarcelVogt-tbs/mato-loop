import asyncio
import hashlib
import io
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
from matoloop_agent.protocol import Error, Hello, Request, Response, SystemInfo

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
            received = {"hellos": []}

            async def handler(websocket) -> None:
                received["authorization"] = websocket.request.headers["Authorization"]
                received["hellos"].append(json.loads(await websocket.recv()))
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
                agent = MatoLoopAgent(config, "agent-secret")
                snapshots = [
                    {
                        "operatingSystem": "Linux",
                        "distribution": f"Test Linux {number}",
                        "kernel": "6.12.0",
                        "architecture": "x86_64",
                        "cpuModel": f"Test CPU {number}",
                        "logicalCpuCount": number,
                        "totalMemoryBytes": number * 1024,
                    }
                    for number in (1, 2)
                ]
                with patch.object(
                    agent,
                    "system_info",
                    side_effect=[
                        {**info, "agentVersion": "1.2.3", "memory": {"availableBytes": 0}}
                        for info in snapshots
                    ],
                ) as collect:
                    await agent._connect_once(asyncio.Event())
                    await agent._connect_once(asyncio.Event())
                self.assertEqual([call.args for call in collect.call_args_list], [(None,), (None,)])

            for hello, info in zip(received["hellos"], snapshots, strict=True):
                self.assertEqual(hello["systemInfo"], info)
                self.assertIsInstance(Hello.model_validate(hello).system_info, SystemInfo)
            hello = received["hellos"][-1]
            self.assertEqual(received["authorization"], "Bearer agent-secret")
            self.assertEqual(hello["protocolVersion"], 1)
            self.assertEqual(hello["computerName"], socket.gethostname())
            self.assertEqual(hello["homeDirectory"], os.fspath(base))
            self.assertIn("agentVersion", hello)
            self.assertNotIn("name", hello)
            self.assertNotIn("roots", hello)
            self.assertNotIn("dataDir", hello)

    def test_system_info_refreshes_linux_probes_and_preserves_live_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent = MatoLoopAgent(configuration(Path(temporary)), "secret")
            files = {
                "/etc/os-release": 'NAME=Test\nPRETTY_NAME="Test Linux 1.2"\n',
                "/proc/cpuinfo": "Processor: fallback\nHardware: board\nmodel name: Test CPU\n",
                "/proc/meminfo": "MemTotal: 8192 kB\nMemAvailable: 2048 kB\n",
            }
            with (
                patch("matoloop_agent.connection.sys.platform", "linux"),
                patch(
                    "matoloop_agent.connection.os.uname",
                    return_value=os.uname_result(("Linux", "host", "6.12", "version", "x86_64")),
                ),
                patch("matoloop_agent.connection.os.cpu_count", return_value=8),
                patch(
                    "matoloop_agent.connection.open",
                    side_effect=lambda path, **_: io.StringIO(files[path]),
                ),
            ):
                info = agent.hello().system_info
                self.assertEqual(
                    info.model_dump(by_alias=True),
                    {
                        "operatingSystem": "Linux",
                        "distribution": "Test Linux 1.2",
                        "kernel": "6.12",
                        "architecture": "x86_64",
                        "cpuModel": "Test CPU",
                        "logicalCpuCount": 8,
                        "totalMemoryBytes": 8192 * 1024,
                    },
                )
                live = agent.system_info(temporary)
                self.assertEqual(
                    live["memory"], {"totalBytes": 8192 * 1024, "availableBytes": 2048 * 1024}
                )
                self.assertEqual(live["disk"]["path"], temporary)
                self.assertGreater(live["disk"]["totalBytes"], 0)
                self.assertIn("availableBytes", live["disk"])
                self.assertEqual(live["platform"], "linux")
                files["/etc/os-release"] = 'PRETTY_NAME="Test Linux 2.0"\n'
                files["/proc/cpuinfo"] = "Processor: fallback\nHardware: new board\n"
                files["/proc/meminfo"] = "malformed\nMemAvailable: bad\nMemTotal: 16384 kB\n"
                refreshed = agent.hello().system_info
                self.assertEqual(refreshed.distribution, "Test Linux 2.0")
                self.assertEqual(refreshed.cpu_model, "new board")
                self.assertEqual(refreshed.total_memory_bytes, 16384 * 1024)
                files["/proc/meminfo"] = "MemTotal: 32768 kB\n"
                files["/proc/cpuinfo"] = "processor: 0\nProcessor: 0\n"
                with patch("matoloop_agent.connection.os.cpu_count", return_value=16):
                    refreshed = agent.hello().system_info
                self.assertEqual(refreshed.logical_cpu_count, 16)
                self.assertEqual(refreshed.total_memory_bytes, 32768 * 1024)
                self.assertIsNone(refreshed.cpu_model)
                files["/proc/meminfo"] += "MemAvailable: 0 kB\n"
                self.assertEqual(agent.system_info(None)["memory"]["availableBytes"], 0)

    def test_hello_tolerates_missing_and_malformed_system_probes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent = MatoLoopAgent(configuration(Path(temporary)), "secret")
            with (
                patch("matoloop_agent.connection.sys.platform", "linux"),
                patch("matoloop_agent.connection.os.uname", side_effect=OSError),
                patch("matoloop_agent.connection.os.cpu_count", side_effect=OSError),
                patch("matoloop_agent.connection.open", side_effect=FileNotFoundError),
            ):
                self.assertEqual(json.loads(serialize_message(agent.hello()))["systemInfo"], {})
            files = {
                "/etc/os-release": 'PRETTY_NAME="unterminated\n',
                "/usr/lib/os-release": "PRETTY_NAME=\n",
                "/proc/cpuinfo": "processor: 0\nmodel name: \nProcessor: fallback\n",
                "/proc/meminfo": "MemTotal: bad kB\nMemAvailable: 0 kB\n",
            }
            with (
                patch("matoloop_agent.connection.sys.platform", "linux"),
                patch(
                    "matoloop_agent.connection.os.uname",
                    return_value=os.uname_result(("L" * 100, "host", "K" * 200, "version", " ")),
                ),
                patch(
                    "matoloop_agent.connection.open",
                    side_effect=lambda path, **_: io.StringIO(files[path]),
                ),
            ):
                for count in (None, 0, -1, True, "8", 2147483648):
                    with patch("matoloop_agent.connection.os.cpu_count", return_value=count):
                        info = agent.hello().system_info
                        self.assertIsNone(info.logical_cpu_count)
                        self.assertIsNone(info.total_memory_bytes)
                        self.assertIsNone(info.distribution)
                        self.assertIsNone(info.architecture)
                        self.assertEqual(info.operating_system, "L" * 80)
                        self.assertEqual(info.kernel, "K" * 160)
                        self.assertEqual(info.cpu_model, "fallback")
                files["/etc/os-release"] = 'PRETTY_NAME="' + "D" * 300 + '"\n'
                files["/proc/cpuinfo"] = "model name: " + "C" * 300
                for total in ("", "0 kB", "-1 kB", "9007199254740992 kB", "1 MB", "1.5 kB"):
                    files["/proc/meminfo"] = f"MemTotal: {total}\nMemAvailable: 0 kB\n"
                    info = agent.hello().system_info
                    self.assertIsNone(info.total_memory_bytes)
                    self.assertEqual(info.distribution, "D" * 255)
                    self.assertEqual(info.cpu_model, "C" * 255)

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
