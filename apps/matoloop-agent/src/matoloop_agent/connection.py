from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import socket
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import websockets
from pydantic import ValidationError

from .browser import BrowserManager
from .browser_contracts import BROWSER_ARGUMENT_MODELS
from .cache import SessionCache
from .config import AgentConfig
from .filesystem import AgentError, Filesystem
from .openscad import OpenScadRunner
from .process import ProcessRunner
from .protocol import (
    ARGUMENT_MODELS,
    CacheEnsureArguments,
    CacheExportArguments,
    CacheStoreArguments,
    Cancel,
    CopyMoveArguments,
    DeleteArguments,
    EditArguments,
    Error,
    Event,
    ExportArguments,
    GlobArguments,
    GrepArguments,
    Hello,
    ImportArguments,
    ListArguments,
    MkdirArguments,
    OpenScadDiscoverArguments,
    OpenScadRenderArguments,
    OpenScadTestArguments,
    PatchArguments,
    ReadArguments,
    RenameArguments,
    Request,
    ResolveDirectoryArguments,
    Response,
    SharedCancelArguments,
    SharedExecuteArguments,
    SharedInspectArguments,
    SharedOpenArguments,
    SharedStageArguments,
    SharedStatusArguments,
    ShellArguments,
    ShellInspectArguments,
    StatArguments,
    SystemInfo,
    SystemInfoArguments,
    TerminalCloseArguments,
    TerminalOpenArguments,
    TerminalReadArguments,
    TerminalResizeArguments,
    TerminalRunArguments,
    TerminalWriteArguments,
    UpsertArguments,
    Welcome,
    WriteArguments,
    parse_inbound,
    validate_arguments,
)
from .search import FilesystemSearch
from .shared_terminal import SHARED_OPERATIONS, SharedTerminals
from .terminal import TerminalManager
from .transfer import TransferClient

logger = logging.getLogger(__name__)
MAX_WIRE_BYTES = 1024 * 1024


def serialize_message(message, max_bytes: int = MAX_WIRE_BYTES) -> str:
    payload = message.model_dump_json(by_alias=True, exclude_none=True)
    if len(payload.encode("utf-8")) >= min(max_bytes, MAX_WIRE_BYTES):
        raise AgentError("operation.tooLarge", "Response exceeds the transport limit")
    return payload


async def _run_mutation(function: Callable, *args, **kwargs) -> dict[str, Any]:
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # A thread cannot be stopped safely. Its real result remains the terminal outcome.
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                pass
        return worker.result()


class MatoLoopAgent:
    def __init__(
        self,
        config: AgentConfig,
        access_key: str,
        *,
        transfer_client: TransferClient | None = None,
    ) -> None:
        if config.backend_url is None or config.agent_id is None:
            raise ValueError("MatoLoop Agent connection is not configured")
        self.config = config
        self.filesystem = Filesystem(config)
        self.cache = SessionCache(config.data_dir, config.max_session_cache_bytes)
        self.process = ProcessRunner(config, self.filesystem)
        self.terminal = TerminalManager(config, self.process)
        self.browser = BrowserManager(config, self.terminal, self.filesystem)
        self.shared_terminal = SharedTerminals(self.terminal)
        self.search = FilesystemSearch(self.filesystem)
        self.transfer = transfer_client or TransferClient(
            max_bytes=config.max_transfer_bytes,
            backend_url=config.backend_url,
            access_key=access_key,
        )
        self.openscad = OpenScadRunner(config, self.filesystem, self.cache, self.transfer)
        self._access_key = access_key
        operations = set(ARGUMENT_MODELS) | set(BROWSER_ARGUMENT_MODELS)
        if self.process.shell is None:
            operations.remove("shell.run")
        if not self.terminal.available:
            operations -= {
                "terminal.open",
                "terminal.read",
                "terminal.write",
                "terminal.resize",
                "terminal.close",
                "terminal.run",
            }
        if not self.shared_terminal.available:
            operations -= SHARED_OPERATIONS
        else:
            operations |= {"terminal.read", "terminal.write", "terminal.resize", "terminal.close"}
        if self.search.executable is None:
            operations -= {"filesystem.glob", "filesystem.grep"}
        if not self.openscad.render_available:
            operations -= {
                "integration.openscad.render",
                "integration.openscad.render6",
            }
        self.operations = sorted(operations)

    def hello(self) -> Hello:
        home = self.filesystem.resolve_directory(os.fspath(self.config.home_directory))
        info = self.system_info(None)
        return Hello(
            agentId=self.config.agent_id,
            agentVersion=info["agentVersion"],
            computerName=socket.gethostname(),
            platform=sys.platform,
            homeDirectory=home["path"],
            operations=self.operations,
            systemInfo=SystemInfo.model_validate(
                {
                    field.alias or name: info.get(field.alias or name)
                    for name, field in SystemInfo.model_fields.items()
                }
            ),
        )

    def system_info(self, current_directory: str | None) -> dict[str, Any]:
        try:
            from importlib.metadata import version

            agent_version = version("matoloop-agent")
        except Exception:
            agent_version = "0.1.0"
        result: dict[str, Any] = {
            "agentVersion": agent_version,
            "platform": sys.platform[:80],
            "operatingSystem": None,
            "kernel": None,
            "architecture": None,
            "logicalCpuCount": None,
        }
        with suppress(OSError, ValueError):
            uname = os.uname()
            result.update(
                operatingSystem=uname.sysname, kernel=uname.release, architecture=uname.machine
            )
        with suppress(OSError, ValueError):
            count = os.cpu_count()
            if type(count) is int and 1 <= count <= 2147483647:
                result["logicalCpuCount"] = count
        if sys.platform.startswith("linux"):
            # Read directly because platform.freedesktop_os_release caches across reconnects.
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
            directory = self.filesystem.resolve_directory(current_directory)
            usage = shutil.disk_usage(directory["path"])
            result["disk"] = {
                "totalBytes": usage.total,
                "availableBytes": usage.free,
                **directory,
            }
        return result

    async def dispatch(
        self, request: Request, stream=None, *, backend_id: str | None = None
    ) -> dict[str, Any]:
        try:
            arguments = validate_arguments(request)
        except (ValidationError, ValueError) as exc:
            if request.operation.startswith("integration.browser."):
                raise AgentError("browser.invalidTarget", "Invalid browser arguments") from exc
            raise AgentError("request.invalid", "Invalid operation arguments") from exc

        if request.operation in BROWSER_ARGUMENT_MODELS:
            return await self.browser.dispatch(request.operation, arguments)
        if isinstance(arguments, SystemInfoArguments):
            return await asyncio.to_thread(self.system_info, arguments.current_directory)
        if isinstance(arguments, ResolveDirectoryArguments):
            return await asyncio.to_thread(self.filesystem.resolve_directory, arguments.path)
        if isinstance(arguments, OpenScadDiscoverArguments):
            return self.openscad.discover()
        if isinstance(arguments, OpenScadTestArguments):
            return await self.openscad.test(arguments.executable_path)

        if isinstance(arguments, StatArguments):
            return await asyncio.to_thread(self.filesystem.stat, arguments.path)
        if isinstance(arguments, ListArguments):
            return await asyncio.to_thread(
                self.filesystem.list,
                arguments.path,
                arguments.offset,
                arguments.limit,
            )
        if isinstance(arguments, ReadArguments):
            return await asyncio.to_thread(
                self.filesystem.read,
                arguments.path,
                arguments.offset,
                arguments.limit,
                arguments.max_line_length,
            )
        if isinstance(arguments, GlobArguments):
            return await self.search.glob(
                arguments.path,
                arguments.pattern,
            )
        if isinstance(arguments, GrepArguments):
            return await self.search.grep(
                arguments.path,
                arguments.pattern,
                arguments.include,
            )
        if isinstance(arguments, WriteArguments):
            return await _run_mutation(
                self.filesystem.write,
                arguments.path,
                arguments.content,
                arguments.expected_sha256,
            )
        if isinstance(arguments, UpsertArguments):
            return await _run_mutation(
                self.filesystem.upsert,
                arguments.path,
                arguments.content,
            )
        if isinstance(arguments, EditArguments):
            return await _run_mutation(
                self.filesystem.edit,
                arguments.path,
                arguments.old_text,
                arguments.new_text,
                arguments.replace_all,
                arguments.expected_sha256,
            )
        if isinstance(arguments, PatchArguments):
            return await _run_mutation(
                self.filesystem.patch,
                arguments.patch,
                arguments.expected_sha256,
            )
        if isinstance(arguments, MkdirArguments):
            return await _run_mutation(
                self.filesystem.mkdir,
                arguments.path,
                arguments.parents,
            )
        if isinstance(arguments, CopyMoveArguments):
            return await _run_mutation(
                self.filesystem.copy_or_move,
                arguments.path,
                arguments.destination,
                move=request.operation == "filesystem.move",
            )
        if isinstance(arguments, RenameArguments):
            return await _run_mutation(
                self.filesystem.rename,
                arguments.path,
                arguments.destination,
            )
        if isinstance(arguments, DeleteArguments):
            return await _run_mutation(
                self.filesystem.delete,
                arguments.path,
                arguments.recursive,
            )
        if isinstance(arguments, ImportArguments):
            transfer_dir = self.config.data_dir / "transfers"
            temporary = await self.transfer.download(
                arguments.transfer,
                transfer_dir,
                expected_size=arguments.size,
                expected_sha256=arguments.sha256,
            )
            try:
                return await _run_mutation(
                    self.filesystem.import_file,
                    arguments.path,
                    temporary,
                    arguments.overwrite,
                    arguments.size,
                    arguments.sha256,
                )
            finally:
                temporary.unlink(missing_ok=True)
        if isinstance(arguments, ExportArguments):
            export = self.filesystem.open_export(arguments.path)
            handle, size, digest = await asyncio.to_thread(export.__enter__)
            try:
                if arguments.expected_sha256 is not None and digest != arguments.expected_sha256:
                    raise AgentError("file.changed", "File changed since it was selected")
                await self.transfer.upload(
                    arguments.transfer,
                    handle,
                    size=size,
                    sha256=digest,
                )
            finally:
                await asyncio.to_thread(export.__exit__, None, None, None)
            return {"size": size, "sha256": digest}
        if isinstance(arguments, CacheEnsureArguments):
            if arguments.artifact.backend_id != backend_id:
                raise AgentError("cache.invalidDescriptor", "Artifact backend does not match")
            return await self.cache.ensure(arguments.artifact, arguments.transfer, self.transfer)
        if isinstance(arguments, CacheExportArguments):
            if arguments.artifact.backend_id != backend_id:
                raise AgentError("cache.invalidDescriptor", "Artifact backend does not match")
            export = self.cache.open_export(arguments.artifact)
            handle, size, digest = await asyncio.to_thread(export.__enter__)
            try:
                try:
                    await self.transfer.upload(
                        arguments.transfer,
                        handle,
                        size=size,
                        sha256=digest,
                    )
                except AgentError as exc:
                    if exc.code.startswith("transfer."):
                        raise AgentError(
                            "cache.exportFailed", "Cached artifact upload failed"
                        ) from exc
                    raise
            finally:
                await asyncio.to_thread(export.__exit__, None, None, None)
            return {"size": size, "sha256": digest}
        if isinstance(arguments, CacheStoreArguments):
            if arguments.artifact.backend_id != backend_id:
                raise AgentError("cache.invalidDescriptor", "Artifact backend does not match")
            export = self.filesystem.open_export(arguments.path)
            handle, _, _ = await asyncio.to_thread(export.__enter__)
            try:
                return await _run_mutation(self.cache.store, arguments.artifact, handle)
            finally:
                await asyncio.to_thread(export.__exit__, None, None, None)
        if isinstance(arguments, ShellArguments):
            if stream is None:

                async def stream(_channel: str, _data: str) -> None:
                    return None

            return await self.process.exec(
                current_directory=arguments.current_directory,
                command=arguments.command,
                timeout=arguments.timeout_seconds,
                stream=stream,
                expected_inspection_revision=arguments.expected_inspection_revision,
            )
        if isinstance(arguments, ShellInspectArguments):
            result = await asyncio.to_thread(
                self.process.inspect_shell,
                arguments.command,
                arguments.current_directory,
            )
            result.setdefault("inspectionRevision", self.process._inspection_revision(result))
            return result
        if isinstance(arguments, SharedOpenArguments):
            return await self.shared_terminal.open(
                arguments.rows, arguments.columns, terminal_id=arguments.terminal_id
            )
        if isinstance(arguments, SharedStatusArguments):
            return await self.shared_terminal.get(arguments.terminal_id).status(
                redisplay=arguments.redisplay
            )
        if isinstance(arguments, SharedExecuteArguments):
            return await self.shared_terminal.get(arguments.terminal_id).execute(
                arguments.command_id,
                arguments.command,
                arguments.expected_revision,
                arguments.expected_context_revision,
                arguments.expected_inspection_revision,
                arguments.timeout_seconds,
                stream,
            )
        if isinstance(arguments, SharedStageArguments):
            return await self.shared_terminal.get(arguments.terminal_id).stage(
                arguments.command_id, arguments.command
            )
        if isinstance(arguments, SharedInspectArguments):
            return await self.shared_terminal.get(arguments.terminal_id).inspect(
                arguments.command_id, freeze=arguments.freeze
            )
        if isinstance(arguments, SharedCancelArguments):
            return await self.shared_terminal.get(arguments.terminal_id).cancel(
                arguments.command_id
            )
        if isinstance(arguments, TerminalOpenArguments):
            return await self.terminal.open(arguments.rows, arguments.columns)
        if isinstance(arguments, TerminalReadArguments):
            return await self.terminal.read(
                arguments.terminal_id,
                arguments.max_bytes,
                arguments.wait_seconds,
            )
        if isinstance(arguments, TerminalWriteArguments):
            return await self.terminal.write(arguments.terminal_id, arguments.data)
        if isinstance(arguments, TerminalResizeArguments):
            return await self.terminal.resize(
                arguments.terminal_id,
                arguments.rows,
                arguments.columns,
            )
        if isinstance(arguments, TerminalCloseArguments):
            return await self.terminal.close(arguments.terminal_id)
        if isinstance(arguments, TerminalRunArguments):
            if stream is None:

                async def stream(_channel: str, _data: str) -> None:
                    return None

            return await self.terminal.run(
                terminal_id=arguments.terminal_id,
                current_directory=arguments.current_directory,
                rows=arguments.rows,
                columns=arguments.columns,
                command=arguments.command,
                timeout=arguments.timeout_seconds,
                expected_inspection_revision=arguments.expected_inspection_revision,
                stream=stream,
            )
        if isinstance(arguments, OpenScadRenderArguments):
            if backend_id is None:
                raise AgentError("cache.invalidDescriptor", "Backend identity is unavailable")
            return await self.openscad.render(
                arguments,
                backend_id,
                six_views=request.operation == "integration.openscad.render6",
            )
        # CacheClearArguments has no fields and is intentionally last.
        if request.operation == "cache.clear":
            return await _run_mutation(lambda: {"removed": self.cache.clear()})
        raise AgentError("operation.unsupported", "Operation is not supported")

    async def run_forever(
        self, access_key_loader: Callable[[], Awaitable[str]] | None = None
    ) -> None:
        try:
            self.cache.evict()
            delay = 1.0
            async with self.transfer:
                while True:
                    connected = asyncio.Event()
                    try:
                        if access_key_loader is not None:
                            self._access_key = await access_key_loader()
                            self.transfer.set_access_key(self._access_key)
                        await self._connect_once(connected)
                        delay = 1.0
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        if connected.is_set():
                            delay = 1.0
                        logger.warning("MatoLoop Agent connection failed; retrying")
                        await asyncio.sleep(delay)
                        delay = min(delay * 2, 30.0)
        finally:
            try:
                await self.browser.close()
            finally:
                await self.terminal.close_all()

    async def _connect_once(self, connected: asyncio.Event) -> None:
        async with websockets.connect(
            self.config.backend_url,
            additional_headers={"Authorization": f"Bearer {self._access_key}"},
            max_size=self.config.max_message_bytes,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15,
            close_timeout=2,
        ) as socket:
            send_lock = asyncio.Lock()

            async def send(message) -> None:
                async with send_lock:
                    await socket.send(serialize_message(message, self.config.max_message_bytes))

            await send(self.hello())
            try:
                first = parse_inbound(await asyncio.wait_for(socket.recv(), 15))
            except (ValidationError, TimeoutError) as exc:
                raise AgentError("protocol.invalid", "Backend welcome was invalid") from exc
            if not isinstance(first, Welcome):
                raise AgentError("protocol.invalid", "Backend did not send welcome")
            if first.agent_id != self.config.agent_id:
                raise AgentError("protocol.invalid", "Backend welcomed a different agent")
            connected.set()

            tasks: dict[str, asyncio.Task] = {}
            completed: set[str] = set()

            async def execute(request: Request) -> None:
                async def stream(channel: str, data: str) -> None:
                    await send(Event(id=request.id, event=channel, data=data))

                try:
                    result = await self.dispatch(request, stream, backend_id=first.agent_id)
                    await send(Response(id=request.id, ok=True, result=result))
                except asyncio.CancelledError:
                    with suppress(Exception):
                        await send(
                            Response(
                                id=request.id,
                                ok=False,
                                error=Error(
                                    code="browser.outcomeUnknown"
                                    if request.operation in BROWSER_ARGUMENT_MODELS
                                    else "operation.cancelled"
                                ),
                            )
                        )
                    raise
                except AgentError as exc:
                    code = exc.code
                    if (
                        request.operation in BROWSER_ARGUMENT_MODELS
                        and not code.startswith("browser.")
                        and not (
                            request.operation == "integration.browser.publish"
                            and code == "path.exists"
                        )
                    ):
                        code = (
                            "browser.fileTooLarge"
                            if code == "operation.tooLarge"
                            else "browser.actionFailed"
                        )
                    await send(
                        Response(
                            id=request.id,
                            ok=False,
                            error=Error(code=code, params=exc.params),
                        )
                    )
                except Exception:
                    logger.error("Execution operation failed")
                    await send(
                        Response(
                            id=request.id,
                            ok=False,
                            error=Error(
                                code="browser.actionFailed"
                                if request.operation in BROWSER_ARGUMENT_MODELS
                                else "operation.failed"
                            ),
                        )
                    )
                finally:
                    tasks.pop(request.id, None)
                    completed.add(request.id)
                    if len(completed) > 10_000:
                        completed.clear()

            try:
                async for raw in socket:
                    try:
                        message = parse_inbound(raw)
                    except ValidationError:
                        await socket.close(code=1007, reason="Invalid protocol message")
                        break
                    if isinstance(message, Request):
                        if message.id in tasks or message.id in completed:
                            await send(
                                Response(
                                    id=message.id,
                                    ok=False,
                                    error=Error(code="request.duplicate"),
                                )
                            )
                        else:
                            tasks[message.id] = asyncio.create_task(execute(message))
                    elif isinstance(message, Cancel):
                        task = tasks.get(message.id)
                        if task is not None:
                            task.cancel()
                    else:
                        await socket.close(code=1007, reason="Unexpected protocol message")
                        break
            finally:
                for task in tasks.values():
                    task.cancel()
                try:
                    await self.browser.close()
                finally:
                    await self.terminal.close_all()
                await asyncio.gather(*tasks.values(), return_exceptions=True)
