from __future__ import annotations

import asyncio
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from matoloop_teams_protocol import (
    MAX_FILE_BYTES,
    Blob,
    Delivery,
    Event,
    Identity,
    PollResult,
    Receipt,
    StreamPreview,
)
from matoloop_teams_protocol.onboarding import pairing_card
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, selectinload

from mato_loop_backend.agent.activity import run_activity_label
from mato_loop_backend.agent.question_decisions import QuestionDecisionError
from mato_loop_backend.agent.run_contracts import AgentRunError
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.events import run_events
from mato_loop_backend.media.attachments import (
    AttachmentError,
    AttachmentService,
    classify_attachment,
    processing_limits,
)
from mato_loop_backend.media.processing import AttachmentProcessingError
from mato_loop_backend.messaging.approvals import (
    MessagingApprovalError,
    decode_approval_callback,
    resolve_approval_action,
)
from mato_loop_backend.messaging.endpoints import pairing_hash
from mato_loop_backend.messaging.formatting import (
    register_channel_text_renderer,
    render_channel_text,
)
from mato_loop_backend.messaging.outbox import enqueue_text, reconcile_runs
from mato_loop_backend.messaging.policy import channel_provider_allowed
from mato_loop_backend.messaging.questions import (
    MessagingQuestionError,
    QuestionInteraction,
    decode_question_callback,
    resolve_question_interaction,
)
from mato_loop_backend.messaging.sessions import (
    ChannelMessageError,
    recent_chat_sessions,
    session_command,
    session_command_parts,
    start_message,
)
from mato_loop_backend.messaging.teams_cards import (
    card_revision,
    help_card,
    interaction_card,
    session_list_card,
)
from mato_loop_backend.messaging.teams_client import (
    TeamsRelayClient,
    TeamsServiceError,
    relay_client,
)
from mato_loop_backend.messaging.teams_formatting import render_teams_text
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    UserAccount,
)
from mato_loop_backend.notifications import NotificationDraft, NotificationService
from mato_loop_backend.permissions.decisions import ApprovalDecisionError
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.public_url import normalize_public_base_url
from mato_loop_backend.settings import InstanceLimits, SettingsStore
from mato_loop_backend.settings.models import TeamsChannelSettings

logger = logging.getLogger(__name__)
register_channel_text_renderer("teams", render_teams_text)


class TeamsAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outboxId: UUID
    revision: str = Field(min_length=64, max_length=64)
    callback: str = Field(min_length=1, max_length=100)
    answer: str | None = Field(default=None, max_length=20_000)
    customAnswer: str | None = Field(default=None, max_length=20_000)


def bound_identity(endpoint: MessagingEndpoint, settings: TeamsChannelSettings) -> Identity:
    state = endpoint.channel_state or {}
    if state.get("revision") != settings.revision or not state.get("bindingId"):
        raise TeamsServiceError("channel.pairingChanged")
    try:
        identity = Identity.model_validate(state.get("identity"))
    except ValidationError:
        raise TeamsServiceError("channel.pairingChanged") from None
    if (
        identity.tenant_id != settings.tenant_id
        or identity.app_id != settings.app_id
        or identity.conversation_id != endpoint.paired_chat_id
        or str(identity.user_id) != endpoint.paired_user_id
        or endpoint.external_identity != f"{identity.tenant_id}:{identity.user_id}"
    ):
        raise TeamsServiceError("channel.pairingChanged")
    return identity


async def available_endpoint(
    db: AsyncSession,
    endpoint_id: UUID,
    settings: TeamsChannelSettings,
    *,
    require_bound: bool = True,
) -> MessagingEndpoint:
    if not await channel_provider_allowed(db, "teams", lock=True):
        raise TeamsServiceError("channel.providerDisabled")
    current = await SettingsStore.load(db, TeamsChannelSettings)
    if current.revision != settings.revision:
        raise TeamsServiceError("channel.pairingChanged")
    endpoint = await db.scalar(
        select(MessagingEndpoint)
        .join(
            UserAccount,
            UserAccount.id == MessagingEndpoint.user_id,
        )
        .where(
            MessagingEndpoint.id == endpoint_id,
            MessagingEndpoint.provider == "teams",
            MessagingEndpoint.enabled.is_(True),
            MessagingEndpoint.deleted_at.is_(None),
            UserAccount.is_active.is_(True),
        )
        .with_for_update(of=MessagingEndpoint)
    )
    if endpoint is None:
        raise TeamsServiceError("channel.pairingChanged")
    if require_bound:
        bound_identity(endpoint, settings)
    return endpoint


async def ingest_event(
    event: Event,
    generation: UUID,
    settings: TeamsChannelSettings,
) -> bool:
    async with AsyncSessionFactory() as db:
        # A committed inbox is the admission receipt, even if its run has since finished.
        existing = await db.get(MessagingInbox, event.id)
        if existing is not None:
            return existing.payload.get("event") == str(event.id)
        if (
            event.expires_at <= datetime.now(UTC)
            or event.generation != generation
            or event.identity.app_id != settings.app_id
            or event.identity.tenant_id != settings.tenant_id
        ):
            return False
        if not await channel_provider_allowed(db, "teams", lock=True):
            return False
        current = await SettingsStore.load(db, TeamsChannelSettings)
        if current.revision != settings.revision:
            return False
        text = event.text.strip()
        command, _, code = text.partition(" ")
        if event.kind == "message" and command in {"/pair", "/start"} and code:
            endpoint = await db.scalar(
                select(MessagingEndpoint)
                .join(
                    UserAccount,
                    UserAccount.id == MessagingEndpoint.user_id,
                )
                .where(
                    MessagingEndpoint.provider == "teams",
                    MessagingEndpoint.enabled.is_(True),
                    MessagingEndpoint.deleted_at.is_(None),
                    UserAccount.is_active.is_(True),
                    MessagingEndpoint.pairing_code_hash == pairing_hash(code),
                    MessagingEndpoint.pairing_expires_at > datetime.now(UTC),
                )
                .with_for_update(of=MessagingEndpoint)
            )
            if (
                endpoint is None
                or event.expires_at <= datetime.now(UTC)
                or endpoint.pairing_expires_at <= datetime.now(UTC)
            ):
                return False
            state = endpoint.channel_state or {}
            candidate = state.get("candidate")
            identity = event.identity.model_dump(mode="json")
            if candidate and candidate != identity:
                return False
            endpoint.channel_state = {"candidate": identity, "revision": settings.revision}
            endpoint.paired_display_name = event.identity.display_name
            db.add(
                MessagingInbox(
                    id=event.id,
                    endpoint_id=endpoint.id,
                    external_event_id=str(event.id),
                    external_message_id=event.message_id,
                    status="completed",
                    payload={"event": str(event.id), "pairing": True},
                )
            )
            await db.commit()
            return True
        endpoint = await db.scalar(
            select(MessagingEndpoint).where(
                MessagingEndpoint.provider == "teams",
                MessagingEndpoint.external_identity
                == f"{event.identity.tenant_id}:{event.identity.user_id}",
                MessagingEndpoint.deleted_at.is_(None),
            )
        )
        if endpoint is None:
            if event.kind == "message" and event.expires_at > datetime.now(UTC):
                raise TeamsServiceError("channel.pairingRequired")
            return False
        endpoint = await available_endpoint(db, endpoint.id, settings)
        if event.expires_at <= datetime.now(UTC):
            return False
        identity = bound_identity(endpoint, settings)
        if (
            identity.user_id != event.identity.user_id
            or identity.conversation_id != event.identity.conversation_id
            or event.received_at < datetime.fromisoformat(endpoint.channel_state["boundAt"])
        ):
            return False
        await db.execute(
            insert(MessagingInbox)
            .values(
                id=event.id,
                endpoint_id=endpoint.id,
                external_event_id=str(event.id),
                external_message_id=event.message_id,
                payload={
                    "event": str(event.id),
                    "chatId": identity.conversation_id,
                    "userId": str(identity.user_id),
                    "bindingId": endpoint.channel_state["bindingId"],
                    "envelope": event.model_dump(mode="json"),
                },
            )
            .on_conflict_do_nothing(index_elements=[MessagingInbox.id])
        )
        endpoint.last_polled_at = datetime.now(UTC)
        endpoint.last_error_code = None
        await db.commit()
        return True


async def process_action(
    db: AsyncSession,
    endpoint: MessagingEndpoint,
    inbox: MessagingInbox,
    event: Event,
) -> None:
    action = TeamsAction.model_validate(event.action)
    item = await db.get(MessagingOutbox, action.outboxId, with_for_update=True)
    if (
        item is None
        or item.endpoint_id != endpoint.id
        or item.status != "delivered"
        or item.destination != event.identity.conversation_id
        or event.source_message_id not in item.external_message_ids
        or not hmac.compare_digest(action.revision, card_revision(item.payload))
    ):
        raise TeamsServiceError("channel.pairingChanged")
    session_picker = item.payload.get("kind") == "teamsSessionPicker"
    if session_picker:
        origin = await db.get(MessagingInbox, item.inbox_id) if item.inbox_id else None
        if origin is None or origin.endpoint_id != endpoint.id:
            raise TeamsServiceError("channel.pairingChanged")
    else:
        if not item.payload.get("runId"):
            raise TeamsServiceError("channel.pairingChanged")
        origin = await db.scalar(
            select(MessagingInbox).where(
                MessagingInbox.agent_run_id == UUID(item.payload["runId"]),
                MessagingInbox.endpoint_id == endpoint.id,
            )
        )
    if (
        origin is None
        or origin.status != "completed"
        or origin.payload.get("bindingId") != endpoint.channel_state.get("bindingId")
    ):
        raise TeamsServiceError("channel.pairingChanged")
    if session_picker:
        prefix, _, session_id = action.callback.partition(":")
        if prefix != "session" or not any(
            session["id"] == session_id for session in item.payload["sessions"]
        ):
            raise TeamsServiceError("channel.pairingChanged")
        response, _ = await session_command(db, inbox, endpoint, f"/use {session_id}")
    elif action.callback.startswith("a1:"):
        approval_id, decision = decode_approval_callback(action.callback)
        await resolve_approval_action(db, endpoint, approval_id, decision, event.source_message_id)
        response = "Freigabe abgelehnt." if decision == "deny" else "Freigabe wurde übernommen."
    else:
        if action.callback.startswith("qtext:"):
            question_id = UUID(action.callback.split(":", 1)[1])
            interaction = QuestionInteraction(action="text", text=(action.answer or "").strip())
        elif action.callback.startswith("qchoices:"):
            question_id = UUID(action.callback.split(":", 1)[1])
            values = action.answer.split(",") if action.answer else []
            if len(values) > 12 or any(value not in {str(i) for i in range(12)} for value in values):
                raise MessagingQuestionError
            interaction = QuestionInteraction(
                action="choices",
                option_indices=[int(value) for value in values],
                text=(action.customAnswer or "").strip() or None,
            )
        else:
            question_id, interaction = decode_question_callback(action.callback)
        result = await resolve_question_interaction(
            db,
            endpoint,
            question_id,
            interaction,
            event.source_message_id,
            commit=False,
            advance_on_answer=True,
        )
        response = (
            "Rückfrage wurde abgelehnt." if result.status == "rejected" else "Antwort wurde übernommen."
        )
        if result.envelope is not None:
            await db.refresh(item)
            # Keep the canonical question outbox and add the newly rendered card's ID to it.
            revision = card_revision(item.payload)
            db.add(
                MessagingOutbox(
                    endpoint_id=endpoint.id,
                    destination=event.identity.conversation_id,
                    deduplication_key=f"question-update:{inbox.id}",
                    payload={"questionOutboxId": str(item.id), "questionRevision": revision},
                )
            )
            return
    enqueue_text(
        db,
        endpoint,
        destination=event.identity.conversation_id,
        key=f"action-result:{inbox.id}",
        text=response,
        inbox_id=inbox.id,
    )


async def process_inbox(
    inbox_id: UUID, settings: TeamsChannelSettings, client: TeamsRelayClient
) -> None:
    async with AsyncSessionFactory() as db:
        inbox = await db.get(MessagingInbox, inbox_id, with_for_update=True)
        if inbox is None or inbox.status != "processing":
            return
        try:
            endpoint = await available_endpoint(db, inbox.endpoint_id, settings)
            event = Event.model_validate(inbox.payload["envelope"])
            if inbox.payload.get("bindingId") != endpoint.channel_state.get("bindingId"):
                raise TeamsServiceError("channel.pairingChanged")
            command_name = session_command_parts(event.text)[0]
            if event.kind == "action":
                await process_action(db, endpoint, inbox, event)
            elif command_name == "/help":
                db.add(MessagingOutbox(
                    endpoint_id=endpoint.id,
                    inbox_id=inbox.id,
                    destination=event.identity.conversation_id,
                    deduplication_key=f"command:{inbox.id}",
                    payload={"kind": "teamsHelp", "card": help_card()},
                ))
            elif command_name == "/sessions":
                sessions = await recent_chat_sessions(db, endpoint, limit=8)
                db.add(MessagingOutbox(
                    endpoint_id=endpoint.id,
                    inbox_id=inbox.id,
                    destination=event.identity.conversation_id,
                    deduplication_key=f"command:{inbox.id}",
                    payload={"kind": "teamsSessionPicker", "sessions": [
                        {"id": str(session.id), "title": " ".join(session.title.split()) or "Chat"}
                        for session in sessions
                    ]},
                ))
            else:
                command = await session_command(db, inbox, endpoint, event.text)
                if command is not None:
                    response, unpaired = command
                    enqueue_text(
                        db,
                        endpoint,
                        destination=event.identity.conversation_id,
                        key=f"command:{inbox.id}",
                        text=response,
                        inbox_id=inbox.id,
                        allow_unpaired=unpaired,
                    )
                else:
                    existing = await db.get(AgentRun, inbox.id)
                    limits = await SettingsStore.load(db, InstanceLimits)
                    if len(event.blobs) > limits.max_attachments_per_message:
                        raise TeamsServiceError("validation.tooManyItems")
                    attachments, total = [], 0
                    for blob in [] if existing else event.blobs:
                        data = await client.request(
                            "GET",
                            f"blobs/{blob.id}",
                            binary=True,
                            params={"operation": str(event.id)},
                        )
                        total += len(data)
                        if len(data) != blob.size or total > limits.max_message_upload_bytes:
                            raise TeamsServiceError("attachment.tooLarge")
                        attachments.append(
                            classify_attachment(
                                data,
                                blob.mime_type,
                                blob.filename,
                                max_bytes=limits.max_upload_bytes,
                                limits=processing_limits(limits),
                            )
                        )
                    # The runtime may commit as part of normal admission. Recover by the stable run ID.
                    run = existing or await start_message(
                        db, endpoint, inbox, event.text, attachments
                    )
                    endpoint = await available_endpoint(db, endpoint.id, settings)
                    inbox = await db.get(MessagingInbox, inbox_id, populate_existing=True)
                    if inbox.payload.get("bindingId") != endpoint.channel_state.get("bindingId"):
                        raise TeamsServiceError("channel.pairingChanged")
                    inbox.session_id, inbox.input_message_id = run.session_id, run.input_message_id
                    inbox.agent_run_id = run.id
                    endpoint.current_session_id = run.session_id
            inbox.status, inbox.processing_at = "completed", None
            inbox.error_code = None
            await db.commit()
        except (
            TeamsServiceError,
            ChannelMessageError,
            AgentRunError,
            AttachmentError,
            AttachmentProcessingError,
            ProviderError,
            ApprovalDecisionError,
            QuestionDecisionError,
            MessagingApprovalError,
            MessagingQuestionError,
            ValidationError,
            ValueError,
            OSError,
        ) as exc:
            await db.rollback()
            logger.warning(
                "Teams inbox processing failed: %s", getattr(exc, "code", "channel.invalidResponse")
            )
            inbox = await db.get(MessagingInbox, inbox_id, with_for_update=True)
            if inbox is not None:
                inbox.status, inbox.processing_at = "failed", None
                inbox.error_code = getattr(exc, "code", "channel.invalidResponse")
                endpoint = await db.get(MessagingEndpoint, inbox.endpoint_id)
                if endpoint is not None:
                    enqueue_text(
                        db,
                        endpoint,
                        destination=inbox.payload["chatId"],
                        key=f"error:{inbox.id}",
                        inbox_id=inbox.id,
                        text="Die Nachricht oder Entscheidung konnte nicht verarbeitet werden. Prüfe die Session und offene Rückfragen in MatoLoop.",
                    )
                await db.commit()


async def claim_inbox() -> UUID | None:
    async with AsyncSessionFactory() as db:
        active = aliased(MessagingInbox)
        item = await db.scalar(
            select(MessagingInbox)
            .join(
                MessagingEndpoint,
                MessagingEndpoint.id == MessagingInbox.endpoint_id,
            )
            .where(
                MessagingEndpoint.provider == "teams",
                ~exists(
                    select(active.id).where(
                        active.endpoint_id == MessagingInbox.endpoint_id,
                        active.id != MessagingInbox.id,
                        active.status == "processing",
                        active.processing_at >= datetime.now(UTC) - timedelta(minutes=10),
                    )
                ),
                or_(
                    MessagingInbox.status == "pending",
                    (MessagingInbox.status == "processing")
                    & (MessagingInbox.processing_at < datetime.now(UTC) - timedelta(minutes=10)),
                ),
            )
            .order_by(MessagingInbox.created_at)
            .with_for_update(of=[MessagingInbox, MessagingEndpoint], skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        item.status, item.processing_at = "processing", datetime.now(UTC)
        item.attempts += 1
        await db.commit()
        return item.id


async def deliver_outbox(
    item_id: UUID, settings: TeamsChannelSettings, client: TeamsRelayClient
) -> None:
    async with AsyncSessionFactory() as db:
        item = await db.get(MessagingOutbox, item_id, with_for_update=True)
        if item is None or item.status not in {"pending", "processing"}:
            return
        try:
            origin = await db.get(MessagingInbox, item.inbox_id) if item.inbox_id else None
            unpaired = item.payload.get("allowUnpaired") is True
            endpoint = await available_endpoint(
                db, item.endpoint_id, settings, require_bound=not unpaired
            )
            if unpaired:
                event = Event.model_validate(origin.payload["envelope"]) if origin else None
                if (
                    event is None
                    or event.kind != "message"
                    or event.text.strip() != "/unlink"
                    or origin.status != "completed"
                    or endpoint.paired_chat_id is not None
                    or event.identity.app_id != settings.app_id
                    or event.identity.tenant_id != settings.tenant_id
                ):
                    raise TeamsServiceError("channel.pairingChanged")
                identity = event.identity
            else:
                identity = bound_identity(endpoint, settings)
            if item.destination != identity.conversation_id:
                raise TeamsServiceError("channel.pairingChanged")
            payload = item.payload
            run_id = payload.get("runId")
            question_item = None
            if payload.get("questionOutboxId"):
                question_item = await db.get(MessagingOutbox, UUID(payload["questionOutboxId"]))
                if (
                    question_item is None
                    or question_item.endpoint_id != endpoint.id
                    or payload.get("questionRevision") != card_revision(question_item.payload)
                ):
                    raise TeamsServiceError("channel.pairingChanged")
                run_id = question_item.payload.get("runId")
            if run_id and origin is None:
                origin = await db.scalar(
                    select(MessagingInbox).where(
                        MessagingInbox.agent_run_id == UUID(run_id),
                        MessagingInbox.endpoint_id == endpoint.id,
                    )
                )
            if origin is not None and (
                origin.payload.get("bindingId") != endpoint.channel_state.get("bindingId")
                or origin.payload.get("chatId") != item.destination
            ):
                raise TeamsServiceError("channel.pairingChanged")
            if run_id and (origin is None or origin.status != "completed"):
                raise TeamsServiceError("channel.pairingChanged")
            rendering_item = question_item or item
            if payload.get("kind") == "teamsSessionPicker":
                if origin is None or origin.status != "completed":
                    raise TeamsServiceError("channel.pairingChanged")
                card = session_list_card(item)
            elif payload.get("kind") in {"teamsHelp", "teamsSessions"}:
                card = payload["card"]
            else:
                card = interaction_card(rendering_item)
            parts = (
                [Delivery(
                    id=uuid5(item.id, "card"), identity=identity, kind="message", card=card,
                    source_message_id=(
                        origin.external_message_id if run_id and origin and question_item is None
                        else None
                    ),
                )]
                if card
                else [
                    Delivery(
                        id=uuid5(item.id, f"text:{index}"),
                        identity=identity,
                        kind="message",
                        text=part.text,
                        text_format="markdown" if part.syntax == "markdown" else "plain",
                        source_message_id=(
                            origin.external_message_id
                            if index == 0 and origin and origin.agent_run_id
                            and item.deduplication_key == f"run:{origin.agent_run_id}"
                            else None
                        ),
                    )
                    for index, part in enumerate(
                        render_channel_text(
                            "teams",
                            str(payload.get("text") or ""),
                            content_format=payload.get("contentFormat", "plain"),
                        )
                    )
                ]
            )
            if payload.get("includeAttachments") and item.source_message_id:
                source = await db.scalar(
                    select(Message)
                    .join(ChatSession)
                    .where(
                        Message.id == item.source_message_id,
                        ChatSession.user_id == endpoint.user_id,
                    )
                    .options(selectinload(Message.attachments))
                )
                if source is None:
                    raise TeamsServiceError("attachment.notFound")
                for attachment in source.attachments:
                    path = await AttachmentService().verified_path(attachment, source.session_id)
                    size = path.stat().st_size
                    if not 0 < size <= MAX_FILE_BYTES:
                        raise TeamsServiceError("attachment.tooLarge")
                    blob = Blob(
                        id=uuid5(item.id, f"blob:{attachment.id}"),
                        filename=attachment.filename or "attachment",
                        mime_type=attachment.mime_type,
                        size=size,
                    )
                    if blob.mime_type.startswith("image/"):
                        with Image.open(path) as image:
                            inline = (
                                max(image.size) <= 1024
                                and blob.size <= 1_000_000
                                and not getattr(image, "is_animated", False)
                            )
                        if not inline:
                            blob.mime_type = "application/octet-stream"
                    if item.attempts == 1:
                        await client.request(
                            "PUT",
                            f"blobs/{blob.id}",
                            content=path.read_bytes(),
                            params={"operation": str(uuid5(item.id, f"file:{attachment.id}"))},
                        )
                    parts.append(
                        Delivery(
                            id=uuid5(item.id, f"file:{attachment.id}"),
                            identity=identity,
                            kind="file",
                            blob=blob,
                        )
                    )
            ids, awaiting = [], False
            for part in parts:
                result = await client.deliver(part, recovering=item.attempts > 1)
                ids.extend(result.message_ids)
                awaiting |= result.status in {"pending", "offered", "sending"}
            item.external_message_ids = ids
            item.status = "pending" if awaiting else "delivered"
            item.processing_at, item.error_code = None, None
            item.next_attempt_at = datetime.now(UTC) + timedelta(seconds=10) if awaiting else None
            item.delivered_at = None if awaiting else datetime.now(UTC)
            if question_item is not None:
                question_item.external_message_ids = [*question_item.external_message_ids, *ids]
            endpoint.last_error_code = None
            await db.commit()
        except (TeamsServiceError, ValidationError, ValueError, AttachmentError, OSError) as exc:
            code = getattr(exc, "code", "channel.invalidResponse")
            await db.rollback()
            logger.warning("Teams delivery failed: %s", code)
            item = await db.get(MessagingOutbox, item_id, with_for_update=True)
            if item:
                item.status, item.processing_at, item.error_code = "failed", None, code
                endpoint = await db.get(MessagingEndpoint, item.endpoint_id)
                if endpoint:
                    endpoint.last_error_code = code
                    if item.deduplication_key.startswith("run:") and code not in {
                        "channel.fileDeclined", "channel.pairingChanged",
                    }:
                        inbox = await db.get(MessagingInbox, item.inbox_id) if item.inbox_id else None
                        session_id = await db.scalar(select(ChatSession.id).where(
                            ChatSession.id == inbox.session_id,
                            ChatSession.user_id == endpoint.user_id,
                        ).with_for_update(read=True, key_share=True)) if inbox and inbox.session_id else None
                        await NotificationService.create(db, NotificationDraft(
                            user_id=endpoint.user_id,
                            session_id=session_id,
                            kind="channel.deliveryFailed",
                            title="Teams-Zustellung nicht bestätigt",
                            body=(
                                "Prüfe, ob die Antwort in Teams angekommen ist. "
                                "Die Antwort findest du in deinem MatoLoop-Chat."
                            ),
                            deduplication_key=f"teams-delivery:{item.id}",
                        ))
                await db.commit()


async def claim_outbox() -> UUID | None:
    async with AsyncSessionFactory() as db:
        active = aliased(MessagingOutbox)
        item = await db.scalar(
            select(MessagingOutbox)
            .join(
                MessagingEndpoint,
                MessagingEndpoint.id == MessagingOutbox.endpoint_id,
            )
            .where(
                MessagingEndpoint.provider == "teams",
                ~exists(
                    select(active.id).where(
                        active.endpoint_id == MessagingOutbox.endpoint_id,
                        active.id != MessagingOutbox.id,
                        active.status == "processing",
                        active.processing_at >= datetime.now(UTC) - timedelta(minutes=2),
                    )
                ),
                or_(
                    (MessagingOutbox.status == "pending")
                    & or_(
                        MessagingOutbox.next_attempt_at.is_(None),
                        MessagingOutbox.next_attempt_at <= datetime.now(UTC),
                    ),
                    (MessagingOutbox.status == "processing")
                    & (MessagingOutbox.processing_at < datetime.now(UTC) - timedelta(minutes=2)),
                ),
            )
            .order_by(MessagingOutbox.created_at)
            .with_for_update(of=[MessagingOutbox, MessagingEndpoint], skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        item.status, item.processing_at = "processing", datetime.now(UTC)
        item.attempts += 1
        await db.commit()
        return item.id


def rejected_delivery(event: Event, *, needs_pairing: bool, pairing_origin: str | None) -> Delivery:
    card = None
    text = "Die Nachricht wurde nicht angenommen. Prüfe die Teams-Kopplung in MatoLoop; abgelaufene Nachrichten werden nicht nachträglich ausgeführt."
    if needs_pairing:
        origin = normalize_public_base_url(pairing_origin)
        card = pairing_card(origin if origin and origin.startswith("https://") else None)
        text = "Dein Teams-Konto ist noch nicht mit MatoLoop verbunden. Deine Nachricht wurde nicht ausgeführt."
    return Delivery(
        id=uuid5(event.id, "rejected"), identity=event.identity,
        kind="message", text=text, card=card,
    )


class TeamsScheduler:
    _tasks: list[asyncio.Task] = []
    _receiver = uuid4()

    @classmethod
    def start(cls) -> None:
        if not cls._tasks:
            cls._tasks = [
                asyncio.create_task(cls.poll_loop()),
                asyncio.create_task(cls.work_loop()),
                asyncio.create_task(cls.progress_loop()),
            ]

    @classmethod
    async def stop(cls) -> None:
        for task in cls._tasks:
            task.cancel()
        await asyncio.gather(*cls._tasks, return_exceptions=True)
        cls._tasks = []

    @staticmethod
    async def settings() -> TeamsChannelSettings | None:
        async with AsyncSessionFactory() as db:
            if not await channel_provider_allowed(db, "teams"):
                return None
            value = await SettingsStore.load(db, TeamsChannelSettings)
            return value if value.credential_id and value.revision else None

    @classmethod
    async def poll_loop(cls) -> None:
        while True:
            try:
                settings = await cls.settings()
                if settings:
                    async with AsyncSessionFactory() as db, relay_client(db, settings) as client:
                        relay_status = await client.status(settings)
                        await db.execute(
                            update(MessagingEndpoint)
                            .where(
                                MessagingEndpoint.provider == "teams",
                                MessagingEndpoint.last_error_code.in_(
                                    (
                                        "channel.unavailable",
                                        "channel.authenticationFailed",
                                        "channel.invalidResponse",
                                    )
                                ),
                            )
                            .values(last_error_code=None)
                        )
                        await db.commit()
                        raw = await client.request(
                            "GET", "events", params={"receiver": str(cls._receiver)}
                        )
                        result = PollResult.model_validate_json(raw)
                        for event in result.events:
                            needs_pairing = False
                            try:
                                admitted = await ingest_event(event, result.generation, settings)
                            except TeamsServiceError as exc:
                                admitted = False
                                needs_pairing = exc.code == "channel.pairingRequired"
                            await client.request(
                                "POST",
                                "receipts",
                                json=Receipt(
                                    event_id=event.id,
                                    generation=event.generation,
                                    status="admitted" if admitted else "rejected",
                                ).model_dump(mode="json"),
                            )
                            if not admitted:
                                await client.deliver(
                                    rejected_delivery(
                                        event, needs_pairing=needs_pairing,
                                        pairing_origin=relay_status.pairing_origin,
                                    )
                                )
                else:
                    await asyncio.sleep(5)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = exc.code if isinstance(exc, TeamsServiceError) else "channel.invalidResponse"
                logger.error("Teams relay polling failed: %s", code, exc_info=False)
                try:
                    async with AsyncSessionFactory() as db:
                        await db.execute(
                            update(MessagingEndpoint)
                            .where(
                                MessagingEndpoint.provider == "teams",
                                MessagingEndpoint.deleted_at.is_(None),
                            )
                            .values(last_error_code=code)
                        )
                        await db.commit()
                except SQLAlchemyError:
                    logger.error(
                        "Teams polling failure state could not be persisted", exc_info=False
                    )
                await asyncio.sleep(5)

    @classmethod
    async def work_loop(cls) -> None:
        while True:
            try:
                settings = await cls.settings()
                if settings:
                    async with AsyncSessionFactory() as db, relay_client(db, settings) as client:
                        while inbox_id := await claim_inbox():
                            await process_inbox(inbox_id, settings, client)
                        await reconcile_runs("teams")
                        while outbox_id := await claim_outbox():
                            await deliver_outbox(outbox_id, settings, client)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Teams relay delivery iteration failed", exc_info=False)
            await asyncio.sleep(2)

    @classmethod
    async def progress_loop(cls) -> None:
        while True:
            try:
                settings = await cls.settings()
                if settings:
                    async with AsyncSessionFactory() as db, relay_client(db, settings) as client:
                        targets = (
                            await db.execute(
                                select(
                                    MessagingInbox.endpoint_id,
                                    MessagingInbox.payload["bindingId"].astext,
                                    MessagingInbox.external_message_id,
                                    AgentRun.id,
                                    AgentRun.pseudonymization_enabled,
                                )
                                .join(
                                    MessagingEndpoint,
                                    MessagingEndpoint.id == MessagingInbox.endpoint_id,
                                )
                                .join(AgentRun, AgentRun.id == MessagingInbox.agent_run_id)
                                .where(
                                    MessagingEndpoint.provider == "teams",
                                    MessagingInbox.status == "completed",
                                    AgentRun.status.in_(("queued", "running")),
                                )
                                .order_by(MessagingInbox.created_at)
                                .limit(50)
                            )
                        ).all()
                        seen = set()
                        for endpoint_id, binding_id, source_id, run_id, pseudonymized in targets:
                            if endpoint_id in seen:
                                continue
                            try:
                                endpoint = await available_endpoint(db, endpoint_id, settings)
                                if binding_id != endpoint.channel_state.get("bindingId"):
                                    continue
                                identity = bound_identity(endpoint, settings)
                                seen.add(endpoint_id)
                                tool = run_events.active_tool(run_id)
                                draft = "" if pseudonymized else run_events.draft_text(run_id)[:8000].lstrip()
                                preview = (
                                    run_activity_label("processing_tool", tool=tool) if tool else draft
                                )
                                if settings.streaming_enabled and preview:
                                    try:
                                        raw = await client.request(
                                            "POST", "streams", missing_ok=True,
                                            json=StreamPreview(
                                                identity=identity, source_message_id=source_id,
                                                text=preview, informative=tool is not None,
                                            ).model_dump(mode="json"),
                                        )
                                        if raw is not None and json.loads(raw) == {"active": True}:
                                            continue
                                    except (TeamsServiceError, ValueError):
                                        logger.warning("Teams preview failed; falling back to typing")
                                await client.deliver(
                                    Delivery(
                                        id=uuid4(),
                                        identity=identity,
                                        kind="typing",
                                    )
                                )
                            except TeamsServiceError:
                                logger.warning(
                                    "Teams typing indicator could not be delivered", exc_info=False
                                )
                            finally:
                                await db.rollback()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("Teams progress iteration failed", exc_info=False)
            await asyncio.sleep(5)
