"""Shared owner, visibility and trust checks for durable session context."""

from uuid import UUID

from sqlalchemy import or_, select

from mato_loop_backend.models import ChatSession
from mato_loop_backend.permissions.policy import session_permission
from mato_loop_backend.sessions.context_state import session_context_state
from mato_loop_backend.settings.models import TrustLevel


async def context_session_permission(db, session, target_id: UUID, trust_level):
    target = await db.scalar(select(ChatSession).where(
        ChatSession.id == target_id, ChatSession.user_id == session.user_id,
        or_(ChatSession.session_type == "chat", ChatSession.id == session.id),
        or_(ChatSession.parent_session_id.is_(None), ChatSession.parent_session_id == session.id,
            ChatSession.id == session.id),
    ).execution_options(populate_existing=True))
    if not isinstance(target, ChatSession):
        target = None
    private = target is not None and trust_level == TrustLevel.PUBLIC and (
        (target_id != session.id and target.trust_level != "public")
        or (bool(session_context_state(target).blocks) and session_context_state(target).trust_level != "public")
    )
    return target, session_permission(target_id, private_access=private)
