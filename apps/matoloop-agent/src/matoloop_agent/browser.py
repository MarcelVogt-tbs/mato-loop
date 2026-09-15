from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import shutil
import signal
import stat
import sys
import tempfile
from pathlib import Path
from time import monotonic
from uuid import uuid4

import websockets

from .browser_contracts import (
    BROWSER_FILE_RESULT,
    MAX_FILE_BYTES,
    BrowserActionResult,
    BrowserClipboardResult,
    BrowserExecuteArguments,
    BrowserFileOkResult,
    BrowserFilesResult,
    BrowserIdArguments,
    BrowserNavigationResult,
    BrowserPublishResult,
    BrowserResizeResult,
    BrowserSetupReadResult,
    BrowserStatus,
)
from .errors import AgentError
from .filesystem import Filesystem, _parts
from .terminal import MAX_BUFFER_BYTES, TerminalManager

logger = logging.getLogger(__name__)
CONTAINER_INSTALLATION = Path("/usr/local/lib/matoloop-agent/tools/browser")
BROWSER_PATH = "/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin"


def browser_command(command: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, command]
    return [sys.executable, "-m", "matoloop_agent", command]


class BrowserManager:
    def __init__(
        self, config, terminal: TerminalManager, filesystem: Filesystem | None = None
    ) -> None:
        self.config = config
        self.terminal = terminal
        self.filesystem = filesystem
        self.lock = asyncio.Lock()
        self.worker = None
        self.container = os.environ.get("MATOLOOP_AGENT_CONTAINER") == "1"
        self.installation = (
            CONTAINER_INSTALLATION
            if self.container
            else config.data_dir.parent / "tools" / "browser"
        )
        self.browser_id = None
        self.owner_id = None
        self.view_task = None
        self.setup = None
        self.setup_offset = 0
        self.setup_task = None
        self.last_status = BrowserStatus(state="missing")
        self.publish_receipts: dict[str, tuple[str, str, dict]] = {}

    def environment(self) -> tuple[dict[str, str], dict]:
        import pwd

        user = pwd.getpwnam("matoloop-browser") if os.getuid() == 0 else pwd.getpwuid(os.getuid())
        environment = {
            "HOME": str(self.config.data_dir / "tools" / "browser"),
            "USER": user.pw_name,
            "LOGNAME": user.pw_name,
            "PATH": BROWSER_PATH,
            "LANG": "C.UTF-8",
            "PLAYWRIGHT_BROWSERS_PATH": str(self.installation),
        }
        options = (
            {"user": user.pw_uid, "group": user.pw_gid, "extra_groups": []}
            if os.getuid() == 0
            else {}
        )
        return environment, options

    @staticmethod
    def directory(path: Path, mode: int, uid: int) -> None:
        # Refuse symlinked ancestors, including a replaced persistent storage mount.
        for ancestor in reversed([path, *path.parents]):
            if ancestor == path:
                ancestor.mkdir(mode=mode, exist_ok=True)
            metadata = ancestor.lstat()
            if not stat.S_ISDIR(metadata.st_mode):
                raise AgentError(
                    "browser.profileOwnerMismatch", "Browser storage is not a directory"
                )
        metadata = path.lstat()
        if metadata.st_uid != uid:
            raise AgentError("browser.profileOwnerMismatch", "Browser storage owner differs")
        path.chmod(mode)

    def storage(self, owner_id: str | None = None) -> Path:
        identity = hashlib.sha256(
            f"{self.config.backend_url}\n{self.config.agent_id}".encode()
        ).hexdigest()
        tools = self.config.data_dir / "tools"
        self.directory(tools, 0o711 if os.getuid() == 0 else 0o700, os.getuid())
        base = tools / "browser"
        self.directory(base, 0o711 if os.getuid() == 0 else 0o700, os.getuid())
        root = base / identity
        self.directory(root, 0o711 if os.getuid() == 0 else 0o700, os.getuid())
        if owner_id is not None:
            binding = json.dumps(
                {
                    "owner": owner_id.lower(),
                    "agent": self.config.agent_id,
                    "backend": self.config.backend_url,
                },
                sort_keys=True,
            ).encode()
            try:
                fd = os.open(
                    root / "binding", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                )
            except FileExistsError:
                fd = os.open(root / "binding", os.O_RDONLY | os.O_NOFOLLOW)
                with os.fdopen(fd, "rb") as handle:
                    if handle.read(16_384) != binding:
                        raise AgentError(
                            "browser.profileOwnerMismatch", "Browser binding differs"
                        ) from None
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(binding)
            self.owner_id = owner_id
        return root

    def runtime_root(self, root: Path) -> Path:
        _, options = self.environment()
        uid = options.get("user", os.getuid())
        runtime = root / "browser"
        try:
            runtime.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            if os.getuid() == 0:
                os.chown(runtime, uid, options["group"], follow_symlinks=False)
        self.directory(runtime, 0o700, uid)
        profile = runtime / "profile"
        if profile.is_symlink():
            raise AgentError("browser.profileOwnerMismatch", "Browser profile is a symlink")
        return runtime

    async def spawn(self, root: Path) -> None:
        environment, options = self.environment()
        self.worker = await asyncio.create_subprocess_exec(
            *browser_command("browser-worker"),
            str(root),
            env=environment,
            cwd="/",
            start_new_session=True,
            **options,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            limit=1024 * 1024,
        )

    async def rpc(self, op: str, **values) -> dict:
        if self.worker is None or self.worker.returncode is not None:
            raise AgentError("browser.notConnected", "Browser worker is unavailable")
        try:
            arguments = values.get("args")
            code_execution = (
                op == "execute"
                and isinstance(arguments, dict)
                and arguments.get("action")
                in {
                    "run_code",
                    "evaluate",
                }
            )
            rpc_timeout = 140 if code_execution else 55
            async with asyncio.timeout(rpc_timeout):
                started = monotonic()
                self.worker.stdin.write(json.dumps({"op": op, **values}).encode() + b"\n")
                await self.worker.stdin.drain()
                raw = await self.worker.stdout.readline()
                roundtrip_ms = max(0, (monotonic() - started) * 1000)
                response = json.loads(raw)
            if op == "execute":
                elapsed_ms = response.get("elapsedMs")
                metrics = response.get("metrics")
                # Optional private diagnostics must not affect the operation's outcome.
                if not (type(elapsed_ms) in (int, float) and 0 <= elapsed_ms <= rpc_timeout * 1000):
                    elapsed_ms = None
                if not (
                    isinstance(metrics, dict)
                    and set(metrics)
                    <= {"settleMs", "settleOutcome", "snapshotMs", "snapshotCharacters"}
                    and metrics.get("settleOutcome", "settled")
                    in {"settled", "network", "loading", "pageActivity", "renderer"}
                    and all(
                        type(metrics[key]) in (int, float)
                        and 0 <= metrics[key] <= rpc_timeout * 1000
                        for key in {"settleMs", "snapshotMs"} & metrics.keys()
                    )
                    and type(metrics.get("snapshotCharacters", 0)) is int
                    and 0 <= metrics.get("snapshotCharacters", 0) <= MAX_FILE_BYTES
                ):
                    metrics = {}
                with contextlib.suppress(ValueError):
                    args = BrowserExecuteArguments.model_validate(values.get("args"))
                    browser_id = (
                        BrowserIdArguments(browserId=self.browser_id).browser_id
                        if self.browser_id is not None
                        else None
                    )
                    logger.info(
                        "Browser execution latency: method=integration.browser.execute "
                        "action=%s browser_id=%s pipe_roundtrip_ms=%.3f worker_elapsed_ms=%s "
                        "worker_phases=%s",
                        args.action,
                        browser_id,
                        roundtrip_ms,
                        elapsed_ms,
                        metrics,
                    )
            if not response["ok"]:
                code = BrowserStatus(state="error", errorCode=response["error"]).error_code
                logger.warning("Browser worker operation failed: %s", code)
                if op == "start":
                    stage = response.get("stage")
                    logger.warning(
                        "Browser startup failed: stage=%s profile=%s",
                        stage
                        if stage in {"storage", "display", "vnc", "chromium", "page", "resize"}
                        else "unknown",
                        "temporary" if values.get("probe") else "persistent",
                    )
                raise AgentError(code, "Browser worker operation failed")
            if op == "file":
                return BROWSER_FILE_RESULT.validate_python(response["result"]).model_dump(
                    by_alias=True, exclude_none=True
                )
            if op == "files":
                return BrowserFilesResult.model_validate(response["result"]).model_dump(
                    by_alias=True, exclude_none=True
                )
            if op == "execute":
                return BrowserActionResult.model_validate(response["result"]).model_dump(
                    by_alias=True
                )
            if op == "clipboard":
                return BrowserClipboardResult.model_validate(response["result"]).model_dump()
            if op == "navigation":
                return BrowserNavigationResult.model_validate(response["result"]).model_dump(
                    by_alias=True
                )
            if op == "resize":
                return BrowserResizeResult.model_validate(response["result"]).model_dump()
            if op == "control":
                return BrowserFileOkResult.model_validate(response["result"]).model_dump()
            return response["result"]
        except (
            TimeoutError,
            json.JSONDecodeError,
            ValueError,
            OSError,
            KeyError,
            TypeError,
        ) as exc:
            reason = (
                "timeout"
                if isinstance(exc, TimeoutError)
                else "invalidJson"
                if isinstance(exc, json.JSONDecodeError)
                else "transport"
                if isinstance(exc, OSError)
                else "invalidResponse"
            )
            logger.warning("Browser worker RPC failed: operation=%s reason=%s", op, reason)
            await self.close()
            raise AgentError("browser.outcomeUnknown", "Browser worker outcome is unknown") from exc
        except asyncio.CancelledError:
            # Never let the next RPC consume a cancelled operation's response.
            await self.close()
            raise

    async def close(self) -> None:
        closing = getattr(self, "_close_task", None)
        if closing is None or closing.done():
            process, self.worker = self.worker, None
            view, self.view_task = self.view_task, None
            self.browser_id = None

            async def close() -> None:
                if view is not None:
                    view.cancel()
                if process is not None:
                    try:
                        with contextlib.suppress(ProcessLookupError):
                            process.terminate()
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(process.wait(), 12)
                    finally:
                        # Reap descendants only after the worker's 10-second cleanup budget.
                        with contextlib.suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        with contextlib.suppress(TimeoutError):
                            await asyncio.wait_for(process.wait(), 1)
                if view is not None:
                    await asyncio.wait([view], timeout=1)

            closing = self._close_task = asyncio.create_task(close())
        # RPC cancellation and connection teardown may close the same worker concurrently.
        cancelled = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                cancelled = True
        closing.result()
        if cancelled:
            raise asyncio.CancelledError

    async def status(self) -> dict:
        if not sys.platform.startswith("linux"):
            return BrowserStatus(state="unsupported", errorCode="browser.unsupported").model_dump(
                by_alias=True
            )
        if self.setup is not None and self.setup.exit_code is None:
            return BrowserStatus(state="installing", setupId=self.setup.terminal_id).model_dump(
                by_alias=True
            )
        if self.setup is not None and self.setup.exit_code != 0:
            return BrowserStatus(
                state="error", setupId=self.setup.terminal_id, errorCode="browser.setupFailed"
            ).model_dump(by_alias=True)
        if self.worker is not None:
            try:
                if (await self.rpc("health"))["ok"]:
                    return self.last_status.model_dump(by_alias=True)
            except AgentError:
                pass
            await self.close()
        try:
            root = self.runtime_root(self.storage())
            await self.spawn(root)
            self.last_status = BrowserStatus.model_validate(await self.rpc("start", probe=True))
        except KeyError:
            self.last_status = BrowserStatus(
                state="missing", missing=["runtimeUser"], errorCode="browser.dependenciesMissing"
            )
        except AgentError as exc:
            self.last_status = BrowserStatus(state="error", errorCode=exc.code)
        finally:
            await self.close()
        self.last_status.setup_id = self.setup.terminal_id if self.setup else None
        return self.last_status.model_dump(by_alias=True)

    async def setup_output(self, entry) -> None:
        try:
            while chunk := await self.terminal._read_fd(entry):
                async with entry.condition:
                    entry.buffer.extend(chunk)
                    self.setup_offset += len(chunk)
                    del entry.buffer[:-MAX_BUFFER_BYTES]
                    entry.condition.notify_all()
        finally:
            entry.eof = True
            self.terminal._close_fd(entry)
            async with entry.condition:
                entry.condition.notify_all()

    async def start_setup(self, owner_id: str) -> dict:
        root = self.storage(owner_id)
        if self.setup is not None and self.setup.exit_code is None:
            return await self.status()
        if self.worker is not None:
            raise AgentError("browser.busy", "Browser runtime is active")
        if self.setup_task is not None:
            await self.setup_task
        script = Path(__file__).with_name("browser-setup.sh")
        environment = {
            "HOME": str(Path.home()),
            "PATH": BROWSER_PATH,
            "LANG": "C.UTF-8",
            "TERM": "xterm-256color",
            "MATOLOOP_BROWSER_EXECUTABLE": sys.executable,
            "MATOLOOP_BROWSER_FROZEN": "1" if getattr(sys, "frozen", False) else "0",
            "MATOLOOP_AGENT_CONTAINER": "1" if self.container else "0",
            "MATOLOOP_BROWSER_DATA_DIR": str(self.config.data_dir),
            "MATOLOOP_BROWSER_ROOT": str(root),
            "MATOLOOP_BROWSER_INSTALLATION": str(self.installation),
        }
        self.setup_offset = 0
        self.setup = self.terminal._spawn(
            str(uuid4()),
            [str(Path("/bin/bash").resolve()), str(script)],
            self.terminal.home,
            24,
            100,
            environment,
        )
        # This entry is deliberately not registered with disconnect-scoped terminals.
        self.setup_task = asyncio.create_task(self.setup_output(self.setup))
        return await self.status()

    async def setup_io(self, action: str, args) -> dict:
        entry = self.setup
        if entry is None or entry.terminal_id != args.setup_id:
            raise AgentError("browser.invalidTarget", "Setup identifier is unavailable")
        if action == "setupwrite":
            await self.terminal._write(entry, base64.b64decode(args.data, validate=True))
            return {"ok": True}
        if action == "setupresize":
            self.terminal._resize(entry, args.rows, args.columns)
            return {"ok": True}
        async with entry.condition:
            if args.offset >= self.setup_offset and not entry.eof and args.wait_seconds:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(entry.condition.wait(), args.wait_seconds)
            start = max(min(args.offset, self.setup_offset), self.setup_offset - len(entry.buffer))
            index = start - (self.setup_offset - len(entry.buffer))
            data = bytes(entry.buffer[index : index + args.max_bytes])
        return BrowserSetupReadResult(
            data=base64.b64encode(data).decode("ascii"),
            offset=start + len(data),
            running=entry.exit_code is None,
            exitCode=entry.exit_code,
        ).model_dump(by_alias=True)

    async def relay(self, socket_path: str, relay_id: str, relay_token: str, ready) -> None:
        url = (
            self.config.backend_url.removesuffix("/execution-agents/connect")
            + f"/browser-relay/{relay_id}"
        )
        writer = None
        tasks = []
        try:
            async with websockets.connect(
                url,
                additional_headers={"Authorization": f"Bearer {relay_token}"},
                max_size=1024 * 1024,
                max_queue=4,
                compression=None,
                open_timeout=10,
                proxy=None,
            ) as websocket:
                reader, writer = await asyncio.open_unix_connection(socket_path)
                ready.set_result(True)

                async def outbound():
                    while chunk := await reader.read(65_536):
                        await websocket.send(chunk)

                async def inbound():
                    async for chunk in websocket:
                        if not isinstance(chunk, bytes):
                            raise ValueError("Binary relay required")
                        writer.write(chunk)
                        await writer.drain()

                tasks = [asyncio.create_task(outbound()), asyncio.create_task(inbound())]
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except Exception:
            if not ready.done():
                ready.set_result(False)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if writer is not None:
                writer.close()
                with contextlib.suppress(ConnectionError):
                    await writer.wait_closed()
            if not ready.done():
                ready.set_result(False)

    async def publish(self, args) -> dict:
        cached = self.publish_receipts.get(args.file_id)
        if cached is not None:
            if cached[:2] != (args.expected_origin, args.path):
                raise AgentError("browser.invalidTarget", "Download publication differs")
            return cached[2]
        if self.filesystem is None:
            raise AgentError("browser.actionFailed", "Filesystem is unavailable")
        root = self.config.home_directory / "browser" / "downloads"
        root_parts, destination_parts = _parts(os.fspath(root)), _parts(args.path)
        if destination_parts[:-1] != root_parts or len(destination_parts) != len(root_parts) + 1:
            raise AgentError("browser.invalidTarget", "Download destination is invalid")
        listing = await self.rpc("files")
        entry = next((item for item in listing["downloads"] if item["id"] == args.file_id), None)
        if (
            entry is None
            or entry["status"] != "ready"
            or entry.get("origin") != args.expected_origin
        ):
            raise AgentError("browser.invalidTarget", "Download is not publishable")
        self.filesystem.mkdir(os.fspath(root), True)
        with tempfile.TemporaryDirectory(
            prefix="browser-publish-", dir=self.config.data_dir
        ) as temporary:
            source = Path(temporary) / "download"
            digest = hashlib.sha256()
            offset = 0
            with source.open("xb") as handle:
                while offset < entry["size"]:
                    chunk = await self.rpc(
                        "file",
                        args={
                            "browserId": args.browser_id,
                            "action": "read",
                            "fileId": args.file_id,
                            "offset": offset,
                        },
                    )
                    data = base64.b64decode(chunk["data"], validate=True)
                    if (
                        chunk["fileId"] != args.file_id
                        or chunk["size"] != entry["size"]
                        or chunk["offset"] != offset + len(data)
                        or not data
                    ):
                        raise AgentError("browser.actionFailed", "Download chunk is invalid")
                    offset = chunk["offset"]
                    if chunk["complete"] != (offset == entry["size"]):
                        raise AgentError("browser.actionFailed", "Download completion is invalid")
                    digest.update(data)
                    handle.write(data)
            if offset != entry["size"] or digest.hexdigest() != entry["sha256"]:
                raise AgentError("browser.actionFailed", "Download content is invalid")
            result = self.filesystem.import_file(
                args.path, source, False, entry["size"], entry["sha256"]
            )
        receipt = BrowserPublishResult.model_validate(result).model_dump()
        self.publish_receipts[args.file_id] = (args.expected_origin, args.path, receipt)
        with contextlib.suppress(AgentError):
            await self.rpc(
                "file",
                args={
                    "browserId": args.browser_id,
                    "action": "delete",
                    "fileId": args.file_id,
                },
            )
        return receipt

    async def stage_upload(self, args) -> dict:
        if self.filesystem is None:
            raise AgentError("browser.actionFailed", "Filesystem is unavailable")
        export = self.filesystem.open_export(args.path)
        handle, size, _ = export.__enter__()
        file_id = None
        try:
            if size > MAX_FILE_BYTES:
                raise AgentError("browser.fileTooLarge", "Browser file is too large")
            staged = await self.rpc(
                "file",
                args={
                    "browserId": args.browser_id,
                    "action": "begin",
                    "filename": Path(args.path).name,
                    "size": size,
                },
            )
            file_id = staged["fileId"]
            offset = 0
            while data := handle.read(65_536):
                staged = await self.rpc(
                    "file",
                    args={
                        "browserId": args.browser_id,
                        "action": "write",
                        "fileId": file_id,
                        "offset": offset,
                        "data": base64.b64encode(data).decode("ascii"),
                    },
                )
                offset = staged["offset"]
            return await self.rpc(
                "file",
                args={"browserId": args.browser_id, "action": "finish", "fileId": file_id},
            )
        except BaseException:
            if file_id is not None:
                with contextlib.suppress(AgentError):
                    await self.rpc(
                        "file",
                        args={
                            "browserId": args.browser_id,
                            "action": "delete",
                            "fileId": file_id,
                        },
                    )
            raise
        finally:
            export.__exit__(None, None, None)

    async def dispatch(self, operation: str, args) -> dict:
        action = operation.rsplit(".", 1)[-1]
        try:
            if action.startswith("setup") and action != "setup":
                return await self.setup_io(action, args)
            async with self.lock:
                if self.worker is not None and self.worker.returncode is not None:
                    await self.close()
                if action == "status":
                    return await self.status()
                if not sys.platform.startswith("linux"):
                    raise AgentError("browser.unsupported", "Browser requires Linux")
                if action == "setup":
                    return await self.start_setup(args.owner_id)
                if action == "reset":
                    root = self.storage(args.owner_id)
                    if self.worker or (self.setup and self.setup.exit_code is None):
                        raise AgentError("browser.busy", "Browser is active")
                    runtime = self.runtime_root(root)
                    # shutil.rmtree uses fd-relative, symlink-resistant traversal on Linux.
                    if not shutil.rmtree.avoids_symlink_attacks:
                        raise AgentError("browser.unsupported", "Safe reset is unavailable")
                    shutil.rmtree(runtime)
                    return {"ok": True}
                if action == "open":
                    root = self.storage(args.owner_id)
                    if not args.allow_network:
                        raise AgentError(
                            "browser.invalidTarget", "Browser network policy denies opening"
                        )
                    if self.setup and self.setup.exit_code is None:
                        raise AgentError("browser.busy", "Browser setup is active")
                    if self.worker:
                        if self.browser_id == args.browser_id and (await self.rpc("health"))["ok"]:
                            return {"browserId": self.browser_id}
                        raise AgentError("browser.busy", "Another browser is active")
                    try:
                        self.publish_receipts.clear()
                        await self.spawn(self.runtime_root(root))
                        self.last_status = BrowserStatus.model_validate(
                            await self.rpc("start", colorScheme=args.color_scheme)
                        )
                        if self.last_status.state != "ready":
                            raise AgentError(
                                self.last_status.error_code or "browser.notReady",
                                "Browser is not ready",
                            )
                        self.browser_id = args.browser_id
                    except BaseException:
                        await self.close()
                        raise
                    return {"browserId": self.browser_id}
                if action == "close" and self.browser_id is None:
                    return {"ok": True}
                if args.browser_id != self.browser_id:
                    raise AgentError("browser.notConnected", "Browser identifier is unavailable")
                if action == "close":
                    await self.close()
                    return {"ok": True}
                if action == "view":
                    if self.view_task:
                        self.view_task.cancel()
                        await asyncio.gather(self.view_task, return_exceptions=True)
                    result = await self.rpc("view")
                    ready = asyncio.get_running_loop().create_future()
                    self.view_task = asyncio.create_task(
                        self.relay(result["socket"], args.relay_id, args.relay_token, ready)
                    )
                    if not await ready:
                        raise AgentError("browser.notConnected", "Browser relay could not connect")
                    return {"ok": True}
                if action == "files":
                    return await self.rpc("files")
                if action == "publish":
                    return await self.publish(args)
                if action == "upload":
                    return await self.stage_upload(args)
                if action == "execute" and not args.allow_network:
                    await self.close()
                    raise AgentError(
                        "browser.invalidTarget", "Browser network policy denies execution"
                    )
                if action in {"resize", "navigation"}:
                    # Drain the response under the same lock even if a viewer disappears.
                    # Cancelling this pipe RPC would otherwise close the live browser.
                    pending = asyncio.create_task(
                        self.rpc(action, args=args.model_dump(by_alias=True, exclude_unset=True))
                    )
                    try:
                        return await asyncio.shield(pending)
                    except asyncio.CancelledError:
                        while not pending.done():
                            with contextlib.suppress(asyncio.CancelledError):
                                await asyncio.shield(pending)
                        pending.result()
                        raise
                return await self.rpc(action, args=args.model_dump(by_alias=True))
        except AgentError as exc:
            if exc.code.startswith("browser.") or (
                action == "publish" and exc.code == "path.exists"
            ):
                raise
            raise AgentError("browser.actionFailed", "Browser operation failed") from None
        except Exception:
            logger.error("Browser operation failed")
            raise AgentError("browser.actionFailed", "Browser operation failed") from None


async def finish_setup() -> int:
    """Complete the fixed setup script with a runtime-user install and a disposable probe."""
    from .config import AgentConfig

    data = Path(os.environ["MATOLOOP_BROWSER_DATA_DIR"])
    config = AgentConfig.model_validate({"homeDirectory": data})
    config._data_directory = data
    manager = BrowserManager(config, None)
    manager.installation = Path(os.environ["MATOLOOP_BROWSER_INSTALLATION"])
    try:
        root = manager.runtime_root(Path(os.environ["MATOLOOP_BROWSER_ROOT"]))
        environment, options = manager.environment()
        environment["HOME"] = str(root)
        if not manager.container:
            manager.directory(manager.installation.parent, 0o711 if options else 0o700, os.getuid())
            if not manager.installation.exists():
                manager.installation.mkdir(mode=0o700)
                if options:
                    os.chown(manager.installation, options["user"], options["group"])
            manager.directory(manager.installation, 0o700, options.get("user", os.getuid()))
            process = await asyncio.create_subprocess_exec(
                *browser_command("browser-install-worker"),
                env=environment,
                cwd="/",
                **options,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if await process.wait() != 0:
                raise AgentError("browser.setupFailed", "Managed browser installation failed")
        await manager.spawn(root)
        status = BrowserStatus.model_validate(await manager.rpc("start", probe=True))
        if status.state != "ready":
            raise AgentError(status.error_code or "browser.notReady", "Browser probe failed")
        print("Browser ist einsatzbereit.", flush=True)
        return 0
    except Exception:
        print("Browser-Einrichtung oder Startpruefung fehlgeschlagen.", flush=True)
        return 1
    finally:
        await manager.close()
