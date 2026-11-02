"""Shared lifecycle for independent background processors."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class BackgroundProcessing:
    _tasks: list[asyncio.Task[None]] = []

    @classmethod
    def start(cls) -> None:
        if cls._tasks:
            return
        from mato_loop_backend.automations import process_pending as automations
        from mato_loop_backend.memory.dreaming import process_pending as dreaming
        from mato_loop_backend.sessions.message_queue import process_pending as messages

        cls._tasks = [asyncio.create_task(cls._repeat(name, process, interval)) for name, process, interval in (
            ("messages", messages, 1), ("automations", automations, 5), ("dreaming", dreaming, 30),
        )]

    @classmethod
    async def stop(cls) -> None:
        from mato_loop_backend.sessions import message_queue

        tasks = [*cls._tasks, *message_queue._deliveries.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cls._tasks = []

    @staticmethod
    async def _repeat(name: str, process: Callable[[], Awaitable[None]], interval: float) -> None:
        while True:
            try:
                await process()
            except Exception as exc:
                # Exception payloads may contain pending messages or upload bytes.
                logger.error("Background processing failed: %s (%s)", name, type(exc).__name__)
            await asyncio.sleep(interval)
