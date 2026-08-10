from __future__ import annotations

import asyncio
import logging
import platform
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

import websockets
from pydantic import ValidationError

from .cache import SessionCache
from .config import AgentConfig
from .process import ProcessRunner
from .protocol import (
    ARGUMENT_MODELS,
    CacheEnsureArguments,
    CacheStoreArguments,
    Cancel,
    CopyMoveArguments,
    DeleteArguments,
    Error,
    Event,
    ExportArguments,
    Hello,
    ImportArguments,
    ListArguments,
    MkdirArguments,
    ProcessArguments,
    ReadArguments,
    RenameArguments,
    Request,
    Response,
    RootDescriptor,
    ShellArguments,
    StatArguments,
    Welcome,
    WriteArguments,
    parse_inbound,
    validate_arguments,
)
from .transfer import TransferClient
from .workspace import AgentError, Workspace

logger = logging.getLogger(__name__)
MAX_WIRE_BYTES = 1024 * 1024


def serialize_message(message, max_bytes: int = MAX_WIRE_BYTES) -> str:
    payload = message.model_dump_json(by_alias=True, exclude_none=True)
    if len(payload.encode("utf-8")) >= min(max_bytes, MAX_WIRE_BYTES):
        raise AgentError("operation.tooLarge", "Response exceeds the transport limit")
    return payload


async def _defer_cancellation(awaitable: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    task = asyncio.ensure_future(awaitable)
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


async def _run_mutation(function: Callable, *args, **kwargs) -> dict[str, Any]:
    return await _defer_cancellation(asyncio.to_thread(function, *args, **kwargs))


class ExecutionAgent:
    def __init__(
        self,
        config: AgentConfig,
        *,
        transfer_client: TransferClient | None = None,
    ) -> None:
        self.config = config
        self.workspace = Workspace(config)
        self.cache = SessionCache(config.data_dir, config.max_session_cache_bytes)
        self.process = ProcessRunner(config, self.workspace)
        self.transfer = transfer_client or TransferClient(
            max_bytes=config.max_transfer_bytes,
            backend_url=config.backend_url,
            registration_token=config.token.get_secret_value(),
        )
        operations = set(ARGUMENT_MODELS)
        if not self.process.executables:
            operations.remove("process.exec")
        if self.process.shell is None:
            operations.remove("shell.run")
        self.operations = sorted(operations)

    def hello(self) -> Hello:
        return Hello(
            agentId=self.config.agent_id,
            name=self.config.name,
            platform=platform.system().lower(),
            operations=self.operations,
            roots=[RootDescriptor.model_validate(root) for root in self.workspace.descriptors()],
        )

    async def dispatch(
        self, request: Request, stream=None, *, backend_id: str | None = None
    ) -> dict[str, Any]:
        try:
            arguments = validate_arguments(request)
        except (ValidationError, ValueError) as exc:
            raise AgentError("request.invalid", "Invalid operation arguments") from exc

        if isinstance(arguments, StatArguments):
            return await asyncio.to_thread(
                self.workspace.stat,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
            )
        if isinstance(arguments, ListArguments):
            return await asyncio.to_thread(
                self.workspace.list,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                arguments.offset,
                arguments.limit,
            )
        if isinstance(arguments, ReadArguments):
            return await asyncio.to_thread(
                self.workspace.read,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                arguments.offset,
                arguments.limit,
                arguments.max_line_length,
            )
        if isinstance(arguments, WriteArguments):
            return await _run_mutation(
                self.workspace.write,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                arguments.content,
                arguments.expected_sha256,
            )
        if isinstance(arguments, MkdirArguments):
            return await _run_mutation(
                self.workspace.mkdir,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                arguments.parents,
            )
        if isinstance(arguments, RenameArguments):
            return await _run_mutation(
                self.workspace.rename,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                arguments.destination,
            )
        if isinstance(arguments, DeleteArguments):
            return await _run_mutation(
                self.workspace.delete,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                arguments.recursive,
            )
        if isinstance(arguments, CopyMoveArguments):
            destination_root = arguments.destination_root_id or arguments.root_id
            destination_revision = arguments.destination_root_revision or arguments.root_revision
            if (arguments.destination_root_id is None) != (
                arguments.destination_root_revision is None
            ):
                raise AgentError("request.invalid", "Destination root and revision must be paired")
            return await _run_mutation(
                self.workspace.copy_or_move,
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
                destination_root,
                destination_revision,
                arguments.destination_working_directory,
                arguments.destination,
                move=request.operation == "workspace.move",
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
                    self.workspace.import_file,
                    arguments.root_id,
                    arguments.root_revision,
                    arguments.working_directory,
                    arguments.path,
                    temporary,
                    arguments.overwrite,
                    arguments.size,
                    arguments.sha256,
                )
            finally:
                temporary.unlink(missing_ok=True)
        if isinstance(arguments, ExportArguments):
            export = self.workspace.open_export(
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
            )
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
            return await _defer_cancellation(
                self.cache.ensure(arguments.artifact, arguments.transfer, self.transfer)
            )
        if isinstance(arguments, CacheStoreArguments):
            if arguments.artifact.backend_id != backend_id:
                raise AgentError("cache.invalidDescriptor", "Artifact backend does not match")
            export = self.workspace.open_export(
                arguments.root_id,
                arguments.root_revision,
                arguments.working_directory,
                arguments.path,
            )
            handle, _, _ = await asyncio.to_thread(export.__enter__)
            try:
                return await _run_mutation(self.cache.store, arguments.artifact, handle)
            finally:
                await asyncio.to_thread(export.__exit__, None, None, None)
        if isinstance(arguments, ProcessArguments):
            if stream is None:

                async def stream(_channel: str, _data: str) -> None:
                    return None

            return await self.process.exec(
                root_id=arguments.root_id,
                revision=arguments.root_revision,
                working_directory=arguments.working_directory,
                program=arguments.program,
                args=arguments.args,
                command=None,
                timeout=arguments.timeout_seconds,
                stream=stream,
            )
        if isinstance(arguments, ShellArguments):
            if stream is None:

                async def stream(_channel: str, _data: str) -> None:
                    return None

            return await self.process.exec(
                root_id=arguments.root_id,
                revision=arguments.root_revision,
                working_directory=arguments.working_directory,
                program=None,
                args=[],
                command=arguments.command,
                timeout=arguments.timeout_seconds,
                stream=stream,
            )
        # CacheClearArguments has no fields and is intentionally last.
        if request.operation == "cache.clear":
            return await _run_mutation(lambda: {"removed": self.cache.clear()})
        raise AgentError("operation.unsupported", "Operation is not supported")

    async def run_forever(self) -> None:
        self.cache.evict()
        delay = 1.0
        async with self.transfer:
            while True:
                try:
                    await self._connect_once()
                    delay = 1.0
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Execution agent connection failed; retrying")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 30.0)

    async def _connect_once(self) -> None:
        async with websockets.connect(
            self.config.backend_url,
            additional_headers={"Authorization": f"Bearer {self.config.token.get_secret_value()}"},
            max_size=self.config.max_message_bytes,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15,
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
                                error=Error(code="operation.cancelled"),
                            )
                        )
                    raise
                except AgentError as exc:
                    await send(
                        Response(
                            id=request.id,
                            ok=False,
                            error=Error(code=exc.code),
                        )
                    )
                except Exception:
                    logger.error("Execution operation failed")
                    await send(
                        Response(
                            id=request.id,
                            ok=False,
                            error=Error(code="operation.failed"),
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
                await asyncio.gather(*tasks.values(), return_exceptions=True)
