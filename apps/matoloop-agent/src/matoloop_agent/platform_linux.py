from __future__ import annotations

import asyncio
import fcntl
import os
import pwd
import shlex
import shutil
import signal
import socket
import sys
import tempfile
import threading
from collections.abc import Awaitable, Callable, Coroutine, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

from .browser import BrowserManager
from .browser_contracts import BROWSER_ARGUMENT_MODELS
from .cache import SessionCache
from .config import AgentConfig
from .credentials_linux import LinuxCredentialStore
from .errors import AgentError
from .filesystem import Filesystem
from .openscad import OpenScadRunner
from .platform import FilesystemAdapter, RuntimeServices
from .process import ProcessRunner
from .protocol import ARGUMENT_MODELS
from .search import FilesystemSearch
from .terminal import TerminalManager
from .transfer import TransferClient


class LinuxPlatform:
    @property
    def platform_id(self) -> str:
        return sys.platform

    def computer_name(self) -> str:
        return socket.gethostname()

    def default_config_path(self, environ: Mapping[str, str]) -> Path:
        configured = environ.get("MATOLOOP_AGENT_CONFIG")
        if configured:
            return Path(configured)
        data_home = Path(environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
        return data_home / "matoloop-agent" / "config.json"

    def credential_store(self, data_dir: Path) -> LinuxCredentialStore:
        return LinuxCredentialStore(data_dir)

    def clear_cache(self, config: AgentConfig) -> int:
        return SessionCache(config.data_dir, config.max_session_cache_bytes).clear()

    def run_auxiliary(self, command: str, arguments: list[str]) -> bool:
        if command == "browser-install":
            from .browser import finish_setup

            raise SystemExit(asyncio.run(finish_setup()))
        if command == "browser-install-worker":
            from playwright.__main__ import main as install_playwright

            if os.getuid() == 0:
                raise SystemExit(1)
            sys.argv = ["playwright", "install", "--no-shell", "chromium"]
            install_playwright()
            return True
        if command == "browser-worker":
            from .browser_worker import serve

            asyncio.run(serve(Path(arguments[0])))
            return True
        return False

    def build_runtime(
        self,
        config: AgentConfig,
        access_key: str,
        *,
        transfer_client: TransferClient | None = None,
    ) -> RuntimeServices:
        filesystem = Filesystem(config)
        cache = SessionCache(config.data_dir, config.max_session_cache_bytes)
        process = ProcessRunner(config, filesystem)
        terminal = TerminalManager(config, process)
        search = FilesystemSearch(filesystem)
        transfer = transfer_client or TransferClient(
            max_bytes=config.max_transfer_bytes,
            backend_url=config.backend_url,
            access_key=access_key,
        )
        openscad = OpenScadRunner(config, filesystem, cache, transfer)
        operations = set(ARGUMENT_MODELS) | set(BROWSER_ARGUMENT_MODELS)
        if shutil.which("git") is None:
            operations.discard("git.changes")
        managed_container = os.environ.get("MATOLOOP_AGENT_INSTALL_ROOT")
        if not (
            os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1"
            and managed_container
            and (Path(managed_container) / "launcher.pid").is_file()
        ):
            operations.discard("maintenance.update")
        root = config.data_dir.parent
        if (
            os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1"
            or not (root / ".managed-by-matoloop-agent").is_file()
            or not (root / "installer.py").is_file()
            or not (root / "bin/python").exists()
            or not shutil.which("sudo")
            or not shutil.which("systemd-run")
        ):
            operations.discard("maintenance.start")
        if not {"maintenance.start", "maintenance.update"}.intersection(operations):
            operations.discard("maintenance.update.interrupt")
        if os.environ.get("MATOLOOP_AGENT_CONTAINER") != "1":
            operations -= {
                "integration.browser.view",
                "integration.browser.resize",
                "integration.browser.navigation",
                "integration.browser.clipboard",
            }
        if process.shell is None:
            operations.remove("shell.run")
        if shutil.which("sudo") is None or os.geteuid() == 0:
            operations.discard("administrator.run")
        if not terminal.available:
            operations -= {
                "terminal.open",
                "terminal.read",
                "terminal.write",
                "terminal.resize",
                "terminal.close",
                "terminal.run",
            }
        if search.executable is None:
            operations -= {"filesystem.glob", "filesystem.grep"}
        if not openscad.render_available:
            operations -= {"integration.openscad.render", "integration.openscad.render6"}
        return RuntimeServices(
            platform=self,
            filesystem=filesystem,
            cache=cache,
            process=process,
            terminal=terminal,
            search=search,
            browser=BrowserManager(config, terminal, filesystem),
            openscad=openscad,
            transfer=transfer,
            operations=tuple(sorted(operations)),
        )

    def maintenance_running(self, config: AgentConfig) -> bool:
        if os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1":
            root = os.environ.get("MATOLOOP_AGENT_INSTALL_ROOT")
            return bool(root and (Path(root) / "update-request.json").exists())
        root = config.data_dir.parent
        if not (root / ".managed-by-matoloop-agent").is_file():
            return False
        try:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                return False
            finally:
                os.close(descriptor)
        except OSError:
            return True

    async def start_maintenance(
        self,
        config: AgentConfig,
        action: str,
        job_id: str,
        password: str | None,
        revision: str | None = None,
        *,
        before_start: Callable[[], Awaitable[None]] | None = None,
    ) -> dict:
        if os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1":
            root = os.environ.get("MATOLOOP_AGENT_INSTALL_ROOT")
            if action != "update" or not root:
                raise AgentError("maintenance.unavailable", "Container maintenance is unavailable")
            if before_start is not None:
                await before_start()
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(root) / "installer.py"),
                "container-request",
                job_id,
                "--revision",
                revision,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() != 0:
                raise AgentError("maintenance.failed", "Container update could not be queued")
            return {"started": True}
        root = config.data_dir.parent
        if (
            not (root / ".managed-by-matoloop-agent").is_file()
            or not (root / "installer.py").is_file()
        ):
            raise AgentError("maintenance.unavailable", "Native maintenance is unavailable")
        sudo = shutil.which("sudo")
        systemd_run = shutil.which("systemd-run")
        if not sudo or not systemd_run:
            raise AgentError("maintenance.unavailable", "System maintenance is unavailable")

        async def authorized(*command: str) -> int:
            argv = [sudo, "-S", "-p", ""] if password else [sudo, "-n"]
            try:
                child = await asyncio.create_subprocess_exec(
                    *argv,
                    *command,
                    stdin=asyncio.subprocess.PIPE if password else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                try:
                    await asyncio.wait_for(
                        child.communicate((password + "\n").encode() if password else None), 15
                    )
                except (TimeoutError, asyncio.CancelledError):
                    child.kill()
                    await child.wait()
                    raise
                return child.returncode
            except (OSError, TimeoutError) as exc:
                raise AgentError("maintenance.failed", "Maintenance could not be started") from exc

        # Invalidate cached sudo timestamps: the independent unit cannot inherit them.
        if await authorized("-k", "-v") != 0:
            raise AgentError(
                "process.sudoAuthFailed" if password else "process.sudoAuthRequired",
                "Sudo authorization failed",
            )

        if before_start is not None:
            await before_start()

        with tempfile.TemporaryDirectory(prefix="matoloop-maintenance-") as directory:
            socket_path = str(Path(directory) / "handoff.sock")
            received = asyncio.get_running_loop().create_future()

            async def handoff(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                try:
                    writer.write((password or "").encode())
                    await writer.drain()
                    writer.write_eof()
                    await writer.drain()
                    if not received.done():
                        received.set_result(True)
                finally:
                    writer.close()
                    await writer.wait_closed()

            server = await asyncio.start_unix_server(handoff, path=socket_path)
            try:
                result = await authorized(
                    systemd_run,
                    "--system",
                    "--collect",
                    f"--unit=matoloop-agent-maintenance-{job_id}",
                    f"--property=User={pwd.getpwuid(os.getuid()).pw_name}",
                    "--property=RuntimeMaxSec=3600",
                    str(root / "bin/python"),
                    str(root / "installer.py"),
                    "maintenance-run",
                    action,
                    job_id,
                    socket_path,
                    str(root),
                    *(["--revision", revision] if revision else []),
                )
                if result != 0:
                    raise AgentError("maintenance.failed", "Maintenance could not be started")
                await asyncio.wait_for(received, 15)
            except TimeoutError as exc:
                raise AgentError("maintenance.failed", "Maintenance handoff failed") from exc
            finally:
                server.close()
                await server.wait_closed()
        return {"started": True}

    def system_info(
        self, current_directory: str | None, filesystem: FilesystemAdapter
    ) -> dict[str, Any]:
        from matoloop_version import get_version

        result: dict[str, Any] = {
            "agentVersion": get_version(),
            "platform": self.platform_id[:80],
            "operatingSystem": None,
            "kernel": None,
            "architecture": None,
            "logicalCpuCount": None,
        }
        with suppress(OSError, ValueError):
            uname = os.uname()
            result.update(
                operatingSystem=uname.sysname,
                kernel=uname.release,
                architecture=uname.machine,
            )
        with suppress(OSError, ValueError):
            count = os.cpu_count()
            if type(count) is int and 1 <= count <= 2147483647:
                result["logicalCpuCount"] = count
        for path in ("/etc/os-release", "/usr/lib/os-release"):
            try:
                with open(path, encoding="utf-8") as handle:
                    for line in handle:
                        name, _, value = line.partition("=")
                        if name.strip() == "PRETTY_NAME":
                            parts = shlex.split(value, comments=True)
                            if len(parts) == 1:
                                result["distribution"] = parts[0]
                            break
                break
            except (OSError, ValueError):
                continue
        with suppress(OSError, ValueError):
            models = {}
            with open("/proc/cpuinfo", encoding="utf-8") as handle:
                for line in handle:
                    name, _, value = line.partition(":")
                    name, value = name.strip(), value.strip()
                    if name in {"model name", "Hardware", "Processor"} and value:
                        if not value.isdecimal():
                            models.setdefault(name, value)
            result["cpuModel"] = next(
                (
                    models[name]
                    for name in ("model name", "Hardware", "Processor")
                    if name in models
                ),
                None,
            )
        values = {}
        try:
            with open("/proc/meminfo", encoding="ascii") as handle:
                for line in handle:
                    name, _, value = line.partition(":")
                    if name in {"MemTotal", "MemAvailable"}:
                        with suppress(ValueError):
                            amount, unit = value.split()
                            size = int(amount) * 1024
                            minimum = 1 if name == "MemTotal" else 0
                            if unit == "kB" and minimum <= size <= 9223372036854775807:
                                values[name] = size
        except (OSError, ValueError):
            pass
        result["totalMemoryBytes"] = values.get("MemTotal")
        if values.keys() >= {"MemTotal", "MemAvailable"}:
            result["memory"] = {
                "totalBytes": values["MemTotal"],
                "availableBytes": values["MemAvailable"],
            }
        for name, limit in (
            ("operatingSystem", 80),
            ("distribution", 255),
            ("kernel", 160),
            ("architecture", 80),
            ("cpuModel", 255),
        ):
            value = result.get(name)
            result[name] = value.strip()[:limit] or None if isinstance(value, str) else None
        if current_directory is not None:
            directory = filesystem.resolve_directory(current_directory)
            usage = shutil.disk_usage(directory["path"])
            result["disk"] = {
                "totalBytes": usage.total,
                "availableBytes": usage.free,
                **directory,
            }
        return result

    def run(self, coroutine: Coroutine[Any, Any, None]) -> None:
        stopping = False
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
        deadline = threading.Timer(25, os._exit, args=(1,))
        deadline.daemon = True

        async def run() -> None:
            nonlocal stopping
            task = asyncio.current_task()

            def stop() -> None:
                nonlocal stopping
                if not stopping:
                    stopping = True
                    deadline.start()
                    task.cancel()

            loop = asyncio.get_running_loop()
            for signum in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(signum, stop)
            try:
                await coroutine
            except asyncio.CancelledError:
                if not stopping:
                    raise

        try:
            asyncio.run(run())
        finally:
            deadline.cancel()
            for signum, handler in previous.items():
                signal.signal(signum, handler)
