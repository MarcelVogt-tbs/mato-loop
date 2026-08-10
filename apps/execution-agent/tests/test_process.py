import asyncio
import tempfile
import time
import unittest
from pathlib import Path

from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.process import ProcessRunner
from mato_loop_execution_agent.workspace import Workspace

AGENT_ID = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
ROOT_ID = "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d"


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_streams_bounded_output_and_times_out_process_group(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = AgentConfig.model_validate(
                {
                    "backendUrl": "ws://localhost/api/execution-agents/connect",
                    "token": "secret",
                    "agentId": AGENT_ID,
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
                    "dataDir": root / "data",
                    "allowInsecure": True,
                    "processExecutables": {"sh": Path("/bin/sh")},
                    "shellExecutable": Path("/bin/sh"),
                    "maxOutputBytes": 1024,
                }
            )
            workspace = Workspace(configuration)
            runner = ProcessRunner(configuration, workspace)
            self.assertEqual(runner.executables["sh"], Path("/bin/sh").resolve())
            revision = workspace.roots[ROOT_ID].revision
            chunks = []

            async def stream(channel: str, data: str) -> None:
                chunks.append((channel, data))

            result = await runner.exec(
                root_id=ROOT_ID,
                revision=revision,
                working_directory=".",
                program="sh",
                args=["-c", "printf out; printf err >&2"],
                command=None,
                timeout=2,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(dict(chunks), {"stdout": "out", "stderr": "err"})

            result = await runner.exec(
                root_id=ROOT_ID,
                revision=revision,
                working_directory=".",
                program=None,
                args=[],
                command="sleep 5",
                timeout=0.05,
                stream=stream,
            )
            self.assertTrue(result["timedOut"])
            self.assertNotEqual(result["exitCode"], 0)

            task = asyncio.create_task(
                runner.exec(
                    root_id=ROOT_ID,
                    revision=revision,
                    working_directory=".",
                    program=None,
                    args=[],
                    command="sleep 5",
                    timeout=10,
                    stream=stream,
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

            started = time.monotonic()
            result = await runner.exec(
                root_id=ROOT_ID,
                revision=revision,
                working_directory=".",
                program=None,
                args=[],
                command="(trap '' TERM; sleep 10) & exit 0",
                timeout=0.05,
                stream=stream,
            )
            self.assertTrue(result["timedOut"])
            self.assertLess(time.monotonic() - started, 2)


if __name__ == "__main__":
    unittest.main()
