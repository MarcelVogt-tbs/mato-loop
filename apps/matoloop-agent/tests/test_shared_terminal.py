import asyncio
import base64
import os
import shutil
import signal
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import test_native_shared as native
from test_terminal import configuration, manager

from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.filesystem import AgentError
from matoloop_agent.protocol import Request, SharedTerminalStatus
from matoloop_agent.shared_terminal import SHARED_OPERATIONS, SharedTerminals


@unittest.skipUnless(native.PTY_AVAILABLE and shutil.which("gcc"), "Native Bash build required")
class SharedTerminalTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        native.NativeSharedTests.setUpClass()
        cls.library = native.NativeSharedTests.library

    @classmethod
    def tearDownClass(cls):
        native.NativeSharedTests.tearDownClass()

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.terminal = manager(configuration(self.home, shell=False))
        self.addAsyncCleanup(self.terminal.close_all)
        self.shared_manager = SharedTerminals(self.terminal)
        self.shared_manager.library = self.library
        self.terminal_id = (await self.shared_manager.open(24, 80))["terminalId"]
        self.shared = self.shared_manager.get(self.terminal_id)
        self.command_id = str(uuid4())

    async def write(self, data):
        await self.terminal.write(self.terminal_id, base64.b64encode(data).decode())

    async def idle(self):
        async with asyncio.timeout(5):
            while True:
                status = await self.shared.status()
                if status["state"] == "idle":
                    return status
                await asyncio.sleep(0.01)

    async def prepare(self, command):
        await self.shared.stage(self.command_id, command)
        return await self.shared.inspect(self.command_id)

    async def execute(self, snapshot, output=None, timeout=3):
        async def stream(channel, text):
            if output is not None:
                output.append((channel, text))

        return await self.shared.execute(
            self.command_id,
            snapshot["command"],
            snapshot["revision"],
            snapshot["contextRevision"],
            snapshot["inspection"]["inspectionRevision"],
            timeout,
            stream,
        )

    async def test_real_shared_state_edited_enter_and_ui_snapshot(self):
        await self.write(b"VALUE=kept; alias show='printf alias:'; mkdir sub; cd sub\r")
        await self.idle()
        staged = await self.shared.stage(self.command_id, 'show; printf \'%s:%s\' "$VALUE" "$PWD"')
        await self.write(b"\x05; printf edited\r")
        snapshot = await self.shared.inspect(self.command_id)
        status = await self.shared.status()
        SharedTerminalStatus.model_validate(status)
        self.assertTrue(status["submitted"])
        self.assertGreater(snapshot["revision"], staged["revision"])
        self.assertFalse(snapshot["inspection"]["analysisComplete"])
        self.assertTrue(snapshot["inspection"]["readOnly"])
        self.assertEqual(snapshot["currentDirectory"], str(self.home / "sub"))
        with self.assertRaises(AgentError):
            await self.shared.execute(
                self.command_id,
                staged["command"],
                staged["revision"],
                snapshot["contextRevision"],
                snapshot["inspection"]["inspectionRevision"],
                2,
                None,
            )
        await self.write(b"UNSEEN\r")
        output = []
        result = await self.execute(snapshot, output)
        text = "".join(data for _, data in output)
        self.assertEqual(result["exitCode"], 0)
        self.assertIn("alias:kept:", text)
        self.assertIn("edited", text)
        self.assertNotIn("UNSEEN", text)
        browser = await self.terminal.read(self.terminal_id, 65536, 0)
        self.assertIn(b"alias:kept:", base64.b64decode(browser["data"]))

    async def test_ctrl_c_checking_and_api_cancel_prevent_stale_execute(self):
        snapshot = await self.prepare("touch never")
        await self.write(b"\x03")
        status = await self.idle()
        self.assertTrue(status["cancelled"])
        with self.assertRaises(AgentError):
            await self.execute(snapshot)
        snapshot = await self.prepare("touch never")
        self.assertEqual(await self.shared.cancel(self.command_id), {})
        with self.assertRaises(AgentError):
            await self.execute(snapshot)
        self.assertFalse((self.home / "never").exists())

    async def test_running_interrupt_timeout_and_bounded_fanout(self):
        snapshot = await self.prepare("sleep 30")
        output = []
        task = asyncio.create_task(self.execute(snapshot, output, timeout=35))
        async with asyncio.timeout(3):
            while self.shared.state["state"] != "running":
                await asyncio.sleep(0.01)
        await self.shared.cancel(self.command_id)
        result = await asyncio.wait_for(task, 3)
        self.assertEqual(result["exitCode"], 130, repr(output))
        await self.idle()
        snapshot = await self.prepare("sleep 30")
        result = await self.execute(snapshot, timeout=0.1)
        self.assertTrue(result["timedOut"])
        self.assertEqual((await self.idle())["state"], "idle")
        snapshot = await self.prepare("printf '%2000000s' x")
        result = await self.execute(snapshot, timeout=10)
        self.assertEqual(result["exitCode"], 0)
        self.assertTrue(result["stdoutTruncated"])
        self.assertLessEqual(result["stdoutBytes"], self.terminal.max_output)
        await self.idle()
        snapshot = await self.prepare("printf '\\377%.0s' {1..3000}")
        output = []
        result = await self.execute(snapshot, output)
        encoded_size = sum(len(text.encode("utf-8")) for _, text in output)
        self.assertEqual(result["stdoutBytes"], encoded_size)
        self.assertLessEqual(encoded_size, self.terminal.max_output)
        self.assertTrue(result["stdoutTruncated"])

    async def test_personal_repl_secondary_prompt_and_expired_stage_are_not_injected(self):
        await self.write(b"read -e -r value\r")
        await asyncio.sleep(0.05)
        with self.assertRaises(AgentError):
            await self.shared.stage(self.command_id, "touch never")
        await self.write(b"personal\r")
        await self.idle()
        await self.write(b"printf '%s' \"\r")
        await asyncio.sleep(0.05)
        self.assertEqual((await self.shared.status())["state"], "busy")
        with self.assertRaises(AgentError):
            await self.shared.stage(self.command_id, "touch never")
        await self.write(b"\x03")
        await self.idle()
        await self.write(b"sleep 1.2\r")
        with self.assertRaises(AgentError):
            await self.shared.stage(self.command_id, "touch never")
        await self.idle()
        self.assertIsNone(self.shared.state["commandId"])
        self.assertFalse((self.home / "never").exists())

    async def test_context_revision_hard_paths_multiline_and_unknown_exit(self):
        old = (await self.shared.status())["contextRevision"]
        await self.write(b"export PATH=/bin; function custom() { printf function; }; cd /tmp\r")
        current = await self.idle()
        self.assertGreater(current["contextRevision"], old)
        snapshot = await self.prepare("custom\nprintf '\\nsecond'\nfalse")
        output = []
        result = await self.execute(snapshot, output)
        self.assertEqual(result["exitCode"], 1)
        self.assertIn("function", "".join(data for _, data in output))
        await self.idle()
        (self.home / "link").symlink_to("/etc/passwd")
        await self.shared.stage(self.command_id, f"cat {self.home}/link")
        with self.assertRaises(AgentError):
            await self.shared.inspect(self.command_id)
        await self.shared.cancel(self.command_id)
        snapshot = await self.prepare("exec /usr/bin/true")
        result = await self.execute(snapshot)
        self.assertIsNone(result["exitCode"])
        self.assertEqual((await self.shared.status())["state"], "closed")

    async def test_exact_wire_dispatch_and_capability(self):
        with patch("matoloop_agent.shared_terminal.native_library", return_value=self.library):
            agent = MatoLoopAgent(configuration(self.home, shell=False), "secret")
        self.addAsyncCleanup(agent.terminal.close_all)
        self.assertIsNone(agent.process.shell)
        self.assertFalse(agent.terminal.available)
        self.assertTrue(SHARED_OPERATIONS.issubset(agent.operations))
        for operation in ("shell.run", "terminal.open", "terminal.run"):
            self.assertNotIn(operation, agent.operations)
        with self.assertRaises(AgentError) as error:
            await agent.terminal.open(24, 80)
        self.assertEqual(error.exception.code, "operation.unsupported")
        request = Request(
            type="request",
            id=str(uuid4()),
            operation="terminal.shared.open",
            arguments={"rows": 24, "columns": 80, "terminalId": str(uuid4())},
        )
        terminal_id = (await agent.dispatch(request))["terminalId"]
        self.assertEqual(terminal_id, request.arguments["terminalId"])
        result = await agent.dispatch(
            Request(
                type="request",
                id=str(uuid4()),
                operation="terminal.shared.status",
                arguments={"terminalId": terminal_id, "redisplay": True},
            )
        )
        self.assertIsNone(result["command"])
        SharedTerminalStatus.model_validate(result)
        shared = agent.shared_terminal.get(terminal_id)
        await shared.stage(self.command_id, "printf wire")
        inspected = await agent.dispatch(
            Request(
                type="request",
                id=str(uuid4()),
                operation="terminal.shared.inspect",
                arguments={
                    "terminalId": terminal_id,
                    "commandId": self.command_id,
                    "freeze": False,
                },
            )
        )
        self.assertEqual(inspected["command"], "printf wire")
        self.assertEqual((await shared.status())["state"], "editing")
        inspected = await shared.inspect(self.command_id)
        self.assertEqual(inspected["inspection"]["classification"], "read")
        output = []

        async def stream(channel, text):
            output.append((channel, text))

        result = await agent.dispatch(
            Request(
                type="request",
                id=str(uuid4()),
                operation="terminal.shared.execute",
                arguments={
                    "terminalId": terminal_id,
                    "commandId": self.command_id,
                    "command": inspected["command"],
                    "expectedRevision": inspected["revision"],
                    "expectedContextRevision": inspected["contextRevision"],
                    "expectedInspectionRevision": inspected["inspection"]["inspectionRevision"],
                    "timeoutSeconds": 3,
                },
            ),
            stream=stream,
        )
        self.assertEqual(result["exitCode"], 0)
        self.assertIn("wire", "".join(text for _, text in output))
        browser = await agent.terminal.read(terminal_id, 65536, 0)
        self.assertIn(b"wire", base64.b64decode(browser["data"]))

    async def test_explicit_pty_argv_needs_neither_configured_shell_nor_pass_fds(self):
        self.assertIsNone(self.terminal.process.shell)
        entry = self.terminal._spawn(
            str(uuid4()),
            [str(Path("/usr/bin/printf").resolve()), "explicit-argv"],
            str(self.home),
            24,
            80,
            {},
        )
        self.addAsyncCleanup(self.terminal._terminate, entry)
        output = bytearray()
        async with asyncio.timeout(3):
            while chunk := await self.terminal._read_fd(entry):
                output.extend(chunk)
            await entry.wait_task
        self.assertEqual(bytes(output), b"explicit-argv")
        self.assertEqual(entry.exit_code, 0)

    async def test_nonfreezing_inspection_does_not_authorize_edits_or_unfreeze(self):
        await self.shared.stage(self.command_id, "printf original")
        initial = await self.shared.inspect(self.command_id, freeze=False)
        self.assertEqual((await self.shared.status())["state"], "editing")
        with self.assertRaises(AgentError) as error:
            await self.execute(initial)
        self.assertEqual(error.exception.code, "terminal.stale")
        await self.write(b"\x17edited")
        current = await self.shared.inspect(self.command_id, freeze=False)
        self.assertGreater(current["revision"], initial["revision"])
        self.assertEqual(current["command"], "printf edited")
        with self.assertRaises(AgentError):
            await self.execute(initial)
        await self.write(b"\r")
        reviewed = await self.shared.inspect(self.command_id)
        self.assertEqual((await self.shared.status())["state"], "checking")
        self.assertEqual(await self.shared.inspect(self.command_id, freeze=False), reviewed)
        await self.write(b"unseen")
        output = []
        result = await self.execute(reviewed, output)
        self.assertEqual(result["exitCode"], 0)
        self.assertIn("edited", "".join(text for _, text in output))
        self.assertNotIn("unseen", "".join(text for _, text in output))

    async def test_older_editable_inspection_cannot_replace_frozen_review(self):
        await self.shared.stage(self.command_id, "printf reviewed")
        entered, release = threading.Event(), threading.Event()
        inspect = self.shared._inspection

        def slow(snapshot):
            if snapshot["state"] == "editing":
                entered.set()
                release.wait(3)
            return inspect(snapshot)

        with patch.object(self.shared, "_inspection", side_effect=slow):
            older = asyncio.create_task(self.shared.inspect(self.command_id, freeze=False))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                reviewed = await self.shared.inspect(self.command_id)
            finally:
                release.set()
            with self.assertRaises(AgentError):
                await older
        self.assertEqual(self.shared.inspected["state"], "checking")
        self.assertEqual((await self.execute(reviewed))["exitCode"], 0)

    async def test_caller_id_open_retries_reuse_one_live_process(self):
        terminal_id = str(uuid4())
        with patch.object(self.terminal, "_spawn", wraps=self.terminal._spawn) as spawn:
            results = await asyncio.gather(
                self.shared_manager.open(24, 80, terminal_id=terminal_id),
                self.shared_manager.open(24, 80, terminal_id=terminal_id),
            )
        self.assertEqual(results, [{"terminalId": terminal_id}] * 2)
        self.assertEqual(spawn.call_count, 1)
        entry = self.terminal._get(terminal_id)
        await self.terminal.resize(terminal_id, 40, 120)
        self.assertEqual(
            await self.shared_manager.open(24, 80, terminal_id=terminal_id), results[0]
        )
        self.assertIs(self.terminal._get(terminal_id), entry)
        with self.assertRaises(AgentError) as error:
            await self.shared_manager.open(40, 120, terminal_id=terminal_id)
        self.assertEqual(error.exception.code, "terminal.exists")
        await self.terminal.close(terminal_id)
        self.assertNotIn(terminal_id, self.terminal._terminals)

    async def test_redisplay_preserves_live_draft_cursor_and_refuses_repl(self):
        await self.shared.stage(self.command_id, "printf ab")
        await self.write(b"\x02")
        before = await self.shared.status()
        while (await self.terminal.read(self.terminal_id, 65536, 0))["data"]:
            pass
        self.assertEqual(await self.shared.status(redisplay=True), before)
        output = bytearray()
        async with asyncio.timeout(2):
            while b"printf ab" not in output:
                result = await self.terminal.read(self.terminal_id, 65536, 0.1)
                output.extend(base64.b64decode(result["data"]))
        self.assertIn(b"\x1b[2J", output)
        await self.write(b"X")
        inspected = await self.shared.inspect(self.command_id)
        self.assertEqual(inspected["command"], "printf aXb")
        self.assertEqual((await self.execute(inspected))["exitCode"], 0)
        await self.idle()
        await self.write(b"read -e -r value\r")
        self.assertEqual((await self.shared.status(redisplay=True))["state"], "busy")
        await self.write(b"\x03")
        await self.idle()

    async def test_stage_ctrl_c_and_cancel_after_completion_do_not_execute_or_interrupt_personal(
        self,
    ):
        for _ in range(3):
            await asyncio.gather(
                self.shared.stage(self.command_id, "touch never"), self.write(b"\x03")
            )
            status = await self.idle()
            self.assertTrue(status["cancelled"])
            self.assertEqual(status["command"], "")
            self.assertFalse((self.home / "never").exists())

        inspected = await self.prepare("printf completed")
        streaming, release = asyncio.Event(), asyncio.Event()

        async def slow_stream(_channel, _text):
            streaming.set()
            await release.wait()

        task = asyncio.create_task(
            self.shared.execute(
                self.command_id,
                inspected["command"],
                inspected["revision"],
                inspected["contextRevision"],
                inspected["inspection"]["inspectionRevision"],
                5,
                slow_stream,
            )
        )
        try:
            await asyncio.wait_for(streaming.wait(), 2)
            await self.idle()
            await self.write(b"sleep 30\r")
            async with asyncio.timeout(2):
                while os.tcgetpgrp(self.shared.entry.fd) == self.shared.entry.pid:
                    await asyncio.sleep(0.01)
            foreground = os.tcgetpgrp(self.shared.entry.fd)
            await self.shared.cancel(self.command_id)
            await asyncio.sleep(0.1)
            os.killpg(foreground, 0)
        finally:
            release.set()
            await self.write(b"\x03")
        self.assertEqual((await asyncio.wait_for(task, 2))["exitCode"], 0)
        await self.idle()

    async def test_inspection_is_cancellable(self):
        await self.shared.stage(self.command_id, "printf unchanged")
        entered, release = threading.Event(), threading.Event()
        inspect = self.shared._inspection

        def slow(snapshot):
            entered.set()
            release.wait(3)
            return inspect(snapshot)

        with patch.object(self.shared, "_inspection", side_effect=slow):
            task = asyncio.create_task(self.shared.inspect(self.command_id))
            await asyncio.to_thread(entered.wait, 2)
            try:
                await asyncio.wait_for(self.write(b"\x03"), 0.5)
            finally:
                release.set()
            with self.assertRaises(AgentError):
                await task
        await self.idle()

    async def test_trusted_signal_callback_does_not_veto_approval(self):
        await self.write(b"trap 'VALUE=changed' USR1\r")
        await self.idle()
        snapshot = await self.prepare("printf '%s' \"$VALUE\"")
        self.assertEqual(snapshot["inspection"]["classification"], "read")
        os.kill(self.shared.entry.pid, signal.SIGUSR1)
        await asyncio.sleep(0.1)
        self.assertEqual((await self.shared.status())["state"], "checking")
        output = []
        self.assertEqual((await self.execute(snapshot, output))["exitCode"], 0)
        self.assertIn("changed", "".join(text for _, text in output))

    async def test_shared_inspection_preserves_the_ordinary_heuristic_and_targets(self):
        for command, classification, complete in (
            ("pwd", "read", True),
            ('unknown "$VALUE"', "read", False),
            ("touch recognized", "write", True),
            ("ls -l /usr/bin/ls > /dev/null", "read", True),
        ):
            with self.subTest(command=command):
                ordinary = self.terminal.process.inspect_shell(command, str(self.home))
                snapshot = await self.prepare(command)
                inspected = snapshot["inspection"]
                self.assertEqual(inspected["classification"], classification)
                self.assertEqual(inspected["readOnly"], classification == "read")
                self.assertEqual(inspected["analysisComplete"], complete)
                self.assertEqual(
                    {key: value for key, value in inspected.items() if key != "inspectionRevision"},
                    {key: value for key, value in ordinary.items() if key != "inspectionRevision"},
                )
                self.assertNotEqual(inspected["inspectionRevision"], ordinary["inspectionRevision"])
                if command.startswith("ls "):
                    self.assertTrue(
                        {"toolchain_metadata", "discard"}.issubset(
                            {target["kind"] for target in inspected["targets"]}
                        )
                    )
                    self.assertTrue(any(unit.get("prefix") for unit in inspected["commands"]))
                await self.shared.cancel(self.command_id)

    async def test_enter_after_freeze_submits_once_and_ctrl_c_wins_pending_ui_approval(self):
        command = "printf '__APPROVED__%s\\n' yes; printf x >> once"
        for count, enter in enumerate((b"\r", b"\n"), start=1):
            snapshot = await self.prepare(command)
            self.assertFalse((await self.shared.status())["submitted"])
            while (await self.terminal.read(self.terminal_id, 65536, 0))["data"]:
                pass
            await self.write(enter * 4)
            status = await self.shared.status()
            self.assertEqual(status["state"], "checking")
            self.assertTrue(status["submitted"])
            self.assertEqual(status["revision"], snapshot["revision"])
            self.assertEqual(status["command"], snapshot["command"])
            before = await self.terminal.read(self.terminal_id, 65536, 0.1)
            self.assertEqual(base64.b64decode(before["data"]), b"")
            if (self.home / "once").exists():
                self.assertEqual((self.home / "once").read_text(), "x" * (count - 1))
            with self.assertRaises(AgentError):
                await self.execute({**snapshot, "revision": snapshot["revision"] + 1})
            output = []
            self.assertEqual((await self.execute(snapshot, output))["exitCode"], 0)
            self.assertEqual("".join(text for _, text in output).count("__APPROVED__yes"), 1)
            await self.write(enter * 3)
            await self.idle()
            self.assertEqual((self.home / "once").read_text(), "x" * count)

        snapshot = await self.prepare("touch never")
        await self.write(b"\r")
        self.assertTrue((await self.shared.status())["submitted"])
        entered, release = threading.Event(), threading.Event()
        inspect = self.shared._inspection

        def slow(current):
            entered.set()
            release.wait(3)
            return inspect(current)

        with patch.object(self.shared, "_inspection", side_effect=slow):
            approval = asyncio.create_task(self.execute(snapshot))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                await self.write(b"\x03")
                self.assertTrue((await self.idle())["cancelled"])
            finally:
                release.set()
            with self.assertRaises(AgentError):
                await approval
        self.assertFalse((self.home / "never").exists())

    async def test_quoted_insert_bracketed_paste_resize_and_cancelled_run_cleanup(self):
        await self.shared.stage(self.command_id, "printf '%s' ''")
        await self.write(b"\x02\x16\n")
        await self.terminal.resize(self.terminal_id, 40, 120)
        snapshot = await self.shared.inspect(self.command_id)
        self.assertIn("\n", snapshot["command"])
        self.assertEqual((await self.execute(snapshot))["exitCode"], 0)
        await self.idle()

        await self.shared.stage(self.command_id, "printf start")
        await self.write(b"\x1b[200~; printf one\nprintf two\x1b[201~")
        snapshot = await self.shared.inspect(self.command_id)
        self.assertIn("\nprintf two", snapshot["command"])
        self.assertEqual((await self.execute(snapshot))["exitCode"], 0)
        await self.idle()
        snapshot = await self.prepare("sleep 30")
        task = asyncio.create_task(self.execute(snapshot, timeout=35))
        while self.shared.state["state"] != "running":
            await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertIsNone(self.shared.capture)
        self.assertFalse(self.shared.executing)
        await self.idle()

    async def test_lost_acceptance_is_unknown_and_never_replayed(self):
        snapshot = await self.prepare("printf x >> once")
        rpc = self.shared._rpc

        async def lost(operation, *args, **kwargs):
            result = await rpc(operation, *args, **kwargs)
            if operation == "execute":
                await asyncio.sleep(0.05)
                raise TimeoutError
            return result

        with patch.object(self.shared, "_rpc", side_effect=lost):
            result = await self.execute(snapshot)
        self.assertIsNone(result["exitCode"])
        self.assertEqual((await self.shared.status())["state"], "closed")
        with self.assertRaises(AgentError):
            await self.shared.stage(self.command_id, snapshot["command"])
        if (self.home / "once").exists():
            self.assertEqual((self.home / "once").read_text(), "x")
