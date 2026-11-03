"""Bounded applied-change details for the owner-facing Dreaming run history."""

from sqlalchemy import update

from mato_loop_backend.models import MemoryDreamChange


def memory_snapshot(item):
    return {"id": str(item.id), "content": item.content, "category": item.category, "revision": item.revision}


def record_change(db, run_id, action, *, before=(), item=None):
    db.add(MemoryDreamChange(
        dream_run_id=run_id, memory_id=item.id if item is not None else None,
        action=action, before=list(before) or None,
        after=memory_snapshot(item) if item is not None else None,
    ))


async def forget_memory_history(db, memory_id):
    await db.execute(update(MemoryDreamChange).where(MemoryDreamChange.memory_id == memory_id).values(
        before=None, after=None,
    ))


async def merge_memory_history(db, memory_ids, survivor_id):
    await db.execute(update(MemoryDreamChange).where(MemoryDreamChange.memory_id.in_(memory_ids)).values(
        memory_id=survivor_id,
    ))
