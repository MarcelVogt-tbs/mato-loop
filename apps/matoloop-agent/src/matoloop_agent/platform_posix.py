from __future__ import annotations

import asyncio
import fcntl
import os
import shutil
import signal
import socket
import threading
from collections.abc import Coroutine
from pathlib import Path
from typing import Any

from .browser import BrowserManager
from .browser_contracts import BROWSER_ARGUMENT_MODELS
from .cache import SessionCache
from .config import AgentConfig
from .credentials_linux import LinuxCredentialStore
from .filesystem import Filesystem
from .platform import RuntimeServices
from .process import ProcessRunner
from .protocol import ARGUMENT_MODELS
from .search import FilesystemSearch
from .terminal import TerminalManager
from .transfer import TransferClient


class PosixPlatform:
    browser_class = BrowserManager

    def computer_name(self) -> str:
        return socket.gethostname()

    def credential_store(self, data_dir: Path) -> LinuxCredentialStore:
        return LinuxCredentialStore(data_dir)

    def clear_cache(self, config: AgentConfig) -> int:
        return SessionCache(config.data_dir, config.max_session_cache_bytes).clear()

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
        operations = {
            op
            for op in set(ARGUMENT_MODELS) | set(BROWSER_ARGUMENT_MODELS)
            if not op.startswith("integration.openscad.")
        }
        if shutil.which("git") is None:
            operations = {op for op in operations if not op.startswith("git.")}
        if process.shell is None:
            operations.discard("shell.run")
        if not terminal.available:
            operations = {op for op in operations if not op.startswith("terminal.")}
        if search.executable is None:
            operations -= {"filesystem.glob", "filesystem.grep"}
        return RuntimeServices(
            platform=self,
            filesystem=filesystem,
            cache=cache,
            process=process,
            terminal=terminal,
            search=search,
            browser=self.browser_class(config, terminal, filesystem),
            openscad=None,
            transfer=transfer,
            operations=tuple(sorted(operations)),
        )

    def maintenance_running(self, config: AgentConfig) -> bool:
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
