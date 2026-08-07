from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID


class ApprovalCoordinator:
    """Coordinates approvals inside the single supported backend process."""

    def __init__(self) -> None:
        self._waiters: dict[UUID, asyncio.Future[Any]] = {}
        self._states: dict[UUID, str] = {}

    def register(self, approval_id: UUID) -> asyncio.Future[Any]:
        existing = self._waiters.get(approval_id)
        if existing is not None and not existing.done():
            raise RuntimeError("approval waiter already exists")
        waiter = asyncio.get_running_loop().create_future()
        self._waiters[approval_id] = waiter
        self._states[approval_id] = "waiting"
        return waiter

    def is_waiting(self, approval_id: UUID) -> bool:
        waiter = self._waiters.get(approval_id)
        return (
            waiter is not None
            and not waiter.done()
            and self._states.get(approval_id) == "waiting"
        )

    def claim_resolution(self, approval_id: UUID) -> bool:
        if not self.is_waiting(approval_id):
            return False
        self._states[approval_id] = "resolving"
        return True

    def release_resolution(self, approval_id: UUID) -> None:
        waiter = self._waiters.get(approval_id)
        if waiter is not None and not waiter.done() and self._states.get(approval_id) == "resolving":
            self._states[approval_id] = "waiting"

    def claim_expiration(self, approval_id: UUID) -> bool:
        if not self.is_waiting(approval_id):
            return False
        self._states[approval_id] = "expiring"
        return True

    def complete_resolution(self, approval_id: UUID, decision: Any) -> bool:
        waiter = self._waiters.get(approval_id)
        if (
            waiter is None
            or waiter.done()
            or self._states.get(approval_id) != "resolving"
        ):
            return False
        waiter.set_result(decision)
        return True

    def resolve(self, approval_id: UUID, decision: Any) -> bool:
        if not self.claim_resolution(approval_id):
            return False
        return self.complete_resolution(approval_id, decision)

    def discard(self, approval_id: UUID) -> None:
        self._waiters.pop(approval_id, None)
        self._states.pop(approval_id, None)


approval_coordinator = ApprovalCoordinator()
question_coordinator = ApprovalCoordinator()
