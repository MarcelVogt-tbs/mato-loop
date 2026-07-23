from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID


class RunEventBroker:
    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._history: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

    async def publish(self, run_id: UUID, event: dict[str, Any]) -> None:
        if event.get("type") == "reasoning.updated":
            self._history[run_id] = [
                item for item in self._history[run_id] if item.get("type") != "reasoning.updated"
            ]
        self._history[run_id].append(event)
        self._history[run_id] = self._history[run_id][-200:]
        for queue in tuple(self._subscribers[run_id]):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
        if event.get("type") in {"run.completed", "run.failed", "run.cancelled"}:
            task = asyncio.create_task(self._cleanup(run_id))
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)

    async def _cleanup(self, run_id: UUID) -> None:
        await asyncio.sleep(60)
        self._history.pop(run_id, None)
        if not self._subscribers.get(run_id):
            self._subscribers.pop(run_id, None)

    async def subscribe(self, run_id: UUID) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=500)
        for event in self._history.get(run_id, []):
            await queue.put(event)
        self._subscribers[run_id].add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield {"type": "keepalive"}
        finally:
            self._subscribers[run_id].discard(queue)
            if not self._subscribers[run_id]:
                self._subscribers.pop(run_id, None)


run_events = RunEventBroker()
