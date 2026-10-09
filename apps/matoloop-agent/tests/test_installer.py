import asyncio
import importlib.util
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from matoloop_agent.config import AgentConfig
from matoloop_agent.platform_linux import LinuxPlatform

INSTALLER_PATH = Path(__file__).parents[3] / "scripts/matoloop-agent-installer.py"
SPEC = importlib.util.spec_from_file_location("matoloop_agent_installer", INSTALLER_PATH)
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_browser_packages_are_not_core_dependencies(self) -> None:
        project = INSTALLER_PATH.parents[1] / "apps/matoloop-agent/pyproject.toml"
        dependencies = project.read_text().split("[project.scripts]", 1)[0]
        self.assertNotIn("playwright", dependencies)
        self.assertNotIn("pillow", dependencies)

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
            self.assertIn('UV_UNMANAGED_INSTALL="$TOOLS_DIR/bin"', text)
            self.assertIn("export UV_PYTHON_INSTALL_BIN=0", text)
            self.assertIn("export UV_MANAGED_PYTHON=1", text)

    def test_installed_wrapper_help_includes_native_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent"
            (root / "bin").mkdir(parents=True)
            (root / "venv/bin").mkdir(parents=True)
            shutil.copy2(
                INSTALLER_PATH.with_name("install-matoloop-agent.sh"),
                root / "bin/matoloop-agent",
            )
            (root / "bin/python").touch(mode=0o700)
            (root / "installer.py").touch()
            runtime = root / "venv/bin/matoloop-agent"
            runtime.write_text("#!/bin/sh\nprintf '%s\\n' 'runtime help'\n")
            runtime.chmod(0o700)

            result = subprocess.run(
                [root / "bin/matoloop-agent", "--help"],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("runtime help", result.stdout)
            self.assertIn("install [--home PATH] [--instance-url URL]", result.stdout)
            self.assertIn("uninstall [--purge] [--yes]", result.stdout)

    def test_system_service_runs_as_installing_user_at_boot(self) -> None:
        text = installer.service_text({"wrapper": Path("/home/test/.local/bin/matoloop-agent")})
        self.assertIn(f"User={installer.pwd.getpwuid(os.getuid()).pw_name}", text)
        self.assertIn("WantedBy=multi-user.target", text)
        self.assertNotIn("WantedBy=default.target", text)

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
            result = subprocess.run(
                [
                    destination / "bin/python",
                    "-c",
                    "import importlib.util; print(importlib.util.find_spec('playwright'))",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout.strip(), "None")

    def test_container_activation_and_failed_start_restore_the_running_release(self) -> None:
        for failure in (None, "start", "launcher"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                for name in ("old", "new"):
                    release = root / "releases" / name
                    executable = release / "venv/bin/matoloop-agent"
                    executable.parent.mkdir(parents=True)
                    command = (
                        "raise SystemExit(1)"
                        if failure == "start" and name == "new"
                        else "import time; time.sleep(60)"
                    )
                    executable.write_text(f"#!/bin/sh\nexec '{sys.executable}' -c '{command}'\n")
                    executable.chmod(0o755)
                    source = release / "source/scripts"
                    source.mkdir(parents=True)
                    (source / "matoloop-agent-installer.py").write_text(f"# {name}\n")
                launcher = root / "installer.py"
                launcher.write_text("# old\n")
                replace = os.replace
                interrupted = False

                def interrupted_replace(
                    source, destination, *, failure=failure, launcher=launcher, replace=replace
                ):
                    nonlocal interrupted
                    if failure == "launcher" and destination == launcher and not interrupted:
                        interrupted = True
                        self.assertEqual(launcher.read_text(), "# old\n")
                        raise OSError("Launcher replacement interrupted")
                    return replace(source, destination)

                state = {"release": "releases/old", "sourceUrl": "unused"}
                installer.write_json(root / "install.json", state)
                installer.container_start(root, state)
                try:
                    with patch.object(installer.os, "replace", side_effect=interrupted_replace):
                        if failure:
                            with self.assertRaises(RuntimeError if failure == "start" else OSError):
                                installer.activate_container_release(
                                    root, state, root / "releases/new"
                                )
                        else:
                            installer.activate_container_release(root, state, root / "releases/new")
                    self.assertEqual(
                        (
                            json.loads((root / "install.json").read_text())["release"],
                            installer._container_process.poll(),
                        ),
                        ("releases/old" if failure else "releases/new", None),
                    )
                finally:
                    installer.container_stop()

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
            system_service = base / "system/matoloop-agent.service"

            def fake_sync(layout, _source, destination):
                self.assertNotIn(("sudo", "-v"), [call.args for call in commands.call_args_list])
                executable = destination / "bin/matoloop-agent"
                executable.parent.mkdir(parents=True)
                executable.touch()

            def fake_run(*command, **_kwargs):
                if command[:4] == ("sudo", "install", "-m", "0644"):
                    Path(command[-1]).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(command[-2], command[-1])
                elif command[:3] == ("sudo", "rm", "-f"):
                    Path(command[-1]).unlink(missing_ok=True)
                return subprocess.CompletedProcess(command, 0)

            arguments = installer.argparse.Namespace(
                source=str(source), managed=False, home=str(home), instance_url="https://mato.test"
            )
            with (
                patch.dict(os.environ, environment),
                patch.object(installer, "SYSTEM_SERVICE_PATH", system_service),
                patch.object(installer, "check_linux"),
                patch.object(installer, "sync_environment", side_effect=fake_sync),
                patch.object(installer, "run", side_effect=fake_run) as commands,
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
                if command[:4] == ("sudo", "install", "-m", "0644"):
                    shutil.copy2(command[-2], command[-1])
                elif command[:3] == ("sudo", "rm", "-f"):
                    Path(command[-1]).unlink(missing_ok=True)
                if "enable" in command and "--now" in command:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch.dict(os.environ, environment),
                patch.object(installer, "SYSTEM_SERVICE_PATH", system_service),
                patch.object(installer, "check_linux"),
                patch.object(installer, "sync_environment", side_effect=fake_sync),
                patch.object(installer, "run", side_effect=fail_start) as commands,
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(arguments)
            self.assertEqual(layout["venv"].resolve(), previous)
            self.assertTrue((previous / "working").exists())

    def test_target_revision_requires_clean_backend_metadata(self) -> None:
        for version in ("2026-10-08-aaaaaaaa", "2026-10-08-aaaaaaaa-dirty"):
            with self.subTest(version=version), patch.object(installer, "build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(
                    json.dumps({"revision": "a" * 40, "version": version}).encode()
                )
                if version.endswith("-dirty"):
                    with self.assertRaises(SystemExit):
                        installer.target_revision("https://mato.test")
                else:
                    self.assertEqual(installer.target_revision("https://mato.test"), "a" * 40)

    def test_update_pins_backend_commit_in_both_directions_without_changing_user_checkout(
        self,
    ) -> None:
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
            helper = author / "libs/python/version/src/matoloop_version/__init__.py"
            helper.parent.mkdir(parents=True)
            helper.touch()
            git("-C", author, "add", ".")
            git("-C", author, "commit", "-m", "Initial")
            first = git("-C", author, "rev-parse", "HEAD").stdout.strip()
            git("-C", author, "remote", "add", "origin", remote)
            git("-C", author, "push", "-u", "origin", "main")
            git("clone", "--branch", "main", remote, source)
            (author / "version").write_text("two")
            git("-C", author, "commit", "-am", "Update")
            second = git("-C", author, "rev-parse", "HEAD").stdout.strip()
            git("-C", author, "push")

            root = base / "agent"
            with patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(root)}):
                layout = installer.paths()
                installer.write_json(
                    layout["state"],
                    {
                        "source": str(source),
                        "sourceType": "checkout",
                        "sourceUrl": str(remote),
                        "homeDirectory": str(base / "home"),
                        "instanceUrl": "https://mato.test",
                    },
                )
                installed = []

                def record_install(arguments):
                    installed.append(
                        git("-C", arguments.source, "rev-parse", "HEAD").stdout.strip()
                    )

                with (
                    patch.object(installer, "target_revision", side_effect=[second, first]),
                    patch.object(installer, "install", side_effect=record_install),
                ):
                    installer.update(in_process=True)
                    installer.update(in_process=True)
            self.assertEqual(
                (installed, (source / "version").read_text()), ([second, first], "one")
            )

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
            system_service = Path(temporary) / "system/matoloop-agent.service"
            with (
                patch.dict(os.environ, environment),
                patch.object(installer, "SYSTEM_SERVICE_PATH", system_service),
            ):
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

                def fake_run(*command, **_kwargs):
                    if command[:3] == ("sudo", "rm", "-f"):
                        Path(command[-1]).unlink(missing_ok=True)
                    return subprocess.CompletedProcess(command, 3)

                with patch.object(installer, "run", side_effect=fake_run):
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
            with (
                patch.dict(os.environ, environment),
                patch.object(
                    installer,
                    "SYSTEM_SERVICE_PATH",
                    Path(temporary) / "system/matoloop-agent.service",
                ),
            ):
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
            with (
                patch.dict(os.environ, environment),
                patch.object(
                    installer,
                    "SYSTEM_SERVICE_PATH",
                    Path(temporary) / "system/matoloop-agent.service",
                ),
            ):
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
            with (
                patch.dict(os.environ, environment),
                patch.object(
                    installer,
                    "SYSTEM_SERVICE_PATH",
                    Path(temporary) / "system/matoloop-agent.service",
                ),
            ):
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

    def test_maintenance_uses_custom_root_and_reports_after_uninstall(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent root"
            (root / "data/agent-state").mkdir(parents=True)
            (root / ".managed-by-matoloop-agent").touch()
            (root / "data/agent-state/credential.json").write_text(
                json.dumps(
                    {
                        "backendUrl": "wss://mato.test/api/execution-agents/connect",
                        "agentId": "agent-id",
                        "accessKey": "private-token",
                    }
                )
            )
            installer.write_json(root / "install.json", {"source": "checkout"})
            socket_path = str(Path(temporary) / "handoff.sock")
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(socket_path)
            listener.listen(1)

            def handoff():
                with listener:
                    connection, _ = listener.accept()
                    with connection:
                        connection.sendall(b"secret")

            thread = threading.Thread(target=handoff)
            thread.start()
            calls = []

            def remove(*, purge, yes):
                assert not purge and yes
                assert os.environ["MATOLOOP_AGENT_INSTALL_ROOT"] == str(root)
                calls.append("uninstall")
                (root / "install.json").unlink()

            def report(request, **_kwargs):
                calls.append(
                    (
                        request.full_url,
                        json.loads(request.data),
                        request.get_header("Authorization"),
                    )
                )
                return io.BytesIO()

            try:
                with (
                    patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(root)}),
                    patch.object(installer, "uninstall", side_effect=remove),
                    patch.object(installer, "build_opener") as opener,
                ):
                    opener.return_value.open.side_effect = report
                    installer.maintenance_run("uninstall", "job-id", socket_path, str(root))
            finally:
                installer._maintenance_password = None
                thread.join(timeout=5)
            self.assertEqual(
                calls,
                [
                    "uninstall",
                    (
                        "https://mato.test/api/execution-agents/agent-id/maintenance/job-id/complete",
                        {"status": "succeeded"},
                        "Bearer private-token",
                    ),
                ],
            )

    def test_maintenance_starts_separate_user_service_without_exposing_password(self) -> None:
        async def check():
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "agent root"
                root.mkdir()
                (root / ".managed-by-matoloop-agent").touch()
                (root / "installer.py").touch()
                config = AgentConfig(homeDirectory=root)
                config._data_directory = root / "data"
                calls = []
                connections = []
                handoffs = []

                class Child:
                    returncode = 0

                    async def communicate(self, data):
                        calls[-1].append(data)

                async def spawn(*command, **_kwargs):
                    calls.append([*command])
                    if "maintenance-run" in command:
                        address = command[command.index("maintenance-run") + 3]

                        async def receive():
                            reader, writer = await asyncio.open_unix_connection(address)
                            connections.append(await reader.read())
                            writer.close()
                            await writer.wait_closed()

                        handoffs.append(asyncio.create_task(receive()))
                    return Child()

                with (
                    patch.dict(os.environ, {"MATOLOOP_AGENT_CONTAINER": "0"}),
                    patch(
                        "matoloop_agent.platform_linux.shutil.which",
                        side_effect=lambda name: f"/usr/bin/{name}",
                    ),
                    patch(
                        "matoloop_agent.platform_linux.asyncio.create_subprocess_exec",
                        side_effect=spawn,
                    ),
                ):
                    result = await LinuxPlatform().start_maintenance(
                        config, "update", "ce137f81-2b83-4e07-950d-c35306533e16", "secret", "a" * 40
                    )
                await asyncio.gather(*handoffs)
                self.assertEqual(result, {"started": True})
                self.assertEqual(connections, [b"secret"])
                self.assertIn("--collect", calls[1])
                self.assertIn(str(root), calls[1])
                self.assertIn("a" * 40, calls[1])
                self.assertNotIn("secret", str(calls[0][:-1]) + str(calls[1][:-1]))

        asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
