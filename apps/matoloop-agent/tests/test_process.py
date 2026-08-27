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
            self.assertEqual(dynamic["classification"], "write")
            self.assertEqual(dynamic["commands"], [])
            read_only = runner.inspect_shell("cat target", os.fspath(root))
            self.assertTrue(read_only["readOnly"])
            self.assertEqual(read_only["classification"], "read")
            self.assertEqual(
                read_only["commands"],
                [{"tokens": ["cat", "target"], "prefix": ["cat"], "classification": "read"}],
            )
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

    async def test_inspection_classifies_flat_commands_and_executes_read_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "first.txt"
            second = root / "second.txt"
            first.write_text("one\ntwo\n")
            second.write_text("three\n")
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            inspected = runner.inspect_shell(
                "cat first.txt | head -n 1 && cat second.txt; pwd",
                os.fspath(root),
            )
            self.assertTrue(inspected["analysisComplete"])
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                inspected["commands"],
                [
                    {
                        "tokens": ["cat", "first.txt"],
                        "prefix": ["cat"],
                        "classification": "read",
                    },
                    {
                        "tokens": ["head", "-n", "1"],
                        "prefix": ["head"],
                        "classification": "read",
                    },
                    {
                        "tokens": ["cat", "second.txt"],
                        "prefix": ["cat"],
                        "classification": "read",
                    },
                    {"tokens": ["pwd"], "prefix": ["pwd"], "classification": "read"},
                ],
            )

            output = []

            async def stream(channel: str, data: str) -> None:
                output.append((channel, data))

            executed = await runner.exec(
                current_directory=os.fspath(root),
                program=None,
                args=[],
                command="cat first.txt | head -n 1 && cat second.txt; pwd",
                timeout=2,
                stream=stream,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(executed["exitCode"], 0)
            stdout = "".join(data for channel, data in output if channel == "stdout")
            self.assertIn("one\nthree\n", stdout)

    def test_inspection_prefixes_are_token_based_and_writes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "target").write_text("safe")
            (root / "repository").mkdir()
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            write = runner.inspect_shell(
                "cat target > output.txt && git -C repository status",
                os.fspath(root),
            )
            self.assertEqual(write["classification"], "write")
            self.assertFalse(write["readOnly"])
            self.assertEqual(write["commands"][0]["classification"], "write")
            self.assertEqual(write["commands"][1]["prefix"], ["git", "status"])

            read = runner.inspect_shell("cat < target", os.fspath(root))
            self.assertEqual(read["classification"], "read")
            self.assertEqual(read["commands"][0]["tokens"], ["cat"])

            unknown = runner.inspect_shell("curl https://example.invalid", os.fspath(root))
            self.assertEqual(unknown["classification"], "write")
            self.assertFalse(unknown["analysisComplete"])
            self.assertEqual(unknown["commands"][0]["prefix"], ["curl"])

            interpreter = runner.inspect_shell("python -c pass", os.fspath(root))
            self.assertEqual(interpreter["classification"], "write")
            self.assertNotIn("prefix", interpreter["commands"][0])

            for command in ("./git status", "git -c alias.check=!true check"):
                with self.subTest(command=command):
                    unsafe_prefix = runner.inspect_shell(command, os.fspath(root))
                    self.assertNotIn("prefix", unsafe_prefix["commands"][0])

            for command in ("cat $(touch target)", "cat 'unterminated", "cat target & pwd"):
                with self.subTest(command=command):
                    rejected = runner.inspect_shell(command, os.fspath(root))
                    self.assertEqual(rejected["classification"], "write")
                    self.assertEqual(rejected["commands"], [])


if __name__ == "__main__":
    unittest.main()
