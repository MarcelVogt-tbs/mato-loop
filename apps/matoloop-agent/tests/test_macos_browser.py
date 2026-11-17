import asyncio
import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from matoloop_agent.browser import _Setup
from matoloop_agent.browser_contracts import BrowserOpenArguments, BrowserSetupReadArguments
from matoloop_agent.browser_macos import (
    MacOSBrowserManager,
    MacOSBrowserRuntime,
    browser_packages,
    finish_setup,
    setup_command,
)
from matoloop_agent.browser_worker import BrowserWorker, serve
from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError


@unittest.skipIf(sys.platform == "win32", "POSIX browser lifecycle")
class MacOSBrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        config = AgentConfig.model_validate(
            {
                "homeDirectory": self.root,
                "backendUrl": "ws://localhost/api/execution-agents/connect",
                "allowInsecure": True,
                "agentId": str(uuid4()),
            }
        )
        config._data_directory = self.root
        self.manager = MacOSBrowserManager(config, None)
        self.addAsyncCleanup(self.manager.close)

    def test_native_user_environment_is_allowlisted(self):
        with (
            patch("sys.platform", "darwin"),
            patch("platform.machine", return_value="arm64"),
            patch("os.getuid", return_value=501),
            patch("pwd.getpwuid", return_value=SimpleNamespace(pw_name="browser-user")),
            patch.dict(
                os.environ, {"MATOLOOP_AGENT_PAIRING_TOKEN": "private", "HTTPS_PROXY": "proxy"}
            ),
        ):
            environment, options = self.manager.environment()
            setup = self.manager.setup_environment()
            self.assertTrue(self.manager.supported)
        self.assertEqual(options, {})
        self.assertNotIn("MATOLOOP_AGENT_PAIRING_TOKEN", setup)
        self.assertNotIn("HTTPS_PROXY", environment)
        self.assertEqual(setup["HTTPS_PROXY"], "proxy")
        self.assertEqual(setup["PLAYWRIGHT_SKIP_BROWSER_GC"], "1")
        self.assertNotIn("homebrew", environment["PATH"].lower())
        for uid, machine in ((0, "arm64"), (501, "x86_64")):
            with (
                patch("sys.platform", "darwin"),
                patch("os.getuid", return_value=uid),
                patch("platform.machine", return_value=machine),
            ):
                with self.assertRaises(AgentError):
                    self.manager.environment()

    async def test_chat_workers_use_native_runtime_and_remain_isolated(self):
        owner = str(uuid4())

        async def spawn(runtime, _root):
            runtime.worker = Mock(returncode=None)

        with (
            patch.object(MacOSBrowserRuntime, "supported", True),
            patch.object(MacOSBrowserRuntime, "runtime_root", side_effect=lambda root: root),
            patch.object(MacOSBrowserRuntime, "spawn", spawn),
            patch.object(MacOSBrowserRuntime, "rpc", AsyncMock(return_value={"state": "ready"})),
            patch.object(MacOSBrowserRuntime, "stop_worker", AsyncMock()),
        ):
            for _ in range(2):
                await self.manager.dispatch(
                    "integration.browser.open",
                    BrowserOpenArguments(
                        ownerId=owner, browserId=str(uuid4()), sessionId=str(uuid4())
                    ),
                )
            first, second = self.manager.runtimes.values()
            self.assertIsInstance(first, MacOSBrowserRuntime)
            self.assertIsNot(first.worker, second.worker)
            await self.manager.close()

    def test_storage_rejects_other_owner_and_symlink(self):
        root = self.manager.storage(str(uuid4()))
        with self.assertRaises(AgentError):
            self.manager.storage(str(uuid4()))
        (root / "browser").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(AgentError):
            self.manager.storage_directory(root / "browser")
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)

    async def test_noninteractive_setup_output_can_be_replayed(self):
        output = asyncio.StreamReader()
        output.feed_data(b"Installing Chromium.\n")
        output.feed_eof()
        entry = _Setup(str(uuid4()), Mock(stdout=output, returncode=0, wait=AsyncMock()))
        self.manager.setup = entry
        await self.manager.setup_output(entry)
        args = BrowserSetupReadArguments(setupId=entry.terminal_id, offset=0)
        first = await self.manager.setup_io("setupread", args)
        self.assertEqual(first, await self.manager.setup_io("setupread", args))
        self.assertEqual(base64.b64decode(first["data"]), b"Installing Chromium.\n")
        with self.assertRaises(AgentError):
            await self.manager.setup_io("setupwrite", args)

    async def test_loopback_view_requires_runtime_secret(self):
        worker = BrowserWorker(self.root)

        async def view(reader, writer):
            writer.write(b"frame")
            await writer.drain()

        worker.view_client = AsyncMock(side_effect=view)
        server = await asyncio.start_server(worker.authenticated_view, "127.0.0.1", 0)
        self.addCleanup(server.close)
        endpoint = {"port": server.sockets[0].getsockname()[1], "token": worker.view_token.hex()}
        async with asyncio.timeout(5):
            with self.assertRaises((AgentError, asyncio.IncompleteReadError)):
                await self.manager.view_connection({**endpoint, "token": bytes(32).hex()})
            worker.view_client.assert_not_awaited()
            reader, writer = await self.manager.view_connection(endpoint)
            try:
                self.assertEqual(await reader.readexactly(5), b"frame")
            finally:
                writer.close()
                await writer.wait_closed()
        server.close()
        await server.wait_closed()

    async def test_macos_worker_uses_private_directory_native_ua_and_authenticated_stream(self):
        executable = self.root / "chromium"
        executable.touch()
        page = Mock(bring_to_front=AsyncMock(), screenshot=AsyncMock())
        context = Mock(pages=[page], browser=Mock(version="test"))
        chromium = Mock(
            executable_path=str(executable),
            launch_persistent_context=AsyncMock(return_value=context),
        )
        worker = BrowserWorker(self.root)
        worker.add_page = Mock()
        worker.page_sessions[page] = asyncio.get_running_loop().create_future()
        worker.page_sessions[page].set_result(None)
        server = Mock(sockets=[Mock(getsockname=Mock(return_value=("localhost", 12345)))])
        with (
            patch("sys.platform", "darwin"),
            patch("matoloop_agent.browser_worker.async_playwright") as playwright,
            patch("asyncio.start_server", AsyncMock(return_value=server)) as start_server,
            patch("subprocess.check_output") as command,
        ):
            playwright.return_value.start = AsyncMock(return_value=Mock(chromium=chromium))
            try:
                await worker.start()
                launch = chromium.launch_persistent_context.call_args.kwargs
                self.assertTrue(launch["chromium_sandbox"])
                self.assertNotIn("user_agent", launch)
                command.assert_not_called()
                self.assertEqual(worker.live.parent, self.root)
                self.assertEqual(worker.live.stat().st_mode & 0o777, 0o700)
                self.assertEqual(start_server.call_args.args[0], worker.authenticated_view)
                self.assertEqual(worker.view_endpoint["token"], worker.view_token.hex())
            finally:
                worker.temporary.cleanup()

    async def test_native_worker_applies_posix_limits_and_denies_root(self):
        import resource

        loop = asyncio.get_running_loop()
        worker = Mock(close=AsyncMock())
        reader = Mock(readline=AsyncMock(return_value=b""))
        with (
            patch("sys.platform", "darwin"),
            patch("platform.machine", return_value="arm64"),
            patch("os.getuid", return_value=501),
            patch("os.umask"),
            patch("resource.setrlimit") as limits,
            patch("matoloop_agent.browser_worker.BrowserWorker", return_value=worker),
            patch("asyncio.StreamReader", return_value=reader),
            patch("asyncio.StreamReaderProtocol"),
            patch.object(loop, "connect_read_pipe", AsyncMock()),
            patch.object(loop, "add_signal_handler"),
        ):
            await serve(self.root)
            self.assertEqual(limits.call_args_list[-1].args, (resource.RLIMIT_CORE, (0, 0)))
            self.assertEqual(limits.call_args_list[0].args[0], resource.RLIMIT_FSIZE)
            with patch("os.getuid", return_value=0), self.assertRaises(RuntimeError):
                await serve(self.root)

    async def test_setup_reuses_browser_group_and_private_uv(self):
        data = self.root / "data"
        data.mkdir()
        uv = self.root / "tools" / "bin" / "uv"
        uv.parent.mkdir(parents=True)
        uv.touch()
        packages = browser_packages(data)
        project = self.root / "checkout/apps/matoloop-agent"
        project.mkdir(parents=True)
        (project / "pyproject.toml").write_text(
            '[dependency-groups]\nbrowser = ["playwright==1.59.0"]\n'
        )
        (self.root / "install.json").write_text(
            json.dumps({"sourceType": "checkout", "source": str(self.root / "checkout")})
        )
        self.assertEqual(browser_packages(data), ["playwright==1.59.0"])
        (self.root / "install.json").unlink()
        process = Mock(pid=12345, wait=AsyncMock(return_value=0))
        with (
            patch.dict(
                os.environ,
                {
                    "MATOLOOP_BROWSER_DATA_DIR": str(data),
                    "MATOLOOP_BROWSER_ROOT": str(data),
                    "MATOLOOP_BROWSER_INSTALLATION": str(self.root / "tools" / "browser"),
                },
            ),
            patch("sys.prefix", str(self.root)),
            patch("sys.base_prefix", "/system"),
            patch("os.umask"),
            patch.object(MacOSBrowserRuntime, "setup_environment", return_value={}),
            patch.object(MacOSBrowserRuntime, "runtime_root", return_value=data),
            patch.object(MacOSBrowserRuntime, "spawn", AsyncMock()),
            patch.object(
                MacOSBrowserRuntime,
                "rpc",
                AsyncMock(
                    return_value={
                        "state": "ready",
                        "browserVersion": "test",
                        "playwrightVersion": "test",
                    }
                ),
            ),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn,
            patch("os.killpg"),
        ):
            self.assertEqual(await finish_setup(install_packages=True), 0)
            self.assertEqual(spawn.call_args_list[0].args[0], str(uv))
            self.assertEqual(list(spawn.call_args_list[0].args[-len(packages) :]), packages)
            self.assertTrue(spawn.call_args_list[0].kwargs["start_new_session"])
            self.assertIn("--no-shell", spawn.call_args_list[1].args)

    async def test_cancelled_setup_command_reaps_its_process_group(self):
        import signal

        process = Mock(pid=12345, wait=AsyncMock(side_effect=[asyncio.CancelledError, 0]))
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
            patch("os.killpg") as kill,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await setup_command([sys.executable, "-c", "pass"], {})
        kill.assert_called_once_with(process.pid, signal.SIGKILL)
