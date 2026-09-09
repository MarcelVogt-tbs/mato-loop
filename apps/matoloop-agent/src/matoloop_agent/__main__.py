from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import os
import signal
import sys
import threading
from collections.abc import Coroutine
from pathlib import Path

from .cache import SessionCache
from .config import AgentConfig, ConnectionConfig
from .connection import MatoLoopAgent
from .credentials import (
    CredentialError,
    NotPairedError,
    PairingTemporaryError,
    ResolvedCredential,
    resolve_credential,
    select_pairing_token,
)


def _environment(name: str) -> str | None:
    return os.getenv(name) or None


def _default_config_path() -> Path:
    configured = _environment("MATOLOOP_AGENT_CONFIG")
    if configured is not None:
        return Path(configured)
    data_home = Path(_environment("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data_home / "matoloop-agent" / "config.json"


def _connection(
    parser: argparse.ArgumentParser,
    config: AgentConfig,
    backend_url: str | None,
    agent_id: str | None,
) -> ConnectionConfig:
    if backend_url is None or agent_id is None:
        parser.error("backend URL and agent ID are required for pairing")
    try:
        return ConnectionConfig(
            backendUrl=backend_url,
            agentId=agent_id,
            allowInsecure=config.allow_insecure,
        )
    except ValueError:
        parser.error("invalid MatoLoop Agent connection settings")


def _pairing_inputs(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
    config: AgentConfig,
) -> tuple[ConnectionConfig, str]:
    backend_url = (
        arguments.backend_url or _environment("MATOLOOP_AGENT_BACKEND_URL") or config.backend_url
    )
    agent_id = arguments.agent_id or _environment("MATOLOOP_AGENT_ID") or config.agent_id
    pairing_token = select_pairing_token(arguments.pairing_token)

    if sys.stdin.isatty():
        if not arguments.backend_url:
            entered = input(
                f"Backend WebSocket URL{f' [{backend_url}]' if backend_url else ''}: "
            ).strip()
            backend_url = entered or backend_url
        entered = input(f"Agent ID{f' [{agent_id}]' if agent_id else ''}: ").strip()
        agent_id = entered or agent_id
        entered = getpass.getpass(
            f"Pairing token{' [from environment]' if pairing_token else ''}: "
        )
        pairing_token = entered or pairing_token

    connection = _connection(parser, config, backend_url, agent_id)
    if pairing_token is None:
        parser.error("pairing token is required for pairing")
    return connection, pairing_token


def _automatic_inputs(
    parser: argparse.ArgumentParser,
    arguments: argparse.Namespace,
    config: AgentConfig,
) -> tuple[ConnectionConfig | None, str | None]:
    environment_token = _environment("MATOLOOP_AGENT_PAIRING_TOKEN")
    if environment_token is not None:
        return (
            _connection(
                parser,
                config,
                _environment("MATOLOOP_AGENT_BACKEND_URL"),
                _environment("MATOLOOP_AGENT_ID"),
            ),
            environment_token,
        )

    pairing_token = select_pairing_token(arguments.pairing_token)
    if pairing_token is None:
        return None, None
    return (
        _connection(
            parser,
            config,
            arguments.backend_url
            or _environment("MATOLOOP_AGENT_BACKEND_URL")
            or config.backend_url,
            arguments.agent_id or _environment("MATOLOOP_AGENT_ID") or config.agent_id,
        ),
        pairing_token,
    )


async def _execute(
    command: str,
    config: AgentConfig,
    connection: ConnectionConfig | None,
    pairing_token: str | None,
) -> None:
    waiting = False
    while True:
        try:
            credential = await resolve_credential(
                data_dir=config.data_dir,
                backend_url=connection.backend_url if connection else None,
                agent_id=connection.agent_id if connection else None,
                pairing_token=pairing_token,
            )
            break
        except NotPairedError:
            if command != "run" or pairing_token is not None:
                raise
            if not waiting:
                logging.getLogger(__name__).info("MatoLoop Agent is waiting for pairing")
                waiting = True
            await asyncio.sleep(1)
        except PairingTemporaryError:
            if command != "run":
                raise
            logging.getLogger(__name__).warning(
                "MatoLoop Agent pairing is temporarily unavailable; retrying"
            )
            await asyncio.sleep(30)

    if command == "pair":
        print("MatoLoop Agent paired successfully")
        return
    try:
        effective_connection = ConnectionConfig(
            backendUrl=credential.backend_url,
            agentId=credential.agent_id,
            allowInsecure=config.allow_insecure,
        )
    except ValueError:
        raise CredentialError("MatoLoop Agent stored connection settings are invalid") from None
    config.backend_url = effective_connection.backend_url
    config.agent_id = effective_connection.agent_id

    async def reload_access_key() -> str:
        refreshed: ResolvedCredential = await resolve_credential(
            data_dir=config.data_dir,
            backend_url=None,
            agent_id=None,
            pairing_token=None,
        )
        return refreshed.access_key

    await MatoLoopAgent(config, credential.access_key).run_forever(reload_access_key)


def _run(coroutine: Coroutine) -> None:
    stopping = False
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGTERM, signal.SIGINT)}
    # asyncio.run also waits for cancellation-resistant requests and executor threads.
    deadline = threading.Timer(25, os._exit, args=(1,))
    deadline.daemon = True

    async def run() -> None:
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


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] in {
        "browser-worker",
        "browser-install",
        "browser-install-worker",
    }:
        if sys.argv[1] == "browser-install":
            from .browser import finish_setup

            raise SystemExit(asyncio.run(finish_setup()))
        elif sys.argv[1] == "browser-install-worker":
            from playwright.__main__ import main as install_playwright

            if os.getuid() == 0:
                raise SystemExit(1)
            sys.argv = ["playwright", "install", "--no-shell", "chromium"]
            install_playwright()
        else:
            from .browser_worker import serve

            asyncio.run(serve(Path(sys.argv[2])))
        return
    parser = argparse.ArgumentParser(description="Standalone MatoLoop Agent")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("run", "pair", "cache-clear"),
        default="run",
    )
    parser.add_argument("--config", type=Path, default=_default_config_path())
    parser.add_argument("--backend-url")
    parser.add_argument("--agent-id")
    parser.add_argument("--pairing-token")
    parser.add_argument(
        "--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO"
    )
    arguments = parser.parse_args()
    logging.basicConfig(
        level=arguments.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for name in ("websockets", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    try:
        config = AgentConfig.load(arguments.config)
        if arguments.command == "cache-clear":
            SessionCache(config.data_dir, config.max_session_cache_bytes).clear()
            return
    except (OSError, ValueError):
        parser.error("invalid or unavailable MatoLoop Agent configuration")
    try:
        if arguments.command == "pair":
            connection, pairing_token = _pairing_inputs(parser, arguments, config)
        else:
            connection, pairing_token = _automatic_inputs(parser, arguments, config)
        _run(_execute(arguments.command, config, connection, pairing_token))
    except CredentialError as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
