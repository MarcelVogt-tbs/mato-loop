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
                command="printf out; printf err >&2",
                timeout=2,
                stream=stream,
            )
            self.assertEqual(result["exitCode"], 0)
            self.assertEqual(dict(chunks), {"stdout": "out", "stderr": "err"})

            with self.assertRaises(AgentError):
                await runner.exec(
                    current_directory="relative",
                    command="pwd",
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
            self.assertTrue(inspected["analysisComplete"])
            self.assertEqual(inspected["classification"], "write")
            self.assertRegex(inspected["inspectionRevision"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                [(target["kind"], target["path"]) for target in inspected["targets"]],
                [
                    ("input", os.fspath(source)),
                    ("output", os.fspath(root / "output.txt")),
                    ("script", os.fspath(script)),
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
                command="sleep 5",
                timeout=0.05,
                stream=stream,
            )
            self.assertTrue(result["timedOut"])

            task = asyncio.create_task(
                runner.exec(
                    current_directory=os.fspath(root),
                    command="sleep 5",
                    timeout=10,
                    stream=stream,
                )
            )
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    def test_inspection_preserves_path_boundaries_and_allows_dynamic_shell(self) -> None:
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
            self.assertTrue(dynamic["analysisComplete"])
            self.assertEqual(dynamic["classification"], "read")
            self.assertEqual(dynamic["commands"][0]["tokens"], ["echo", "$HOME"])
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
            self.assertTrue(all(item["analysisComplete"] for item in inspections.values()))
            self.assertEqual(inspections["generic"]["classification"], "write")
            self.assertTrue(
                all(
                    inspections[name]["classification"] == "read"
                    for name in ("interpreter", "source", "git")
                )
            )
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
                command="cat first.txt | head -n 1 && cat second.txt; pwd",
                timeout=2,
                stream=stream,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(executed["exitCode"], 0)
            stdout = "".join(data for channel, data in output if channel == "stdout")
            self.assertIn("one\nthree\n", stdout)

    async def test_inspection_allows_static_coding_discovery_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))
            command = (
                "pwd && printf '\\n--- top-level ---\\n' && "
                "find . -maxdepth 2 -mindepth 1 -not -path './.git/*' -print "
                "| sort | head -200"
            )

            inspected = runner.inspect_shell(command, os.fspath(root))

            self.assertTrue(inspected["analysisComplete"])
            self.assertTrue(inspected["readOnly"])
            self.assertEqual(inspected["classification"], "read")
            self.assertEqual(
                [unit["tokens"] for unit in inspected["commands"]],
                [
                    ["pwd"],
                    ["printf", "\\n--- top-level ---\\n"],
                    [
                        "find",
                        ".",
                        "-maxdepth",
                        "2",
                        "-mindepth",
                        "1",
                        "-not",
                        "-path",
                        "./.git/*",
                        "-print",
                    ],
                    ["sort"],
                    ["head", "-200"],
                ],
            )
            self.assertTrue(all(unit["classification"] == "read" for unit in inspected["commands"]))
            self.assertEqual(
                [(target["kind"], target["path"]) for target in inspected["targets"]],
                [("directory", os.fspath(root))],
            )

            quoted_pattern = runner.inspect_shell(
                "find . -type f -name '*.py' -print0", os.fspath(root)
            )
            self.assertTrue(quoted_pattern["readOnly"])

            before = list(root.iterdir())
            output = []

            async def stream(channel: str, data: str) -> None:
                output.append((channel, data))

            executed = await runner.exec(
                current_directory=os.fspath(root),
                command=command,
                timeout=2,
                stream=stream,
                expected_inspection_revision=inspected["inspectionRevision"],
            )
            self.assertEqual(executed["exitCode"], 0)
            self.assertIn("--- top-level ---", "".join(data for _, data in output))
            self.assertEqual(list(root.iterdir()), before)

    def test_inspection_recognizes_definite_mutations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = configuration(root)
            runner = ProcessRunner(config, Filesystem(config))

            for command in (
                "find . -delete",
                "find . -exec cat {} ;",
                "find . -fprint output.txt",
                "sort -o output.txt",
                "sort --output=output.txt",
                "printf -v value test",
                "printf -vvalue test",
                "printf -v'x[$(touch output.txt)]' %s value",
                "printf %2hn PATH && find .",
                "printf '%s\\n' test > output.txt",
                'printf "$(touch output.txt)"',
                "printf safe#; touch output.txt",
                "find . -name victim# -delete && pwd",
            ):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, os.fspath(root))
                    self.assertFalse(inspected["readOnly"])
                    self.assertEqual(inspected["classification"], "write")

            for command in (
                "make test",
                "npm test",
                "python -c pass",
                "curl https://example.invalid",
                "find . -maxdepth invalid",
                "find . -name *.py",
                "printf target & pwd",
                "git branch --show-current",
                "git config user.name",
                "git remote -v",
                "git stash list",
                "git tag --list",
                "git worktree list",
            ):
                with self.subTest(command=command):
                    inspected = runner.inspect_shell(command, os.fspath(root))
                    self.assertTrue(inspected["analysisComplete"])
                    self.assertEqual(inspected["classification"], "read")

            for command in (
                "rm target",
                "npm install",
                "git commit -m test",
                "git branch feature",
                "git config user.name Test",
                "git stash",
                "curl -XPOST https://example.invalid",
                "curl -ooutput.txt https://example.invalid",
            ):
                with self.subTest(command=command):
                    self.assertEqual(
                        runner.inspect_shell(command, os.fspath(root))["classification"],
                        "write",
                    )

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
            self.assertEqual(unknown["classification"], "read")
            self.assertTrue(unknown["analysisComplete"])
            self.assertEqual(unknown["commands"][0]["prefix"], ["curl"])

            interpreter = runner.inspect_shell("python -c pass", os.fspath(root))
            self.assertEqual(interpreter["classification"], "read")
            self.assertNotIn("prefix", interpreter["commands"][0])

            for command in ("./git status", "git -c alias.check=!true check"):
                with self.subTest(command=command):
                    unsafe_prefix = runner.inspect_shell(command, os.fspath(root))
                    self.assertNotIn("prefix", unsafe_prefix["commands"][0])

            nested = runner.inspect_shell("cat $(touch target)", os.fspath(root))
            self.assertEqual(nested["classification"], "write")
            self.assertEqual(len(nested["commands"]), 2)

            rejected = runner.inspect_shell("cat 'unterminated", os.fspath(root))
            self.assertEqual(rejected["classification"], "write")
            self.assertEqual(rejected["commands"], [])


if __name__ == "__main__":
    unittest.main()
