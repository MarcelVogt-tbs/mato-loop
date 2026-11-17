"""User-only macOS lifecycle for the shared Playwright browser worker."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import platform
import signal
import sys
import tomllib
from pathlib import Path

from .browser import BrowserManager, NativeBrowserRuntime
from .browser_storage import browser_file
from .errors import AgentError


class MacOSBrowserRuntime(NativeBrowserRuntime):
    def __init__(self, config, terminal, filesystem=None):
        config = config.model_copy()
        config._data_directory = config.data_dir.resolve()
        super().__init__(config, terminal, filesystem)
        self.container = False
        self.installation = config.data_dir.parent / "tools" / "browser"

    @property
    def supported(self) -> bool:
        return sys.platform == "darwin" and platform.machine() == "arm64" and os.getuid() != 0

    def environment(self) -> tuple[dict[str, str], dict]:
        if not self.supported:
            raise AgentError("browser.unsupported", "Browser requires a native Apple Silicon user")
        import pwd

        user = pwd.getpwuid(os.getuid())
        return {
            "HOME": str(self.config.data_dir / "tools" / "browser"),
            "USER": user.pw_name,
            "LOGNAME": user.pw_name,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "PLAYWRIGHT_BROWSERS_PATH": str(self.installation),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        }, {}

    def setup_environment(self) -> dict[str, str]:
        environment, _ = self.environment()
        environment.update(
            (key, value)
            for key, value in os.environ.items()
            if key
            in {
                "HTTP_PROXY",
                "HTTPS_PROXY",
                "ALL_PROXY",
                "NO_PROXY",
                "SSL_CERT_FILE",
                "SSL_CERT_DIR",
                "NODE_EXTRA_CA_CERTS",
                "UV_NATIVE_TLS",
                "PLAYWRIGHT_DOWNLOAD_HOST",
                "PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST",
            }
        )
        environment.update(
            UV_CACHE_DIR=str(self.config.data_dir.parent / "cache" / "uv"),
            UV_PYTHON_INSTALL_DIR=str(self.config.data_dir.parent / "tools" / "python"),
            UV_NO_PROGRESS="1",
            # Keep previous revisions available until an update can no longer roll back.
            PLAYWRIGHT_SKIP_BROWSER_GC="1",
        )
        return environment

    def storage_directory(self, path: Path) -> None:
        self.directory(path, 0o700, os.getuid())

    async def start_setup(self, owner_id: str) -> dict:
        if not self.supported:
            raise AgentError("browser.unsupported", "Browser host is unsupported")
        if self.setup is not None and self.setup.exit_code is None:
            return await self.status()
        if self.worker is not None:
            raise AgentError("browser.busy", "Browser runtime is active")
        if self.setup_task is not None:
            await self.setup_task
        root = self.storage(owner_id)
        return await self.launch_setup(root, self.setup_environment())


class MacOSBrowserManager(BrowserManager, MacOSBrowserRuntime):
    runtime_class = MacOSBrowserRuntime


def browser_packages(data: Path) -> list[str]:
    """Reuse the browser group from managed source or an explicit development checkout."""
    project = data.parent / "source" / "apps" / "matoloop-agent" / "pyproject.toml"
    state = data.parent / "install.json"
    if state.is_file():
        with browser_file(state) as handle:
            installation = json.load(handle)
        if installation.get("sourceType") == "checkout":
            project = Path(installation["source"]) / "apps/matoloop-agent/pyproject.toml"
    if not project.is_file():
        project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with browser_file(project) as handle:
        return tomllib.load(handle)["dependency-groups"]["browser"]


async def setup_command(command: list[str], environment: dict[str, str]) -> int:
    """Reap this command's group, including uv build and Playwright download children."""
    spawning = asyncio.create_task(
        asyncio.create_subprocess_exec(*command, env=environment, cwd="/", start_new_session=True)
    )
    try:
        process = await asyncio.shield(spawning)
        return await process.wait()
    finally:
        # Cancellation can arrive before create_subprocess_exec returns its process handle.
        while not spawning.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(spawning)
        process = spawning.result()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        reaping = asyncio.create_task(process.wait())
        while not reaping.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(reaping)
        reaping.result()


async def finish_setup(*, install_packages: bool = False) -> int:
    from .config import AgentConfig

    data = Path(os.environ["MATOLOOP_BROWSER_DATA_DIR"]).resolve()
    config = AgentConfig.model_validate({"homeDirectory": data})
    config._data_directory = data
    manager = MacOSBrowserManager(config, None)
    manager.installation = Path(os.environ["MATOLOOP_BROWSER_INSTALLATION"])
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    try:
        environment = manager.setup_environment()
        os.umask(0o077)
        root = manager.runtime_root(Path(os.environ["MATOLOOP_BROWSER_ROOT"]).resolve())
        if install_packages:
            # Never mutate a system interpreter or use tools from Homebrew/PATH.
            if sys.prefix == sys.base_prefix:
                raise AgentError("browser.setupFailed", "Browser setup requires a private runtime")
            manager.directory(Path(sys.prefix).resolve(), 0o700, os.getuid())
            uv = data.parent / "tools" / "bin" / "uv"
            manager.storage_directory(uv.parent)
            if not uv.is_file() or uv.is_symlink() or uv.stat().st_uid != os.getuid():
                raise AgentError("browser.setupFailed", "Browser setup requires private uv")
            print(
                "Installing browser Python packages in the private Agent environment.", flush=True
            )
            if (
                await setup_command(
                    [
                        str(uv),
                        "pip",
                        "install",
                        "--python",
                        sys.executable,
                        *browser_packages(data),
                    ],
                    environment,
                )
                != 0
            ):
                raise AgentError("browser.setupFailed", "Browser package installation failed")
        manager.storage_directory(manager.installation.parent)
        manager.storage_directory(manager.installation)
        print("Installing the matching Chromium version.", flush=True)
        if (
            await setup_command(
                [sys.executable, "-m", "playwright", "install", "--no-shell", "chromium"],
                environment,
            )
            != 0
        ):
            raise AgentError("browser.setupFailed", "Browser installation failed")
        print("Starting the sandboxed browser runtime check.", flush=True)
        await manager.spawn(root)
        status = await manager.rpc("start")
        if status["state"] != "ready":
            raise AgentError(status.get("errorCode") or "browser.notReady", "Browser probe failed")
        print(f"Chromium: {status['browserVersion']}", flush=True)
        print(f"Playwright: {status['playwrightVersion']}", flush=True)
        print("Browser is ready.", flush=True)
        return 0
    except Exception:
        print("Browser setup or startup check failed.", flush=True)
        return 1
    finally:
        try:
            await manager.close()
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
