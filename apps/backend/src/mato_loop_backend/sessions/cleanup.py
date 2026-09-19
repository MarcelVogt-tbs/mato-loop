from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.session_secrets import session_secrets
from mato_loop_backend.models import ChatSession
from mato_loop_backend.session_storage import SessionStorage

logger = logging.getLogger(__name__)


async def descendant_sessions(
    db: AsyncSession,
    session_ids: Sequence[UUID],
    *,
    spawned_by_run_ids: Sequence[UUID] | None = None,
) -> list[tuple[UUID, UUID | None]]:
    statement = select(ChatSession.id, ChatSession.project_id).where(
        ChatSession.parent_session_id.in_(session_ids)
    )
    if spawned_by_run_ids is not None:
        statement = statement.where(ChatSession.spawned_by_run_id.in_(spawned_by_run_ids))
    descendants = statement.cte("descendant_sessions", recursive=True)
    descendants = descendants.union(
        select(ChatSession.id, ChatSession.project_id).join(
            descendants, ChatSession.parent_session_id == descendants.c.id
        )
    )
    return list((await db.execute(select(descendants))).all())


@asynccontextmanager
async def deleting_child_sessions(
    db: AsyncSession,
    session_id: UUID,
    *,
    spawned_by_run_ids: Sequence[UUID] | None = None,
) -> AsyncIterator[None]:
    """Stage child storage around the caller's deletion and commit, after tasks stop."""
    children = await descendant_sessions(
        db, [session_id], spawned_by_run_ids=spawned_by_run_ids
    )
    storage = SessionStorage()
    trashed = []
    try:
        for child_id, project_id in children:
            task = asyncio.create_task(
                asyncio.to_thread(storage.trash_session, child_id, project_id)
            )
            try:
                tree = await asyncio.shield(task)
            except asyncio.CancelledError:
                tree = await task
                trashed.append((child_id, project_id, tree))
                raise
            trashed.append((child_id, project_id, tree))
        if children:
            await db.execute(
                delete(ChatSession)
                .where(ChatSession.id.in_([child_id for child_id, _ in children]))
                .execution_options(synchronize_session=False)
            )
        yield
    except BaseException:
        await db.rollback()
        remaining = set((await db.scalars(
            select(ChatSession.id).where(ChatSession.id.in_([child_id for child_id, _, _ in trashed]))
        )).all()) if trashed else set()
        for child_id, project_id, tree in trashed:
            if tree is not None:
                try:
                    if child_id in remaining:
                        await asyncio.to_thread(storage.restore_session, child_id, tree, project_id)
                    else:
                        await asyncio.to_thread(storage.purge_trashed, tree)
                except BaseException:
                    logger.exception("Could not restore child session storage")
        raise
    else:
        session_secrets.clear_sessions([child_id for child_id, _ in children])
        for _, _, tree in trashed:
            try:
                await asyncio.to_thread(storage.purge_trashed, tree)
            except BaseException:
                logger.exception("Could not purge child session storage")
