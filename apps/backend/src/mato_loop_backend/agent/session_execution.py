"""Serialize root execution while keeping interaction waiters alive.

Like the approval coordinator, these leases belong to the single backend process.
They schedule work; they never authorize it or replace post-approval checks.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import UUID


@dataclass
class ExecutionSlot:
    session_id: UUID
    lock: asyncio.Lock
    held: bool = False
    parked: bool = False


_slots: dict[UUID, ExecutionSlot] = {}
_locks: dict[UUID, asyncio.Lock] = {}


async def acquire(run_id: UUID, session_id: UUID):
    lock = _locks.setdefault(session_id, asyncio.Lock())
    slot = ExecutionSlot(session_id, lock)
    _slots[run_id] = slot
    await lock.acquire()
    slot.held = True


def release(run_id: UUID):
    slot = _slots.pop(run_id, None)
    if slot is None:
        return
    if slot.held:
        slot.lock.release()
    if not any(other.session_id == slot.session_id for other in _slots.values()):
        _locks.pop(slot.session_id, None)


def parked_runs() -> tuple[UUID, ...]:
    return tuple(run_id for run_id, slot in _slots.items() if slot.parked)


def has_other_runs(run_id: UUID, session_id: UUID) -> bool:
    return any(key != run_id and slot.session_id == session_id for key, slot in _slots.items())


def session_runs(session_id: UUID) -> tuple[UUID, ...]:
    return tuple(key for key, slot in _slots.items() if slot.session_id == session_id)


@asynccontextmanager
async def interaction_wait(run_id: UUID):
    """Yield only at an actual committed interaction, then serialize its continuation."""
    slot = _slots.get(run_id)
    from mato_loop_backend.agent.subagents import has_children

    yielded = slot is not None and slot.held
    yielded = yielded and not has_children(run_id)
    if yielded:
        slot.parked = True
        slot.held = False
        slot.lock.release()
    try:
        yield
    except asyncio.CancelledError:
        # Cancellation performs no approved continuation and must not wait for
        # another root (owner deletion/shutdown may be cancelling it next).
        if yielded:
            slot.parked = False
        raise
    finally:
        if yielded and slot.parked:
            # Close admission before waiting for an executing review to finish.
            slot.parked = False
            await slot.lock.acquire()
            slot.held = True
