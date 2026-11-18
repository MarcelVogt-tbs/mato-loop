from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.messaging.policy import channel_provider_allowed
from mato_loop_backend.models import (
    AgentRun,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    UserAccount,
)


def enqueue_text(
    db: AsyncSession,
    endpoint: MessagingEndpoint,
    *,
    destination: str,
    key: str,
    text: str,
    inbox_id: UUID | None = None,
    allow_unpaired: bool = False,
) -> None:
    db.add(
        MessagingOutbox(
            endpoint_id=endpoint.id,
            inbox_id=inbox_id,
            destination=destination,
            deduplication_key=key,
            payload={"text": text, "allowUnpaired": allow_unpaired},
        )
    )


async def reconcile_runs(provider: str = "telegram") -> int:
    async with AsyncSessionFactory() as db:
        allowed = await channel_provider_allowed(db, provider, lock=True)
        rows = list(
            (
                await db.execute(
                    select(MessagingInbox, MessagingEndpoint, AgentRun)
                    .join(MessagingEndpoint, MessagingEndpoint.id == MessagingInbox.endpoint_id)
                    .join(UserAccount, UserAccount.id == MessagingEndpoint.user_id)
                    .join(AgentRun, AgentRun.id == MessagingInbox.agent_run_id)
                    .where(
                        MessagingInbox.status == "completed",
                        MessagingEndpoint.provider == provider,
                        AgentRun.status.in_(("completed", "failed", "cancelled")),
                        UserAccount.is_active.is_(True),
                    )
                    .with_for_update(of=MessagingInbox, skip_locked=True)
                )
            ).all()
        )
        count = 0
        for inbox, endpoint, run in rows:
            if await db.scalar(
                select(MessagingOutbox.id).where(
                    MessagingOutbox.endpoint_id == endpoint.id,
                    MessagingOutbox.deduplication_key == f"run:{run.id}",
                )
            ):
                continue
            result = await db.get(Message, run.result_message_id) if run.result_message_id else None
            text = (
                canonical_assistant_content(result.content).strip()
                if run.status == "completed" and result is not None
                else "Die Antwort wurde abgebrochen."
                if run.status == "cancelled"
                else (
                    "MatoLoop wurde neu gestartet. Dein Auftrag wurde beendet; "
                    "offene Rückfragen und Freigaben dazu sind nicht mehr gültig."
                )
                if run.error == "agent.backendRestarted"
                else "Der Agent-Auftrag konnte nicht abgeschlossen werden."
            )
            db.add(
                MessagingOutbox(
                    endpoint_id=endpoint.id,
                    inbox_id=inbox.id,
                    source_message_id=result.id if result else None,
                    destination=inbox.payload["chatId"],
                    deduplication_key=f"run:{run.id}",
                    payload={
                        "text": text,
                        "includeAttachments": result is not None,
                        **(
                            {"contentFormat": "markdown"}
                            if run.status == "completed" and result is not None
                            else {}
                        ),
                    },
                    status="pending" if allowed else "failed",
                    error_code=None if allowed else "channel.providerDisabled",
                )
            )
            count += 1
        await db.commit()
        return count
