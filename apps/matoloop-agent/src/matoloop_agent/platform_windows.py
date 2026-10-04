"""Native Windows composition of the standalone Agent's core capabilities."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import socket
from collections.abc import Coroutine, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any
from uuid import UUID

from .cache_windows import WindowsCache
from .config import AgentConfig
from .credentials_windows import WindowsCredentialStore
from .errors import AgentError
from .filesystem_windows import WindowsFilesystem
from .platform import FilesystemAdapter, RuntimeServices
from .process_windows import WindowsProcess
from .search_windows import WindowsSearch
from .transfer import TransferClient


class _Unavailable:
    async def close(self) -> None:
        return None

    async def close_all(self) -> None:
        return None


class WindowsPlatform:
    @property
    def platform_id(self) -> str:
        return "win32"

    def computer_name(self) -> str:
        return socket.gethostname()

    def default_config_path(self, environ: Mapping[str, str]) -> Path:
        return Path(
            environ.get("MATOLOOP_AGENT_CONFIG")
            or Path(environ["LOCALAPPDATA"]) / "matoloop-agent" / "data" / "config.json"
        )

    def credential_store(self, data_dir: Path) -> WindowsCredentialStore:
        return WindowsCredentialStore(data_dir)

    def clear_cache(self, config: AgentConfig) -> int:
        return WindowsCache(config.data_dir, config.max_session_cache_bytes).clear()

    @staticmethod
    def _maintenance_available(config: AgentConfig) -> bool:
        root = config.data_dir.parent
        if (
            not (root / ".managed-by-matoloop-agent").is_file()
            or not (root / "installer.ps1").is_file()
        ):
            return False
        try:
            return (
                json.loads((root / "install.json").read_text(encoding="utf-8-sig")).get(
                    "maintenanceReady"
                )
                is True
            )
        except (OSError, ValueError, AttributeError):
            return False

    async def start_maintenance(
        self, config: AgentConfig, action: str, job_id: str, password: str | None
    ) -> dict:
        if not self._maintenance_available(config):
            raise AgentError("maintenance.unavailable", "Native maintenance is unavailable")
        if (
            action not in {"update", "uninstall"}
            or str(UUID(job_id)) != job_id
            or password is not None
        ):
            raise AgentError("request.invalid", "Invalid Windows maintenance request")
        request = config.data_dir / "maintenance-request.json"
        try:
            with request.open("x", encoding="utf-8") as file:
                json.dump({"action": action, "jobId": job_id}, file)
        except OSError as exc:
            raise AgentError(
                "maintenance.failed", "Maintenance request could not be prepared"
            ) from exc
        try:
            shell = os.path.join(
                os.environ["SystemRoot"], "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
            )
            process = await asyncio.create_subprocess_exec(
                shell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; "
                "Start-ScheduledTask -TaskName 'MatoLoop Agent Maintenance'",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), 15)
            if process.returncode:
                raise AgentError("maintenance.failed", "Maintenance task could not be started")
        except BaseException:
            request.unlink(missing_ok=True)
            raise
        return {"started": True}

    def run_auxiliary(self, command: str, arguments: list[str]) -> bool:
        return False

    def build_runtime(
        self, config: AgentConfig, access_key: str, *, transfer_client=None
    ) -> RuntimeServices:
        filesystem = WindowsFilesystem(config)
        cache = WindowsCache(config.data_dir, config.max_session_cache_bytes)
        process = WindowsProcess(config, filesystem)
        unavailable = _Unavailable()
        transfer = transfer_client or TransferClient(
            max_bytes=config.max_transfer_bytes,
            backend_url=config.backend_url,
            access_key=access_key,
        )
        operations = (
            "system.info",
            "filesystem.resolve",
            "filesystem.stat",
            "filesystem.list",
            "filesystem.read",
            "filesystem.write",
            "filesystem.upsert",
            "filesystem.edit",
            "filesystem.patch",
            "filesystem.mkdir",
            "filesystem.rename",
            "filesystem.delete",
            "filesystem.copy",
            "filesystem.move",
            "filesystem.import",
            "filesystem.export",
            "filesystem.glob",
            "filesystem.grep",
            "cache.ensure",
            "cache.store",
            "cache.export",
            "cache.clear",
            "shell.inspect",
            "shell.run",
        )
        return RuntimeServices(
            platform=self,
            filesystem=filesystem,
            cache=cache,
            process=process,
            terminal=unavailable,
            shared_terminal=unavailable,
            search=WindowsSearch(filesystem),
            browser=unavailable,
            openscad=unavailable,
            transfer=transfer,
            operations=operations
            + (("maintenance.start",) if self._maintenance_available(config) else ()),
        )

    def system_info(self, current_directory: str | None, filesystem: FilesystemAdapter) -> dict:
        try:
            agent_version = version("matoloop-agent")
        except Exception:
            agent_version = "0.1.0"
        result = {
            "agentVersion": agent_version,
            "platform": self.platform_id,
            "operatingSystem": "Windows",
            "distribution": platform.platform()[:255],
            "kernel": platform.version()[:160],
            "architecture": platform.machine()[:80],
            "logicalCpuCount": os.cpu_count(),
        }
        if current_directory is not None:
            directory = filesystem.resolve_directory(current_directory)
            usage = shutil.disk_usage(current_directory)
            result["disk"] = {"totalBytes": usage.total, "availableBytes": usage.free, **directory}
        return result

    def run(self, coroutine: Coroutine[Any, Any, None]) -> None:
        try:
            asyncio.run(coroutine)
        except KeyboardInterrupt:
            pass
