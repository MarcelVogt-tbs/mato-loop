"""Run the ordinary purge workflows for expired trash entries."""
import logging
from datetime import UTC, datetime
from functools import partial

from sqlalchemy import select

from mato_loop_backend.api.routes.projects import project_delete_impact_token, purge_project
from mato_loop_backend.automations import automation_impact_token
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.models import Automation, ChatSession, Project
from mato_loop_backend.sessions.service import delete_session, owned_session

logger = logging.getLogger(__name__)


async def purge_expired():
    async with AsyncSessionFactory() as db:
        due = [(model, item_id, owner_id) for model in (Project, ChatSession)
               for item_id, owner_id in (await db.execute(select(model.id, model.user_id).where(
                   model.delete_at <= datetime.now(UTC),
                   *([ChatSession.parent_session_id.is_(None)] if model is ChatSession else []),
               ))).all()]
    for model, item_id, owner_id in due:
        try:
            async with AsyncSessionFactory() as db:
                session_ids = list(await db.scalars(select(ChatSession.id).where(
                    ChatSession.project_id == item_id, ChatSession.parent_session_id.is_(None),
                ))) if model is Project else [item_id]
                linked = list(await db.scalars(select(Automation).where(
                    Automation.user_id == owner_id, Automation.execution_session_id.in_(session_ids),
                )))
                if model is Project:
                    await purge_project(item_id, owner_id, db, automation_mode="deleteAutomations",
                                        automation_impact=project_delete_impact_token(session_ids, linked), expired_only=True)
                else:
                    await delete_session(item_id, owner_id, db, automation_mode="deleteAutomations",
                                         automation_impact=automation_impact_token(linked), storage_factory=SessionStorage,
                                         owned_session_query=partial(owned_session, include_deleted=True), expired_only=True)
        except Exception:
            logger.exception("Expired trash entry could not be deleted: %s", item_id)
