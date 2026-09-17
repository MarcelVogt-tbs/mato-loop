import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

INSTALLER_PATH = Path(__file__).parents[3] / "scripts/matoloop-agent-installer.py"
SPEC = importlib.util.spec_from_file_location("matoloop_agent_installer", INSTALLER_PATH)
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_system_dependencies_install_only_missing_packages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            packages = Path(temporary) / "system-packages.txt"
            layout = {"packages": packages}

            def fake_run(*command, **_kwargs):
                if command[0] == "dpkg-query":
                    status = b"ii " if command[-1] == "bash" else b""
                    return subprocess.CompletedProcess(command, 0 if status else 1, stdout=status)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.object(installer.shutil, "which", return_value="/usr/bin/tool"),
                patch.object(installer, "confirm", return_value=True),
                patch.object(installer, "run", side_effect=fake_run) as commands,
            ):
                installer.install_system_dependencies(layout)

            installed = commands.call_args_list[-1].args
            self.assertEqual(
                installed[:5],
                ("sudo", "apt-get", "install", "-y", "--no-install-recommends"),
            )
            self.assertNotIn("bash", installed)
            self.assertEqual(set(packages.read_text().splitlines()), set(installed[5:]))

    def test_home_directory_is_never_a_valid_purge_root(self) -> None:
        with patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(Path.home())}):
            with self.assertRaises(SystemExit):
                installer.check_safe_root(installer.paths())
        disguised_home = Path.home() / "unused" / ".."
        with patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(disguised_home)}):
            with self.assertRaises(SystemExit):
                installer.check_safe_root(installer.paths())

    def test_bootstrap_help_has_no_install_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [INSTALLER_PATH.with_name("install-matoloop-agent.sh"), "--help"],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "HOME": temporary},
            )
            self.assertIn("Usage:", result.stdout)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_bootstrap_help_works_from_a_pipe(self) -> None:
        result = subprocess.run(
            ["sh", "-s", "--", "--help"],
            input=INSTALLER_PATH.with_name("install-matoloop-agent.sh").read_text(),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Usage:", result.stdout)

    def test_backend_url_validation_and_conversion(self) -> None:
        self.assertEqual(
            installer.backend_url("https://mato.example/base/"),
            ("wss://mato.example/base/api/execution-agents/connect", False),
        )
        self.assertEqual(
            installer.backend_url("http://127.0.0.1:10020"),
            ("ws://127.0.0.1:10020/api/execution-agents/connect", True),
        )
        for invalid in ("ws://mato.example", "https://mato.example?", "https://mato.example#"):
            with self.subTest(invalid=invalid), self.assertRaises(SystemExit):
                installer.backend_url(invalid)

    def test_preserved_credentials_restore_the_paired_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            credential = Path(temporary) / "credential.json"
            credential.write_text(
                json.dumps({"backendUrl": "wss://mato.example/base/api/execution-agents/connect"})
            )
            self.assertEqual(installer.paired_instance(credential), "https://mato.example/base")

    def test_installed_script_is_the_command_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent root"
            bin_home = Path(temporary) / "bin"
            source = Path(__file__).parents[3]
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(bin_home),
            }
            with patch.dict(os.environ, environment):
                layout = installer.paths()
                root.mkdir()
                installer.write_manager(layout, source)
            self.assertEqual(layout["wrapper"].resolve(), root / "bin/matoloop-agent")
            text = layout["wrapper"].read_text()
            self.assertIn('exec "$PYTHON" "$MANAGER" "$@"', text)
            self.assertIn('export MATOLOOP_AGENT_CONFIG="$INSTALL_ROOT/data/config.json"', text)
            self.assertIn('exec "$INSTALL_ROOT/venv/bin/matoloop-agent" "$@"', text)
            self.assertIn("*/bin/matoloop-agent)", text)
            self.assertIn('INSTALL_ROOT=$(dirname "$(dirname "$RESOLVED_SCRIPT")")', text)

    def test_environment_runs_through_final_activation_link(self) -> None:
        uv = shutil.which("uv")
        if uv is None:
            self.skipTest("uv is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent"
            layout = installer.paths()
            layout = {**layout, "root": root, "uv": Path(uv)}
            destination = root / "venvs/current"
            installer.sync_environment(layout, Path(__file__).parents[3], destination)
            (root / "venv").symlink_to(destination)
            subprocess.run(
                [root / "venv/bin/matoloop-agent", "--help"],
                check=True,
                stdout=subprocess.DEVNULL,
            )

    def test_checkout_install_keeps_every_managed_file_under_selected_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "agent root"
            source = base / "checkout"
            home = base / "workspace"
            (source / "apps/matoloop-agent").mkdir(parents=True)
            (source / "apps/matoloop-agent/pyproject.toml").touch()
            (source / "scripts").mkdir()
            (source / "scripts/matoloop-agent-installer.py").write_text(INSTALLER_PATH.read_text())
            shutil.copy2(INSTALLER_PATH.with_name("install-matoloop-agent.sh"), source / "scripts")
            (root / "tools/bin").mkdir(parents=True)
            (root / "tools/bin/uv").touch()
            (root / ".managed-by-matoloop-agent").touch()
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(base / "bin"),
                "XDG_CONFIG_HOME": str(base / "config"),
            }

            def fake_sync(layout, _source, destination):
                executable = destination / "bin/matoloop-agent"
                executable.parent.mkdir(parents=True)
                executable.touch()

            arguments = installer.argparse.Namespace(
                source=str(source), managed=False, home=str(home), instance_url="https://mato.test"
            )
            with (
                patch.dict(os.environ, environment),
                patch.object(installer, "check_linux"),
                patch.object(installer, "sync_environment", side_effect=fake_sync),
                patch.object(installer, "run"),
            ):
                installer.install(arguments)
                layout = installer.paths()
            self.assertTrue(layout["wrapper"].is_file())
            self.assertTrue(layout["wrapper"].is_symlink())
            self.assertTrue(layout["service"].is_file())
            self.assertTrue(layout["venv"].is_dir())
            self.assertEqual(installer.read_state(layout)["sourceType"], "checkout")
            self.assertEqual(
                json.loads((layout["data"] / "config.json").read_text())["homeDirectory"], str(home)
            )
            previous = layout["venv"].resolve()
            (previous / "working").touch()

            def fail_start(*command, **_kwargs):
                if "enable" in command and "--now" in command:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.dict(os.environ, environment),
                patch.object(installer, "check_linux"),
                patch.object(installer, "sync_environment", side_effect=fake_sync),
                patch.object(installer, "run", side_effect=fail_start),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(arguments)
            self.assertEqual(layout["venv"].resolve(), previous)
            self.assertTrue((previous / "working").exists())

    def test_update_pulls_the_checkout_tracking_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            remote = base / "remote.git"
            author = base / "author"
            source = base / "source"

            def git(*arguments):
                return subprocess.run(
                    ["git", *map(str, arguments)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            git("init", "--bare", remote)
            git("init", "-b", "main", author)
            git("-C", author, "config", "user.name", "MatoLoop Test")
            git("-C", author, "config", "user.email", "test@example.invalid")
            (author / "version").write_text("one")
            git("-C", author, "add", "version")
            git("-C", author, "commit", "-m", "Initial")
            git("-C", author, "remote", "add", "origin", remote)
            git("-C", author, "push", "-u", "origin", "main")
            git("clone", "--branch", "main", remote, source)
            (author / "version").write_text("two")
            git("-C", author, "commit", "-am", "Update")
            git("-C", author, "push")

            root = base / "agent"
            with patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(root)}):
                layout = installer.paths()
                installer.write_json(
                    layout["state"],
                    {
                        "source": str(source),
                        "sourceType": "checkout",
                        "homeDirectory": str(base / "home"),
                        "instanceUrl": "https://mato.test",
                    },
                )
                original_run = installer.run

                def record_git(*command, **kwargs):
                    if command[0] == installer.sys.executable:
                        return subprocess.CompletedProcess(command, 0)
                    return original_run(*command, **kwargs)

                with patch.object(installer, "run", side_effect=record_git) as commands:
                    installer.update()
            self.assertEqual((source / "version").read_text(), "two")
            reinstall = commands.call_args_list[-1].args
            self.assertEqual(
                reinstall[:3],
                (
                    installer.sys.executable,
                    source / "scripts/matoloop-agent-installer.py",
                    "install",
                ),
            )
            self.assertNotIn("--managed", reinstall)

    def test_uninstall_preserves_data_without_purge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            bin_home = Path(temporary) / "bin"
            config_home = Path(temporary) / "config"
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(bin_home),
                "XDG_CONFIG_HOME": str(config_home),
            }
            with patch.dict(os.environ, environment):
                layout = installer.paths()
                (layout["data"] / "agent-state").mkdir(parents=True)
                (layout["data"] / "agent-state/credential.json").write_text("secret")
                (layout["data"] / "config.json").write_text("configuration")
                (layout["data"] / "cache").mkdir()
                layout["venv"].mkdir()
                layout["marker"].touch()
                installer.write_json(layout["state"], {"source": "test"})
                layout["service"].parent.mkdir(parents=True)
                layout["service"].write_text(installer.service_text(layout))
                launcher = root / "bin/matoloop-agent"
                launcher.parent.mkdir()
                launcher.touch()
                layout["wrapper"].parent.mkdir(parents=True)
                layout["wrapper"].symlink_to(launcher)
                inactive = subprocess.CompletedProcess([], 3)
                with patch.object(installer, "run", return_value=inactive):
                    installer.uninstall(purge=False, yes=True)
                self.assertTrue((layout["data"] / "agent-state/credential.json").exists())
                self.assertFalse((layout["data"] / "config.json").exists())
                self.assertFalse((layout["data"] / "cache").exists())
                self.assertFalse(layout["venv"].exists())
                self.assertFalse(layout["wrapper"].exists())
                self.assertFalse(layout["service"].exists())

    def test_purge_removes_complete_managed_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(Path(temporary) / "bin"),
                "XDG_CONFIG_HOME": str(Path(temporary) / "config"),
            }
            with patch.dict(os.environ, environment):
                layout = installer.paths()
                layout["data"].mkdir(parents=True)
                layout["marker"].touch()
                installer.write_json(layout["state"], {"source": "test"})
                layout["packages"].write_text("ripgrep\n")
                inactive = subprocess.CompletedProcess([], 3)
                with (
                    patch.object(installer, "confirm", return_value=True),
                    patch.object(installer.shutil, "which", return_value="/usr/bin/tool"),
                    patch.object(installer, "run", return_value=inactive) as commands,
                ):
                    installer.uninstall(purge=True, yes=True)
                self.assertFalse(root.exists())
                calls = [call.args for call in commands.call_args_list]
                self.assertIn(
                    ("sudo", "apt-get", "remove", "-y", "--", "ripgrep"),
                    calls,
                )
                self.assertLess(
                    calls.index(
                        (
                            "systemctl",
                            "--user",
                            "is-active",
                            "--quiet",
                            "matoloop-agent.service",
                        )
                    ),
                    calls.index(("sudo", "apt-get", "remove", "-y", "--", "ripgrep")),
                )

    def test_uninstall_keeps_files_when_the_running_service_cannot_stop(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(Path(temporary) / "bin"),
                "XDG_CONFIG_HOME": str(Path(temporary) / "config"),
            }
            with patch.dict(os.environ, environment):
                layout = installer.paths()
                layout["data"].mkdir(parents=True)
                layout["marker"].touch()
                installer.write_json(layout["state"], {"source": "test"})
                layout["service"].parent.mkdir(parents=True)
                layout["service"].write_text(installer.service_text(layout))

                def fake_run(*command, **_kwargs):
                    raise subprocess.CalledProcessError(1, command)

                with patch.object(installer, "run", side_effect=fake_run):
                    with self.assertRaises(subprocess.CalledProcessError):
                        installer.uninstall(purge=True, yes=True)
                self.assertTrue(root.exists())

    def test_uninstall_keeps_files_when_service_is_running_without_unit_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(Path(temporary) / "bin"),
                "XDG_CONFIG_HOME": str(Path(temporary) / "config"),
            }
            with patch.dict(os.environ, environment):
                layout = installer.paths()
                layout["data"].mkdir(parents=True)
                layout["marker"].touch()
                installer.write_json(layout["state"], {"source": "test"})

                def fake_run(*command, **_kwargs):
                    return subprocess.CompletedProcess(command, 0)

                with patch.object(installer, "run", side_effect=fake_run):
                    with self.assertRaises(SystemExit):
                        installer.uninstall(purge=True, yes=True)
                self.assertTrue(root.exists())


if __name__ == "__main__":
    unittest.main()
