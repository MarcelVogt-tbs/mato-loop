import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path

from mato_loop_execution_agent.config import AgentConfig
from mato_loop_execution_agent.process import ProcessRunner
from mato_loop_execution_agent.workspace import AgentError, Workspace

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
                working_directory=str(root),
                program="sh",
                args=["-c", "printf out; printf err >&2"],
                command=None,
                timeout=2,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(dict(chunks), {"stdout": "out", "stderr": "err"})

            chunks.clear()
            result = await runner.exec(
                root_id=ROOT_ID,
                revision=revision,
                working_directory=str(root),
                program=None,
                args=[],
                command='printf %s "$PATH"',
                timeout=2,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(
                dict(chunks)["stdout"],
                "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            )

            readable = root / "readable.txt"
            readable.write_text("safe read")
            inspection = runner.inspect_shell("cat readable.txt", str(root), ROOT_ID, revision)
            fake_shell = root / "fake-shell"
            fake_shell.write_text("#!/bin/sh\nexit 99\n")
            fake_shell.chmod(0o700)
            configured_shell = runner.shell
            runner.shell = fake_shell
            chunks.clear()
            result = await runner.exec(
                root_id=ROOT_ID,
                revision=revision,
                working_directory=str(root),
                program=None,
                args=[],
                command="cat readable.txt",
                timeout=2,
                stream=stream,
                expected_inspection_revision=inspection["inspectionRevision"],
            )
            runner.shell = configured_shell
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(dict(chunks)["stdout"], "safe read")

            result = await runner.exec(
                root_id=ROOT_ID,
                revision=revision,
                working_directory=str(root),
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
                    working_directory=str(root),
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
                working_directory=str(root),
                program=None,
                args=[],
                command="(trap '' TERM; sleep 10) & exit 0",
                timeout=0.05,
                stream=stream,
            )
            self.assertTrue(result["timedOut"])
            self.assertLess(time.monotonic() - started, 2)

            with self.assertRaises(AgentError):
                await runner.exec(
                    root_id=ROOT_ID,
                    revision="0" * 64,
                    working_directory=str(root),
                    program="sh",
                    args=[],
                    command=None,
                    timeout=1,
                    stream=stream,
                )

    async def test_inspects_direct_shell_paths_without_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "script.py"
            source = root / "env.sh"
            script.write_text("print('unused')")
            source.write_text("VALUE=unused")
            python = Path(os.readlink("/proc/self/exe"))
            configuration = AgentConfig.model_validate(
                {
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
                    "dataDir": root / "data",
                    "processExecutables": {"python": python},
                }
            )
            workspace = Workspace(configuration)
            runner = ProcessRunner(configuration, workspace)
            revision = workspace.roots[ROOT_ID].revision

            inspected = runner.inspect_shell(
                "python script.py < env.sh > output.txt", str(root), ROOT_ID, revision
            )
            self.assertTrue(inspected["analysisComplete"])
            self.assertFalse(inspected["readOnly"])
            self.assertRegex(inspected["inspectionRevision"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                [(target["kind"], target["relativePath"]) for target in inspected["targets"]],
                [("script", "script.py"), ("input", "env.sh"), ("output", "output.txt")],
            )
            original_revision = inspected["inspectionRevision"]
            script.write_text("print('changed')")
            self.assertNotEqual(
                runner.inspect_shell(
                    "python script.py < env.sh > output.txt", str(root), ROOT_ID, revision
                )["inspectionRevision"],
                original_revision,
            )
            (root / "package").mkdir()
            self.assertEqual(
                runner.inspect_shell("python package", str(root), ROOT_ID, revision)["targets"][0][
                    "kind"
                ],
                "directory",
            )
            self.assertFalse(
                runner.inspect_shell("echo $HOME", str(root), ROOT_ID, revision)["analysisComplete"]
            )
            (root / "escape").symlink_to(root.parent / "outside")
            self.assertFalse(
                runner.inspect_shell("python script.py > escape", str(root), ROOT_ID, revision)[
                    "analysisComplete"
                ]
            )
            self.assertFalse(
                runner.inspect_shell("unknown command", str(root), ROOT_ID, revision)[
                    "analysisComplete"
                ]
            )
            self.assertFalse(
                runner.inspect_shell("/bin/sh script.py", str(root), ROOT_ID, revision)[
                    "analysisComplete"
                ]
            )

            read_only = runner.inspect_shell("cat script.py", str(root), ROOT_ID, revision)
            self.assertTrue(read_only["analysisComplete"])
            self.assertTrue(read_only["readOnly"])
            self.assertEqual(read_only["targets"][0]["relativePath"], "script.py")
            self.assertFalse(
                runner.inspect_shell("cat script.py > output.txt", str(root), ROOT_ID, revision)[
                    "readOnly"
                ]
            )
            self.assertFalse(
                runner.inspect_shell("python script.py", str(root), ROOT_ID, revision)["readOnly"]
            )
            self.assertFalse(
                runner.inspect_shell("cat script.py | wc -l", str(root), ROOT_ID, revision)[
                    "readOnly"
                ]
            )
            link = root / "link"
            link.symlink_to(root.parent / "outside")
            self.assertFalse(
                runner.inspect_shell("cat link", str(root), ROOT_ID, revision)["readOnly"]
            )
            for unsafe in (
                "ls -L .",
                "tail -f script.py",
                "wc --files0-from=script.py",
                "sha256sum -c script.py",
            ):
                self.assertFalse(
                    runner.inspect_shell(unsafe, str(root), ROOT_ID, revision)["readOnly"]
                )

            aliased = AgentConfig.model_validate(
                {
                    "name": "Agent",
                    "roots": [{"id": ROOT_ID, "label": "Root", "path": root}],
                    "dataDir": root / "alias-data",
                    "processExecutables": {"rm": Path("/usr/bin/cat")},
                }
            )
            self.assertFalse(
                ProcessRunner(aliased, Workspace(aliased)).inspect_shell(
                    "rm script.py", str(root), ROOT_ID, revision
                )["readOnly"]
            )


if __name__ == "__main__":
    unittest.main()
