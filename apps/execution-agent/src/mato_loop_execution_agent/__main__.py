from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from .cache import SessionCache
from .config import AgentConfig
from .connection import ExecutionAgent


def main() -> None:
    parser = argparse.ArgumentParser(description="MatoLoop standalone execution agent")
    parser.add_argument("command", nargs="?", choices=("run", "cache-clear"), default="run")
    parser.add_argument("--config", type=Path, required=True)
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
        agent = ExecutionAgent(config)
    except (OSError, ValueError):
        parser.error("invalid or unavailable execution agent configuration")
    try:
        asyncio.run(agent.run_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
