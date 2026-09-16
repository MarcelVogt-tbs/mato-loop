#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

SOURCE_URL = "https://git.devmato.net/devmato/MatoLoop.git"
SYSTEM_PACKAGES = (
    "bash",
    "gcc",
    "libc6-dev",
    "bash-builtins",
    "libreadline-dev",
    "openscad",
    "ripgrep",
)


def paths() -> dict[str, Path]:
    home = Path.home()
    requested_root = (
        Path(
            os.environ.get("MATOLOOP_AGENT_INSTALL_ROOT")
            or Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "matoloop-agent"
        )
        .expanduser()
        .absolute()
    )
    root = requested_root.resolve(strict=False)
    state_path = root / "install.json"
    try:
        stored = json.loads(state_path.read_text(encoding="utf-8"))
        stored = stored if isinstance(stored, dict) else {}
    except (OSError, ValueError):
        stored = {}
    return {
        "requested_root": requested_root,
        "root": root,
        "state": state_path,
        "data": root / "data",
        "source": root / "source",
        "venv": root / "venv",
        "venvs": root / "venvs",
        "uv": root / "tools/bin/uv",
        "python": root / "bin/python",
        "installer": root / "installer.py",
        "packages": root / "system-packages.txt",
        "marker": root / ".managed-by-matoloop-agent",
        "wrapper": Path(
            stored.get("wrapperPath")
            or os.environ.get("MATOLOOP_AGENT_WRAPPER_PATH")
            or Path(os.environ.get("XDG_BIN_HOME", home / ".local/bin")) / "matoloop-agent"
        ),
        "service": Path(
            stored.get("servicePath")
            or Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
            / "systemd/user/matoloop-agent.service"
        ),
    }


def fail(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def check_safe_root(layout: dict[str, Path]) -> None:
    root = layout["root"].absolute()
    home = Path.home().resolve()
    if root == Path(root.anchor) or root == home or root in home.parents:
        fail("unsafe managed installation root")
    requested = layout["requested_root"]
    if any(path.is_symlink() for path in (requested, *requested.parents)):
        fail("managed installation paths must not contain symlinks")


def run(*command: str | Path, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("check", True)
    return subprocess.run([os.fspath(item) for item in command], **kwargs)


def read_state(layout: dict[str, Path]) -> dict:
    if layout["state"].is_symlink():
        fail(f"invalid installer state: {layout['state']}")
    try:
        value = json.loads(layout["state"].read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        fail(f"invalid installer state: {layout['state']}")
    if not isinstance(value, dict):
        fail(f"invalid installer state: {layout['state']}")
    return value


def write_json(path: Path, value: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def write_file(path: Path, content: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prompt(label: str, default: str) -> str:
    if not sys.stdin.isatty():
        return default
    return input(f"{label} [{default}]: ").strip() or default


def confirm(label: str, *, default: bool) -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input(f"{label} [{'Y/n' if default else 'y/N'}] ").strip().lower()
    return answer in {"y", "yes"} or (default and not answer)


def backend_url(instance_url: str) -> tuple[str, bool]:
    value = instance_url.rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
            and not any(character.isspace() for character in value)
        )
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            valid = False
    except ValueError:
        valid = False
    if not valid:
        fail("the MatoLoop instance URL must be a valid http:// or https:// URL")
    websocket = "ws" if parsed.scheme == "http" else "wss"
    return (
        f"{websocket}://{value.split('://', 1)[1]}/api/execution-agents/connect",
        websocket == "ws",
    )


def paired_instance(credential: Path) -> str | None:
    try:
        value = json.loads(credential.read_text(encoding="utf-8"))["backendUrl"]
    except FileNotFoundError:
        return None
    except (KeyError, OSError, TypeError, ValueError):
        fail(f"invalid Agent credentials: {credential}")
    if not isinstance(value, str) or not value.endswith("/api/execution-agents/connect"):
        fail(f"invalid Agent credentials: {credential}")
    if value.startswith("ws://"):
        return f"http://{value[5 : -len('/api/execution-agents/connect')]}"
    if value.startswith("wss://"):
        return f"https://{value[6 : -len('/api/execution-agents/connect')]}"
    fail(f"invalid Agent credentials: {credential}")


def check_linux() -> None:
    if not sys.platform.startswith("linux"):
        fail(f"native installation is not supported on {sys.platform} yet")
    if os.getuid() == 0:
        fail("the native Agent must be installed as a regular user")
    for command in ("systemctl",):
        if shutil.which(command) is None:
            fail(f"{command} is required for the Linux user service")


def install_system_dependencies(layout: dict[str, Path]) -> None:
    if layout["packages"].is_symlink():
        fail("invalid system package tracking file")
    if shutil.which("apt-get") is None or shutil.which("dpkg-query") is None:
        print("Automatic system dependency installation requires Debian or Ubuntu.")
        return
    missing = [
        package
        for package in SYSTEM_PACKAGES
        if run(
            "dpkg-query",
            "-W",
            "-f=${db:Status-Abbrev}",
            package,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        ).stdout
        != b"ii "
    ]
    if not missing:
        return
    print("Missing system packages: " + " ".join(missing))
    if not confirm("Install missing system dependencies?", default=True):
        print("Continuing without optional system dependencies.")
        return
    if shutil.which("sudo") is None:
        fail("sudo is required to install system dependencies")
    run("sudo", "apt-get", "update")
    run("sudo", "apt-get", "install", "-y", "--no-install-recommends", *missing)
    tracked = set(missing)
    try:
        tracked.update(layout["packages"].read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        pass
    write_file(layout["packages"], ("\n".join(sorted(tracked)) + "\n").encode(), 0o600)


def sync_environment(layout: dict[str, Path], source: Path, destination: Path) -> None:
    project = source / "apps/matoloop-agent"
    if not (project / "pyproject.toml").is_file():
        fail(f"not a MatoLoop checkout: {source}")
    environment = {
        **os.environ,
        "UV_CACHE_DIR": os.fspath(layout["root"] / "cache/uv"),
        "UV_PYTHON_INSTALL_DIR": os.fspath(layout["root"] / "tools/python"),
        "VIRTUAL_ENV": os.fspath(destination),
    }
    run(
        layout["uv"],
        "sync",
        "--directory",
        project,
        "--frozen",
        "--no-dev",
        "--no-editable",
        "--active",
        env=environment,
    )
    executable = destination / (
        "Scripts/matoloop-agent.exe" if os.name == "nt" else "bin/matoloop-agent"
    )
    if not executable.is_file():
        fail("uv did not install the MatoLoop Agent executable")


def try_build_shared_terminal(source: Path) -> None:
    project = source / "apps/matoloop-agent"
    output = project / "src/matoloop_agent/native/shared.so"
    output.unlink(missing_ok=True)
    if not all(shutil.which(command) for command in ("sh", "gcc")):
        return
    temporary = output.with_suffix(".so.tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        run("sh", project / "build-native.sh", temporary, stdout=subprocess.DEVNULL)
        temporary.replace(output)
    except subprocess.CalledProcessError:
        temporary.unlink(missing_ok=True)
        print(
            "Shared terminal prerequisites are unavailable; continuing without it.", file=sys.stderr
        )


def copy_source(source: Path, destination: Path, *, keep_git: bool) -> None:
    ignored = [".venv", "__pycache__", ".pytest_cache", ".ruff_cache"]
    if not keep_git:
        ignored.append(".git")
    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=shutil.ignore_patterns(*ignored),
    )


def service_text(layout: dict[str, Path]) -> str:
    executable = (
        os.fspath(layout["wrapper"]).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    )
    return f"""[Unit]
Description=MatoLoop Computer Agent
After=network-online.target
Wants=network-online.target

[Service]
ExecStart="{executable}" run
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=default.target
"""


def write_manager(layout: dict[str, Path], source: Path) -> None:
    shutil.copy2(source / "scripts/matoloop-agent-installer.py", layout["installer"])
    launcher = layout["root"] / "bin/matoloop-agent"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "scripts/install-matoloop-agent.sh", launcher)
    launcher.chmod(0o700)
    python = Path(sys.executable).resolve()
    layout["python"].parent.mkdir(parents=True, exist_ok=True)
    layout["python"].unlink(missing_ok=True)
    layout["python"].symlink_to(python)
    layout["wrapper"].parent.mkdir(parents=True, exist_ok=True)
    if layout["wrapper"].is_dir() and not layout["wrapper"].is_symlink():
        fail(f"the command path is a directory: {layout['wrapper']}")
    layout["wrapper"].unlink(missing_ok=True)
    layout["wrapper"].symlink_to(launcher)


def write_runtime_files(layout: dict[str, Path], source: Path) -> None:
    write_manager(layout, source)
    if layout["service"].is_symlink():
        fail(f"the user service path is a symlink: {layout['service']}")
    write_file(layout["service"], service_text(layout).encode(), 0o600)


def install(arguments: argparse.Namespace) -> None:
    check_linux()
    layout = paths()
    check_safe_root(layout)
    legacy = Path.home() / "mato-loop-agent"
    if (
        not layout["state"].exists()
        and (legacy / "source/.git/matoloop-installer-managed").exists()
    ):
        fail(
            "an old installer-managed setup exists; remove it with its installer before continuing"
        )
    if not layout["uv"].is_file():
        fail("private uv runtime is missing; run scripts/install-matoloop-agent.sh")
    if layout["root"].is_dir() and not layout["marker"].is_file():
        if any(layout["root"].iterdir()):
            fail("the installation root is not empty and is not managed by MatoLoop")
    if not layout["marker"].exists() and (
        layout["wrapper"].exists()
        or layout["wrapper"].is_symlink()
        or layout["service"].exists()
        or layout["service"].is_symlink()
    ):
        fail("an unmanaged MatoLoop Agent wrapper or user service already exists")
    if layout["marker"].is_symlink():
        fail("the installation ownership marker is invalid")
    if layout["service"].is_symlink():
        fail(f"the user service path is a symlink: {layout['service']}")
    for managed_file in (
        layout["installer"],
        layout["root"] / "bin/matoloop-agent",
        layout["python"],
    ):
        if managed_file.is_symlink() and managed_file != layout["python"]:
            fail(f"managed installation file is a symlink: {managed_file}")

    state = read_state(layout)
    source = Path(arguments.source or state.get("source", "")).expanduser().resolve()
    managed = arguments.managed if arguments.source else state.get("sourceType") == "managed"
    if not source.is_dir():
        fail("the configured source checkout is unavailable")
    layout["root"].mkdir(parents=True, exist_ok=True)
    if layout["data"].is_symlink():
        fail("managed installation paths must not be symlinks")
    layout["root"].chmod(0o700)
    layout["marker"].touch(mode=0o600, exist_ok=True)
    if not state:
        write_manager(layout, source)
    home = Path(
        arguments.home
        or state.get("homeDirectory")
        or prompt("Agent home directory", os.fspath(Path.home() / "mato-loop-agent/workspace"))
    ).expanduser()
    home.mkdir(parents=True, exist_ok=True)
    home = home.resolve()
    if home == layout["root"] or layout["root"] in home.parents or home in layout["root"].parents:
        fail("the Agent home directory must be outside the managed installation")
    credential = layout["data"] / "agent-state/credential.json"
    if credential.is_symlink():
        fail(f"invalid Agent credentials: {credential}")
    paired_url = paired_instance(credential)
    if arguments.instance_url and paired_url and arguments.instance_url.rstrip("/") != paired_url:
        fail("the instance URL cannot change while pairing credentials are preserved")
    instance = (
        arguments.instance_url
        or paired_url
        or state.get("instanceUrl")
        or prompt("MatoLoop instance URL", "http://127.0.0.1:10020")
    )
    websocket, insecure = backend_url(instance)

    layout["data"].mkdir(parents=True, exist_ok=True)
    layout["data"].chmod(0o700)
    install_system_dependencies(layout)
    staged_source = layout["root"] / (".source-new" if managed else ".source-build")
    shutil.rmtree(staged_source, ignore_errors=True)
    cleanup_paths = [staged_source]

    def cleanup_staging() -> None:
        for path in cleanup_paths:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            else:
                shutil.rmtree(path, ignore_errors=True)

    atexit.register(cleanup_staging)
    copy_source(source, staged_source, keep_git=managed)
    active_source = staged_source
    try_build_shared_terminal(active_source)
    layout["venvs"].mkdir(exist_ok=True)
    new_venv = layout["venvs"] / secrets.token_hex(8)
    cleanup_paths.append(new_venv)
    sync_environment(layout, active_source, new_venv)
    run(new_venv / "bin/matoloop-agent", "--help", stdout=subprocess.DEVNULL)
    if not managed:
        shutil.rmtree(active_source)

    config_path = layout["data"] / "config.json"
    if config_path.is_symlink():
        fail(f"invalid Agent configuration: {config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except (OSError, ValueError):
        fail(f"invalid Agent configuration: {config_path}")
    config.update({"homeDirectory": os.fspath(home), "allowInsecure": insecure})
    shell = shutil.which("bash")
    openscad = shutil.which("openscad")
    if shell:
        config["shellExecutable"] = shell
    else:
        config.pop("shellExecutable", None)
    if openscad:
        config["openscadExecutable"] = openscad
    else:
        config.pop("openscadExecutable", None)

    active = (
        run(
            "systemctl", "--user", "is-active", "--quiet", "matoloop-agent.service", check=False
        ).returncode
        == 0
    )
    enabled = (
        run(
            "systemctl", "--user", "is-enabled", "--quiet", "matoloop-agent.service", check=False
        ).returncode
        == 0
    )
    old_source = layout["root"] / ".source-old"
    old_venv = layout["venv"].resolve() if layout["venv"].is_symlink() else None
    shutil.rmtree(old_source, ignore_errors=True)
    managed_files = (
        layout["installer"],
        layout["root"] / "bin/matoloop-agent",
        layout["service"],
        config_path,
        layout["state"],
    )
    snapshots = {
        path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.is_file() else None
        for path in managed_files
    }
    new_state = {
        "source": os.fspath(layout["source"] if managed else source),
        "sourceType": "managed" if managed else "checkout",
        "sourceUrl": state.get("sourceUrl")
        or os.environ.get("MATOLOOP_AGENT_SOURCE_URL", SOURCE_URL),
        "homeDirectory": os.fspath(home),
        "instanceUrl": instance.rstrip("/"),
        "wrapperPath": os.fspath(layout["wrapper"]),
        "servicePath": os.fspath(layout["service"]),
    }
    try:
        if active:
            run("systemctl", "--user", "stop", "matoloop-agent.service")
        if managed and layout["source"].exists():
            layout["source"].replace(old_source)
        if managed:
            active_source.replace(layout["source"])
            source = layout["source"]
        temporary_link = layout["root"] / ".venv-link"
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(new_venv)
        os.replace(temporary_link, layout["venv"])
        write_json(config_path, config)
        write_runtime_files(layout, source)
        write_json(layout["state"], new_state)
        run("systemctl", "--user", "daemon-reload")
        run("systemctl", "--user", "enable", "--now", "matoloop-agent.service")
        for _ in range(2):
            time.sleep(1)
            run("systemctl", "--user", "is-active", "--quiet", "matoloop-agent.service")
    except BaseException:
        run("systemctl", "--user", "stop", "matoloop-agent.service", check=False)
        if old_venv is None:
            layout["venv"].unlink(missing_ok=True)
        else:
            temporary_link = layout["root"] / ".venv-link"
            temporary_link.unlink(missing_ok=True)
            temporary_link.symlink_to(old_venv)
            os.replace(temporary_link, layout["venv"])
        shutil.rmtree(new_venv, ignore_errors=True)
        if managed and state:
            shutil.rmtree(layout["source"], ignore_errors=True)
            if old_source.exists():
                old_source.replace(layout["source"])
                source = layout["source"]
        for path, snapshot in snapshots.items():
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                write_file(path, *snapshot)
        if not state:
            write_json(layout["state"], new_state)
        run(
            "systemctl",
            "--user",
            "enable" if enabled else "disable",
            "matoloop-agent.service",
            check=False,
        )
        if layout["venv"].exists():
            run("systemctl", "--user", "daemon-reload", check=False)
            if active:
                run("systemctl", "--user", "start", "matoloop-agent.service", check=False)
        raise
    shutil.rmtree(old_source, ignore_errors=True)
    if old_venv is not None and old_venv != new_venv and layout["venvs"] in old_venv.parents:
        shutil.rmtree(old_venv, ignore_errors=True)
    cleanup_paths.clear()
    atexit.unregister(cleanup_staging)

    if not credential.exists() and sys.stdin.isatty():
        print("Create a computer under Einstellungen > Computer, then enter its values.")
        run(layout["wrapper"], "pair", "--backend-url", websocket)
    print(f"MatoLoop Agent installed. Status: {layout['wrapper']} status")


def update() -> None:
    layout = paths()
    state = read_state(layout)
    if not state:
        fail("MatoLoop Agent is not installed")
    git = shutil.which("git")
    if git is None:
        fail("git is required to update this source installation")
    source = Path(state["source"])
    if run(git, "-C", source, "status", "--porcelain", capture_output=True, text=True).stdout:
        fail("the source checkout has local changes; use install to deploy them without pulling")
    run(git, "-C", source, "symbolic-ref", "--quiet", "HEAD")
    run(git, "-C", source, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    run(git, "-C", source, "pull", "--ff-only")
    command = [
        sys.executable,
        source / "scripts/matoloop-agent-installer.py",
        "install",
        "--source",
        source,
        "--home",
        state["homeDirectory"],
        "--instance-url",
        state["instanceUrl"],
    ]
    if state.get("sourceType") == "managed":
        command.append("--managed")
    run(*command)


def status() -> None:
    layout = paths()
    state = read_state(layout)
    if not state:
        fail("MatoLoop Agent is not installed")
    print(f"Source: {state['source']} ({state['sourceType']})")
    print(f"Data:   {layout['data']}")
    print(f"Home:   {state['homeDirectory']}")
    run("systemctl", "--user", "status", "--no-pager", "matoloop-agent.service", check=False)


def uninstall(purge: bool, yes: bool) -> None:
    layout = paths()
    check_safe_root(layout)
    state = read_state(layout)
    if not state and not layout["service"].exists() and not layout["marker"].is_file():
        print("MatoLoop Agent is not installed.")
        return
    if not layout["marker"].is_file() or layout["marker"].is_symlink():
        fail("the installation ownership marker is missing; no files were removed")
    if layout["data"].is_symlink():
        fail("the managed data path is a symlink; no files were removed")
    launcher = layout["root"] / "bin/matoloop-agent"
    if layout["wrapper"].exists() or layout["wrapper"].is_symlink():
        if not layout["wrapper"].is_symlink() or layout["wrapper"].resolve() != launcher:
            fail("the MatoLoop Agent command was replaced; no files were removed")
    if layout["service"].exists():
        if layout["service"].is_symlink() or layout["service"].read_text() != service_text(layout):
            fail("the MatoLoop Agent user service was replaced; no files were removed")
    if not yes and sys.stdin.isatty():
        detail = " and credentials" if purge else " (credentials will be preserved)"
        if input(f"Remove MatoLoop Agent{detail}? [y/N] ").strip().lower() not in {
            "y",
            "yes",
            "j",
            "ja",
        }:
            return
    elif not yes:
        fail("unattended uninstall requires --yes")
    run("systemctl", "--user", "stop", "matoloop-agent.service", check=False)
    if (
        run(
            "systemctl", "--user", "is-active", "--quiet", "matoloop-agent.service", check=False
        ).returncode
        == 0
    ):
        fail("the MatoLoop Agent service is still running; no files were removed")
    packages = []
    if layout["packages"].is_symlink():
        fail("the system package tracking file is invalid")
    try:
        packages = layout["packages"].read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        pass
    if packages and confirm("Remove system packages installed by MatoLoop?", default=False):
        if any(
            not package
            or package.startswith("-")
            or not all(character.isalnum() or character in "+.-:" for character in package)
            for package in packages
        ):
            fail("the tracked system package list is invalid")
        if shutil.which("sudo") is None or shutil.which("apt-get") is None:
            fail("sudo and apt-get are required to remove system dependencies")
        run("sudo", "apt-get", "remove", "-y", "--", *packages)
    if layout["service"].exists():
        run("systemctl", "--user", "disable", "matoloop-agent.service")
    layout["service"].unlink(missing_ok=True)
    run("systemctl", "--user", "daemon-reload", check=False)
    layout["wrapper"].unlink(missing_ok=True)
    if purge:
        shutil.rmtree(layout["root"], ignore_errors=True)
    else:
        if layout["venv"].is_symlink() or layout["venv"].is_file():
            layout["venv"].unlink(missing_ok=True)
        else:
            shutil.rmtree(layout["venv"], ignore_errors=True)
        for name in ("source", "venvs", "tools", "cache", "bin"):
            shutil.rmtree(layout["root"] / name, ignore_errors=True)
        if layout["data"].is_dir():
            for child in layout["data"].iterdir():
                if child.name == "agent-state":
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        layout["installer"].unlink(missing_ok=True)
        layout["state"].unlink(missing_ok=True)
    for directory in (layout["wrapper"].parent, layout["service"].parent, layout["root"]):
        try:
            directory.rmdir()
        except OSError:
            pass
    print("MatoLoop Agent removed." + ("" if purge else f" Data preserved at {layout['data']}"))


def main() -> None:
    parser = argparse.ArgumentParser(description="MatoLoop Agent lifecycle manager")
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser("install")
    install_parser.add_argument("--source")
    install_parser.add_argument("--managed", action="store_true")
    install_parser.add_argument("--home")
    install_parser.add_argument("--instance-url")
    subparsers.add_parser("update")
    subparsers.add_parser("status")
    uninstall_parser = subparsers.add_parser("uninstall")
    uninstall_parser.add_argument("--purge", action="store_true")
    uninstall_parser.add_argument("--yes", action="store_true")
    arguments = parser.parse_args()
    try:
        if arguments.command == "install":
            install(arguments)
        elif arguments.command == "update":
            update()
        elif arguments.command == "status":
            status()
        else:
            uninstall(arguments.purge, arguments.yes)
    except (OSError, subprocess.CalledProcessError):
        fail("the installation command failed")


if __name__ == "__main__":
    main()
