import asyncio
import base64
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from test_terminal import configuration, manager

from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.errors import AgentError
from matoloop_agent.protocol import Request, SharedTerminalStatus
from matoloop_agent.shared_terminal import SHARED_OPERATIONS, SharedTerminals
from matoloop_agent.terminal import PTY_AVAILABLE


@unittest.skipUnless(PTY_AVAILABLE and shutil.which("bash"), "Bash PTY required")
class SharedTerminalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.home = Path(self.directory.name)
        self.terminal = manager(configuration(self.home, shell=False))
        self.addAsyncCleanup(self.terminal.close_all)
        self.shared_manager = SharedTerminals(self.terminal)
        self.terminal_id = (await self.shared_manager.open(24, 80))["terminalId"]
        self.shared = self.shared_manager.get(self.terminal_id)
        self.command_id = str(uuid4())

    async def write(self, data):
        await self.terminal.write(self.terminal_id, base64.b64encode(data).decode())

    async def output_until(self, marker, timeout=3):
        output = bytearray()
        async with asyncio.timeout(timeout):
            while marker not in output:
                result = await self.terminal.read(self.terminal_id, 65536, 0.1)
                output.extend(base64.b64decode(result["data"]))
        return bytes(output)

    async def human_prompt(self):
        async with asyncio.timeout(3):
            while self.shared.prompt != "primary":
                await asyncio.sleep(0.01)

    async def prepare(self, command):
        await self.shared.acquire()
        return await self.shared.prepare(self.command_id, command)

    async def execute(self, prepared, output=None, timeout=3, environment=None):
        async def stream(channel, text):
            if output is not None:
                output.append((channel, text))

        return await self.shared.execute(
            self.command_id,
            prepared["inspection"]["inspectionRevision"],
            timeout,
            stream,
            environment,
        )

    async def test_acquire_requires_empty_primary_prompt_and_preserves_partial_input(self):
        await self.write(b"printf partial")
        with self.assertRaises(AgentError) as error:
            await self.shared.acquire()
        self.assertEqual(error.exception.code, "terminal.busy")
        await self.write(b"-kept\\n\r")
        self.assertIn(b"partial-kept", await self.output_until(b"partial-kept"))
        await self.human_prompt()

        await self.write(b"sleep 30\r")
        async with asyncio.timeout(2):
            while os.tcgetpgrp(self.shared.entry.fd) == self.shared.entry.pid:
                await asyncio.sleep(0.01)
        with self.assertRaises(AgentError):
            await self.shared.acquire()
        await self.write(b"\x03")
        await self.human_prompt()
        self.assertEqual((await self.shared.acquire())["state"], "agent")

    async def test_manual_readline_history_and_completion_remain_normal(self):
        (self.home / "completion-target").write_text("")
        await self.write(b"printf '%s' completion-t\t\r")
        self.assertIn(b"completion-target", await self.output_until(b"completion-target"))
        await self.human_prompt()
        await self.write(b"printf HISTORY\r")
        await self.output_until(b"HISTORY")
        await self.human_prompt()
        await self.write(b"\x1b[A\r")
        self.assertIn(b"HISTORY", await self.output_until(b"HISTORY"))

    async def test_prepare_execute_preserves_bash_state_and_excludes_human_writes(self):
        await self.write(b"export VALUE=kept; custom() { printf function; }; mkdir sub; cd sub\r")
        await self.human_prompt()
        command = 'custom\nprintf "\\n%s:%s:Grüße" "$VALUE" "$PWD"\nfalse'
        prepared = await self.prepare(command)
        self.assertEqual(prepared["command"], command)
        self.assertEqual(prepared["currentDirectory"], str(self.home / "sub"))
        SharedTerminalStatus.model_validate(await self.shared.status())
        with self.assertRaises(AgentError) as error:
            await self.write(b"unseen")
        self.assertEqual(error.exception.code, "terminal.busy")
        with self.assertRaises(AgentError):
            await self.shared.prepare(self.command_id, "printf changed")

        output = []
        result = await self.execute(prepared, output)
        self.assertEqual(result["exitCode"], 1)
        text = "".join(data for _, data in output)
        self.assertIn("function", text)
        self.assertIn("kept:", text)
        self.assertIn("Grüße", text)
        self.assertEqual((await self.shared.status())["state"], "agent")
        self.assertEqual((await self.shared.release())["state"], "human")
        await self.human_prompt()
        await self.write(b"printf '%s' \"$VALUE\"\r")
        self.assertIn(b"kept", await self.output_until(b"kept"))

    async def test_environment_is_exact_and_scoped_to_one_command(self):
        await self.write(b"export TOKEN=original; mkdir sub\r")
        await self.human_prompt()
        prepared = await self.prepare('printf "%s" "$TOKEN"; TOKEN=changed; cd sub; false')
        output = []

        result = await self.execute(
            prepared,
            output,
            environment={"TOKEN": "  exact secret  "},
        )

        self.assertEqual(result["exitCode"], 1)
        self.assertIn("  exact secret  ", "".join(data for _, data in output))
        await self.shared.release()
        await self.human_prompt()
        await self.write(b'printf \'%s:%s\' "$TOKEN" "$PWD"\r')
        terminal_output = await self.output_until(b"original:")
        self.assertIn(b"original:", terminal_output)
        self.assertIn(os.fsencode(self.home / "sub"), terminal_output)

    async def test_cancel_and_timeout_return_control_without_replay(self):
        prepared = await self.prepare("sleep 30")
        task = asyncio.create_task(self.execute(prepared, timeout=35))
        async with asyncio.timeout(2):
            while (await self.shared.status())["state"] != "running":
                await asyncio.sleep(0.01)
        interrupts = 0
        interrupt = self.shared.interrupt

        def lose_first_interrupt():
            nonlocal interrupts
            interrupts += 1
            if interrupts > 1:
                interrupt()

        with patch.object(self.shared, "interrupt", side_effect=lose_first_interrupt):
            await self.shared.cancel(self.command_id)
            result = await asyncio.wait_for(task, 3)
        self.assertEqual(result["exitCode"], 130)
        self.assertGreaterEqual(interrupts, 2)

        self.command_id = str(uuid4())
        prepared = await self.shared.prepare(self.command_id, "sleep 30")
        result = await self.execute(prepared, timeout=0.1)
        self.assertTrue(result["timedOut"])
        self.assertEqual(result["exitCode"], 130)

    async def test_dispatch_uses_exclusive_control_contract(self):
        agent = MatoLoopAgent(configuration(self.home, shell=False), "secret")
        self.addAsyncCleanup(agent.terminal.close_all)
        self.assertTrue(SHARED_OPERATIONS.issubset(agent.operations))
        self.assertFalse(
            {"terminal.shared.stage", "terminal.shared.inspect"} & set(agent.operations)
        )

        terminal_id = str(uuid4())

        async def dispatch(operation, arguments):
            return await agent.dispatch(
                Request(
                    type="request",
                    id=str(uuid4()),
                    operation=operation,
                    arguments=arguments,
                )
            )

        await dispatch(
            "terminal.shared.open",
            {"rows": 24, "columns": 80, "terminalId": terminal_id},
        )
        await dispatch("terminal.shared.acquire", {"terminalId": terminal_id})
        prepared = await dispatch(
            "terminal.shared.prepare",
            {"terminalId": terminal_id, "commandId": self.command_id, "command": "printf wire"},
        )
        result = await dispatch(
            "terminal.shared.execute",
            {
                "terminalId": terminal_id,
                "commandId": self.command_id,
                "expectedInspectionRevision": prepared["inspection"]["inspectionRevision"],
            },
        )
        self.assertEqual(result["exitCode"], 0)
        status = await dispatch("terminal.shared.release", {"terminalId": terminal_id})
        self.assertEqual(status["state"], "human")


if __name__ == "__main__":
    unittest.main()
