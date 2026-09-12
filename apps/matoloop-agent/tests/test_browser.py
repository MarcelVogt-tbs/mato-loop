import asyncio
import base64
import json
import os
import re
import shutil
import socket
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import websockets
from PIL import Image
from pydantic import ValidationError

from matoloop_agent.browser import CONTAINER_INSTALLATION, BrowserManager, browser_command
from matoloop_agent.browser_contracts import (
    BROWSER_ARGUMENT_MODELS,
    BROWSER_FILE_RESULT,
    BrowserActionResult,
    BrowserClipboardArguments,
    BrowserControlArguments,
    BrowserExecuteArguments,
    BrowserFileArguments,
    BrowserIdArguments,
    BrowserNavigationArguments,
    BrowserOpenArguments,
    BrowserOwnerArguments,
    BrowserResizeArguments,
    BrowserResizeResult,
    BrowserSetupReadArguments,
    BrowserStatusArguments,
)
from matoloop_agent.browser_worker import OPENBOX_CONFIG, BrowserWorker, launch_error_code, serve
from matoloop_agent.config import AgentConfig
from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.filesystem import AgentError
from matoloop_agent.protocol import ARGUMENT_MODELS


class BrowserTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.owner_id, self.browser_id = str(uuid4()), str(uuid4())
        config = AgentConfig.model_validate(
            {
                "homeDirectory": self.root,
                "backendUrl": "ws://localhost/api/execution-agents/connect",
                "allowInsecure": True,
                "agentId": str(uuid4()),
                "shellExecutable": Path("/bin/bash"),
            }
        )
        config._data_directory = self.root
        self.agent = MatoLoopAgent(config, "not-for-browser")
        self.manager = self.agent.browser

    def test_openbox_config_disables_desktop_switching(self):
        root = ET.fromstring(OPENBOX_CONFIG)
        namespace = {"ob": "http://openbox.org/3.4/rc"}
        self.assertEqual(root.findtext("ob:desktops/ob:number", namespaces=namespace), "1")
        self.assertEqual(root.findall("ob:mouse/ob:context[@name='Root']/*", namespace), [])

    async def test_translation_prompt_setting_is_verified_and_temporary_tab_closed(self):
        worker = BrowserWorker(self.root)
        settings = SimpleNamespace(goto=AsyncMock(), evaluate=AsyncMock(), close=AsyncMock())
        worker.context = SimpleNamespace(new_page=AsyncMock(return_value=settings))
        for result in (True, False, RuntimeError("settings unavailable")):
            with self.subTest(result=result):
                settings.close.reset_mock()
                settings.evaluate.side_effect = result if isinstance(result, Exception) else None
                settings.evaluate.return_value = result
                if result is True:
                    await worker.disable_translation_prompt()
                else:
                    with self.assertRaises(
                        RuntimeError if isinstance(result, Exception) else AgentError
                    ):
                        await worker.disable_translation_prompt()
                settings.goto.assert_awaited_with(
                    "chrome://settings/languages", wait_until="domcontentloaded"
                )
                self.assertIn(
                    "setPref('translate.enabled', false", settings.evaluate.call_args.args[0]
                )
                settings.close.assert_awaited_once()

    @unittest.skipUnless(
        os.environ.get("MATOLOOP_BROWSER_NAVIGATION_TEST") == "1", "Opt-in headed input fence test"
    )
    async def test_repeated_native_input_fences(self):
        worker = BrowserWorker(self.root)
        viewers, diagnostics, frames = [], [], []
        spawn = asyncio.create_subprocess_exec

        async def traced_spawn(*args, **kwargs):
            # Synthetic profile only; retain subprocess diagnostics on assertion failure.
            if args[0] == "x11vnc" and "-R" in args:
                log = tempfile.TemporaryFile()
                kwargs["stderr"] = log
                process = await spawn(*args, **kwargs)
                diagnostics.append((args, process, log))
                return process
            return await spawn(*args, **kwargs)

        async def rfb():
            reader, writer = await asyncio.open_unix_connection(worker.live / "vnc")
            viewers.append(writer)
            self.assertEqual(await reader.readexactly(12), b"RFB 003.008\n")
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            count = (await reader.readexactly(1))[0]
            self.assertIn(1, await reader.readexactly(count))
            writer.write(b"\x01")
            await writer.drain()
            self.assertEqual(await reader.readexactly(4), b"\x00" * 4)
            writer.write(b"\x01")
            await writer.drain()
            init = await reader.readexactly(24)
            await reader.readexactly(int.from_bytes(init[-4:], "big"))
            writer.write(struct.pack(">BBHii", 2, 0, 2, 0, -223))
            writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
            await writer.drain()
            update = await reader.readexactly(4)
            self.assertEqual(update[0], 0)
            for _ in range(int.from_bytes(update[2:], "big")):
                _, _, width, height, encoding = struct.unpack(
                    ">HHHHi", await reader.readexactly(12)
                )
                self.assertEqual(encoding, 0)
                await reader.readexactly(width * height * (init[4] // 8))

            async def updates():
                try:
                    while True:
                        writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
                        await writer.drain()
                        update = await reader.readexactly(4)
                        self.assertEqual(update[0], 0)
                        for _ in range(int.from_bytes(update[2:], "big")):
                            _, _, width, height, encoding = struct.unpack(
                                ">HHHHi", await reader.readexactly(12)
                            )
                            if encoding == -223:
                                continue
                            self.assertEqual(encoding, 0)
                            await reader.readexactly(width * height * (init[4] // 8))
                        await asyncio.sleep(0.02)
                except (ConnectionError, asyncio.IncompleteReadError):
                    pass

            frames.append(asyncio.create_task(updates()))
            return reader, writer

        try:
            with patch("asyncio.create_subprocess_exec", traced_spawn):
                async with asyncio.timeout(90):
                    self.assertEqual((await worker.start())["state"], "ready")
                    await worker.context.route(
                        "http://input-fence.test/**",
                        lambda route: route.fulfill(
                            content_type="text/html",
                            body='<textarea style="width:100%;height:300px">retained</textarea>',
                        ),
                    )
                    page = worker.context.pages[0]
                    await page.goto("http://input-fence.test/foreground")
                    background = await worker.context.new_page()
                    await background.goto("http://input-fence.test/background")
                    await background.evaluate(
                        "setInterval(() => history.replaceState({}, '', '?t=' + Date.now()), 50)"
                    )
                    await page.bring_to_front()
                    session = await worker.context.browser.new_browser_cdp_session()
                    processes = (await session.send("SystemInfo.getProcessInfo"))["processInfo"]
                    pid = next(p["id"] for p in processes if p["type"] == "browser")
                    for iteration in range(12):
                        _, writer = await rfb()
                        for width, height in ((551, 827), (1001, 701), (320, 240), (1280, 800)):
                            await worker.resize(width, height)
                        await worker.navigation_state()
                        self.assertEqual(
                            await worker.clipboard(
                                BrowserClipboardArguments(
                                    browserId=self.browser_id, action="write", text="clipboard"
                                )
                            ),
                            {"text": "clipboard"},
                        )
                        writer.write(struct.pack(">BBHI", 4, 1, 0, 0xFFE3))
                        writer.write(struct.pack(">BBHH", 5, 1, 30, 30))
                        await writer.drain()
                        await asyncio.sleep(0.1)
                        if iteration % 3 == 0:
                            tasks = list(worker.vnc_sessions)
                            writer.close()
                            await writer.wait_closed()
                            await asyncio.gather(*tasks)
                        sessions = list(worker.vnc_sessions.values())
                        self.assertEqual(await worker.control(False), {"ok": True})
                        self.assertTrue(all(server.returncode is None for server, _ in sessions))
                        self.assertEqual(
                            await worker.x_command(
                                "x11vnc",
                                "-norc",
                                "-nocmds",
                                "-display",
                                worker.environment["DISPLAY"],
                                "-auth",
                                worker.environment["XAUTHORITY"],
                                "-Q",
                                "DIRECT:pointer_mask",
                            ),
                            "aro=pointer_mask:0x0\n",
                        )
                        self.assertEqual(
                            await worker.clipboard(
                                BrowserClipboardArguments(browserId=self.browser_id, action="read")
                            ),
                            {"text": "clipboard"},
                        )
                        writer.close()  # Backend notifies/disconnects the old viewer here.
                        await writer.wait_closed()
                        self.assertEqual(await worker.control(True), {"ok": True})
                        await frames[-1]
                    processes = (await session.send("SystemInfo.getProcessInfo"))["processInfo"]
                    self.assertEqual(
                        next(p["id"] for p in processes if p["type"] == "browser"), pid
                    )
                    self.assertEqual(await page.locator("textarea").input_value(), "retained")
                    self.assertIn("?t=", background.url)
                    print(
                        f"REPEATED INPUT FENCE PASS PID {pid}: 12 handoffs, "
                        f"{sum(p.returncode != 0 for _, p, _ in diagnostics)} unacknowledged "
                        "attempts recovered; same browser, retained document/clipboard, "
                        "live read-only stream, native input cleared",
                        flush=True,
                    )
        except BaseException:
            for args, process, log in diagnostics:
                log.seek(0)
                print("SYNTHETIC X11VNC", args, process.returncode, log.read().decode(), flush=True)
            raise
        finally:
            for writer in viewers:
                writer.close()
            await worker.close()
            await asyncio.gather(*frames)
            for _, _, log in diagnostics:
                log.close()

    async def test_input_fence_failure_is_not_acknowledged(self):
        worker = BrowserWorker(self.root)
        worker.environment = {"DISPLAY": ":private", "XAUTHORITY": "/private/Xauthority"}
        process = SimpleNamespace(
            returncode=0,
            stdout=SimpleNamespace(
                readexactly=AsyncMock(
                    side_effect=asyncio.IncompleteReadError(b"ans=viewonly:0", 1025)
                )
            ),
            wait=AsyncMock(),
        )
        task = asyncio.create_task(asyncio.sleep(10))
        worker.vnc_sessions[task] = (SimpleNamespace(returncode=None), worker.environment)
        timeout = asyncio.timeout
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)),
            patch("asyncio.timeout", lambda _: timeout(0.1)),
            patch.object(worker, "x_command", AsyncMock()) as direct,
        ):
            with self.assertRaises(TimeoutError):
                await worker.control(False)
            direct.assert_not_awaited()
        self.assertTrue(task.cancelled())
        self.assertFalse(worker.manual)
        worker.vnc_sessions.clear()
        with patch.object(
            worker, "x_command", AsyncMock(side_effect=AgentError("browser.actionFailed", "Test"))
        ) as direct:
            for _ in range(2):
                with self.assertRaises(AgentError):
                    await worker.control(False)
            self.assertEqual(direct.await_count, 2)  # Failed fences cannot become no-op successes.

        task = asyncio.create_task(asyncio.sleep(10))
        worker.vnc_sessions[task] = (SimpleNamespace(returncode=None), worker.environment)
        with (
            patch("asyncio.create_subprocess_exec", AsyncMock()) as remote,
            patch.object(worker, "x_command", AsyncMock(return_value="")) as direct,
        ):
            self.assertEqual(await worker.control(False, rotate_view=True), {"ok": True})
            remote.assert_not_awaited()
            direct.assert_awaited_once()
        self.assertTrue(task.cancelled())

    def test_bounded_contract_and_operations(self):
        self.assertEqual(len(ARGUMENT_MODELS) + len(BROWSER_ARGUMENT_MODELS), 55)
        self.assertLessEqual(len(self.agent.hello().operations), 64)
        for width, height in ((320, 240), (3840, 2160), (1001, 701)):
            args = BrowserResizeArguments(browserId=self.browser_id, width=width, height=height)
            self.assertEqual((args.width, args.height), (width, height))
        for invalid in (
            {"width": 319},
            {"width": 3841},
            {"height": 239},
            {"height": 2161},
            {"width": True},
            {"width": "1001"},
            {"height": 701.0},
            {"display": ":0"},
            {"output": "screen"},
            {"modeline": "injected"},
        ):
            with self.assertRaises(ValidationError):
                BrowserResizeArguments.model_validate(
                    {"browserId": self.browser_id, "width": 1001, "height": 701, **invalid}
                )
        with self.assertRaises(ValidationError):
            BrowserResizeResult(width=1001, height=701, browserId=self.browser_id)
        for args in (
            {"action": "evaluate"},
            {"action": "navigate", "url": "file:///etc/passwd"},
            {"action": "fill", "selector": "input"},
            {"action": "tabs", "tabId": "../../file"},
        ):
            with self.assertRaises(ValidationError):
                BrowserExecuteArguments(browserId=self.browser_id, **args)
        with self.assertRaises(ValidationError):
            BrowserFileArguments(browserId=self.browser_id, action="begin", filename="../x", size=1)
        with self.assertRaises(ValidationError):
            BrowserFileArguments(browserId=self.browser_id, action="choose")
        with self.assertRaises(ValidationError):
            BrowserFileArguments(
                browserId=self.browser_id, action="choose", fileId=str(uuid4()), selector="input"
            )
        self.assertEqual(
            BROWSER_FILE_RESULT.validate_python({"ok": True}).model_dump(), {"ok": True}
        )
        with self.assertRaises(ValidationError):
            BROWSER_FILE_RESULT.validate_python({"ok": False})
        for arguments in (
            {"action": "write"},
            {"action": "read", "text": "bad"},
            {"action": "copy", "text": "bad"},
            {"action": "copy", "key": "Control+c"},
            {"action": "write", "text": "\u00e4" * 8193},
        ):
            with self.assertRaises(ValidationError):
                BrowserClipboardArguments(browserId=self.browser_id, **arguments)
        with self.assertRaises(ValidationError):
            BrowserFileArguments(
                browserId=self.browser_id,
                action="write",
                fileId=str(uuid4()),
                data=base64.b64encode(b"x" * 65_537).decode(),
            )

    async def test_opaque_staging_limits_and_symlink_rejection(self):
        worker = BrowserWorker(self.root)
        worker.live = self.root

        async def file(action, **kwargs):
            return await worker.file(
                BrowserFileArguments(browserId=self.browser_id, action=action, **kwargs)
            )

        entry = await file("begin", filename="test.txt", size=3)
        file_id = entry["fileId"]
        with self.assertRaises(AgentError):
            await file("finish", fileId=file_id)
        result = await file("write", fileId=file_id, data="YWJj")
        self.assertEqual(result["offset"], 3)
        with self.assertRaises(AgentError):
            await file("write", fileId=file_id, offset=3, data="ZA==")
        self.assertTrue((await file("finish", fileId=file_id))["complete"])
        with self.assertRaises(AgentError):
            await file("read", fileId=file_id)
        worker.files[file_id]["download"] = True
        self.assertEqual((await file("read", fileId=file_id, offset=1, maxBytes=1))["data"], "Yg==")
        (self.root / file_id).unlink()
        (self.root / file_id).symlink_to("/etc/passwd")
        with self.assertRaises(OSError):
            await file("read", fileId=file_id)
        await file("delete", fileId=file_id)
        self.assertFalse((self.root / file_id).is_symlink())

    def test_network_diagnostics_are_bounded_and_omit_authentication(self):
        worker = BrowserWorker(self.root)
        worker.network_diagnostic(
            "response", "https://user:password@[::1]:443/path?token=secret#key", 503
        )
        self.assertEqual(worker.diagnostics[-1], "response 503 https://[::1]:443/path")
        worker.network_diagnostic("requestfailed", "https://user:password@example.test/fail?secret")
        self.assertEqual(worker.diagnostics[-1], "requestfailed https://example.test/fail")
        worker.network_diagnostic("requestfailed", "data:text/plain,secret")
        self.assertEqual(worker.diagnostics[-1], "requestfailed [URL omitted]")
        for _ in range(60):
            worker.network_diagnostic("response", "https://example.test/" + "x" * 1000, 200)
        self.assertEqual(len(worker.diagnostics), 50)
        self.assertTrue(all(len(entry) <= 500 for entry in worker.diagnostics))

    async def test_clipboard_write_waits_out_oversized_previous_selection(self):
        worker = BrowserWorker(self.root)
        worker.environment = {}
        owner = SimpleNamespace(
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock(), close=Mock()),
            returncode=None,
            wait=AsyncMock(),
            kill=Mock(),
        )
        old = SimpleNamespace(
            stdout=SimpleNamespace(readexactly=AsyncMock(return_value=b"x" * 16385)),
            returncode=0,
            wait=AsyncMock(),
        )
        current = SimpleNamespace(
            stdout=SimpleNamespace(
                readexactly=AsyncMock(side_effect=asyncio.IncompleteReadError(b"new", 16385))
            ),
            returncode=0,
            wait=AsyncMock(),
        )
        with patch(
            "asyncio.create_subprocess_exec", AsyncMock(side_effect=[owner, old, current])
        ) as spawn:
            result = await worker.clipboard(
                BrowserClipboardArguments(
                    browserId=self.browser_id,
                    action="write",
                    text="new",
                )
            )
        self.assertEqual(result, {"text": "new"})
        self.assertEqual(spawn.await_count, 3)

    def test_binding_and_clean_environment(self):
        before = self.root.stat().st_mode
        root = self.manager.storage(self.owner_id)
        self.assertEqual(root.parent, self.root / "tools" / "browser")
        self.assertEqual(self.root.stat().st_mode, before)
        self.assertEqual(self.manager.storage(self.owner_id), root)
        with self.assertRaises(AgentError) as error:
            self.manager.storage(str(uuid4()))
        self.assertEqual(error.exception.code, "browser.profileOwnerMismatch")
        (root / "binding").unlink()
        (root / "binding").symlink_to("/etc/passwd")
        with self.assertRaises(OSError):
            self.manager.storage(self.owner_id)
        with patch.dict(os.environ, {"MATOLOOP_AGENT_CONTAINER": "0"}):
            native = BrowserManager(self.agent.config, self.agent.terminal)
            self.assertEqual(native.installation, self.root.parent / "tools" / "browser")
        with patch.dict(os.environ, {"MATOLOOP_AGENT_CONTAINER": "1"}):
            container = BrowserManager(self.agent.config, self.agent.terminal)
            self.assertEqual(container.installation, CONTAINER_INSTALLATION)
        environment, options = self.manager.environment()
        self.assertNotIn("MATOLOOP_AGENT_PAIRING_TOKEN", environment)
        self.assertNotIn("MATOLOOP_AGENT_BACKEND_URL", environment)
        self.assertEqual(environment["HOME"], str(self.root / "tools" / "browser"))
        self.assertIn("/usr/sbin", environment["PATH"].split(":"))
        if os.getuid() == 0:
            self.assertNotEqual(options["user"], 0)
            self.assertEqual(options["extra_groups"], [])
        with patch("sys.frozen", True, create=True):
            self.assertEqual(
                browser_command("browser-install"), [os.sys.executable, "browser-install"]
            )

    def test_seccomp_excludes_vsock(self):
        app = Path(__file__).resolve().parents[1]
        profile = json.loads((app / "docker" / "browser-seccomp.json").read_text())
        rules = [rule for rule in profile["syscalls"] if "socket" in rule["names"]]
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["args"], [{"index": 0, "value": 40, "op": "SCMP_CMP_NE"}])
        if os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1":
            with self.assertRaises(PermissionError):
                socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)

    def test_native_setup_refreshes_metadata_before_package_detection(self):
        script = (
            Path(__file__).resolve().parents[1] / "src" / "matoloop_agent" / "browser-setup.sh"
        ).read_text()
        probe = script.index("apt-cache show libasound2t64")
        self.assertLess(script.index("    apt-get update"), probe)
        self.assertLess(script.index("    sudo -- apt-get update"), probe)

    async def test_worker_stderr_is_discarded_and_errors_are_controlled(self):
        process = SimpleNamespace(
            returncode=None,
            stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
            stdout=SimpleNamespace(
                readline=AsyncMock(return_value=b'{"ok":false,"error":"browser.notReady"}\n')
            ),
        )
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            await self.manager.spawn(self.root)
            self.assertEqual(spawn.call_args.kwargs["stderr"], asyncio.subprocess.DEVNULL)
            with self.assertLogs("matoloop_agent.browser", level="WARNING") as logs:
                with self.assertRaises(AgentError):
                    await self.manager.rpc("start")
            self.assertEqual(
                logs.output,
                [
                    "WARNING:matoloop_agent.browser:"
                    "Browser worker operation failed: browser.notReady",
                    "WARNING:matoloop_agent.browser:"
                    "Browser startup failed: stage=unknown profile=persistent",
                ],
            )

    async def test_open_passes_color_scheme_to_new_browser_context(self):
        self.manager.spawn = AsyncMock()
        self.manager.rpc = AsyncMock(return_value={"state": "ready"})

        result = await self.manager.dispatch(
            "integration.browser.open",
            BrowserOpenArguments(
                ownerId=self.owner_id, browserId=self.browser_id, colorScheme="dark"
            ),
        )

        self.assertEqual(result, {"browserId": self.browser_id})
        self.manager.rpc.assert_awaited_once_with("start", colorScheme="dark")

    async def test_worker_response_failures_log_only_controlled_diagnostics(self):
        for response, reason in (
            (b'{"private-token"', "invalidJson"),
            (b'{"ok":true,"result":{"private-token":true}}', "invalidResponse"),
            (OSError("private-token"), "transport"),
            (TimeoutError("private-token"), "timeout"),
        ):
            with self.subTest(reason=reason):
                readline = AsyncMock(
                    side_effect=response if isinstance(response, Exception) else None,
                    return_value=response if isinstance(response, bytes) else b"",
                )
                self.manager.worker = SimpleNamespace(
                    returncode=None,
                    stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
                    stdout=SimpleNamespace(readline=readline),
                )
                with (
                    patch.object(self.manager, "close", new=AsyncMock()) as close,
                    self.assertLogs("matoloop_agent.browser", level="WARNING") as logs,
                ):
                    with self.assertRaises(AgentError) as error:
                        await self.manager.rpc("execute", args={"action": "tabs"})
                self.assertEqual(error.exception.code, "browser.outcomeUnknown")
                close.assert_awaited_once()
                self.assertEqual(
                    logs.records[0].getMessage(),
                    f"Browser worker RPC failed: operation=execute reason={reason}",
                )
                self.assertNotIn("private-token", "\n".join(logs.output))

    async def test_execute_latency_is_private_and_logs_only_safe_metadata(self):
        args = BrowserExecuteArguments(
            browserId=str(uuid4()),
            action="fill",
            selector="#private-selector",
            text="private-input-token",
            url="https://private.test/path?token=private-url-token",
        )
        result = BrowserActionResult(text="private-page-content").model_dump(by_alias=True)
        self.manager.browser_id = self.browser_id
        self.manager.worker = SimpleNamespace(
            returncode=None,
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock()),
        )
        for ok in (True, False):
            with self.subTest(ok=ok):
                response = {
                    "ok": ok,
                    "elapsedMs": 12.5,
                    "metrics": {
                        "settleMs": 1800.0,
                        "settleOutcome": "network",
                        "snapshotMs": 102.0,
                        "snapshotCharacters": 32000,
                    },
                    "action": "private-worker-action",
                }
                response.update({"result": result} if ok else {"error": "browser.actionFailed"})
                self.manager.worker.stdout.readline.return_value = json.dumps(response).encode()
                with (
                    patch("matoloop_agent.browser.monotonic", side_effect=[10, 10.25]),
                    self.assertLogs("matoloop_agent.browser", level="INFO") as logs,
                ):
                    if ok:
                        self.assertEqual(
                            await self.manager.rpc("execute", args=args.model_dump(by_alias=True)),
                            result,
                        )
                    else:
                        with self.assertRaises(AgentError) as error:
                            await self.manager.rpc("execute", args=args.model_dump(by_alias=True))
                        self.assertEqual(error.exception.code, "browser.actionFailed")
                self.assertEqual(
                    logs.records[0].getMessage(),
                    "Browser execution latency: method=integration.browser.execute "
                    f"action=fill browser_id={self.browser_id} "
                    "pipe_roundtrip_ms=250.000 worker_elapsed_ms=12.5 "
                    "worker_phases={'settleMs': 1800.0, 'settleOutcome': 'network', "
                    "'snapshotMs': 102.0, 'snapshotCharacters': 32000}",
                )
                self.assertNotIn("private", "\n".join(logs.output))
                self.assertNotIn(args.browser_id, "\n".join(logs.output))

    async def test_execute_latency_ignores_malformed_optional_diagnostics(self):
        args = {"browserId": self.browser_id, "action": "tabs"}
        result = BrowserActionResult().model_dump(by_alias=True)
        self.manager.worker = SimpleNamespace(
            returncode=None,
            stdin=SimpleNamespace(write=Mock(), drain=AsyncMock()),
            stdout=SimpleNamespace(readline=AsyncMock()),
        )
        for diagnostic in (
            {},
            *(
                {"elapsedMs": value}
                for value in (
                    None,
                    True,
                    False,
                    -1,
                    55_001,
                    10**1000,
                    float("nan"),
                    float("inf"),
                    -float("inf"),
                    "private-token",
                    [],
                    {"private": "token"},
                )
            ),
            {"elapsedMs": 0},
            {"elapsedMs": 55_000},
        ):
            with self.subTest(diagnostic=diagnostic):
                self.manager.worker.stdout.readline.return_value = json.dumps(
                    {"ok": True, "result": result, **diagnostic}
                ).encode()
                with self.assertLogs("matoloop_agent.browser", level="INFO") as logs:
                    self.assertEqual(await self.manager.rpc("execute", args=args), result)
                value = diagnostic.get("elapsedMs")
                expected = value if type(value) is int and value in (0, 55_000) else None
                self.assertEqual(logs.records[0].args[:2], ("tabs", None))
                self.assertGreaterEqual(logs.records[0].args[2], 0)
                self.assertEqual(logs.records[0].args[3], expected)
                self.assertEqual(logs.records[0].args[4], {})
                self.assertNotIn("private", "\n".join(logs.output))
        for op, arguments in (
            ("private-method", args),
            ("execute", {**args, "action": "private-action"}),
            ("execute", {**args, "action": "fill"}),
            ("execute", None),
        ):
            with self.subTest(op=op, arguments=arguments):
                with self.assertNoLogs("matoloop_agent.browser", level="INFO"):
                    self.assertEqual(await self.manager.rpc(op, args=arguments), result)

    async def test_worker_times_only_validated_execute_in_private_envelope(self):
        args = {"browserId": self.browser_id, "action": "tabs"}
        requests = [
            {"op": "execute", "args": args},
            {"op": "execute", "args": args},
            {"op": "execute", "args": {**args, "action": "private-invalid-action"}},
            {"op": "health"},
        ]
        reader = asyncio.StreamReader()
        for request in requests:
            reader.feed_data(json.dumps(request).encode() + b"\n")
        reader.feed_eof()
        worker = BrowserWorker(self.root)
        result = BrowserActionResult().model_dump(by_alias=True)
        loop = asyncio.get_running_loop()
        with (
            patch("matoloop_agent.browser_worker.os.getuid", return_value=1000),
            patch("matoloop_agent.browser_worker.os.umask"),
            patch("matoloop_agent.browser_worker.sys.stdin", SimpleNamespace(buffer=Mock())),
            patch("matoloop_agent.browser_worker.resource.setrlimit"),
            patch("matoloop_agent.browser_worker.BrowserWorker", return_value=worker),
            patch("matoloop_agent.browser_worker.asyncio.StreamReader", return_value=reader),
            patch.object(loop, "connect_read_pipe", new=AsyncMock()),
            patch.object(loop, "add_signal_handler"),
            patch.object(
                worker,
                "execute",
                new=AsyncMock(
                    side_effect=[result, AgentError("browser.actionFailed", "private-error-token")]
                ),
            ) as execute,
            patch.object(worker, "close", new=AsyncMock()),
            patch("matoloop_agent.browser_worker.monotonic", side_effect=[1, 1.25, 2, 2.5]),
            patch("builtins.print") as output,
        ):
            await serve(self.root)
        responses = [json.loads(call.args[0]) for call in output.call_args_list]
        self.assertEqual(
            responses,
            [
                {"ok": True, "result": result, "elapsedMs": 250.0},
                {"ok": False, "error": "browser.actionFailed", "elapsedMs": 500.0},
                {"ok": False, "error": "browser.actionFailed"},
                {"ok": True, "result": {"ok": False}},
            ],
        )
        self.assertEqual(execute.await_count, 2)
        self.assertIsInstance(execute.await_args.args[0], BrowserExecuteArguments)

    def test_profile_lock_error_is_specific_without_mutating_singletons(self):
        lock = self.root / "SingletonLock"
        lock.symlink_to("old-container-12345")
        self.assertEqual(
            launch_error_code(
                RuntimeError(
                    "The profile appears to be in use by another Chromium process (12345) "
                    "on another computer (old-container). "
                    "Secret profile path /private/token. sandbox"
                )
            ),
            "profileInUse",
        )
        self.assertEqual(os.readlink(lock), "old-container-12345")
        self.assertEqual(
            launch_error_code(RuntimeError("No usable sandbox!")), "sandboxUnavailable"
        )
        self.assertEqual(
            launch_error_code(RuntimeError("Permission denied opening profile")), "notReady"
        )

    async def test_resize_cancellation_drains_rpc_without_closing_browser(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def rpc(*args, **kwargs):
            started.set()
            await finish.wait()
            return {"width": 1001, "height": 701}

        self.manager.worker = SimpleNamespace(returncode=None)
        self.manager.browser_id = self.browser_id
        with (
            patch.object(self.manager, "rpc", side_effect=rpc),
            patch.object(self.manager, "close", new=AsyncMock()) as close,
        ):
            pending = asyncio.create_task(
                self.manager.dispatch(
                    "integration.browser.resize",
                    BrowserResizeArguments(browserId=self.browser_id, width=1001, height=701),
                )
            )
            await started.wait()
            pending.cancel()
            await asyncio.sleep(0)
            self.assertTrue(self.manager.lock.locked())
            pending.cancel()
            await asyncio.sleep(0)
            self.assertTrue(self.manager.lock.locked())
            finish.set()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            close.assert_not_awaited()
            self.assertEqual(self.manager.browser_id, self.browser_id)

    async def test_copy_without_view_or_native_libraries_fails_cleanly(self):
        worker = BrowserWorker(self.root)
        args = BrowserClipboardArguments(browserId=self.browser_id, action="copy")
        with self.assertRaises(AgentError) as error:
            await worker.clipboard(args)
        self.assertEqual(error.exception.code, "browser.actionFailed")
        worker.vnc_sessions[None] = (SimpleNamespace(returncode=None), {"DISPLAY": ":private"})
        with patch("matoloop_agent.browser_worker.ctypes.CDLL", side_effect=OSError):
            with self.assertRaises(AgentError) as error:
                await worker.clipboard(args)
        self.assertEqual(error.exception.code, "browser.actionFailed")
        self.assertTrue(worker.manual)

    async def test_setup_requires_real_runtime_probe_and_preserves_private_parents(self):
        self.manager.container = True
        self.manager.installation = self.root / "missing-installation"
        before = self.root.stat().st_mode
        result = await self.manager.start_setup(self.owner_id)
        self.assertEqual(result["state"], "installing")
        await asyncio.wait_for(self.manager.setup.wait_task, 30)
        await self.manager.setup_task
        self.assertNotEqual(self.manager.setup.exit_code, 0)
        self.assertEqual(self.root.stat().st_mode, before)
        output = await self.manager.setup_io(
            "setupread", BrowserSetupReadArguments(setupId=result["setupId"], offset=0)
        )
        self.assertIn("Startpruefung fehlgeschlagen", base64.b64decode(output["data"]).decode())

    async def test_setup_replay_survives_terminal_and_browser_disconnect(self):
        entry = self.agent.terminal._spawn(
            str(uuid4()),
            [str(Path("/bin/bash").resolve()), "-c", "printf first; sleep .15; printf second"],
            str(self.root),
            24,
            80,
            {"PATH": "/usr/bin:/bin"},
        )
        self.manager.setup = entry
        self.manager.setup_task = asyncio.create_task(self.manager.setup_output(entry))
        await self.agent.terminal.close_all()
        await self.manager.close()
        await entry.wait_task
        await self.manager.setup_task
        args = BrowserSetupReadArguments(setupId=entry.terminal_id, offset=0)
        result = await self.manager.setup_io("setupread", args)
        self.assertEqual(base64.b64decode(result["data"]), b"firstsecond")
        self.assertEqual(result["offset"], 11)
        self.assertFalse(result["running"])
        self.assertEqual(await self.manager.setup_io("setupread", args), result)
        self.manager.setup_offset = 2_000_000
        entry.buffer[:] = b"last"
        result = await self.manager.setup_io("setupread", args)
        self.assertEqual(result["offset"], 2_000_000)
        self.assertEqual(base64.b64decode(result["data"]), b"last")

    async def test_binary_relay_reconnects_without_closing_runtime(self):
        seen = []

        async def vnc(reader, writer):
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            seen.append(await reader.readexactly(4))
            writer.close()
            await writer.wait_closed()

        async def backend(websocket):
            self.assertEqual(websocket.request.path, "/api/browser-relay/" + self.browser_id)
            self.assertEqual(websocket.request.headers["Authorization"], "Bearer test-relay-token")
            self.assertEqual(await websocket.recv(), b"RFB 003.008\n")
            await websocket.send(b"test")
            await websocket.wait_closed()

        async with (
            await asyncio.start_unix_server(vnc, path=str(self.root / "vnc")),
            websockets.serve(backend, "127.0.0.1", 0) as server,
        ):
            port = server.sockets[0].getsockname()[1]
            self.manager.config.backend_url = f"ws://127.0.0.1:{port}/api/execution-agents/connect"
            for _ in range(2):
                ready = asyncio.get_running_loop().create_future()
                task = asyncio.create_task(
                    self.manager.relay(
                        str(self.root / "vnc"), self.browser_id, "test-relay-token", ready
                    )
                )
                self.assertTrue(await ready)
                await asyncio.wait_for(task, 5)
        self.assertEqual(seen, [b"test", b"test"])

    @unittest.skipUnless(os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), "Bundled browser smoke test")
    async def test_real_sandboxed_headed_runtime(self):
        manager = self.manager
        # Test-only asset selection permits validation in an older image without rebuilding it.
        manager.installation = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
        manager.container = True
        self.root.chmod(0o711)
        received = []

        async def http(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
            except asyncio.IncompleteReadError:
                writer.close()
                await writer.wait_closed()
                return
            received.append(request)
            status = b"200 OK"
            if request.startswith(b"GET /download "):
                body = b"browser-download"
                headers = b"Content-Disposition: attachment; filename=download.txt\r\n"
            elif request.startswith(b"GET /fail "):
                writer.close()
                await writer.wait_closed()
                return
            elif request.startswith(b"GET /missing?"):
                status = b"503 Service Unavailable"
                body = b"Unavailable"
                headers = b"Content-Type: text/plain\r\n"
            elif request.startswith(b"GET /sw.js "):
                body = (
                    b"self.addEventListener('install',()=>self.skipWaiting());"
                    b"self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));"
                )
                headers = b"Content-Type: application/javascript\r\n"
            elif request.startswith(b"GET /identity "):
                body = b'<!doctype html><title>Identity</title><input id="secret" type="password">'
                headers = b"Content-Type: text/html\r\n"
            elif request.startswith(b"GET /screenshot-fixture "):
                body = (
                    b"<!doctype html><style>body{margin:0;height:300vh;background:lime;"
                    b"overflow:hidden}canvas{display:block}</style><canvas></canvas><script>"
                    b'const c=document.querySelector("canvas");'
                    b"function draw(){c.width=innerWidth;c.height=innerHeight;"
                    b'const x=c.getContext("2d"),d=x.createImageData(c.width,c.height);'
                    b"let seed=42;for(let i=0;i<d.data.length;i+=4){"
                    b"seed^=seed<<13;seed^=seed>>>17;seed^=seed<<5;"
                    b"d.data[i]=seed&255;d.data[i+1]=(seed>>>8)&255;"
                    b"d.data[i+2]=(seed>>>16)&255;d.data[i+3]=255;}"
                    b'x.putImageData(d,0,0);x.fillStyle="red";x.fillRect(0,0,c.width,40);'
                    b'x.fillStyle="blue";x.fillRect(0,c.height-40,c.width,40);}'
                    b'addEventListener("resize",draw);draw();</script>'
                )
                headers = b"Content-Type: text/html\r\n"
            else:
                body = (
                    b'<!doctype html><title>Browser smoke</title><input id="name">'
                    b'<input id="password" type="password"><textarea id="literal"></textarea>'
                    b'<form id="form" action="/submit"><input id="checked" type="checkbox"></form>'
                    b'<button id="retarget" onclick="'
                    b"document.getElementById('download').href='/other';"
                    b"document.getElementById('form').action='/other-submit'\">Retarget</button>"
                    b'<label>Suche<input placeholder="Suchbegriff"></label>'
                    b'<input id="file" type="file" onchange="document.getElementById(\'result\').'
                    b"innerText=this.files[0].name+':'+this.files[0].type\">"
                    b'<p id="result">Browser smoke</p>'
                    b'<a id="download" href="/download">Download</a>'
                    b'<button id="network" onclick="fetch(\'/missing?token=secret\');'
                    b"fetch('/fail').catch(()=>{})\">Network</button>"
                    b'<p id="sw">Service worker pending</p><script>'
                    b"document.hasFocus=()=>true;"
                    b"navigator.serviceWorker.register('/sw.js').then(()=>navigator.serviceWorker.ready)"
                    b".then(()=>document.getElementById('sw').textContent='Service worker ready');"
                    b"</script>"
                )
                headers = (
                    b"Content-Type: text/html\r\nSet-Cookie: smoke=persisted; Max-Age=86400\r\n"
                )
            writer.write(
                b"HTTP/1.1 "
                + status
                + b"\r\n"
                + headers
                + b"Content-Length: "
                + str(len(body)).encode()
                + b"\r\nConnection: close\r\n\r\n"
                + body
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(http, "127.0.0.1", 0)
        url = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/"

        async def execute(action, **kwargs):
            return await manager.dispatch(
                "integration.browser.execute",
                BrowserExecuteArguments(browserId=self.browser_id, action=action, **kwargs),
            )

        async def file(action, **kwargs):
            return await manager.dispatch(
                "integration.browser.file",
                BrowserFileArguments(browserId=self.browser_id, action=action, **kwargs),
            )

        async def control(manual):
            return await manager.dispatch(
                "integration.browser.control",
                BrowserControlArguments(browserId=self.browser_id, manual=manual),
            )

        async def screenshot_bytes(result):
            descriptor = result["screenshot"]
            chunks = []
            try:
                for offset in range(0, descriptor["size"], 65_536):
                    chunk = await file("read", fileId=descriptor["fileId"], offset=offset)
                    data = base64.b64decode(chunk["data"], validate=True)
                    self.assertLessEqual(len(data), 65_536)
                    self.assertEqual(chunk["size"], descriptor["size"])
                    self.assertEqual(chunk["offset"], offset + len(data))
                    self.assertEqual(chunk["complete"], chunk["offset"] == descriptor["size"])
                    chunks.append(data)
                image = b"".join(chunks)
                self.assertEqual(len(image), descriptor["size"])
                return image
            finally:
                await file("delete", fileId=descriptor["fileId"])

        async def clipboard(action, text=None):
            return await manager.dispatch(
                "integration.browser.clipboard",
                BrowserClipboardArguments(browserId=self.browser_id, action=action, text=text),
            )

        async def copied(expected):
            for _ in range(30):
                result = await clipboard("read")
                if result["text"] == expected:
                    return
                await asyncio.sleep(0.05)
            self.assertEqual(result, {"text": expected})

        async def rfb():
            path = (await manager.rpc("view"))["socket"]
            reader, writer = await asyncio.open_unix_connection(path)
            self.assertEqual(await reader.readexactly(12), b"RFB 003.008\n")
            writer.write(b"RFB 003.008\n")
            await writer.drain()
            count = (await reader.readexactly(1))[0]
            self.assertIn(1, await reader.readexactly(count))
            writer.write(b"\x01")
            await writer.drain()
            self.assertEqual(await reader.readexactly(4), b"\x00" * 4)
            writer.write(b"\x01")
            await writer.drain()
            init = await reader.readexactly(24)
            await reader.readexactly(int.from_bytes(init[-4:], "big"))
            writer.write(struct.pack(">BBHii", 2, 0, 2, 0, -223))
            writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
            await writer.drain()
            update = await asyncio.wait_for(reader.readexactly(4), 5)
            self.assertEqual(update[:2], b"\x00\x00")
            for _ in range(int.from_bytes(update[2:], "big")):
                rectangle = await reader.readexactly(12)
                _, _, width, height, encoding = struct.unpack(">HHHHi", rectangle)
                self.assertEqual(encoding, 0)
                await reader.readexactly(width * height * (init[4] // 8))
            writer.write(
                struct.pack(">BBHH", 5, 1, 640, 400) + struct.pack(">BBHH", 5, 0, 640, 400)
            )
            await writer.drain()
            await asyncio.sleep(1)
            return reader, writer

        async def key(writer, value, *, control=False):
            if control:
                writer.write(struct.pack(">BBHI", 4, 1, 0, 0xFFE3))
            writer.write(
                struct.pack(">BBHI", 4, 1, 0, value) + struct.pack(">BBHI", 4, 0, 0, value)
            )
            if control:
                writer.write(struct.pack(">BBHI", 4, 0, 0, 0xFFE3))
            await writer.drain()
            await asyncio.sleep(0.15)

        viewers = []

        try:
            setup = await manager.dispatch(
                "integration.browser.setup", BrowserOwnerArguments(ownerId=self.owner_id)
            )
            self.assertEqual(setup["state"], "installing")
            await manager.setup.wait_task
            await manager.setup_task
            self.assertEqual(manager.setup.exit_code, 0)
            status = await manager.dispatch("integration.browser.status", BrowserStatusArguments())
            print("BROWSER READINESS: " + json.dumps(status), flush=True)
            if status["errorCode"] == "browser.sandboxUnavailable":
                self.skipTest("Host denies Chromium sandbox; controlled readiness code returned")
            self.assertEqual(status["state"], "ready", status)
            result = await manager.dispatch(
                "integration.browser.open",
                BrowserOpenArguments(ownerId=self.owner_id, browserId=self.browser_id),
            )
            self.assertEqual(result["browserId"], self.browser_id)
            navigation = await manager.dispatch(
                "integration.browser.navigation",
                BrowserNavigationArguments(browserId=self.browser_id, action="state"),
            )
            self.assertEqual(
                set(navigation),
                {"tabs", "activeTabId", "canGoBack", "canGoForward", "loading", "nativeChrome"},
            )
            self.assertEqual(navigation["activeTabId"], navigation["tabs"][0]["id"])
            self.assertFalse(navigation["nativeChrome"])
            result = await manager.dispatch(
                "integration.browser.execute",
                BrowserExecuteArguments(browserId=self.browser_id, action="screenshot"),
            )
            self.assertTrue((await screenshot_bytes(result)).startswith(b"\x89PNG"))
            self.assertEqual(len(result["tabs"]), 1)
            await execute("navigate", url=url + "screenshot-fixture")
            for width, height, expected in (
                (1280, 800, (1280, 800)),
                (3840, 2160, (1464, 823)),
            ):
                await manager.dispatch(
                    "integration.browser.resize",
                    BrowserResizeArguments(browserId=self.browser_id, width=width, height=height),
                )
                async with asyncio.timeout(15):
                    while True:
                        capture = await execute("screenshot")
                        self.assertEqual(capture["downloads"], [])
                        data = await screenshot_bytes(capture)
                        with Image.open(BytesIO(data)) as image:
                            if image.size == expected:
                                self.assertGreater(len(data), 500_000)
                                self.assertEqual(
                                    image.getpixel((image.width // 2, 0))[:3], (255, 0, 0)
                                )
                                self.assertEqual(
                                    image.getpixel((image.width // 2, image.height - 1))[:3],
                                    (0, 0, 255),
                                )
                                break
                        await asyncio.sleep(0.1)
                print(
                    f"BROWSER SCREENSHOT: {width}x{height} -> {expected}, {len(data)} PNG bytes",
                    flush=True,
                )
            await manager.dispatch(
                "integration.browser.resize",
                BrowserResizeArguments(browserId=self.browser_id, width=1280, height=800),
            )
            await execute("navigate", url=url)
            literal = "\u00e4\u00f6\u00fc \u4e2d\u6587 \U0001f680\nsecond line\n"
            self.assertEqual(await clipboard("write", literal), {"text": literal})
            await execute("click", selector="#literal")
            await execute("press", key="Control+v")
            await execute("press", key="Control+a")
            await clipboard("write", "replacement")
            await execute("press", key="Control+c")
            await copied(literal)

            reader, writer = await rfb()
            viewers.append(writer)
            await execute("click", selector="#literal")
            await key(writer, ord("a"), control=True)
            for _ in range(2):
                self.assertEqual(await clipboard("copy"), {"text": literal})
            await execute("fill", selector="#literal", text="\u00e4" * 8193)
            await key(writer, ord("a"), control=True)
            with self.assertRaises(AgentError) as error:
                await clipboard("copy")
            self.assertEqual(error.exception.code, "browser.fileTooLarge")
            await execute("fill", selector="#literal", text=literal)
            await key(writer, ord("a"), control=True)
            await key(writer, 0xFF53)  # Right collapses the native textarea selection.
            await clipboard("write", "stale prior clipboard")
            with self.assertRaises(AgentError) as error:
                await clipboard("copy")
            self.assertEqual(error.exception.code, "browser.actionFailed")
            self.assertEqual(await clipboard("read"), {"text": "stale prior clipboard"})
            await manager.dispatch(
                "integration.browser.navigation",
                BrowserNavigationArguments(
                    browserId=self.browser_id, action="chrome", nativeChrome=True
                ),
            )
            # Click the real omnibox; Ctrl+L is owned by the embedded host UI in app mode.
            writer.write(struct.pack(">BBHH", 5, 1, 300, 62) + struct.pack(">BBHH", 5, 0, 300, 62))
            await writer.drain()
            await key(writer, ord("a"), control=True)
            for _ in range(2):
                self.assertEqual(await clipboard("copy"), {"text": url})
            await key(writer, 0xFF1B)  # Escape restores page focus.
            await manager.dispatch(
                "integration.browser.navigation",
                BrowserNavigationArguments(
                    browserId=self.browser_id, action="chrome", nativeChrome=False
                ),
            )
            print(
                "BROWSER COPY: fresh native textarea and address-bar copies, "
                "identical text twice, stale selection refused",
                flush=True,
            )
            extra_tab = (await execute("newtab", url=url))["tabId"]
            original_tabs = (await execute("tabs"))["tabs"]
            worker_pid = manager.worker.pid
            fingerprint = (await execute("inspect", guard=True))["data"]["snapshotSha256"]
            for width, height in (
                (1921, 1081),
                (641, 481),
                (1001, 701),
                (320, 240),
                (3840, 2160),
                (1280, 800),
            ):
                applied = await manager.dispatch(
                    "integration.browser.resize",
                    BrowserResizeArguments(browserId=self.browser_id, width=width, height=height),
                )
                self.assertEqual(applied, {"width": width, "height": height})
                # DesktopSize must arrive on this same authenticated RFB connection.
                writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
                await writer.drain()
                async with asyncio.timeout(10):
                    resized = False
                    while not resized:
                        update = await reader.readexactly(4)
                        self.assertEqual(
                            update[0], 0
                        )  # The second byte is unspecified RFB padding.
                        for _ in range(int.from_bytes(update[2:], "big")):
                            rectangle = await reader.readexactly(12)
                            _, _, w, h, encoding = struct.unpack(">HHHHi", rectangle)
                            if encoding == -223:
                                self.assertEqual((w, h), (width, height))
                                resized = True
                            else:
                                self.assertEqual(encoding, 0)
                                await reader.readexactly(w * h * 4)
                        if not resized:
                            writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
                            await writer.drain()
                            await asyncio.sleep(0.05)
                async with asyncio.timeout(10):
                    scale = min(
                        1,
                        1568 / width,
                        1568 / height,
                        (1.15 * 1024 * 1024 / (width * height)) ** 0.5,
                    )
                    expected = (int(width * scale), int(height * scale))
                    while True:
                        image = await screenshot_bytes(await execute("screenshot"))
                        page_width, page_height = struct.unpack(">II", image[16:24])
                        if (page_width, page_height) == expected:
                            break
                        await asyncio.sleep(0.1)
                self.assertEqual(manager.worker.pid, worker_pid)
                self.assertEqual((await execute("tabs"))["tabs"], original_tabs)
                changed = (await execute("inspect", guard=True))["data"]["snapshotSha256"]
                self.assertNotEqual(changed, fingerprint)
                fingerprint = changed
                print(
                    f"BROWSER RESIZE: desktop {width}x{height}, page {page_width}x{page_height}, "
                    "same RFB and worker",
                    flush=True,
                )
            await execute("closetab", tabId=extra_tab)
            await execute("click", selector="#literal")
            await execute("press", key="Control+a")
            await execute("press", key="Control+c")
            await copied(literal)
            await execute("click", selector="#literal")
            await execute("press", key="Control+a")
            self.assertIn("[ref=", (await execute("inspect"))["text"])
            await key(writer, ord("a"))
            await execute("inspect")
            await execute("press", key="Control+a")
            await execute("press", key="Control+c")
            await copied("a")
            self.assertEqual(await control(False), {"ok": True})
            locked_reader, locked_writer = await rfb()
            viewers.append(locked_writer)
            await key(writer, ord("b"))
            await key(locked_writer, ord("b"))
            await execute("press", key="Control+a")
            await execute("press", key="Control+c")
            await copied("a")
            with self.assertRaises(AgentError):
                await clipboard("write", "forbidden")
            with self.assertRaises(AgentError) as error:
                await clipboard("copy")
            self.assertEqual(error.exception.code, "browser.busy")
            writer.write(b"\x06\x00\x00\x00" + struct.pack(">I", 3) + b"bad")
            await writer.drain()
            self.assertEqual(await clipboard("read"), {"text": "a"})
            # Leave a partial key message queued while locked; unlocking must discard it.
            writer.write(struct.pack(">BBHI", 4, 1, 0, ord("z"))[:4])
            await writer.drain()
            self.assertEqual(await control(True), {"ok": True})
            # Wait for EOF; resize requests may have left pixel updates queued on this stream.
            await asyncio.wait_for(reader.read(), 3)
            self.assertEqual(await asyncio.wait_for(locked_reader.read(), 3), b"")
            reader, writer = await rfb()
            viewers.append(writer)
            await execute("click", selector="#literal")
            await execute("press", key="Control+a")
            await key(writer, ord("c"))
            await execute("press", key="Control+a")
            await execute("press", key="Control+c")
            await copied("c")
            inspection = await execute("inspect")
            match = re.search(r'textbox "Suche" \[ref=((?:f\d+)?e\d+)\]', inspection["text"])
            self.assertIsNotNone(match, inspection["text"])
            search_ref = match.group(1)
            self.assertLessEqual(len(inspection["text"]), 32768)
            self.assertEqual(len(inspection["data"]["snapshotSha256"]), 64)
            observed = await execute(
                "fill", selector=f"aria-ref={search_ref}", text="Selector works", observe=True
            )
            self.assertIn("Selector works", observed["text"])
            await execute("fill", selector="#password", text="SECRET_FIELD_VALUE")
            before = await execute("inspect", guard=True)
            self.assertIn("SECRET_FIELD_VALUE", before["text"])
            await execute("fill", selector="#password", text="CHANGED_PRIVATE_VALUE")
            after = await execute("inspect", guard=True)
            self.assertIn("CHANGED_PRIVATE_VALUE", after["text"])
            self.assertNotEqual(before["text"], after["text"])
            self.assertNotEqual(before["data"]["snapshotSha256"], after["data"]["snapshotSha256"])
            await execute("click", selector="#checked")
            checked = await execute("inspect", guard=True)
            self.assertNotEqual(after["data"]["snapshotSha256"], checked["data"]["snapshotSha256"])
            await execute("click", selector="#retarget")
            changed = await execute("inspect", guard=True)
            self.assertNotEqual(
                checked["data"]["snapshotSha256"], changed["data"]["snapshotSha256"]
            )
            await execute("navigate", url=url + "identity")
            first_document = await execute("inspect")
            await execute("navigate", url=url + "identity")
            second_document = await execute("inspect")
            self.assertIn("textbox", first_document["text"])
            self.assertIn("textbox", second_document["text"])
            self.assertNotEqual(
                first_document["data"]["snapshotSha256"], second_document["data"]["snapshotSha256"]
            )
            await execute("navigate", url=url)
            await execute("fill", selector="#name", text="Browser smoke")
            upload_id = (await file("begin", filename="upload.txt", size=3))["fileId"]
            await file("write", fileId=upload_id, data="YWJj")
            await file("finish", fileId=upload_id)
            with self.assertRaises(AgentError) as error:
                await file("choose", fileId=upload_id)
            self.assertEqual(error.exception.code, "browser.invalidTarget")
            # A native chooser event is triggered by the same click delivered through VNC.
            await execute("click", selector="#file")
            self.assertEqual(await file("choose", fileId=upload_id), {"ok": True})
            with self.assertRaises(AgentError):
                await file("choose", fileId=upload_id)
            await execute("upload", selector="#file", uploadId=upload_id)
            self.assertIn("upload.txt:text/plain", (await execute("inspect"))["text"])
            for _ in range(50):
                if "Service worker ready" in (await execute("inspect"))["text"]:
                    break
                await asyncio.sleep(0.05)
            self.assertIn("Service worker ready", (await execute("inspect"))["text"])
            await execute("click", selector="#network")
            for _ in range(50):
                diagnostics = (await execute("diagnostics"))["text"]
                if "requestfailed " + url + "fail" in diagnostics:
                    break
                await asyncio.sleep(0.05)
            self.assertIn("response 503 " + url + "missing", diagnostics)
            self.assertIn("requestfailed " + url + "fail", diagnostics)
            self.assertNotIn("token=secret", diagnostics)

            original_tab = (await execute("tabs"))["tabId"]
            await execute("click", selector="#file")
            other_tab = (await execute("newtab", url=url))["tabId"]
            with self.assertRaises(AgentError):
                await file("choose", fileId=upload_id)
            await execute("diagnostics", tabId=original_tab)
            await execute("tabs", tabId=original_tab)
            with self.assertRaises(AgentError):
                await file("choose", fileId=upload_id)
            await execute("inspect", tabId=original_tab)
            self.assertEqual(await file("choose", fileId=upload_id), {"ok": True})
            await execute("click", selector="#file")
            await execute("navigate", tabId=original_tab, url=url + "?fresh")
            with self.assertRaises(AgentError):
                await file("choose", fileId=upload_id)
            await execute("click", tabId=other_tab, selector="#file")
            await execute("closetab", tabId=other_tab)
            with self.assertRaises(AgentError):
                await file("choose", fileId=upload_id)
            await execute("click", selector="#download")
            for _ in range(50):
                downloads = (await execute("tabs"))["downloads"]
                if downloads:
                    break
                await asyncio.sleep(0.05)
            self.assertEqual(len(downloads), 1)
            download = await file("read", fileId=downloads[0]["id"])
            self.assertEqual(base64.b64decode(download["data"]), b"browser-download")
            await manager.dispatch(
                "integration.browser.close", BrowserIdArguments(browserId=self.browser_id)
            )
            profile = manager.runtime_root(manager.storage(self.owner_id)) / "profile"
            self.assertTrue(profile.is_dir())
            preferences = json.loads((profile / "Default" / "Preferences").read_text())
            self.assertIs(preferences["translate"]["enabled"], False)
            await manager.dispatch(
                "integration.browser.open",
                BrowserOpenArguments(ownerId=self.owner_id, browserId=self.browser_id),
            )
            received.clear()
            await execute("navigate", url=url)
            self.assertTrue(any(b"Cookie: smoke=persisted" in request for request in received))
            with self.assertRaises(AgentError):
                await file("read", fileId=downloads[0]["id"])
            with self.assertRaises(AgentError):
                await execute("tabs", allowNetwork=False)
            self.assertIsNone(manager.worker)
            await manager.dispatch(
                "integration.browser.reset", BrowserOwnerArguments(ownerId=self.owner_id)
            )
            self.assertFalse(profile.exists())
        finally:
            for writer in viewers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await manager.close()
            shutil.rmtree(manager.storage(), ignore_errors=True)
