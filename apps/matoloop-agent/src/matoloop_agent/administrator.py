from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from .config import AgentConfig
from .errors import AgentError
from .filesystem import Filesystem
from .protocol import ARGUMENT_MODELS, AdministratorArguments


async def sudo_options(password: str | None, timeout: float) -> tuple[str, list[str]]:
    from .process import EXECUTION_PATH, ProcessRunner

    sudo = shutil.which("sudo", path=EXECUTION_PATH)
    if sudo is None:
        raise AgentError("process.sudoUnavailable", "Sudo is unavailable")
    sudo = os.fspath(ProcessRunner._resolve_executable(Path(sudo)))
    attempts = [("-n", None)]
    if password is not None:
        attempts.append(("-S", password))
    for option, secret in attempts:
        process = await asyncio.create_subprocess_exec(
            sudo,
            option,
            *(["-p", ""] if secret is not None else []),
            "-v",
            cwd="/",
            stdin=asyncio.subprocess.PIPE if secret is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            await asyncio.wait_for(
                process.communicate((secret + "\n").encode() if secret is not None else None),
                min(timeout, 10),
            )
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode == 0:
            return sudo, ["-S", "-p", ""] if password is not None else ["-n"]
    raise AgentError(
        "process.sudoAuthFailed" if password is not None else "process.sudoAuthRequired",
        "Sudo authentication failed",
    )


async def run_administrator(
    config: AgentConfig, request: AdministratorArguments, stream=None
) -> dict:
    if request.operation == "authenticate":
        try:
            await sudo_options(None, 10)
        except AgentError as exc:
            if exc.code == "process.sudoAuthRequired":
                return {"passwordless": False}
            raise
        except TimeoutError as exc:
            raise AgentError("process.timedOut", "Sudo authentication timed out") from exc
        return {"passwordless": True}
    started = asyncio.get_running_loop().time()
    timeout = min(
        request.arguments.get("timeoutSeconds") or config.default_timeout_seconds,
        config.max_timeout_seconds,
    )
    try:
        sudo, options = await sudo_options(request.sudo_password, timeout)
    except TimeoutError as exc:
        raise AgentError("process.timedOut", "Sudo authentication timed out") from exc
    timeout -= asyncio.get_running_loop().time() - started
    if timeout <= 0:
        raise AgentError("process.timedOut", "Sudo authentication timed out")
    # Host-owned executable, module and environment; request JSON stays on a private descriptor.
    payload = json.dumps(
        {
            "config": config.model_dump(
                mode="json", by_alias=True, exclude={"backend_url", "agent_id"}
            ),
            "operation": request.operation,
            "arguments": request.arguments,
        }
    ).encode()
    request_file = tempfile.TemporaryFile()
    request_file.write(payload)
    request_file.flush()
    request_file.seek(0)
    # sudo may skip reading a password when its timestamp is valid. Keep worker data
    # separate from sudo stdin so a cached login cannot consume or corrupt the request.
    request_path = f"/proc/{os.getpid()}/fd/{request_file.fileno()}"
    try:
        return await _run_worker(config, request, sudo, options, request_path, timeout, stream)
    finally:
        request_file.close()


async def _run_worker(config, request, sudo, options, request_path, timeout, stream) -> dict:
    process = await asyncio.create_subprocess_exec(
        sudo,
        *options,
        "--",
        sys.executable,
        "-I",
        "-m",
        "matoloop_agent.administrator",
        request_path,
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
        limit=config.max_message_bytes + 1,
    )
    if "-S" in options:
        try:
            process.stdin.write((request.sudo_password + "\n").encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
    process.stdin.close()

    async def receive() -> dict | None:
        response = None
        while line := await process.stdout.readline():
            message = json.loads(line)
            if "event" in message:
                if message["event"] not in {"stdout", "stderr"} or not isinstance(
                    message.get("data"), str
                ):
                    raise ValueError("Invalid worker event")
                if stream is not None:
                    # JSON escaping still fits each chunk in the existing 64 KiB event limit.
                    for offset in range(0, len(message["data"]), 8000):
                        await stream(message["event"], message["data"][offset : offset + 8000])
            else:
                response = message
        await process.wait()
        return response

    task = asyncio.create_task(receive())
    try:
        response = await asyncio.wait_for(asyncio.shield(task), timeout + 5)
    except asyncio.CancelledError:
        if request.operation in {"filesystem.write", "filesystem.edit"}:
            # Finish atomic mutations and report their real outcome, like ordinary file writes.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            response = task.result()
        else:
            await _stop_worker(process)
            raise
    except TimeoutError as exc:
        await _stop_worker(process)
        raise AgentError("process.timedOut", "Administrator operation timed out") from exc
    except (ValueError, KeyError, TypeError) as exc:
        await _stop_worker(process)
        raise AgentError("process.startFailed", "Administrator worker failed") from exc
    if response is None and process.returncode == 1:
        raise AgentError(
            "process.sudoAuthFailed" if request.sudo_password else "process.sudoAuthRequired",
            "Sudo authentication failed",
        )
    if not isinstance(response, dict):
        raise AgentError("process.startFailed", "Administrator worker failed")
    if "error" in response:
        raise AgentError(
            response["error"]["code"],
            "Administrator operation failed",
            response["error"].get("params"),
        )
    return response["result"]


async def _stop_worker(process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), 10)
    except TimeoutError:
        process.kill()
        await process.wait()


async def worker(payload: dict) -> dict:
    from .process import ProcessRunner

    config = AgentConfig.model_validate_json(json.dumps(payload["config"]))
    request = AdministratorArguments.model_validate(
        {"operation": payload["operation"], "arguments": payload["arguments"]}
    )
    arguments = ARGUMENT_MODELS[request.operation].model_validate(request.arguments)
    files = Filesystem(config)
    if request.operation == "filesystem.read":
        return files.read(
            arguments.path, arguments.offset, arguments.limit, arguments.max_line_length
        )
    if request.operation == "filesystem.write":
        return files.write(arguments.path, arguments.content, arguments.expected_sha256)
    if request.operation == "filesystem.edit":
        return files.edit(
            arguments.path,
            arguments.old_text,
            arguments.new_text,
            arguments.replace_all,
            arguments.expected_sha256,
        )
    process = ProcessRunner(config, files)
    if request.operation == "shell.inspect":
        return process.inspect_shell(arguments.command, arguments.current_directory)
    if arguments.expected_inspection_revision is None:
        raise AgentError("request.invalid", "Administrator shell requires an inspection revision")

    async def stream(channel: str, value: str) -> None:
        print(json.dumps({"event": channel, "data": value}), flush=True)

    result = await process.exec(
        command=arguments.command,
        current_directory=arguments.current_directory,
        timeout=arguments.timeout_seconds,
        environment=arguments.environment,
        expected_inspection_revision=arguments.expected_inspection_revision,
        stream=stream,
    )
    return result


async def _worker_main(payload: dict) -> dict:
    task = asyncio.current_task()
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, task.cancel)
    return await worker(payload)


def main() -> None:
    try:
        with open(sys.argv[1], "rb") as request_file:
            raw = request_file.read(262_145)
        if len(raw) > 262_144 or os.geteuid() != 0:
            raise AgentError("request.invalid", "Invalid administrator worker request")
        response = {"result": asyncio.run(_worker_main(json.loads(raw)))}
    except AgentError as exc:
        response = {"error": {"code": exc.code, **({"params": exc.params} if exc.params else {})}}
    except (ValueError, KeyError, TypeError, IndexError, ValidationError):
        response = {"error": {"code": "request.invalid"}}
    except PermissionError:
        response = {"error": {"code": "file.accessDenied"}}
    except OSError:
        response = {"error": {"code": "process.startFailed"}}
    print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
