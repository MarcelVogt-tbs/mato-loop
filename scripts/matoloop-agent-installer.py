#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import fcntl
import hashlib
import json
import os
import platform
import plistlib
import pwd
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

SOURCE_URL = "https://github.com/devmato/mato-loop.git"
SYSTEM_SERVICE_PATH = Path("/etc/systemd/system/matoloop-agent.service")
MACOS_SERVICE_LABEL = "com.matoloop.agent"
MACOS_RIPGREP_VERSION = "15.2.0"
MACOS_RIPGREP_ARCHIVE = f"ripgrep-{MACOS_RIPGREP_VERSION}-aarch64-apple-darwin"
MACOS_RIPGREP_URL = (
    "https://github.com/BurntSushi/ripgrep/releases/download/"
    f"{MACOS_RIPGREP_VERSION}/{MACOS_RIPGREP_ARCHIVE}.tar.gz"
)
MACOS_RIPGREP_SHA256 = "3750b2e93f37e0c692657da574d7019a101c0084da05a790c83fd335bad973e4"
MACOS_RIPGREP_BINARY_SHA256 = "a326a1fb48074202e9ad41e4cd1e389eeea372c8c6f7d7e80da81176d5d9430e"
SYSTEM_PACKAGES = (
    "bash",
    "ripgrep",
)
_maintenance_password: str | None = None
_installation_lock_fd: int | None = None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def paths() -> dict[str, Path]:
    home = Path.home()
    default_root = (
        home / "Library/Application Support/MatoLoop Agent"
        if sys.platform == "darwin"
        else Path(os.environ.get("XDG_DATA_HOME", home / ".local/share")) / "matoloop-agent"
    )
    requested_root = (
        Path(os.environ.get("MATOLOOP_AGENT_INSTALL_ROOT") or default_root).expanduser().absolute()
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
        "service": home / "Library/LaunchAgents/com.matoloop.agent.plist"
        if sys.platform == "darwin"
        else SYSTEM_SERVICE_PATH,
        "legacy_service": Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        / "systemd/user/matoloop-agent.service",
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
    if command[0] == "sudo" and _maintenance_password is not None:
        command = ("sudo", "-S", "-p", "", *command[1:])
        kwargs["input"] = (_maintenance_password + "\n").encode()
    elif command[0] == "sudo" and os.environ.get("MATOLOOP_AGENT_MAINTENANCE") == "1":
        command = ("sudo", "-n", *command[1:])
    return subprocess.run([os.fspath(item) for item in command], **kwargs)


@contextmanager
def installation_lock():
    global _installation_lock_fd
    layout = paths()
    check_safe_root(layout)
    layout["root"].mkdir(parents=True, exist_ok=True, mode=0o700)
    inherited = os.environ.get("MATOLOOP_AGENT_INSTALL_LOCK_FD")
    descriptor = (
        int(inherited)
        if inherited is not None
        else os.open(layout["root"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    )
    try:
        actual, expected = os.fstat(descriptor), layout["root"].stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            fail("the inherited installation lock does not match the managed root")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail("another installation or maintenance command is running")
        _installation_lock_fd = descriptor
        yield
    finally:
        _installation_lock_fd = None
        os.close(descriptor)


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


def validate_revision(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        fail("the installation target must be a full Git commit SHA")
    return value


def target_revision(instance: str) -> str:
    backend_url(instance)
    try:
        with build_opener(_NoRedirect).open(
            instance.rstrip("/") + "/api/execution-agents/installation-target", timeout=30
        ) as response:
            target = json.loads(response.read(4097))
    except (OSError, ValueError):
        fail("the backend installation target is unavailable; use a published backend build")
    if not isinstance(target, dict):
        fail("the backend returned an invalid installation target")
    revision = validate_revision(target.get("revision"))
    if not isinstance(target.get("version"), str) or not re.fullmatch(
        rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{revision[:8]}", target["version"]
    ):
        fail("the backend installation target is not a clean source version")
    return revision


def fetch_source(repository: str, revision: str, destination: Path) -> None:
    validate_revision(revision)
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    run("git", "init", "--quiet", destination)
    run("git", "-C", destination, "remote", "add", "--", "origin", repository)
    run(
        "git",
        "-C",
        destination,
        "fetch",
        "--depth=1",
        "--no-tags",
        "origin",
        revision,
        env=environment,
    )
    actual = run(
        "git", "-C", destination, "rev-parse", "FETCH_HEAD^{commit}", capture_output=True, text=True
    ).stdout.strip()
    if actual != revision:
        fail("the fetched source does not match the backend commit")
    run("git", "-C", destination, "checkout", "--detach", revision)
    if not (destination / "libs/python/version/src/matoloop_version/__init__.py").is_file():
        fail("the target commit does not support versioned Agent installations")


def check_linux() -> None:
    # Keep one native lifecycle, with platform-specific service management only.
    if sys.platform == "darwin":
        if os.getuid() == 0:
            fail("the native Agent must be installed as a regular user")
        if platform.machine() != "arm64":
            fail("native macOS installation requires Apple Silicon (arm64), without Rosetta")
        if shutil.which("launchctl") is None:
            fail("launchctl is required for the macOS user LaunchAgent")
        run("launchctl", "print", f"gui/{os.getuid()}", stdout=subprocess.DEVNULL)
        return
    if not sys.platform.startswith("linux"):
        fail(f"native installation is not supported on {sys.platform} yet")
    if os.getuid() == 0:
        fail("the native Agent must be installed as a regular user")
    for command in ("sudo", "systemctl"):
        if shutil.which(command) is None:
            fail(f"{command} is required for the Linux system service")


def install_macos_ripgrep(layout: dict[str, Path]) -> None:
    executable = layout["root"] / "tools/bin/rg"
    if any(path.is_symlink() for path in (executable, *executable.parents)):
        fail("private ripgrep paths must not contain symlinks")
    if executable.is_file() and hashlib.sha256(executable.read_bytes()).hexdigest() == (
        MACOS_RIPGREP_BINARY_SHA256
    ):
        run(executable, "--version", stdout=subprocess.DEVNULL)
        return
    print(f"Installing private ripgrep {MACOS_RIPGREP_VERSION} for Apple Silicon...", flush=True)
    with tempfile.TemporaryDirectory(prefix=".ripgrep-", dir=layout["root"]) as temporary:
        archive = Path(temporary) / "ripgrep.tar.gz"
        run(
            "curl",
            "--fail",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--silent",
            "--show-error",
            "--max-time",
            "120",
            "--max-filesize",
            str(20 * 1024 * 1024),
            "--output",
            archive,
            MACOS_RIPGREP_URL,
        )
        if hashlib.sha256(archive.read_bytes()).hexdigest() != MACOS_RIPGREP_SHA256:
            fail("the private ripgrep archive checksum does not match")
        with tarfile.open(archive, "r:gz") as bundle:
            # Read only exact regular members; never extract archive paths or links.
            for name in ("rg", "COPYING", "LICENSE-MIT", "UNLICENSE"):
                member = bundle.getmember(f"{MACOS_RIPGREP_ARCHIVE}/{name}")
                if not member.isfile() or member.size > 20 * 1024 * 1024:
                    fail("the private ripgrep archive has an invalid member")
                with bundle.extractfile(member) as stream:
                    content = stream.read()
                if name == "rg":
                    if hashlib.sha256(content).hexdigest() != MACOS_RIPGREP_BINARY_SHA256:
                        fail("the private ripgrep executable checksum does not match")
                    candidate = Path(temporary) / "rg"
                    write_file(candidate, content, 0o700)
                    run(candidate, "--version", stdout=subprocess.DEVNULL)
                else:
                    write_file(layout["root"] / "tools/ripgrep" / name, content, 0o600)
        write_file(executable, candidate.read_bytes(), 0o700)


def install_system_dependencies(layout: dict[str, Path]) -> None:
    if sys.platform == "darwin":
        # Git is supplied by Apple's CLT; never start a GUI installer from maintenance.
        if (
            shutil.which("git") is None
            or run(
                "xcode-select",
                "-p",
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            != 0
        ):
            fail("Git is required; install Apple's Command Line Tools with xcode-select --install")
        run("git", "--version", stdout=subprocess.DEVNULL)
        install_macos_ripgrep(layout)
        return
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


def sync_environment(
    layout: dict[str, Path], source: Path, destination: Path, *, browser: bool = False
) -> None:
    project = source / "apps/matoloop-agent"
    if not (project / "pyproject.toml").is_file():
        fail(f"not a MatoLoop checkout: {source}")
    environment = {
        **os.environ,
        "UV_CACHE_DIR": os.fspath(layout["root"] / "cache/uv"),
        "UV_PYTHON_INSTALL_DIR": os.fspath(layout["root"] / "tools/python"),
        "VIRTUAL_ENV": os.fspath(destination),
        "UV_PROJECT_ENVIRONMENT": os.fspath(destination),
    }
    run(
        layout["uv"],
        "sync",
        "--directory",
        project,
        "--frozen",
        "--no-dev",
        "--no-editable",
        "--refresh-package",
        "matoloop-agent",
        "--refresh-package",
        "matoloop-version",
        "--active",
        *(["--group", "browser"] if browser else []),
        env=environment,
    )
    executable = destination / (
        "Scripts/matoloop-agent.exe" if os.name == "nt" else "bin/matoloop-agent"
    )
    if not executable.is_file():
        fail("uv did not install the MatoLoop Agent executable")


def copy_source(source: Path, destination: Path) -> None:
    included = {
        ".": {"apps", "libs", "scripts", "LICENSE"},
        "apps": {"matoloop-agent"},
        "apps/matoloop-agent": {"src", "pyproject.toml", "uv.lock", "README.md"},
        "libs": {"python"},
        "libs/python": {"version"},
        "libs/python/version": {"src", "pyproject.toml"},
        "scripts": {
            "matoloop-agent-installer.py",
            "install-matoloop-agent.sh",
            "install-matoloop-agent.ps1",
            "matoloop-agent.cmd",
            "matoloop-agent-cli.ps1",
        },
    }
    generated = shutil.ignore_patterns(
        "__pycache__", "*.pyc", "*.egg-info", ".env*", "build-version.txt", "build-revision.txt"
    )

    def ignore(directory, names):
        allowed = included.get(Path(directory).relative_to(source).as_posix())
        return set(names) - allowed if allowed is not None else generated(directory, names)

    shutil.copytree(
        source,
        destination,
        symlinks=True,
        ignore=ignore,
    )


def stamp_source(source: Path, destination: Path) -> None:
    relative = Path("libs/python/version/src/matoloop_version/build-version.txt")
    stamp = source / relative
    if not (source / ".git").exists() and stamp.is_file():
        value = stamp.read_text(encoding="ascii").strip()
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}(?:-dirty)?", value):
            fail("the installed source version is invalid")
        write_file(destination / relative, (value + "\n").encode("ascii"), 0o644)
    else:
        run(
            sys.executable,
            destination / relative.with_name("__init__.py"),
            "--source",
            source,
            "--output",
            destination / relative,
        )


def service_text(layout: dict[str, Path]) -> str:
    if sys.platform == "darwin":
        return plistlib.dumps(
            {
                "Label": MACOS_SERVICE_LABEL,
                "ProgramArguments": [str(layout["wrapper"]), "run"],
                "RunAtLoad": True,
                "KeepAlive": True,
                "ThrottleInterval": 5,
                "Umask": 0o077,
                "EnvironmentVariables": {
                    "PATH": native_path(layout),
                    "MATOLOOP_AGENT_INSTALL_ROOT": str(layout["root"]),
                    "MATOLOOP_AGENT_CONFIG": str(layout["data"] / "config.json"),
                },
                "StandardOutPath": str(layout["data"] / "agent.log"),
                "StandardErrorPath": str(layout["data"] / "agent-error.log"),
            },
            sort_keys=True,
        ).decode()
    executable = (
        os.fspath(layout["wrapper"]).replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    )
    return f"""[Unit]
Description=MatoLoop Computer Agent
After=network-online.target
Wants=network-online.target

[Service]
User={pwd.getpwuid(os.getuid()).pw_name}
ExecStart="{executable}" run
Restart=on-failure
RestartSec=5
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def install_service(layout: dict[str, Path], content: bytes) -> None:
    if sys.platform == "darwin":
        write_file(layout["service"], content, 0o600)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".matoloop-agent.", dir=layout["root"])
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        run("sudo", "install", "-m", "0644", temporary, layout["service"])
    finally:
        Path(temporary).unlink(missing_ok=True)


def remove_service(layout: dict[str, Path]) -> None:
    if sys.platform == "darwin":
        layout["service"].unlink(missing_ok=True)
        return
    run("sudo", "rm", "-f", layout["service"])


def native_path(layout: dict[str, Path]) -> str:
    return ":".join(
        (
            str(layout["root"] / "tools/bin"),
            str(Path.home() / ".local/bin"),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/usr/sbin",
            "/sbin",
        )
    )


def service_target() -> str:
    return f"gui/{os.getuid()}/{MACOS_SERVICE_LABEL}"


def service_loaded() -> bool:
    return (
        run(
            "launchctl",
            "print",
            service_target(),
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def macos_service_pid() -> int | None:
    result = run(
        "launchctl",
        "kickstart",
        "-p",
        service_target(),
        check=False,
        capture_output=True,
        text=True,
    )
    # -p documents a PID result, unlike the explicitly unstable print output.
    if result.returncode == 0 and result.stdout.strip().isdecimal():
        pid = int(result.stdout.strip())
        if pid > 1:
            return pid
    return None


def service_active(*, check: bool = False, expected_pid: int | None = None) -> bool:
    if sys.platform == "darwin":
        pid = macos_service_pid()
        active = pid is not None and (expected_pid is None or pid == expected_pid)
        if active:
            try:
                os.kill(pid, 0)
            except OSError:
                active = False
        if check and not active:
            fail("the macOS LaunchAgent failed its startup check")
        return active
    return (
        run("systemctl", "is-active", "--quiet", "matoloop-agent.service", check=check).returncode
        == 0
    )


def service_enabled() -> bool:
    if sys.platform == "darwin":
        result = run(
            "launchctl", "print-disabled", f"gui/{os.getuid()}", capture_output=True, text=True
        )
        return re.search(rf'"{re.escape(MACOS_SERVICE_LABEL)}"\s*=>\s*true', result.stdout) is None
    return (
        run("systemctl", "is-enabled", "--quiet", "matoloop-agent.service", check=False).returncode
        == 0
    )


def service_stop(*, check: bool = True) -> None:
    if sys.platform == "darwin":
        if service_loaded():
            run("launchctl", "bootout", service_target(), check=check)
        return
    run("sudo", "systemctl", "stop", "matoloop-agent.service", check=check)


def service_reload() -> None:
    if sys.platform != "darwin":
        run("sudo", "systemctl", "daemon-reload")


def service_enable(enabled: bool, *, check: bool = True) -> None:
    if sys.platform == "darwin":
        run("launchctl", "enable" if enabled else "disable", service_target(), check=check)
    else:
        run(
            "sudo",
            "systemctl",
            "enable" if enabled else "disable",
            "matoloop-agent.service",
            check=check,
        )


def service_start(layout: dict[str, Path], *, check: bool = True) -> None:
    if sys.platform == "darwin":
        run("launchctl", "bootstrap", f"gui/{os.getuid()}", layout["service"], check=check)
    else:
        run("sudo", "systemctl", "start", "matoloop-agent.service", check=check)


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


def install(arguments: argparse.Namespace) -> None:
    check_linux()
    layout = paths()
    check_safe_root(layout)
    if sys.platform == "darwin":
        os.environ["PATH"] = native_path(layout) + ":" + os.environ.get("PATH", "")
        if any(path.is_symlink() for path in layout["service"].parents):
            fail("LaunchAgent directories must not contain symlinks")
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
        or layout["legacy_service"].exists()
        or layout["legacy_service"].is_symlink()
    ):
        fail("an unmanaged MatoLoop Agent wrapper or service already exists")
    if layout["marker"].is_symlink():
        fail("the installation ownership marker is invalid")
    if layout["service"].is_symlink():
        fail(f"the system service path is a symlink: {layout['service']}")
    if layout["legacy_service"].exists() or layout["legacy_service"].is_symlink():
        fail("uninstall the legacy MatoLoop Agent user service before installing")
    for managed_file in (
        layout["installer"],
        layout["root"] / "bin/matoloop-agent",
        layout["python"],
    ):
        if managed_file.is_symlink() and managed_file != layout["python"]:
            fail(f"managed installation file is a symlink: {managed_file}")

    state = read_state(layout)
    if layout["service"].exists() and not state:
        fail("an unmanaged MatoLoop Agent system service already exists")
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
    config_path = layout["data"] / "config.json"
    if config_path.is_symlink():
        fail(f"invalid Agent configuration: {config_path}")
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        config = {}
    except (OSError, ValueError):
        fail(f"invalid Agent configuration: {config_path}")
    if not isinstance(config, dict):
        fail(f"invalid Agent configuration: {config_path}")
    home = Path(
        arguments.home
        or state.get("homeDirectory")
        or (config.get("homeDirectory") if sys.platform == "darwin" else None)
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
    copy_source(source, staged_source)
    stamp_source(source, staged_source)
    active_source = staged_source
    layout["venvs"].mkdir(exist_ok=True)
    new_venv = layout["venvs"] / secrets.token_hex(8)
    cleanup_paths.append(new_venv)
    browser_installation = layout["root"] / "tools/browser"
    browser = browser_installation.exists()
    sync_environment(layout, active_source, new_venv, browser=browser)
    run(new_venv / "bin/matoloop-agent", "--help", stdout=subprocess.DEVNULL)
    if browser:
        with tempfile.TemporaryDirectory(prefix="matoloop-browser-check-") as temporary:
            probe = Path(temporary).resolve()
            probe.chmod(0o755)
            run(
                new_venv / "bin/python",
                "-m",
                "matoloop_agent",
                "browser-install",
                env={
                    **os.environ,
                    "MATOLOOP_BROWSER_DATA_DIR": str(layout["data"]),
                    "MATOLOOP_BROWSER_ROOT": str(probe),
                    "MATOLOOP_BROWSER_INSTALLATION": str(browser_installation),
                    # Keep the previous environment's Chromium available for rollback.
                    "PLAYWRIGHT_SKIP_BROWSER_GC": "1",
                },
            )
    if not managed:
        shutil.rmtree(active_source)

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

    active = service_loaded() if sys.platform == "darwin" else service_active()
    loaded = service_loaded() if sys.platform == "darwin" else active
    enabled = service_enabled()
    old_source = layout["root"] / ".source-old"
    old_venv = layout["venv"].resolve() if layout["venv"].is_symlink() else None
    shutil.rmtree(old_source, ignore_errors=True)
    managed_files = (
        layout["installer"],
        layout["root"] / "bin/matoloop-agent",
        config_path,
        layout["state"],
    )
    snapshots = {
        path: (path.read_bytes(), path.stat().st_mode & 0o777) if path.is_file() else None
        for path in managed_files
    }
    service_snapshot = layout["service"].read_bytes() if layout["service"].is_file() else None
    new_state = {
        "source": os.fspath(layout["source"] if managed else source),
        "sourceType": "managed" if managed else "checkout",
        "sourceUrl": state.get("sourceUrl")
        or os.environ.get("MATOLOOP_AGENT_SOURCE_URL", SOURCE_URL),
        "homeDirectory": os.fspath(home),
        "instanceUrl": instance.rstrip("/"),
        "wrapperPath": os.fspath(layout["wrapper"]),
    }
    if sys.platform != "darwin":
        run("sudo", "-v")
    source_replaced = False
    try:
        if loaded:
            service_stop()
        if managed and layout["source"].exists():
            layout["source"].replace(old_source)
        if managed:
            active_source.replace(layout["source"])
            source_replaced = True
            source = layout["source"]
        temporary_link = layout["root"] / ".venv-link"
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(new_venv)
        os.replace(temporary_link, layout["venv"])
        write_json(config_path, config)
        write_manager(layout, source)
        write_json(layout["state"], new_state)
        install_service(layout, service_text(layout).encode())
        service_reload()
        if sys.platform == "darwin":
            service_enable(True)
            service_start(layout)
        else:
            run("sudo", "systemctl", "enable", "--now", "matoloop-agent.service")
        startup_pid = macos_service_pid() if sys.platform == "darwin" else None
        if sys.platform == "darwin" and startup_pid is None:
            fail("the macOS LaunchAgent did not start")
        for _ in range(2):
            time.sleep(1)
            service_active(check=True, expected_pid=startup_pid)
    except BaseException:
        service_stop(check=False)
        if old_venv is None:
            layout["venv"].unlink(missing_ok=True)
        else:
            temporary_link = layout["root"] / ".venv-link"
            temporary_link.unlink(missing_ok=True)
            temporary_link.symlink_to(old_venv)
            os.replace(temporary_link, layout["venv"])
        shutil.rmtree(new_venv, ignore_errors=True)
        if managed and state:
            if source_replaced:
                shutil.rmtree(layout["source"], ignore_errors=True)
            if old_source.exists():
                old_source.replace(layout["source"])
                source = layout["source"]
        for path, snapshot in snapshots.items():
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                write_file(path, *snapshot)
        if service_snapshot is None:
            remove_service(layout)
        else:
            install_service(layout, service_snapshot)
        if not state:
            write_json(layout["state"], new_state)
        service_enable(enabled, check=False)
        if layout["venv"].exists():
            service_reload()
            if loaded:
                service_start(layout, check=False)
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


def run_maintenance_installer(*command: str | Path, **kwargs) -> None:
    # subprocess.run kills its child when a signal handler raises. Defer the
    # worker interruption instead, so the target installer can finish rollback.
    child = None
    interrupted = False

    def interrupt(_signal, _frame):
        nonlocal interrupted
        interrupted = True
        if child is not None:
            try:
                child.terminate()
            except ProcessLookupError:
                pass

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        with subprocess.Popen([os.fspath(item) for item in command], **kwargs) as child:
            if interrupted:
                child.terminate()
            returncode = child.wait()
        if interrupted:
            raise SystemExit("Maintenance interrupted after target installer rollback")
        if returncode:
            raise subprocess.CalledProcessError(returncode, command)
    finally:
        signal.signal(signal.SIGTERM, previous)


def update(*, in_process: bool = False, revision: str | None = None) -> None:
    layout = paths()
    state = read_state(layout)
    if not state:
        fail("MatoLoop Agent is not installed")
    instance = (
        paired_instance(layout["data"] / "agent-state/credential.json") or state["instanceUrl"]
    )
    revision = target_revision(instance) if revision is None else validate_revision(revision)
    # Stage managed sources even for local installs; never change the user's checkout.
    with tempfile.TemporaryDirectory(prefix=".update-", dir=layout["root"]) as temporary:
        source = Path(temporary) / "source"
        fetch_source(state.get("sourceUrl", SOURCE_URL), revision, source)
        if in_process:
            install(
                argparse.Namespace(
                    source=os.fspath(source),
                    managed=True,
                    home=state["homeDirectory"],
                    instance_url=instance,
                )
            )
        else:
            execute = (
                run_maintenance_installer
                if sys.platform == "darwin" and os.environ.get("MATOLOOP_AGENT_MAINTENANCE") == "1"
                else run
            )
            execute(
                sys.executable,
                source / "scripts/matoloop-agent-installer.py",
                "install",
                "--source",
                source,
                "--managed",
                "--home",
                state["homeDirectory"],
                "--instance-url",
                instance,
                **(
                    {
                        "env": {
                            **os.environ,
                            "MATOLOOP_AGENT_INSTALL_LOCK_FD": str(_installation_lock_fd),
                        },
                        "pass_fds": (_installation_lock_fd,),
                    }
                    if _installation_lock_fd is not None
                    else {}
                ),
            )


def maintenance_start(action: str, job_id: str, root: str, revision: str | None = None) -> None:
    if sys.platform != "darwin":
        fail("maintenance launch requires macOS")
    if str(UUID(job_id)) != job_id or action not in {"update", "uninstall"}:
        fail("invalid maintenance request")
    if action == "update":
        validate_revision(revision)
    elif revision is not None:
        fail("uninstall must not specify a revision")
    check_linux()
    os.environ["MATOLOOP_AGENT_INSTALL_ROOT"] = root
    layout = paths()
    check_safe_root(layout)
    if layout["data"].is_symlink():
        fail("invalid maintenance data directory")
    for name in ("maintenance.log", "maintenance-error.log"):
        descriptor = os.open(
            layout["data"] / name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                fail("invalid maintenance log ownership")
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
    label = f"{MACOS_SERVICE_LABEL}.maintenance.{job_id}"
    # Darwin's sockaddr_un limit requires a short path, not the user's TMPDIR.
    with tempfile.TemporaryDirectory(prefix="ml-maint-", dir="/private/tmp") as directory:
        socket_path = str(Path(directory) / "handoff.sock")
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(socket_path)
            listener.listen(1)
            listener.settimeout(15)
            plist = Path(directory) / "worker.plist"
            write_file(
                plist,
                plistlib.dumps(
                    {
                        "Label": label,
                        "ProgramArguments": [
                            str(layout["python"]),
                            str(layout["installer"]),
                            "maintenance-supervise",
                            action,
                            job_id,
                            socket_path,
                            str(layout["root"]),
                            *(["--revision", revision] if revision else []),
                        ],
                        "RunAtLoad": True,
                        "KeepAlive": False,
                        "LaunchOnlyOnce": True,
                        "ProcessType": "Background",
                        "Umask": 0o077,
                        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
                        "WorkingDirectory": str(layout["root"]),
                        "StandardOutPath": str(layout["data"] / "maintenance.log"),
                        "StandardErrorPath": str(layout["data"] / "maintenance-error.log"),
                    }
                ),
                0o600,
            )
            handed_off = False
            launch_options = {
                "timeout": 15,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            try:
                run("/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", plist, **launch_options)
                channel, _ = listener.accept()
                with channel:
                    channel.shutdown(socket.SHUT_WR)
                    handed_off = True
            finally:
                if not handed_off:
                    try:
                        run(
                            "/bin/launchctl",
                            "bootout",
                            f"gui/{os.getuid()}/{label}",
                            check=False,
                            **launch_options,
                        )
                    except (OSError, subprocess.SubprocessError):
                        pass


def maintenance_supervise(
    action: str, job_id: str, socket_path: str, root: str, revision: str | None = None
) -> None:
    if sys.platform != "darwin":
        fail("maintenance supervision requires macOS")
    job_id = str(UUID(job_id))
    os.environ["MATOLOOP_AGENT_INSTALL_ROOT"] = root
    layout = paths()
    check_safe_root(layout)
    credential = None
    process = None
    completed = False
    received, sent = os.pipe()

    def interrupt(_signal, _frame):
        raise SystemExit("Maintenance supervisor interrupted; requesting rollback")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        # Keep a bounded, owner-checked credential snapshot for forced-stop reporting.
        if any(path.is_symlink() for path in (layout["data"], layout["data"] / "agent-state")):
            fail("invalid maintenance credential directory")
        descriptor = os.open(
            layout["data"] / "agent-state/credential.json", os.O_RDONLY | os.O_NOFOLLOW
        )
        with os.fdopen(descriptor, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_size > 65536
            ):
                fail("invalid maintenance credential ownership")
            credential = json.loads(handle.read(65537))
        if not isinstance(credential, dict) or not all(
            isinstance(credential.get(key), str) and credential[key]
            for key in ("backendUrl", "agentId", "accessKey")
        ):
            fail("invalid maintenance credentials")
        command = [
            sys.executable,
            str(layout["installer"]),
            "maintenance-run",
            action,
            job_id,
            socket_path,
            str(layout["root"]),
            *(["--revision", validate_revision(revision)] if revision else []),
        ]
        try:
            process = subprocess.Popen(
                command,
                start_new_session=True,
                pass_fds=(sent,),
                env={**os.environ, "MATOLOOP_AGENT_MAINTENANCE_RESULT_FD": str(sent)},
            )
        except OSError:
            report_maintenance(credential, job_id, "failed", layout["data"])
            raise
        os.close(sent)
        sent = -1
        try:
            process.wait(timeout=3600)
            completed = True
        except subprocess.TimeoutExpired:
            print("Maintenance deadline exceeded; requesting rollback.", flush=True)
    finally:
        # Repeated cancellation must not interrupt rollback, reporting or child reaping.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        try:
            if process is not None:
                finish_maintenance_worker(
                    process,
                    completed,
                    received,
                    credential,
                    job_id,
                    layout,
                    retry_completion=action == "uninstall",
                )
                if action == "uninstall":
                    cleanup_maintenance_tools(layout)
        finally:
            os.close(received)
            if sent != -1:
                os.close(sent)
            # Restore default handling immediately before the final self-unload effect.
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            run(
                "/bin/launchctl",
                "bootout",
                f"gui/{os.getuid()}/{MACOS_SERVICE_LABEL}.maintenance.{job_id}",
                check=False,
                timeout=10,
            )


def finish_maintenance_worker(
    process, completed, received, credential, job_id, layout, *, retry_completion=False
) -> None:
    if not completed:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            pass
    # Reap leftover commands before checking the outcome, on every exit path.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()
    os.set_blocking(received, False)
    try:
        outcome = os.read(received, 32)
    except BlockingIOError:
        outcome = b""
    pending = layout["data"] / "agent-state" / f"maintenance-{job_id}.json"
    retained = False
    if not pending.is_symlink():
        try:
            record = json.loads(pending.read_text())
            retained = (
                record.get("jobId") == job_id
                and record.get("agentId") == credential["agentId"]
                and record.get("backendUrl") == credential["backendUrl"]
                and record.get("status") in {"succeeded", "failed"}
            )
        except (OSError, ValueError, AttributeError):
            pass
    if retained and retry_completion:
        # Uninstall leaves no running Agent to replay a merely retained outcome.
        report_maintenance(credential, job_id, record["status"], layout["data"])
    elif outcome not in {b"succeeded", b"failed"} and not retained:
        report_maintenance(credential, job_id, "failed", layout["data"])


def maintenance_run(
    action: str, job_id: str, socket_path: str, root: str, revision: str | None = None
) -> None:
    global _maintenance_password
    os.environ["MATOLOOP_AGENT_INSTALL_ROOT"] = root
    layout = paths()
    check_safe_root(layout)
    UUID(job_id)
    if sys.platform == "darwin":
        check_linux()
        os.environ["PATH"] = native_path(layout)
    if not layout["marker"].is_file() or not read_state(layout):
        fail("MatoLoop Agent is not installed")
    # main() holds the installation lock before this handoff. On macOS the empty
    # payload acknowledges readiness; the independent launchd job needs no password.
    with socket.socket(socket.AF_UNIX) as channel:
        channel.settimeout(10)
        channel.connect(socket_path)
        with channel.makefile("rb") as stream:
            password = bytearray(stream.read(4097))
    if len(password) > 4096:
        fail("invalid maintenance authorization")
    _maintenance_password = password.decode("utf-8") or None
    password.clear()
    if sys.platform == "darwin" and _maintenance_password is not None:
        fail("macOS user maintenance must not receive an administrator password")
    os.environ["MATOLOOP_AGENT_MAINTENANCE"] = "1"
    credential = json.loads((layout["data"] / "agent-state/credential.json").read_text())
    supervised = "MATOLOOP_AGENT_MAINTENANCE_RESULT_FD" in os.environ
    result = "succeeded"
    try:
        if action == "update":
            os.environ["GIT_TERMINAL_PROMPT"] = "0"
            update(in_process=sys.platform != "darwin", revision=validate_revision(revision))
        else:
            uninstall(purge=False, yes=True, defer_tools=True)
    except BaseException:
        result = "failed"
        raise
    finally:
        report_maintenance(credential, job_id, result, layout["data"])
        if action == "uninstall" and result == "succeeded" and not supervised:
            cleanup_maintenance_tools(layout)


def cleanup_maintenance_tools(layout: dict[str, Path]) -> None:
    # The worker and its supervisor can both lazily import from private Python.
    # Keep it until reporting finishes, and never remove a still-installed runtime.
    if layout["state"].exists():
        return
    tools = layout["root"] / "tools"
    if tools.is_dir() and not tools.is_symlink():
        shutil.rmtree(tools)
    elif tools.exists() or tools.is_symlink():
        fail(f"invalid managed installation path: {tools}")


def report_maintenance(credential: dict, job_id: str, result: str, data: Path) -> None:
    pending = data / "agent-state" / f"maintenance-{UUID(job_id)}.json"
    # Keep only identity and outcome; replay uses the Agent's current credential.
    try:
        write_json(
            pending,
            {
                "backendUrl": credential["backendUrl"],
                "agentId": credential["agentId"],
                "jobId": job_id,
                "status": result,
            },
        )
        acknowledge_maintenance(result)
    except OSError:
        print("Maintenance outcome could not be retained; attempting direct delivery.", flush=True)
    address = urlsplit(credential["backendUrl"])
    endpoint = urlunsplit(
        (
            "https" if address.scheme == "wss" else "http",
            address.netloc,
            f"{address.path.removesuffix('/connect')}/{credential['agentId']}/maintenance/{job_id}/complete",
            "",
            "",
        )
    )
    reporter = build_opener(_NoRedirect)
    payload = json.dumps({"status": result}).encode()
    for attempt in range(3):
        try:
            with reporter.open(
                Request(
                    endpoint,
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {credential['accessKey']}",
                        "Content-Type": "application/json",
                    },
                ),
                timeout=10,
            ) as response:
                response.read()
            acknowledge_maintenance(result)
            pending.unlink(missing_ok=True)
            break
        except OSError:
            if attempt < 2:
                time.sleep(2)


def acknowledge_maintenance(result: str) -> None:
    # Only the supervisor supplies this private inherited descriptor. A durable
    # record or successful delivery must exist before it suppresses fallback.
    descriptor = os.environ.pop("MATOLOOP_AGENT_MAINTENANCE_RESULT_FD", None)
    if sys.platform == "darwin" and descriptor is not None:
        try:
            os.write(int(descriptor), result.encode("ascii"))
        except (OSError, ValueError):
            pass


def container_root() -> Path:
    root = Path(os.environ["MATOLOOP_AGENT_INSTALL_ROOT"])
    if os.geteuid() != 0 or os.environ.get("MATOLOOP_AGENT_CONTAINER") != "1":
        fail("Container lifecycle requires the managed root container")
    return root


def container_init(repository: str) -> None:
    root = container_root()
    if (root / "install.json").exists():
        fail("Container installation is already initialized")
    write_json(
        root / "install.json",
        {
            "sourceUrl": repository,
            "release": "releases/image",
        },
    )
    write_file(root / "installer.py", Path(__file__).read_bytes(), 0o755)


def container_request(job_id: str | None, revision: str | None) -> None:
    root = container_root()
    if job_id:
        UUID(job_id)
        validate_revision(revision)
    else:
        data = Path(os.environ.get("MATOLOOP_AGENT_CONFIG", "/data/config.json")).resolve().parent
        instance = paired_instance(data / "agent-state/credential.json")
        if instance is None:
            fail("pair the Agent before requesting an update")
        revision = target_revision(instance)
    os.kill(int((root / "launcher.pid").read_text()), 0)
    # Publish a complete request exclusively, including during update preparation.
    with tempfile.NamedTemporaryFile(mode="w", dir=root, encoding="utf-8") as stream:
        json.dump({"jobId": job_id, "revision": revision}, stream)
        stream.flush()
        os.link(stream.name, root / "update-request.json")


def prepare_container_release(root: Path, state: dict, revision: str) -> Path:
    validate_revision(revision)
    release = Path(tempfile.mkdtemp(prefix="update-", dir=root / "releases"))
    release.chmod(0o755)  # The unprivileged browser worker executes this environment.
    try:
        checkout = release / "checkout"
        fetch_source(state["sourceUrl"], revision, checkout)
        source = release / "source"
        copy_source(checkout, source)
        stamp_source(checkout, source)
        shutil.rmtree(checkout)
        sync_environment(
            {"root": root, "uv": Path(shutil.which("uv") or "uv")},
            source,
            release / "venv",
            browser=True,
        )
        python = release / "venv/bin/python"
        run(
            "sh",
            source / "apps/matoloop-agent/src/matoloop_agent/container-browser-setup.sh",
            python,
        )
        run(release / "venv/bin/matoloop-agent", "--help", stdout=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix="matoloop-browser-check-") as temporary:
            probe = Path(temporary)
            probe.chmod(0o755)
            run(
                python,
                "-m",
                "matoloop_agent",
                "browser-install",
                env={
                    **os.environ,
                    "MATOLOOP_BROWSER_DATA_DIR": str(
                        Path(os.environ.get("MATOLOOP_AGENT_CONFIG", "/data/config.json"))
                        .resolve()
                        .parent
                    ),
                    "MATOLOOP_BROWSER_ROOT": str(probe),
                    "MATOLOOP_BROWSER_INSTALLATION": "/usr/local/lib/matoloop-agent/tools/browser",
                },
            )
        return release
    except BaseException:
        shutil.rmtree(release)
        raise


_container_process: subprocess.Popen | None = None


def container_start(root: Path, state: dict) -> None:
    global _container_process
    executable = root / state["release"] / "venv/bin/matoloop-agent"
    _container_process = subprocess.Popen(
        [executable, "run"],
        start_new_session=True,
        env={
            **os.environ,
            "PATH": f"{executable.parent}:{os.environ['PATH']}",
            "UV_PROJECT_ENVIRONMENT": str(executable.parent.parent),
        },
    )


def container_stop() -> None:
    global _container_process
    process = _container_process
    if process is None:
        return
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    # Reap remaining shell/browser workers in the Agent process group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _container_process = None


def activate_container_release(root: Path, state: dict, release: Path) -> dict:
    candidate = {
        **state,
        "release": str(release.relative_to(root)),
        "previousRelease": state["release"],
    }
    # The journal allows a container restart to recover an interrupted activation.
    write_json(root / "install.json", candidate)
    try:
        container_stop()
        container_start(root, candidate)
        for _ in range(3):
            time.sleep(1)
            if _container_process.poll() is not None:
                raise RuntimeError("The updated Agent failed its startup check")
        write_file(
            root / "installer.py",
            (release / "source/scripts/matoloop-agent-installer.py").read_bytes(),
            0o755,
        )
        candidate.pop("previousRelease")
        write_json(root / "install.json", candidate)
    except BaseException:
        container_stop()
        write_json(root / "install.json", state)
        write_file(
            root / "installer.py",
            (root / state["release"] / "source/scripts/matoloop-agent-installer.py").read_bytes(),
            0o755,
        )
        container_start(root, state)
        shutil.rmtree(release)
        raise
    shutil.rmtree(root / state["release"], ignore_errors=True)
    return candidate


def container_run() -> None:
    root = container_root()
    # One launcher owns the process and update queue for this container.
    with (root / "launcher.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = json.loads((root / "install.json").read_text())
        request = root / "update-request.json"
        interrupted = "previousRelease" in state
        if interrupted:
            state["release"] = state.pop("previousRelease")
            write_json(root / "install.json", state)
            write_file(
                root / "installer.py",
                (
                    root / state["release"] / "source/scripts/matoloop-agent-installer.py"
                ).read_bytes(),
                0o755,
            )
        interrupted = interrupted and request.exists()

        def shutdown(_signal, _frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        (root / "launcher.pid").write_text(str(os.getpid()))
        try:
            container_start(root, state)
            while _container_process.poll() is None:
                if request.exists():
                    try:
                        job = json.loads(request.read_text())
                        job_id = str(UUID(job["jobId"])) if job["jobId"] else None
                        revision = validate_revision(job["revision"])
                    except (ValueError, KeyError, TypeError, SystemExit):
                        request.unlink()
                        print("Discarded an invalid container update request.", flush=True)
                        continue
                    data = (
                        Path(os.environ.get("MATOLOOP_AGENT_CONFIG", "/data/config.json"))
                        .resolve()
                        .parent
                    )
                    credential = (
                        json.loads((data / "agent-state/credential.json").read_text())
                        if job_id
                        else None
                    )
                    result = "failed"
                    try:
                        if not interrupted:
                            release = prepare_container_release(root, state, revision)
                            state = activate_container_release(root, state, release)
                            result = "succeeded"
                    except (
                        OSError,
                        ValueError,
                        RuntimeError,
                        subprocess.SubprocessError,
                        SystemExit,
                    ):
                        print(
                            "Container Agent update failed; previous installation retained.",
                            flush=True,
                        )
                    finally:
                        request.unlink(missing_ok=True)
                        interrupted = False
                        if job_id:
                            report_maintenance(credential, job_id, result, data)
                        else:
                            print(f"Container Agent update {result}.", flush=True)
                time.sleep(0.5)
            raise SystemExit(_container_process.returncode)
        except KeyboardInterrupt:
            pass
        finally:
            container_stop()
            (root / "launcher.pid").unlink(missing_ok=True)


def status() -> None:
    layout = paths()
    state = read_state(layout)
    if not state:
        fail("MatoLoop Agent is not installed")
    print(f"Source: {state['source']} ({state['sourceType']})")
    print(f"Data:   {layout['data']}")
    print(f"Home:   {state['homeDirectory']}")
    if sys.platform == "darwin":
        print(f"Service: {layout['service']} (starts at user login)")
        run("launchctl", "print", service_target(), check=False)
    else:
        run("systemctl", "status", "--no-pager", "matoloop-agent.service", check=False)


def uninstall(purge: bool, yes: bool, *, defer_tools: bool = False) -> None:
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
            fail("the MatoLoop Agent system service was replaced; no files were removed")
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
    service_stop(check=False)
    if service_loaded() if sys.platform == "darwin" else service_active():
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
        service_enable(False)
    remove_service(layout)
    service_reload()
    layout["wrapper"].unlink(missing_ok=True)
    if purge:
        shutil.rmtree(layout["root"])
    else:
        if layout["venv"].is_symlink() or layout["venv"].is_file():
            layout["venv"].unlink(missing_ok=True)
        else:
            shutil.rmtree(layout["venv"])
        for name in ("source", "venvs", "tools", "cache", "bin"):
            target = layout["root"] / name
            if target.is_dir() and not target.is_symlink():
                if name != "tools" or not defer_tools:
                    shutil.rmtree(target)
            elif target.exists() or target.is_symlink():
                fail(f"invalid managed installation path: {target}")
        if layout["data"].is_dir():
            for child in layout["data"].iterdir():
                if child.name == "agent-state" or (
                    sys.platform == "darwin" and child.name == "config.json"
                ):
                    continue
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        layout["installer"].unlink(missing_ok=True)
        layout["state"].unlink(missing_ok=True)
    for directory in (layout["wrapper"].parent, layout["root"]):
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
    image_parser = subparsers.add_parser("container-init")
    image_parser.add_argument("repository")
    subparsers.add_parser("container-run")
    request_parser = subparsers.add_parser("container-request")
    request_parser.add_argument("job_id", nargs="?")
    request_parser.add_argument("--revision")
    uninstall_parser = subparsers.add_parser("uninstall")
    uninstall_parser.add_argument("--purge", action="store_true")
    uninstall_parser.add_argument("--yes", action="store_true")
    for command in ("maintenance-start", "maintenance-run", "maintenance-supervise"):
        maintenance_parser = subparsers.add_parser(command, help=argparse.SUPPRESS)
        maintenance_parser.add_argument("action", choices=("update", "uninstall"))
        maintenance_parser.add_argument("job_id")
        if command != "maintenance-start":
            maintenance_parser.add_argument("socket_path")
        maintenance_parser.add_argument("root")
        maintenance_parser.add_argument("--revision")
    arguments = parser.parse_args()
    previous_sigterm = None
    try:
        if arguments.command in {"maintenance-start", "maintenance-run", "maintenance-supervise"}:
            os.environ["MATOLOOP_AGENT_INSTALL_ROOT"] = arguments.root
        if sys.platform == "darwin" and (
            arguments.command in {"maintenance-start", "maintenance-run"}
            or (
                arguments.command == "install"
                and os.environ.get("MATOLOOP_AGENT_MAINTENANCE") == "1"
            )
        ):

            def interrupt(_signal, _frame):
                # Allow rollback and durable reporting to complete during the grace period.
                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                raise SystemExit("Maintenance interrupted; restoring the previous installation")

            previous_sigterm = signal.signal(signal.SIGTERM, interrupt)
        mutating = arguments.command in {"install", "update", "uninstall", "maintenance-run"}
        if mutating and sys.platform == "darwin":
            check_linux()
        with installation_lock() if mutating else nullcontext():
            if arguments.command == "container-init":
                container_init(arguments.repository)
            elif arguments.command == "container-run":
                container_run()
            elif arguments.command == "container-request":
                container_request(arguments.job_id, arguments.revision)
            elif arguments.command == "install":
                install(arguments)
            elif arguments.command == "update":
                update()
            elif arguments.command == "status":
                status()
            elif arguments.command == "uninstall":
                uninstall(arguments.purge, arguments.yes)
            elif arguments.command == "maintenance-start":
                maintenance_start(
                    arguments.action, arguments.job_id, arguments.root, arguments.revision
                )
            elif arguments.command == "maintenance-supervise":
                maintenance_supervise(
                    arguments.action,
                    arguments.job_id,
                    arguments.socket_path,
                    arguments.root,
                    arguments.revision,
                )
            else:
                maintenance_run(
                    arguments.action,
                    arguments.job_id,
                    arguments.socket_path,
                    arguments.root,
                    arguments.revision,
                )
    except (OSError, ValueError, subprocess.SubprocessError):
        fail("the installation command failed")
    finally:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    main()
