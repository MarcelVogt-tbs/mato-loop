import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from matoloop_agent.config import AgentConfig
from matoloop_agent.filesystem import AgentError, Filesystem
from matoloop_agent.process import ProcessRunner


def configuration(home: Path) -> AgentConfig:
    config = AgentConfig.model_validate(
        {
            "homeDirectory": home,
            "processExecutables": {"sh": Path("/bin/sh")},
            "shellExecutable": Path("/bin/sh"),
            "maxOutputBytes": 1024,
        }
    )
    config._data_directory = home
    return config


class ProcessTests(unittest.IsolatedAsyncioTestCase):
    async def test_exec_uses_absolute_descriptor_validated_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            chunks = []

            async def stream(channel: str, data: str) -> None:
                chunks.append((channel, data))

            result = await runner.exec(
                current_directory=os.fspath(root),
                program="sh",
                args=["-c", "printf out; printf err >&2"],
                command=None,
                timeout=2,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(dict(chunks), {"stdout": "out", "stderr": "err"})

            with self.assertRaises(AgentError):
                await runner.exec(
                    current_directory="relative",
                    program="sh",
                    args=[],
                    command=None,
                    timeout=1,
                    stream=stream,
                )

    async def test_shell_timeout_and_inspection_revision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "script.py"
            source = root / "env.sh"
            script.write_text("print('unused')")
            source.write_text("VALUE=unused")
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            inspected = runner.inspect_shell(
                "python script.py < env.sh > output.txt", os.fspath(root)
            )
            self.assertFalse(inspected["analysisComplete"])
            self.assertRegex(inspected["inspectionRevision"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                [(target["kind"], target["path"]) for target in inspected["targets"]],
                [
                    ("script", os.fspath(script)),
                    ("input", os.fspath(source)),
                    ("output", os.fspath(root / "output.txt")),
                ],
            )
            original_revision = inspected["inspectionRevision"]
            script.write_text("print('changed')")
            changed = runner.inspect_shell(
                "python script.py < env.sh > output.txt", os.fspath(root)
            )
            self.assertNotEqual(changed["inspectionRevision"], original_revision)

            async def stream(_channel: str, _data: str) -> None:
                return None

            result = await runner.exec(
                current_directory=os.fspath(root),
                program=None,
                args=[],
                command="sleep 5",
                timeout=0.05,
                stream=stream,
            )
            self.assertTrue(result["timedOut"])

            task = asyncio.create_task(
                runner.exec(
                    current_directory=os.fspath(root),
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

    def test_inspection_rejects_symlink_targets_and_dynamic_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.write_text("safe")
            (root / "link").symlink_to(target)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            symlink = runner.inspect_shell("cat link", os.fspath(root))
            self.assertFalse(symlink["readOnly"])
            self.assertEqual(symlink["targets"], [])
            dynamic = runner.inspect_shell("echo $HOME", os.fspath(root))
            self.assertFalse(dynamic["analysisComplete"])
            read_only = runner.inspect_shell("cat target", os.fspath(root))
            self.assertTrue(read_only["readOnly"])
            self.assertEqual(read_only["targets"][0]["path"], os.fspath(target))

            source = root / "env.sh"
            source.write_text("VALUE=safe")
            repository = root / "repository"
            repository.mkdir()
            inspections = {
                "generic": runner.inspect_shell("touch ./target", os.fspath(root)),
                "interpreter": runner.inspect_shell("python target", os.fspath(root)),
                "source": runner.inspect_shell("source env.sh", os.fspath(root)),
                "git": runner.inspect_shell("git -C repository status", os.fspath(root)),
            }
            self.assertTrue(all(not item["analysisComplete"] for item in inspections.values()))
            for name, expected in {
                "generic": target,
                "interpreter": target,
                "source": source,
                "git": repository,
            }.items():
                self.assertEqual(inspections[name]["targets"][0]["path"], os.fspath(expected))


if __name__ == "__main__":
    unittest.main()
