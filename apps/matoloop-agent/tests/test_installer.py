import asyncio
import importlib.util
import io
import json
import os
import plistlib
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from matoloop_agent.config import AgentConfig
from matoloop_agent.errors import AgentError
from matoloop_agent.maintenance_results import replay_maintenance_results
from matoloop_agent.platform_linux import LinuxPlatform

INSTALLER_PATH = Path(__file__).parents[3] / "scripts/matoloop-agent-installer.py"
SPEC = importlib.util.spec_from_file_location("matoloop_agent_installer", INSTALLER_PATH)
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def test_failed_completion_is_retained_and_replayed_with_the_current_credential(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            config = AgentConfig(
                homeDirectory=data,
                backendUrl="wss://mato.test/api/execution-agents/connect",
                agentId="2ea504cc-e3c6-4e0d-aa05-080328100fe8",
            )
            config._data_directory = data
            credential = {
                "backendUrl": config.backend_url,
                "agentId": config.agent_id,
                "accessKey": "old-key",
            }
            job_id = "bc7f3dfd-d674-4781-b5fa-22ee0c6e5eb0"
            with (
                patch.object(installer, "build_opener") as opener,
                patch.object(installer.time, "sleep"),
            ):
                opener.return_value.open.side_effect = OSError("Offline")
                installer.report_maintenance(credential, job_id, "succeeded", data)
            pending = data / "agent-state" / f"maintenance-{job_id}.json"
            self.assertNotIn("old-key", pending.read_text())
            requests = []

            def complete(request):
                requests.append(
                    (
                        request.url.path,
                        json.loads(request.content),
                        request.headers["Authorization"],
                    )
                )
                return httpx.Response(204)

            async def replay():
                client = httpx.AsyncClient(transport=httpx.MockTransport(complete))
                with patch(
                    "matoloop_agent.maintenance_results.httpx.AsyncClient", return_value=client
                ):
                    await replay_maintenance_results(config, "current-key")

            asyncio.run(replay())
            self.assertEqual(
                (requests, pending.exists()),
                (
                    [
                        (
                            f"/api/execution-agents/{config.agent_id}/maintenance/{job_id}/complete",
                            {"status": "succeeded"},
                            "Bearer current-key",
                        )
                    ],
                    False,
                ),
            )

    def test_completion_replay_checks_identity_and_retires_superseded_jobs(self) -> None:
        for scenario in ("foreign", "superseded", "offline", "symlink"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                data = Path(temporary)
                config = AgentConfig(
                    homeDirectory=data,
                    backendUrl="wss://mato.test/api/execution-agents/connect",
                    agentId="2ea504cc-e3c6-4e0d-aa05-080328100fe8",
                )
                config._data_directory = data
                job_id = "bc7f3dfd-d674-4781-b5fa-22ee0c6e5eb0"
                pending = data / "agent-state" / f"maintenance-{job_id}.json"
                installer.write_json(
                    pending,
                    {
                        "backendUrl": "wss://other.test/api/execution-agents/connect"
                        if scenario == "foreign"
                        else config.backend_url,
                        "agentId": config.agent_id,
                        "jobId": job_id,
                        "status": "failed",
                    },
                )
                if scenario == "symlink":
                    target = data / "outside.json"
                    pending.rename(target)
                    pending.symlink_to(target)
                requests = []

                def complete(request, requests=requests, scenario=scenario):
                    requests.append(request)
                    return httpx.Response(
                        503 if scenario == "offline" else 409,
                        json={"error": {"code": "maintenance.jobUnavailable"}},
                    )

                async def replay(config=config, complete=complete):
                    client = httpx.AsyncClient(transport=httpx.MockTransport(complete))
                    with patch(
                        "matoloop_agent.maintenance_results.httpx.AsyncClient",
                        return_value=client,
                    ):
                        await replay_maintenance_results(config, "key")

                asyncio.run(replay())
                self.assertEqual(
                    (len(requests), pending.exists()),
                    (0 if scenario in {"foreign", "symlink"} else 1, scenario != "superseded"),
                )

    def test_native_lock_excludes_other_commands_and_survives_installer_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".managed-by-matoloop-agent").touch()
            config_path = root / "data/config.json"
            installer.write_json(config_path, {"homeDirectory": str(root)})
            config = AgentConfig.load(config_path)
            script = (
                f"import runpy; module = runpy.run_path({str(INSTALLER_PATH)!r}); "
                "lock = module['installation_lock'](); lock.__enter__(); "
                "print('acquired'); lock.__exit__(None, None, None)"
            )
            environment = {
                **os.environ,
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "MATOLOOP_AGENT_CONTAINER": "0",
            }
            with patch.dict(os.environ, environment), installer.installation_lock():
                self.assertTrue(LinuxPlatform().maintenance_running(config))
                denied = subprocess.run(
                    [sys.executable, "-c", script],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                inherited = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    env={
                        **environment,
                        "MATOLOOP_AGENT_INSTALL_LOCK_FD": str(installer._installation_lock_fd),
                    },
                    pass_fds=(installer._installation_lock_fd,),
                )
            self.assertEqual(
                (
                    "another installation" in denied.stderr,
                    inherited.stdout.strip(),
                    LinuxPlatform().maintenance_running(config),
                ),
                (True, "acquired", False),
            )

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
            source = root / "source"
            installer.copy_source(Path(__file__).parents[3], source)
            installer.stamp_source(Path(__file__).parents[3], source)
            self.assertEqual(
                (
                    sorted(path.name for path in source.iterdir()),
                    sorted(path.name for path in (source / "apps").iterdir()),
                ),
                (["LICENSE", "apps", "libs", "scripts"], ["matoloop-agent"]),
            )
            snapshot = root / "reinstalled-source"
            installer.copy_source(source, snapshot)
            installer.stamp_source(source, snapshot)
            installer.sync_environment(layout, snapshot, destination)
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
                    "import importlib.util; from matoloop_agent import __version__; "
                    "print(__version__); print(importlib.util.find_spec('playwright'))",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            stamp = source / "libs/python/version/src/matoloop_version/build-version.txt"
            self.assertEqual(
                result.stdout.strip().splitlines(), [stamp.read_text().strip(), "None"]
            )

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
            browser_modes = []

            def fake_sync(layout, _source, destination, *, browser=False):
                browser_modes.append(browser)
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
            (root / "tools/browser").mkdir()

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
            self.assertEqual(
                (
                    browser_modes,
                    any("browser-install" in call.args for call in commands.call_args_list),
                ),
                ([False, True], True),
            )

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
            (root / "venv").mkdir()
            private_module = root / "tools/python/encodings/idna.py"
            private_module.parent.mkdir(parents=True)
            shutil.copyfile(importlib.util.find_spec("encodings.idna").origin, private_module)
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

            def report(request, **_kwargs):
                spec = importlib.util.spec_from_file_location("private_idna", private_module)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                calls.append(
                    (
                        request.full_url,
                        json.loads(request.data),
                        request.get_header("Authorization"),
                        module.ToASCII("münchen"),
                        (root / "install.json").exists(),
                    )
                )
                return io.BytesIO()

            try:
                with (
                    patch.dict(
                        os.environ,
                        {
                            "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                            "MATOLOOP_AGENT_WRAPPER_PATH": str(Path(temporary) / "bin/agent"),
                        },
                    ),
                    patch.object(installer, "SYSTEM_SERVICE_PATH", root / "service"),
                    patch.object(installer, "run", return_value=subprocess.CompletedProcess([], 3)),
                    patch.object(installer, "build_opener") as opener,
                ):
                    opener.return_value.open.side_effect = report
                    installer.maintenance_run(
                        "uninstall", "2ea504cc-e3c6-4e0d-aa05-080328100fe8", socket_path, str(root)
                    )
            finally:
                installer._maintenance_password = None
                thread.join(timeout=5)
            self.assertEqual(
                (
                    calls,
                    (root / "tools").exists(),
                    (root / "data/agent-state/credential.json").exists(),
                ),
                (
                    [
                        (
                            "https://mato.test/api/execution-agents/agent-id/maintenance/2ea504cc-e3c6-4e0d-aa05-080328100fe8/complete",
                            {"status": "succeeded"},
                            "Bearer private-token",
                            b"xn--mnchen-3ya",
                            False,
                        ),
                    ],
                    False,
                    True,
                ),
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
                cleanups = []

                async def before_start():
                    self.assertIn("-v", calls[-1])
                    cleanups.append(True)

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
                        config,
                        "update",
                        "ce137f81-2b83-4e07-950d-c35306533e16",
                        "secret",
                        "a" * 40,
                        before_start=before_start,
                    )
                    self.assertEqual(cleanups, [True])
                    cleanups.clear()
                    Child.returncode = 1
                    with self.assertRaises(AgentError):
                        await LinuxPlatform().start_maintenance(
                            config,
                            "update",
                            "ce137f81-2b83-4e07-950d-c35306533e16",
                            "wrong",
                            "a" * 40,
                            before_start=before_start,
                        )
                    self.assertEqual(cleanups, [])
                await asyncio.gather(*handoffs)
                self.assertEqual(result, {"started": True})
                self.assertEqual(connections, [b"secret"])
                self.assertIn("--collect", calls[1])
                self.assertIn(str(root), calls[1])
                self.assertIn("a" * 40, calls[1])
                self.assertNotIn("secret", str(calls[0][:-1]) + str(calls[1][:-1]))

        asyncio.run(check())


class MacOSInstallerTests(unittest.TestCase):
    def test_private_ripgrep_verifies_archive_before_replacing_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            binary = b"verified executable"
            archive = io.BytesIO()
            with tarfile.open(fileobj=archive, mode="w:gz") as bundle:
                for name in ("rg", "COPYING", "LICENSE-MIT", "UNLICENSE"):
                    content = binary if name == "rg" else b"license"
                    member = tarfile.TarInfo(f"{installer.MACOS_RIPGREP_ARCHIVE}/{name}")
                    member.size = len(content)
                    bundle.addfile(member, io.BytesIO(content))
            payload = archive.getvalue()
            executable = root / "tools/bin/rg"
            installer.write_file(executable, b"old executable", 0o700)

            def download(*command, **_kwargs):
                if command[0] == "curl":
                    Path(command[command.index("--output") + 1]).write_bytes(payload)
                return subprocess.CompletedProcess(command, 0)

            with patch.object(installer, "run", side_effect=download):
                with self.assertRaises(SystemExit):
                    installer.install_macos_ripgrep({"root": root})
                self.assertEqual(executable.read_bytes(), b"old executable")
                with (
                    patch.object(
                        installer,
                        "MACOS_RIPGREP_SHA256",
                        installer.hashlib.sha256(payload).hexdigest(),
                    ),
                    patch.object(
                        installer,
                        "MACOS_RIPGREP_BINARY_SHA256",
                        installer.hashlib.sha256(binary).hexdigest(),
                    ),
                ):
                    installer.install_macos_ripgrep({"root": root})
            self.assertEqual(executable.read_bytes(), binary)

    def test_macos_startup_rejects_a_crashing_job_even_if_launchd_restarts_it(self) -> None:
        with (
            patch.object(installer.sys, "platform", "darwin"),
            patch.object(
                installer,
                "run",
                return_value=subprocess.CompletedProcess(
                    [],
                    0,
                    stdout="1235\n",
                ),
            ),
        ):
            with self.assertRaises(SystemExit):
                installer.service_active(check=True, expected_pid=1234)

    def test_launchagent_uses_login_domain_and_private_paths_with_spaces(self) -> None:
        with (
            patch.object(installer.sys, "platform", "darwin"),
            patch.object(installer.Path, "home", return_value=Path("/Users/test")),
            patch.dict(os.environ, {}, clear=True),
        ):
            layout = installer.paths()
            job = plistlib.loads(installer.service_text(layout).encode())
        self.assertEqual(
            layout["root"], Path("/Users/test/Library/Application Support/MatoLoop Agent")
        )
        self.assertEqual(job["ProgramArguments"], [str(layout["wrapper"]), "run"])
        self.assertTrue(job["RunAtLoad"])
        self.assertNotIn("UserName", job)
        self.assertIn(str(layout["root"] / "tools/bin"), job["EnvironmentVariables"]["PATH"])

    def test_macos_install_rollback_browser_probe_and_uninstall_preserve_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "checkout"
            root = base / "Library/Application Support/MatoLoop Agent"
            workspace = base / "workspace"
            (source / "apps/matoloop-agent").mkdir(parents=True)
            (source / "apps/matoloop-agent/pyproject.toml").touch()
            (source / "scripts").mkdir()
            for name in ("matoloop-agent-installer.py", "install-matoloop-agent.sh"):
                shutil.copy2(INSTALLER_PATH.with_name(name), source / "scripts" / name)
            (root / "tools/bin").mkdir(parents=True)
            (root / "tools/bin/uv").touch()
            (root / ".managed-by-matoloop-agent").touch()
            environment = {
                "MATOLOOP_AGENT_INSTALL_ROOT": str(root),
                "XDG_BIN_HOME": str(base / "bin"),
            }
            loaded = False
            fail_bootstrap = False
            commands = []
            browser_modes = []

            def fake_sync(_layout, _source, destination, *, browser=False):
                browser_modes.append(browser)
                executable = destination / "bin/matoloop-agent"
                executable.parent.mkdir(parents=True)
                executable.touch()

            def fake_run(*command, **kwargs):
                nonlocal loaded, fail_bootstrap
                commands.append(command)
                if command[:2] == ("launchctl", "bootstrap"):
                    if fail_bootstrap:
                        fail_bootstrap = False
                        raise subprocess.CalledProcessError(1, command)
                    loaded = True
                elif command[:2] == ("launchctl", "bootout"):
                    loaded = False
                elif command[:2] == ("launchctl", "print") and "/" in command[2]:
                    if command[2].count("/") == 2:
                        return subprocess.CompletedProcess(
                            command,
                            0 if loaded else 113,
                            stdout="state = running\n" if loaded else "",
                        )
                elif command[:2] == ("launchctl", "kickstart"):
                    return subprocess.CompletedProcess(command, 0, stdout=str(os.getpid()))
                if "browser-install" in command:
                    self.assertEqual(kwargs["env"]["PLAYWRIGHT_SKIP_BROWSER_GC"], "1")
                    (root / "tools/browser").mkdir(exist_ok=True)
                return subprocess.CompletedProcess(command, 0, stdout="")

            arguments = installer.argparse.Namespace(
                source=str(source),
                managed=True,
                home=str(workspace),
                instance_url="https://mato.test",
            )
            with (
                patch.dict(os.environ, environment),
                patch.object(installer.sys, "platform", "darwin"),
                patch.object(installer.platform, "machine", return_value="arm64"),
                patch.object(installer.os, "getuid", return_value=501),
                patch.object(installer.Path, "home", return_value=base),
                patch.object(
                    installer.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
                ),
                patch.object(installer, "sync_environment", side_effect=fake_sync),
                patch.object(installer, "install_macos_ripgrep"),
                patch.object(installer, "run", side_effect=fake_run),
                patch.object(installer.time, "sleep"),
                patch.object(installer.sys.stdin, "isatty", return_value=False),
            ):
                installer.install(arguments)
                self.assertFalse(any("browser-install" in command for command in commands))
                layout = installer.paths()
                previous = layout["venv"].resolve()
                old_state = layout["state"].read_bytes()
                old_service = layout["service"].read_bytes()
                credential = layout["data"] / "agent-state/credential.json"
                credential.parent.mkdir()
                credential.write_text(
                    json.dumps({"backendUrl": "wss://mato.test/api/execution-agents/connect"})
                )
                (workspace / "user-file").touch()
                # Browser Integration setup is optional; only subsequent installs retain it.
                (root / "tools/browser").mkdir()
                fail_bootstrap = True
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(arguments)
                self.assertEqual(layout["venv"].resolve(), previous)
                self.assertEqual(layout["state"].read_bytes(), old_state)
                self.assertEqual(layout["service"].read_bytes(), old_service)
                self.assertTrue(loaded)
                installer.uninstall(purge=False, yes=True)
                self.assertTrue(credential.is_file())
                self.assertEqual(
                    json.loads((layout["data"] / "config.json").read_text())["homeDirectory"],
                    str(workspace),
                )
                self.assertTrue((workspace / "user-file").exists())
                self.assertFalse(layout["service"].exists())
                self.assertFalse(loaded)
            self.assertEqual(browser_modes, [False, True])
            self.assertFalse(
                any("sudo" in command or "systemctl" in command for command in commands)
            )

    def test_macos_maintenance_launcher_hands_off_or_unloads_only_its_job(self) -> None:
        job_id = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
        temporary_directory = tempfile.TemporaryDirectory
        for failed in (False, True):
            with self.subTest(failed=failed), temporary_directory() as temporary:
                root = Path(temporary) / "agent root"
                (root / "data").mkdir(parents=True)
                commands, observed, threads = [], [], []

                def bootstrap(
                    *command,
                    failed=failed,
                    commands=commands,
                    observed=observed,
                    threads=threads,
                    **_kwargs,
                ):
                    commands.append(command)
                    if command[1] != "bootstrap":
                        return
                    if failed:
                        raise subprocess.TimeoutExpired(command, 15)
                    arguments = plistlib.loads(Path(command[3]).read_bytes())["ProgramArguments"]

                    def worker():
                        with socket.socket(socket.AF_UNIX) as channel:
                            channel.settimeout(5)
                            channel.connect(arguments[5])
                            observed.append(channel.recv(1))

                    thread = threading.Thread(target=worker)
                    threads.append(thread)
                    thread.start()

                with (
                    patch.dict(os.environ),
                    patch.object(installer.sys, "platform", "darwin"),
                    patch.object(installer, "check_linux"),
                    patch.object(installer, "run", side_effect=bootstrap),
                    patch.object(
                        installer.tempfile,
                        "TemporaryDirectory",
                        side_effect=lambda **kwargs: temporary_directory(prefix=kwargs["prefix"]),
                    ),
                ):
                    with self.assertRaises(subprocess.TimeoutExpired) if failed else nullcontext():
                        installer.maintenance_start("update", job_id, str(root), "a" * 40)
                    for thread in threads:
                        thread.join(timeout=6)
                self.assertEqual(
                    (observed, [command[1:] for command in commands[1:]]),
                    (
                        [],
                        [("bootout", f"gui/{os.getuid()}/com.matoloop.agent.maintenance.{job_id}")],
                    )
                    if failed
                    else ([b""], []),
                )

    def test_macos_maintenance_handoff_holds_lock_without_password(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "agent root"
            (root / "data/agent-state").mkdir(parents=True)
            (root / ".managed-by-matoloop-agent").touch()
            installer.write_json(root / "install.json", {"source": "checkout"})
            installer.write_json(root / "data/agent-state/credential.json", {"agentId": "test"})
            # Keep the UNIX socket path below the macOS sun_path limit.
            with tempfile.TemporaryDirectory(prefix="handoff-") as handoff_directory:
                address = str(Path(handoff_directory) / "sock")
                listener = socket.socket(socket.AF_UNIX)
                listener.bind(address)
                listener.listen(1)
                observed = []

                def handoff():
                    with listener:
                        listener.settimeout(5)
                        channel, _ = listener.accept()
                        with channel:
                            observed.append(installer._installation_lock_fd is not None)
                            channel.shutdown(socket.SHUT_WR)

                thread = threading.Thread(target=handoff)
                thread.start()
                try:
                    with (
                        patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(root)}),
                        patch.object(installer.sys, "platform", "darwin"),
                        patch.object(installer, "check_linux"),
                        patch.object(installer, "update") as update,
                        patch.object(installer, "report_maintenance") as report,
                        patch.object(
                            installer.sys,
                            "argv",
                            [
                                "installer.py",
                                "maintenance-run",
                                "update",
                                "2ea504cc-e3c6-4e0d-aa05-080328100fe8",
                                address,
                                str(root),
                                "--revision",
                                "a" * 40,
                            ],
                        ),
                    ):
                        installer.main()
                    update.assert_called_once_with(in_process=False, revision="a" * 40)
                    self.assertEqual(report.call_args.args[2], "succeeded")
                    self.assertEqual(observed, [True])
                    self.assertIsNone(installer._maintenance_password)
                finally:
                    thread.join(timeout=6)

    def test_macos_target_installer_finishes_before_worker_interrupts(self) -> None:
        events = []
        child = Mock()
        child.__enter__ = Mock(return_value=child)
        child.__exit__ = Mock(return_value=False)
        child.terminate.side_effect = lambda: events.append("terminate")

        def wait():
            installer.signal.getsignal(installer.signal.SIGTERM)(installer.signal.SIGTERM, None)
            events.append("rollback finished")
            return 1

        child.wait.side_effect = wait
        with patch.object(installer.subprocess, "Popen", return_value=child):
            with self.assertRaises(SystemExit):
                installer.run_maintenance_installer("python", "target-installer.py")
        self.assertEqual(events, ["terminate", "rollback finished"])

    def test_macos_supervisor_reaps_deadline_and_preserves_reported_success(self) -> None:
        for action, outcome, installed in (
            ("update", b"", True),
            ("update", b"succeeded", True),
            ("uninstall", b"", True),
            ("uninstall", b"", False),
            ("uninstall", b"succeeded", False),
            ("uninstall", b"retained", False),
        ):
            with (
                self.subTest(action=action, outcome=outcome, installed=installed),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                (root / "tools/python").mkdir(parents=True)
                if installed:
                    installer.write_json(root / "install.json", {"source": "checkout"})
                credential = {
                    "backendUrl": "wss://mato.test/api/execution-agents/connect",
                    "agentId": "test",
                    "accessKey": "test-key",
                }
                installer.write_json(root / "data/agent-state/credential.json", credential)
                child = Mock(pid=12345)
                child.wait.side_effect = [
                    subprocess.TimeoutExpired("worker", 3600),
                    subprocess.TimeoutExpired("worker", 30),
                    -9,
                    -9,
                ]
                events = []
                job_id = "2ea504cc-e3c6-4e0d-aa05-080328100fe8"
                if outcome == b"retained":
                    installer.write_json(
                        root / "data/agent-state" / f"maintenance-{job_id}.json",
                        {
                            "backendUrl": credential["backendUrl"],
                            "agentId": credential["agentId"],
                            "jobId": job_id,
                            "status": "succeeded",
                        },
                    )

                def report(*_args, root=root, events=events):
                    self.assertTrue((root / "tools/python").is_dir())
                    events.append(_args[2])

                def spawn(_command, outcome=outcome, child=child, **kwargs):
                    self.assertTrue(kwargs["start_new_session"])
                    if outcome == b"retained":
                        outcome = b"succeeded"
                    os.write(kwargs["pass_fds"][0], outcome)
                    return child

                with (
                    patch.dict(os.environ, {"MATOLOOP_AGENT_INSTALL_ROOT": str(root)}),
                    patch.object(installer.sys, "platform", "darwin"),
                    patch.object(installer.subprocess, "Popen", side_effect=spawn),
                    patch.object(installer.os, "killpg") as kill,
                    patch.object(
                        installer,
                        "report_maintenance",
                        side_effect=report,
                    ),
                    patch.object(
                        installer,
                        "run",
                        side_effect=lambda *_args, events=events, **_kwargs: events.append(
                            "bootout"
                        ),
                    ),
                ):
                    installer.maintenance_supervise(
                        action,
                        job_id,
                        "/unused",
                        str(root),
                        "a" * 40 if action == "update" else None,
                    )
                self.assertEqual(
                    events,
                    ["succeeded", "bootout"]
                    if outcome == b"retained"
                    else ["bootout"]
                    if outcome
                    else ["failed", "bootout"],
                )
                self.assertEqual(kill.call_args_list[0].args, (12345, installer.signal.SIGTERM))
                self.assertEqual((root / "tools").exists(), installed)

    def test_macos_rejects_root_and_intel_before_launching_services(self) -> None:
        for uid, machine in ((0, "arm64"), (501, "x86_64")):
            with (
                self.subTest(uid=uid, machine=machine),
                patch.object(installer.sys, "platform", "darwin"),
                patch.object(installer.os, "getuid", return_value=uid),
                patch.object(installer.platform, "machine", return_value=machine),
                patch.object(installer, "run") as commands,
            ):
                with self.assertRaises(SystemExit):
                    installer.check_linux()
                commands.assert_not_called()


if __name__ == "__main__":
    unittest.main()
