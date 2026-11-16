"""Shared session lifecycle predicates; ownership and trust remain separate."""
from sqlalchemy import select

from mato_loop_backend.models import ChatSession, Project


def archived_session_clause():
    return ChatSession.archived_at.is_not(None) | select(Project.id).where(
        Project.id == ChatSession.project_id, Project.archived_at.is_not(None),
    ).correlate(ChatSession).exists()


def deleted_session_clause():
    roots = select(ChatSession.id).where(
        ChatSession.delete_at.is_not(None) | ChatSession.project_id.in_(
            select(Project.id).where(Project.delete_at.is_not(None)),
        ),
    ).correlate(None).cte(recursive=True)
    roots = roots.union(select(ChatSession.id).join(roots, ChatSession.parent_session_id == roots.c.id))
    return ChatSession.id.in_(select(roots.c.id))


async def is_session_deleted(db, session):
    return await db.scalar(select(deleted_session_clause()).where(ChatSession.id == session.id)) is True


async def require_session_available(db, session):
    from .service import SessionServiceError

    if await is_session_deleted(db, session):
        raise SessionServiceError(409, "session.inTrash")


async def session_work_blocked(db, session):
    """Return a domain error code, leaving transport-specific exceptions to callers."""
    if await is_session_deleted(db, session):
        return "session.inTrash"
    if session.archived_at is not None or (session.project_id is not None and await db.scalar(
        select(Project.id).where(Project.id == session.project_id, Project.archived_at.is_not(None)),
    ) is not None):
        return "session.archived"
    return None
