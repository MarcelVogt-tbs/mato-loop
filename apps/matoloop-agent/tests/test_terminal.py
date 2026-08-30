import asyncio
import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from matoloop_agent.config import AgentConfig
from matoloop_agent.connection import MatoLoopAgent
from matoloop_agent.filesystem import AgentError, Filesystem
from matoloop_agent.process import ProcessRunner
from matoloop_agent.protocol import Request
from matoloop_agent.terminal import PTY_AVAILABLE, TerminalManager

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
TERMINAL_ID = "bbc46aa8-698d-4554-92c2-1cd5c6cc33a6"
TERMINAL_OPERATIONS = {
    "terminal.open",
    "terminal.read",
    "terminal.write",
    "terminal.resize",
    "terminal.close",
    "terminal.run",
}


def configuration(home: Path, *, shell: bool = True) -> AgentConfig:
    config = AgentConfig.model_validate(
        {
            "homeDirectory": home,
            "backendUrl": "ws://localhost/api/execution-agents/connect",
            "agentId": AGENT_ID,
            "allowInsecure": True,
            "processExecutables": {"sh": Path("/bin/sh")},
            "shellExecutable": Path("/bin/sh") if shell else None,
            "maxOutputBytes": 4096,
        }
    )
    config._data_directory = home
    return config


def manager(config: AgentConfig) -> TerminalManager:
    filesystem = Filesystem(config)
    return TerminalManager(config, ProcessRunner(config, filesystem))


@unittest.skipUnless(PTY_AVAILABLE, "POSIX PTY support is required")
class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def _wait_for_run(self, terminal: TerminalManager, terminal_id: str) -> None:
        for _attempt in range(20):
            if terminal_id in terminal._runs:
                return
            await asyncio.sleep(0.01)
        self.fail("terminal run was not registered")

    async def _read_until(
        self, terminal: TerminalManager, terminal_id: str, expected: bytes
    ) -> bytes:
        output = bytearray()
        for _attempt in range(20):
            result = await terminal.read(terminal_id, 65_536, 0.25)
            output.extend(base64.b64decode(result["data"]))
            if expected in output:
                return bytes(output)
        self.fail(f"terminal output did not contain {expected!r}: {bytes(output)!r}")

    async def test_interactive_shell_preserves_state_bytes_size_and_ctrl_c(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            terminal = manager(configuration(home))
            terminal_id = (await terminal.open(24, 80))["terminalId"]
            try:
                command = b"printf '__HOME__%s\\n' \"$PWD\"; VALUE=kept\n"
                await terminal.write(terminal_id, base64.b64encode(command).decode())
                output = await self._read_until(terminal, terminal_id, f"__HOME__{home}".encode())
                self.assertIn(f"__HOME__{home}".encode(), output)

                await terminal.write(
                    terminal_id,
                    base64.b64encode(b"printf '__STATE__%s\\n' \"$VALUE\"\n").decode(),
                )
                await self._read_until(terminal, terminal_id, b"__STATE__kept")

                await terminal.write(
                    terminal_id,
                    base64.b64encode(b"printf '__RAW__\\001\\002\\377__END__\\n'\n").decode(),
                )
                raw = await self._read_until(terminal, terminal_id, b"\x01\x02\xff")
                self.assertIn(b"\x01\x02\xff", raw)

                self.assertEqual(
                    await terminal.resize(terminal_id, 33, 101),
                    {},
                )
                await terminal.write(
                    terminal_id,
                    base64.b64encode(b"printf '__SIZE__'; stty size\n").decode(),
                )
                await self._read_until(terminal, terminal_id, b"__SIZE__33 101")

                await terminal.write(terminal_id, base64.b64encode(b"sleep 10\n").decode())
                await asyncio.sleep(0.1)
                await terminal.write(terminal_id, base64.b64encode(b"\x03").decode())
                await terminal.write(
                    terminal_id,
                    base64.b64encode(b"printf '__ALIVE__%s\\n' yes\n").decode(),
                )
                await self._read_until(terminal, terminal_id, b"__ALIVE__yes")
            finally:
                result = await terminal.close(terminal_id)
            self.assertEqual(result, {})

    async def test_one_shot_commands_aliases_and_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            current = home / "workspace"
            current.mkdir()
            terminal = manager(configuration(home))
            self.addAsyncCleanup(terminal.close_all)
            output = []

            async def stream(channel: str, data: str) -> None:
                output.append((channel, data))

            personal_id = (await terminal.open(24, 80))["terminalId"]
            with self.assertRaises(AgentError):
                await terminal.run(
                    terminal_id=personal_id,
                    current_directory=os.fspath(current),
                    rows=31,
                    columns=99,
                    program=None,
                    args=[],
                    command="pwd",
                    timeout=2,
                    expected_inspection_revision=None,
                    stream=stream,
                )

            result = await terminal.run(
                terminal_id=TERMINAL_ID,
                current_directory=os.fspath(current),
                rows=31,
                columns=99,
                program=None,
                args=[],
                command='printf \'__ENV__%s|%s|\' "$HOME" "$PWD"; stty size; exit 7',
                timeout=2,
                expected_inspection_revision=None,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 7)
            self.assertEqual(result["stderrBytes"], 0)
            self.assertFalse(result["stderrTruncated"])
            self.assertIn(
                f"__ENV__{current}|{current}|31 99",
                "".join(data for _channel, data in output),
            )
            with self.assertRaises(AgentError):
                await terminal.write(TERMINAL_ID, base64.b64encode(b"unused").decode())

            output.clear()
            inspected = terminal.process.inspect_shell("pwd", os.fspath(current))
            self.assertTrue(inspected["readOnly"])
            environment_builder = terminal.process.execution_environment
            with patch.object(
                terminal.process,
                "execution_environment",
                wraps=environment_builder,
            ) as build_environment:
                restricted = await terminal.run(
                    terminal_id=TERMINAL_ID,
                    current_directory=os.fspath(current),
                    rows=24,
                    columns=80,
                    program=None,
                    args=[],
                    command="pwd",
                    timeout=2,
                    expected_inspection_revision=inspected["inspectionRevision"],
                    stream=stream,
                )
            self.assertEqual(restricted["exitCode"], 0)
            self.assertIn(os.fspath(current), "".join(data for _channel, data in output))
            environment = environment_builder(*build_environment.call_args.args)
            self.assertEqual(environment["HOME"], os.fspath(current))
            self.assertEqual(environment["PATH"], "/usr/bin:/bin")

            await terminal.close(personal_id)

            output.clear()
            alias_result = await terminal.run(
                terminal_id=TERMINAL_ID,
                current_directory=os.fspath(current),
                rows=24,
                columns=80,
                program="sh",
                args=["-c", "printf alias"],
                command=None,
                timeout=2,
                expected_inspection_revision=None,
                stream=stream,
            )
            self.assertEqual(alias_result["exitCode"], 0)
            self.assertEqual("".join(data for _channel, data in output), "alias")

            with self.assertRaises(AgentError):
                await terminal.run(
                    terminal_id=TERMINAL_ID,
                    current_directory=os.fspath(current),
                    rows=24,
                    columns=80,
                    program=None,
                    args=[],
                    command="pwd",
                    timeout=2,
                    expected_inspection_revision="0" * 64,
                    stream=stream,
                )

            task = asyncio.create_task(
                terminal.run(
                    terminal_id=TERMINAL_ID,
                    current_directory=os.fspath(current),
                    rows=24,
                    columns=80,
                    program=None,
                    args=[],
                    command="sleep 10",
                    timeout=20,
                    expected_inspection_revision=None,
                    stream=stream,
                )
            )
            await self._wait_for_run(terminal, TERMINAL_ID)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertNotIn(TERMINAL_ID, terminal._runs)

            for close_all in (False, True):
                task = asyncio.create_task(
                    terminal.run(
                        terminal_id=TERMINAL_ID,
                        current_directory=os.fspath(current),
                        rows=24,
                        columns=80,
                        program=None,
                        args=[],
                        command="sleep 60",
                        timeout=3600,
                        expected_inspection_revision=None,
                        stream=stream,
                    )
                )
                await self._wait_for_run(terminal, TERMINAL_ID)
                if close_all:
                    await asyncio.wait_for(terminal.close_all(), 3)
                else:
                    await asyncio.wait_for(terminal.close(TERMINAL_ID), 3)
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
                self.assertNotIn(TERMINAL_ID, terminal._runs)

    async def test_capability_advertisement_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            config = configuration(Path(temporary))
            agent = MatoLoopAgent(config, "secret")
            self.assertTrue(TERMINAL_OPERATIONS.issubset(agent.operations))
            await agent.terminal.open(24, 80)
            await agent.terminal.close_all()
            self.assertEqual(agent.terminal._terminals, {})
            self.assertEqual(agent.terminal._runs, {})

            without_shell = MatoLoopAgent(configuration(Path(temporary), shell=False), "secret")
            self.assertTrue(TERMINAL_OPERATIONS.isdisjoint(without_shell.operations))
            with patch("matoloop_agent.terminal.PTY_AVAILABLE", False):
                without_pty = MatoLoopAgent(config, "secret")
            self.assertTrue(TERMINAL_OPERATIONS.isdisjoint(without_pty.operations))

    async def test_dispatches_terminal_run_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            agent = MatoLoopAgent(configuration(Path(temporary)), "secret")
            agent.terminal.run = AsyncMock(
                return_value={
                    "exitCode": 0,
                    "timedOut": False,
                    "durationMs": 1,
                    "stdoutBytes": 0,
                    "stderrBytes": 0,
                    "stdoutTruncated": False,
                    "stderrTruncated": False,
                }
            )
            await agent.dispatch(
                Request.model_validate(
                    {
                        "type": "request",
                        "id": TERMINAL_ID,
                        "operation": "terminal.run",
                        "arguments": {
                            "terminalId": AGENT_ID,
                            "currentDirectory": os.fspath(Path(temporary)),
                            "rows": 41,
                            "columns": 132,
                            "command": "pwd",
                        },
                    }
                )
            )
            self.assertEqual(agent.terminal.run.await_args.kwargs["rows"], 41)
            self.assertEqual(agent.terminal.run.await_args.kwargs["columns"], 132)


if __name__ == "__main__":
    unittest.main()
