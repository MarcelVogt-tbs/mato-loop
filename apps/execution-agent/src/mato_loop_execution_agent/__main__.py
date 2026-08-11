from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .cache import SessionCache
from .config import AgentConfig
from .connection import ExecutionAgent
from .credentials import CredentialError, resolve_credential, select_pairing_token


def main() -> None:
    parser = argparse.ArgumentParser(description="MatoLoop standalone execution agent")
    parser.add_argument("command", nargs="?", choices=("run", "cache-clear"), default="run")
    parser.add_argument("--config", type=Path, required=True)
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
        parser.error("invalid or unavailable execution agent configuration")
    try:
        pairing_token = select_pairing_token(config.token, arguments.pairing_token)

        async def run() -> None:
            access_key = await resolve_credential(
                data_dir=config.data_dir,
                backend_url=config.backend_url,
                agent_id=config.agent_id,
                pairing_token=pairing_token,
            )
            await ExecutionAgent(config, access_key).run_forever()

        asyncio.run(run())
    except CredentialError as exc:
        logging.getLogger(__name__).error("%s", exc)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
