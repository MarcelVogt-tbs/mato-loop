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
        self._subagents: dict[UUID, tuple[UUID, dict[str, Any]]] = {}

    def register_subagent(
        self, run_id: UUID, parent_run_id: UUID, summary: dict[str, Any]
    ) -> None:
        self._subagents[run_id] = (parent_run_id, dict(summary))

    def unregister_subagent(self, run_id: UUID) -> None:
        self._subagents.pop(run_id, None)

    async def publish(self, run_id: UUID, event: dict[str, Any]) -> None:
        if event.get("type") in {"reasoning.updated", "context.summary.updated"}:
            self._history[run_id] = [
                item
                for item in self._history[run_id]
                if item.get("type") != event.get("type")
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

        parent = self._subagents.get(run_id)
        if parent is not None:
            parent_run_id, summary = parent
            event_type = event.get("type", "")
            request_event = event_type.startswith(("approval.", "question."))
            if request_event:
                provenance = {
                    "subagentSessionId": summary["sessionId"],
                    "subagentTitle": summary["title"],
                }
                forwarded = {**event, **provenance}
                for key in ("approval", "question"):
                    if isinstance(event.get(key), dict):
                        forwarded[key] = {
                            **event[key],
                            "payload": {**(event[key].get("payload") or {}), **provenance},
                        }
                await self.publish(parent_run_id, forwarded)
            if request_event or event_type in {
                "run.started", "run.timing", "run.completed", "run.failed", "run.cancelled",
            }:
                # This is an invalidation hint; clients reload the durable child status.
                await self.publish(parent_run_id, {
                    "type": "subagent.updated", "sessionId": summary["sessionId"],
                    "runId": str(run_id), "toolCallId": summary["toolCallId"],
                })

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
