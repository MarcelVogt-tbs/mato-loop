from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
from contextlib import aclosing
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import String, cast, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from telegram import Bot, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import InvalidToken, NetworkError, RetryAfter, TelegramError, TimedOut

from mato_loop_backend.agent.activity import reasoning_activity_label, run_activity_label
from mato_loop_backend.agent.question_decisions import QuestionDecisionError
from mato_loop_backend.agent.run_contracts import AgentRunError
from mato_loop_backend.agent.runtime import AgentRuntime
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.events import run_events
from mato_loop_backend.files.storage import SessionStorage, SessionStorageError
from mato_loop_backend.media.attachments import (
    AttachmentError,
    AttachmentInput,
    AttachmentService,
    classify_attachment,
    processing_limits,
)
from mato_loop_backend.media.processing import AttachmentProcessingError
from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.messaging.approvals import (
    ApprovalDecision,
    MessagingApprovalError,
    decode_approval_callback,
    encode_approval_callback,
    parse_approval_command,
    parse_approval_payload,
    resolve_approval_action,
)
from mato_loop_backend.messaging.formatting import RenderedChannelText, render_channel_text
from mato_loop_backend.messaging.policy import channel_provider_allowed
from mato_loop_backend.messaging.questions import (
    MessagingQuestionEnvelope,
    MessagingQuestionError,
    QuestionInteraction,
    QuestionInteractionResult,
    decode_question_callback,
    encode_question_callback,
    parse_question_envelope,
    resolve_question_interaction,
)
from mato_loop_backend.messaging.telegram_formatting import TELEGRAM_HTML
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    AutomationRun,
    ChatSession,
    Message,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    QuestionRequest,
    TitleGenerationStatus,
    UserAccount,
)
from mato_loop_backend.permissions.decisions import ApprovalDecisionError
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.security import load_credential, read_credential
from mato_loop_backend.settings import (
    InstanceLimits,
    SettingsStore,
)
from mato_loop_backend.settings.effective import default_agent_profile_id

logger = logging.getLogger(__name__)
TELEGRAM_CREDENTIAL_KIND = "telegram.bot_token"
PAIRING_TTL = timedelta(minutes=15)
POLL_LEASE = timedelta(seconds=40)
POLL_TIMEOUT_SECONDS = 20
POLL_REQUEST_TIMEOUT_SECONDS = 30
PROBE_TIMEOUT_SECONDS = 10
PROCESSING_TIMEOUT = timedelta(minutes=10)
SESSION_LIST_LIMIT = 10
SESSION_MAX_AGE = timedelta(days=7)
OUTBOX_MAX_ATTEMPTS = 5
TYPING_REFRESH_SECONDS = 4
DRAFT_UPDATE_SECONDS = 1
DRAFT_REFRESH_SECONDS = 20
DRAFT_CLEAR_TEXT = "\u2063"
MESSAGE_CONTENT_MAX_LENGTH = 200_000


class TelegramServiceError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def pairing_code() -> str:
    return secrets.token_urlsafe(9)


def pairing_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


async def probe_bot(token: str) -> tuple[str, str | None]:
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            async with Bot(token=token) as bot:
                account = bot.bot
    except InvalidToken as exc:
        raise TelegramServiceError("channel.authenticationFailed") from exc
    except (NetworkError, TelegramError, TimedOut, TimeoutError) as exc:
        raise TelegramServiceError("channel.unavailable") from exc
    if not account.is_bot:
        raise TelegramServiceError("channel.invalidResponse")
    return str(account.id), account.username


async def endpoint_token(db: AsyncSession, endpoint: MessagingEndpoint) -> str:
    if endpoint.credential_id is None:
        raise TelegramServiceError("channel.authenticationFailed")
    credential = await load_credential(
        db, endpoint.credential_id, owner_id=endpoint.user_id, populate_existing=True
    )
    if credential is None or credential.kind != TELEGRAM_CREDENTIAL_KIND:
        raise TelegramServiceError("channel.authenticationFailed")
    payload = read_credential(credential)
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise TelegramServiceError("channel.authenticationFailed")
    return token


def new_pairing(endpoint: MessagingEndpoint) -> str:
    code = pairing_code()
    endpoint.pairing_code_hash = pairing_hash(code)
    endpoint.pairing_expires_at = datetime.now(UTC) + PAIRING_TTL
    return code


def endpoint_payload(endpoint: MessagingEndpoint, *, code: str | None = None) -> dict:
    paired = endpoint.paired_chat_id is not None
    return {
        "id": str(endpoint.id),
        "name": endpoint.name,
        "provider": endpoint.provider,
        "enabled": endpoint.enabled,
        "hasCredential": endpoint.credential_id is not None,
        "externalHandle": endpoint.external_handle,
        "pairingState": "paired"
        if paired
        else "pending"
        if endpoint.pairing_code_hash
        and endpoint.pairing_expires_at
        and endpoint.pairing_expires_at > datetime.now(UTC)
        else "unpaired",
        "connected": endpoint.last_error_code is None,
        "pairedDisplayName": endpoint.paired_display_name,
        "currentSessionId": str(endpoint.current_session_id)
        if endpoint.current_session_id
        else None,
        "pairingExpiresAt": endpoint.pairing_expires_at.isoformat()
        if endpoint.pairing_expires_at
        else None,
        **({"pairingCode": code} if code else {}),
    }


def _normalized_update(update: Update) -> dict | None:
    callback = update.callback_query
    if callback is not None:
        message = callback.message
        sender = callback.from_user
        chat = message.chat if message is not None else None
        if message is None or chat is None or chat.type != "private":
            return None
        return {
            "type": "callback_action",
            "chatId": str(chat.id),
            "userId": str(sender.id),
            "displayName": (sender.full_name or sender.username or str(sender.id))[:160],
            "callbackQueryId": callback.id,
            "callbackData": callback.data or "",
            "sourceMessageId": str(message.message_id),
            "text": "",
            "media": [],
        }
    message = update.effective_message
    sender = update.effective_user
    chat = update.effective_chat
    if message is None or sender is None or chat is None or chat.type != "private":
        return None
    media = []
    if message.photo:
        photo = message.photo[-1]
        media.append(
            {
                "fileId": photo.file_id,
                "fileSize": photo.file_size,
                "filename": f"telegram-photo-{message.message_id}.jpg",
                "mimeType": "image/jpeg",
                "kind": "image",
            }
        )
    for item, kind, fallback_mime, fallback_name in (
        (message.document, "file", "application/octet-stream", "document"),
        (message.audio, "audio", "audio/mpeg", "audio"),
        (message.voice, "voice", "audio/ogg", "voice.ogg"),
        (message.video, "video", "video/mp4", "video.mp4"),
    ):
        if item is None:
            continue
        media.append(
            {
                "fileId": item.file_id,
                "fileSize": item.file_size,
                "filename": getattr(item, "file_name", None) or fallback_name,
                "mimeType": getattr(item, "mime_type", None) or fallback_mime,
                "kind": kind,
            }
        )
    return {
        "type": "message",
        "chatId": str(chat.id),
        "userId": str(sender.id),
        "displayName": (sender.full_name or sender.username or str(sender.id))[:160],
        "text": message.text or message.caption or "",
        "replyToMessageId": (
            str(message.reply_to_message.message_id)
            if message.reply_to_message is not None
            else None
        ),
        "media": media,
    }


def _enqueue_text(
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


async def invalidate_pending_inbox(
    db: AsyncSession, endpoint_id: UUID, *, exclude_id: UUID | None = None
) -> None:
    conditions = [
        MessagingInbox.endpoint_id == endpoint_id,
        MessagingInbox.status.in_(("pending", "processing")),
    ]
    if exclude_id is not None:
        conditions.append(MessagingInbox.id != exclude_id)
    await db.execute(
        update(MessagingInbox)
        .where(*conditions)
        .values(
            status="failed",
            processing_at=None,
            next_attempt_at=None,
            error_code="channel.pairingChanged",
        )
    )
    await db.execute(
        update(MessagingInbox)
        .where(
            MessagingInbox.endpoint_id == endpoint_id,
            MessagingInbox.status == "completed",
            MessagingInbox.id != exclude_id if exclude_id is not None else True,
            ~exists(
                select(MessagingOutbox.id).where(
                    MessagingOutbox.inbox_id == MessagingInbox.id
                )
            ),
        )
        .values(status="failed", error_code="channel.pairingChanged")
    )


async def stop_endpoint_work(
    db: AsyncSession, endpoint_id: UUID, *, exclude_inbox_id: UUID | None = None
) -> None:
    await invalidate_pending_inbox(db, endpoint_id, exclude_id=exclude_inbox_id)
    runs = list(
        (
            await db.scalars(
                select(AutomationRun).where(
                    AutomationRun.delivery_endpoint_id == endpoint_id,
                    AutomationRun.delivery_status == "pending",
                )
            )
        ).all()
    )
    for run in runs:
        run.delivery_channel_type = "app"
        run.delivery_endpoint_id = None
        run.delivery_destination = None
    outbox_items = list(
        (
            await db.scalars(
                select(MessagingOutbox).where(
                    MessagingOutbox.endpoint_id == endpoint_id,
                    MessagingOutbox.status.in_(("pending", "processing")),
                )
            )
        ).all()
    )
    for item in outbox_items:
        item.status = "failed"
        item.processing_at = None
        item.next_attempt_at = None
        item.error_code = "channel.pairingChanged"


async def _release_poll_lease(
    endpoint_id: UUID, owner: str, *, error_code: str | None = None
) -> None:
    async with AsyncSessionFactory() as db:
        endpoint = await db.scalar(
            select(MessagingEndpoint)
            .where(
                MessagingEndpoint.id == endpoint_id,
                MessagingEndpoint.poll_lease_owner == owner,
            )
            .with_for_update()
        )
        if endpoint is None:
            await db.rollback()
            return
        endpoint.poll_lease_owner = None
        endpoint.poll_lease_until = None
        if error_code is not None:
            endpoint.last_error_code = error_code
        await db.commit()


async def _ingest_updates(
    endpoint_id: UUID, owner: str, updates: tuple[Update, ...]
) -> None:
    async with AsyncSessionFactory() as db:
        allowed = await channel_provider_allowed(db, "telegram", lock=True)
        endpoint_hint = await db.get(MessagingEndpoint, endpoint_id)
        if endpoint_hint is None:
            await db.rollback()
            return
        user = await db.scalar(
            select(UserAccount)
            .where(UserAccount.id == endpoint_hint.user_id)
            .with_for_update()
        )
        endpoint = await db.scalar(
            select(MessagingEndpoint)
            .where(MessagingEndpoint.id == endpoint_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if endpoint is None or endpoint.poll_lease_owner != owner:
            await db.rollback()
            return
        endpoint.poll_lease_owner = None
        endpoint.poll_lease_until = None
        if (
            not allowed
            or endpoint.provider != "telegram"
            or not endpoint.enabled
            or endpoint.deleted_at is not None
            or user is None
            or not user.is_active
        ):
            if updates:
                endpoint.poll_offset = max(
                    endpoint.poll_offset, max(update.update_id for update in updates) + 1
                )
            await db.commit()
            return
        now = datetime.now(UTC)
        for update in updates:
            endpoint.poll_offset = max(endpoint.poll_offset, update.update_id + 1)
            payload = _normalized_update(update)
            if payload is None:
                continue
            if endpoint.paired_chat_id is None:
                command, _, code = payload["text"].strip().partition(" ")
                if (
                    command.split("@", 1)[0] == "/start"
                    and code
                    and endpoint.pairing_code_hash
                    and endpoint.pairing_expires_at
                    and endpoint.pairing_expires_at > now
                    and hmac.compare_digest(pairing_hash(code.strip()), endpoint.pairing_code_hash)
                ):
                    endpoint.paired_user_id = payload["userId"]
                    endpoint.paired_chat_id = payload["chatId"]
                    endpoint.paired_display_name = payload["displayName"]
                    endpoint.pairing_code_hash = None
                    endpoint.pairing_expires_at = None
                    _enqueue_text(
                        db,
                        endpoint,
                        destination=payload["chatId"],
                        key=f"pair:{update.update_id}",
                        text="Telegram wurde mit MatoLoop verbunden. Sende /help für Befehle.",
                    )
                continue
            if (
                payload["chatId"] != endpoint.paired_chat_id
                or payload["userId"] != endpoint.paired_user_id
            ):
                continue
            await db.execute(
                insert(MessagingInbox)
                .values(
                    endpoint_id=endpoint.id,
                    external_event_id=str(update.update_id),
                    external_message_id=(
                        str(payload["sourceMessageId"])
                        if payload.get("sourceMessageId")
                        else str(update.effective_message.message_id)
                        if update.effective_message is not None
                        else None
                    ),
                    payload=payload,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        MessagingInbox.endpoint_id,
                        MessagingInbox.external_event_id,
                    ]
                )
            )
        endpoint.last_polled_at = now
        endpoint.last_error_code = None
        await db.commit()


async def _poll(endpoint_id: UUID, owner: str) -> None:
    async with AsyncSessionFactory() as db:
        if not await channel_provider_allowed(db, "telegram", lock=True):
            await db.rollback()
            return
        now = datetime.now(UTC)
        endpoint = await db.scalar(
            select(MessagingEndpoint)
            .join(UserAccount, UserAccount.id == MessagingEndpoint.user_id)
            .where(
                MessagingEndpoint.id == endpoint_id,
                MessagingEndpoint.provider == "telegram",
                MessagingEndpoint.enabled.is_(True),
                MessagingEndpoint.deleted_at.is_(None),
                MessagingEndpoint.credential_id.is_not(None),
                UserAccount.is_active.is_(True),
                or_(
                    MessagingEndpoint.poll_lease_until.is_(None),
                    MessagingEndpoint.poll_lease_until < now,
                    MessagingEndpoint.poll_lease_owner == owner,
                ),
            )
            .with_for_update(skip_locked=True)
        )
        if endpoint is None:
            return
        endpoint.poll_lease_owner = owner
        endpoint.poll_lease_until = now + POLL_LEASE
        offset = endpoint.poll_offset
        try:
            token = await endpoint_token(db, endpoint)
        except TelegramServiceError as exc:
            endpoint.last_error_code = exc.code
            endpoint.poll_lease_owner = None
            endpoint.poll_lease_until = None
            await db.commit()
            return
        await db.commit()
    try:
        async with asyncio.timeout(POLL_REQUEST_TIMEOUT_SECONDS):
            async with Bot(token=token) as bot:
                updates = await bot.get_updates(
                    offset=offset,
                    timeout=POLL_TIMEOUT_SECONDS,
                    read_timeout=POLL_TIMEOUT_SECONDS + 5,
                    connect_timeout=5,
                    allowed_updates=["message", "callback_query"],
                )
        await _ingest_updates(endpoint_id, owner, tuple(updates))
    except asyncio.CancelledError:
        await _release_poll_lease(endpoint_id, owner)
        raise
    except InvalidToken:
        code = "channel.authenticationFailed"
        logger.warning("Telegram polling failed for endpoint %s: %s", endpoint_id, code)
        await _release_poll_lease(endpoint_id, owner, error_code=code)
    except (NetworkError, TelegramError, TimedOut, TimeoutError):
        code = "channel.unavailable"
        logger.warning("Telegram polling failed for endpoint %s: %s", endpoint_id, code)
        await _release_poll_lease(endpoint_id, owner, error_code=code)


async def _download_media(
    db: AsyncSession, endpoint: MessagingEndpoint, media: list[dict]
) -> list[AttachmentInput]:
    if not media:
        return []
    limits = await SettingsStore.load(db, InstanceLimits)
    if len(media) > limits.max_attachments_per_message:
        raise TelegramServiceError("validation.tooManyItems")
    token = await endpoint_token(db, endpoint)
    await db.commit()
    attachments = []
    declared_total = 0
    total = 0
    async with Bot(token=token) as bot:
        for item in media:
            declared_size = item.get("fileSize")
            if not isinstance(declared_size, int) or declared_size < 0:
                raise TelegramServiceError("channel.invalidResponse")
            declared_total += declared_size
            if (
                declared_size > limits.max_upload_bytes
                or declared_total > limits.max_message_upload_bytes
            ):
                raise TelegramServiceError("attachment.tooLarge")
            remote = await bot.get_file(item["fileId"])
            data = bytes(await remote.download_as_bytearray())
            total += len(data)
            if len(data) > limits.max_upload_bytes or total > limits.max_message_upload_bytes:
                raise TelegramServiceError("attachment.tooLarge")
            try:
                attachment = classify_attachment(
                    data,
                    item["mimeType"],
                    item["filename"],
                    max_bytes=limits.max_upload_bytes,
                    limits=processing_limits(limits),
                )
            except AttachmentError as exc:
                raise TelegramServiceError(exc.code) from exc
            if item.get("kind") == "voice":
                if attachment.kind != "audio":
                    raise TelegramServiceError("channel.invalidResponse")
                attachment = replace(attachment, metadata={"voiceMessage": True})
            attachments.append(attachment)
    return attachments


async def _new_session(
    db: AsyncSession,
    endpoint: MessagingEndpoint,
    content: str,
    attachments: list[AttachmentInput],
    run_id: UUID,
) -> AgentRun:
    item = ChatSession(
        user_id=endpoint.user_id,
        title="Neue Session",
        title_generation_status=TitleGenerationStatus.PENDING.value,
        model_config_id=None,
        agent_profile_id=await default_agent_profile_id(db),
    )
    db.add(item)
    await db.flush()
    storage = SessionStorage()
    try:
        await asyncio.to_thread(storage.provision, item.id)
        return await AgentRuntime.start(
            db,
            item,
            content,
            run_id=run_id,
            attachments=attachments,
            source_type="endpoint",
            source_provider="telegram",
            source_endpoint_id=endpoint.id,
        )
    except BaseException:
        await db.rollback()
        try:
            await asyncio.to_thread(storage.remove_session, item.id)
        except (OSError, SessionStorageError):
            logger.exception("Unpersisted Telegram session storage could not be removed")
        raise


async def _command(
    db: AsyncSession, inbox: MessagingInbox, endpoint: MessagingEndpoint, text: str
) -> bool:
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].casefold()
    if command not in {"/new", "/sessions", "/use", "/current", "/stop", "/unlink", "/help"}:
        return False
    response = ""
    if command == "/help":
        response = (
            "/new - nächste Nachricht in einer neuen Session\n"
            "/sessions - letzte Sessions anzeigen\n"
            "/use <ID> - Session auswählen\n"
            "/current - aktuelle Session anzeigen\n"
            "/stop - laufende Antwort stoppen\n"
            "/unlink - Telegram-Verknüpfung lösen"
        )
    elif command == "/new":
        endpoint.current_session_id = None
        response = "Die nächste Nachricht startet eine neue Session."
    elif command == "/sessions":
        cutoff = datetime.now(UTC) - SESSION_MAX_AGE
        activity = (
            select(Message.session_id, Message.created_at)
            .where(Message.created_at >= cutoff)
            .distinct(Message.session_id)
            .order_by(Message.session_id, Message.created_at.desc())
            .subquery()
        )
        sessions = list(
            (
                await db.scalars(
                    select(ChatSession)
                    .join(activity, activity.c.session_id == ChatSession.id)
                    .where(
                        ChatSession.user_id == endpoint.user_id,
                        ChatSession.session_type == "chat",
                        ChatSession.parent_session_id.is_(None),
                    )
                    .order_by(activity.c.created_at.desc(), ChatSession.id.desc())
                    .limit(SESSION_LIST_LIMIT)
                )
            ).all()
        )
        response = (
            "Letzte Sessions:\n"
            + "\n".join(f"{str(item.id)[:8]}  {item.title}" for item in sessions)
            if sessions
            else "Keine Session aus den letzten sieben Tagen gefunden."
        )
    elif command == "/use":
        short_id = argument.strip().casefold()
        session = None
        if 4 <= len(short_id) <= 36 and all(c in "0123456789abcdef-" for c in short_id):
            matches = list(
                (
                    await db.scalars(
                        select(ChatSession).where(
                            ChatSession.user_id == endpoint.user_id,
                            ChatSession.session_type == "chat",
                            ChatSession.parent_session_id.is_(None),
                            cast(ChatSession.id, type_=String).startswith(short_id),
                        )
                    )
                ).all()
            )
            session = matches[0] if len(matches) == 1 else None
        if session is None:
            response = "Session nicht gefunden. Nutze /sessions für gültige IDs."
        else:
            endpoint.current_session_id = session.id
            response = f"Aktuelle Session: {session.title} ({str(session.id)[:8]})"
    elif command == "/current":
        session = await db.get(ChatSession, endpoint.current_session_id) if endpoint.current_session_id else None
        response = (
            f"Aktuelle Session: {session.title} ({str(session.id)[:8]})"
            if session is not None and session.user_id == endpoint.user_id
            else "Keine Session ausgewählt. Die nächste Nachricht startet eine neue."
        )
    elif command == "/stop":
        run_id = (
            await db.scalar(
                select(AgentRun.id).join(ChatSession, ChatSession.id == AgentRun.session_id).where(
                    AgentRun.session_id == endpoint.current_session_id,
                    ChatSession.user_id == endpoint.user_id,
                    ChatSession.parent_session_id.is_(None),
                    AgentRun.parent_run_id.is_(None),
                    AgentRun.status.in_(("queued", "running", "waiting_approval", "waiting_input")),
                )
            )
            if endpoint.current_session_id
            else None
        )
        response = "Antwort wird gestoppt." if run_id and AgentRuntime.cancel(run_id) else "Keine laufende Antwort."
    else:
        await stop_endpoint_work(db, endpoint.id, exclude_inbox_id=inbox.id)
        endpoint.paired_user_id = None
        endpoint.paired_chat_id = None
        endpoint.paired_display_name = None
        endpoint.current_session_id = None
        response = "Telegram wurde von MatoLoop getrennt."
    _enqueue_text(
        db,
        endpoint,
        destination=inbox.payload["chatId"],
        key=f"command:{inbox.id}",
        text=response,
        inbox_id=inbox.id,
        allow_unpaired=command == "/unlink",
    )
    return True


def _approval_result_text(
    result: dict[str, str] | None = None, error_code: str | None = None
) -> str:
    if result is not None:
        return {
            "allow_once": "Für diesen Lauf freigegeben.",
            "allow_session": "Für diese Session freigegeben.",
            "deny": "Freigabe abgelehnt.",
        }.get(result.get("decision", ""), "Freigabe wurde verarbeitet.")
    if error_code == "approval.alreadyDecided":
        return "Die Freigabe wurde bereits entschieden."
    return "Die Freigabe ist nicht mehr verfügbar."


async def _process_approval_action(
    db: AsyncSession,
    inbox: MessagingInbox,
    endpoint: MessagingEndpoint,
    approval_id: UUID,
    decision: ApprovalDecision,
    *,
    source_external_message_id: str | None = None,
    reply_with_message: bool,
) -> tuple[str, str | None]:
    inbox_id = inbox.id
    endpoint_id = endpoint.id
    try:
        result = await resolve_approval_action(
            db,
            endpoint,
            approval_id,
            decision,
            source_external_message_id,
        )
        response = _approval_result_text(result)
        resolved_decision = result.get("decision")
    except MessagingApprovalError:
        await db.rollback()
        response = _approval_result_text(error_code="approval.notFound")
        resolved_decision = None
    except ApprovalDecisionError as exc:
        await db.rollback()
        response = _approval_result_text(error_code=exc.code)
        resolved_decision = None

    inbox = await db.get(MessagingInbox, inbox_id)
    endpoint = await db.get(MessagingEndpoint, endpoint_id)
    if inbox is None or endpoint is None:
        return response, resolved_decision
    inbox.status = "completed"
    inbox.processing_at = None
    inbox.error_code = None
    if reply_with_message:
        _enqueue_text(
            db,
            endpoint,
            destination=str(inbox.payload["chatId"]),
            key=f"approval-result:{inbox.id}",
            text=response,
            inbox_id=inbox.id,
        )
    await db.commit()
    return response, resolved_decision


async def _answer_approval_callback(
    token: str,
    callback_query_id: str,
    chat_id: str,
    source_message_id: str,
    text: str,
    *,
    remove_buttons: bool,
) -> None:
    try:
        async with Bot(token=token) as bot:
            await bot.answer_callback_query(callback_query_id=callback_query_id, text=text)
            if remove_buttons:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=int(source_message_id),
                    reply_markup=None,
                )
    except (InvalidToken, NetworkError, TelegramError, TimedOut, ValueError):
        logger.warning("Telegram approval callback acknowledgement failed")


def _telegram_button_text(text: str, limit: int = 64) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= limit:
        return text
    return encoded[: limit - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def _question_prompt(envelope: MessagingQuestionEnvelope) -> str:
    question = envelope.questions[envelope.current_index]
    lines = [
        f"Frage {envelope.current_index + 1} von {len(envelope.questions)}: {question.header}",
        "",
        question.question,
    ]
    current_answers = envelope.answers.get(question.id, [])
    if current_answers:
        selected = ", ".join(current_answers)
        if len(selected) > 500:
            selected = f"{selected[:499]}…"
        lines.extend(("", f"Auswahl: {selected}"))
    if envelope.awaiting_text:
        lines.extend(("", "Antworte direkt auf diese Nachricht mit deiner eigenen Antwort."))
    elif question.type == "free_text":
        lines.extend(("", "Antworte direkt auf diese Nachricht."))
    elif question.type == "multiple_choice":
        lines.extend(("", "Du kannst mehrere Antworten auswählen."))
    else:
        lines.extend(("", "Wähle eine Antwort aus."))
    return "\n".join(lines)


def _question_markup(envelope: MessagingQuestionEnvelope) -> InlineKeyboardMarkup:
    question = envelope.questions[envelope.current_index]
    selected = set(envelope.answers.get(question.id, []))
    rows = []
    if question.type != "free_text" and not envelope.awaiting_text:
        action = "select" if question.type == "single_choice" else "toggle"
        for index, option in enumerate(question.options):
            prefix = "✓ " if option.label in selected else ""
            rows.append(
                [
                    InlineKeyboardButton(
                        _telegram_button_text(f"{prefix}{option.label}"),
                        callback_data=encode_question_callback(
                            envelope.question_id,
                            QuestionInteraction(action=action, optionIndex=index),
                        ),
                    )
                ]
            )
        if question.allow_custom:
            rows.append(
                [
                    InlineKeyboardButton(
                        "Eigene Antwort",
                        callback_data=encode_question_callback(
                            envelope.question_id, QuestionInteraction(action="custom")
                        ),
                    )
                ]
            )
    navigation = []
    if envelope.current_index > 0 or envelope.awaiting_text:
        navigation.append(
            InlineKeyboardButton(
                "Zurück",
                callback_data=encode_question_callback(
                    envelope.question_id, QuestionInteraction(action="back")
                ),
            )
        )
    if not _question_expects_text(envelope):
        navigation.append(
            InlineKeyboardButton(
                "Antworten senden"
                if envelope.current_index == len(envelope.questions) - 1
                else "Weiter",
                callback_data=encode_question_callback(
                    envelope.question_id,
                    QuestionInteraction(
                        action="submit"
                        if envelope.current_index == len(envelope.questions) - 1
                        else "next"
                    ),
                ),
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                "Ablehnen",
                callback_data=encode_question_callback(
                    envelope.question_id, QuestionInteraction(action="reject")
                ),
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _render_question(envelope: MessagingQuestionEnvelope) -> RenderedChannelText:
    parts = render_channel_text(
        "telegram", _question_prompt(envelope), content_format="plain"
    )
    return parts[-1] if parts else RenderedChannelText("")


def _question_expects_text(envelope: MessagingQuestionEnvelope) -> bool:
    question = envelope.questions[envelope.current_index]
    return envelope.awaiting_text or (
        question.type == "free_text" and not envelope.answers.get(question.id)
    )


async def _send_question_reply_prompt(
    bot: Bot, chat_id: str, envelope: MessagingQuestionEnvelope
) -> str | None:
    if not _question_expects_text(envelope):
        return None
    question = envelope.questions[envelope.current_index]
    placeholder = (
        question.placeholder
        if question.type == "free_text" and question.placeholder
        else "Antwort eingeben"
    )
    sent = await bot.send_message(
        chat_id=chat_id,
        text="Antwort eingeben:",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder=_telegram_button_text(placeholder),
        ),
    )
    return str(sent.message_id)


def _question_main_message_id(
    outbox: MessagingOutbox, envelope: MessagingQuestionEnvelope
) -> str | None:
    message_ids = outbox.external_message_ids
    if not message_ids:
        return None
    if _question_expects_text(envelope) and len(message_ids) > 1:
        return message_ids[-2]
    return message_ids[-1]


async def _track_question_messages(
    db: AsyncSession,
    question_id: UUID,
    main_message_id: str,
    reply_message_id: str | None,
) -> None:
    outbox = await db.scalar(
        select(MessagingOutbox)
        .where(MessagingOutbox.deduplication_key == f"question:{question_id}")
        .with_for_update()
    )
    if (
        outbox is None
        or outbox.status != "delivered"
        or main_message_id not in outbox.external_message_ids
    ):
        await db.rollback()
        return
    outbox.external_message_ids = [
        main_message_id,
        *([reply_message_id] if reply_message_id is not None else []),
    ]
    await db.commit()


def _question_result_text(
    result: QuestionInteractionResult | None = None, error_code: str | None = None
) -> str:
    if result is not None:
        if result.status == "answered":
            return "Antworten gesendet."
        if result.status == "rejected":
            return "Rückfrage abgelehnt."
        return "Auswahl aktualisiert."
    if error_code == "question.alreadyAnswered":
        return "Die Rückfrage wurde bereits beantwortet."
    return "Die Rückfrage ist nicht mehr verfügbar."


async def _process_question_interaction(
    db: AsyncSession,
    inbox: MessagingInbox,
    endpoint: MessagingEndpoint,
    question_id: UUID,
    interaction: QuestionInteraction,
    source_external_message_id: str,
) -> tuple[QuestionInteractionResult | None, str]:
    inbox_id = inbox.id
    endpoint_id = endpoint.id
    try:
        result = await resolve_question_interaction(
            db,
            endpoint,
            question_id,
            interaction,
            source_external_message_id,
        )
        if interaction.action == "select" and result.envelope is not None:
            follow_up = (
                "submit"
                if result.envelope.current_index == len(result.envelope.questions) - 1
                else "next"
            )
            result = await resolve_question_interaction(
                db,
                endpoint,
                question_id,
                QuestionInteraction(action=follow_up),
                source_external_message_id,
            )
        response = _question_result_text(result)
    except MessagingQuestionError:
        await db.rollback()
        result = None
        response = _question_result_text(error_code="question.notFound")
    except QuestionDecisionError as exc:
        await db.rollback()
        result = None
        response = _question_result_text(error_code=exc.code)

    inbox = await db.get(MessagingInbox, inbox_id)
    endpoint = await db.get(MessagingEndpoint, endpoint_id)
    if inbox is None or endpoint is None:
        return result, response
    inbox.status = "completed"
    inbox.processing_at = None
    inbox.error_code = None
    if result is None and inbox.payload.get("type") == "message":
        _enqueue_text(
            db,
            endpoint,
            destination=str(inbox.payload["chatId"]),
            key=f"question-error:{inbox.id}",
            text=response,
            inbox_id=inbox.id,
        )
    await db.commit()
    return result, response


async def _update_question_message(
    token: str,
    chat_id: str,
    source_message_id: str,
    result: QuestionInteractionResult,
    *,
    callback_query_id: str | None = None,
    response: str = "",
) -> str | None:
    try:
        async with Bot(token=token) as bot:
            if callback_query_id is not None:
                await bot.answer_callback_query(
                    callback_query_id=callback_query_id, text=response
                )
            if result.envelope is None:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=int(source_message_id),
                    reply_markup=None,
                )
                return None
            rendered = _render_question(result.envelope)
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=int(source_message_id),
                text=rendered.text,
                parse_mode=ParseMode.HTML if rendered.syntax == TELEGRAM_HTML else None,
                reply_markup=_question_markup(result.envelope),
            )
            return await _send_question_reply_prompt(bot, chat_id, result.envelope)
    except (InvalidToken, NetworkError, TelegramError, TimedOut, ValueError):
        logger.warning("Telegram question update failed")
        return None


async def process_inbox(inbox_id: UUID) -> UUID | None:
    async with AsyncSessionFactory() as db:
        inbox = await db.get(MessagingInbox, inbox_id)
        endpoint_hint = (
            await db.get(MessagingEndpoint, inbox.endpoint_id) if inbox else None
        )
        allowed = await channel_provider_allowed(db, "telegram", lock=True)
        user = (
            await db.scalar(
                select(UserAccount)
                .where(UserAccount.id == endpoint_hint.user_id)
                .with_for_update()
            )
            if endpoint_hint
            else None
        )
        endpoint = (
            await db.scalar(
                select(MessagingEndpoint)
                .where(MessagingEndpoint.id == inbox.endpoint_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if inbox
            else None
        )
        if inbox is None or endpoint is None or inbox.status != "processing":
            return None
        if (
            endpoint.provider != "telegram"
            or not endpoint.enabled
            or endpoint.deleted_at is not None
            or user is None
            or not user.is_active
            or endpoint.paired_chat_id != inbox.payload.get("chatId")
            or endpoint.paired_user_id != inbox.payload.get("userId")
            or not allowed
        ):
            inbox.status = "failed"
            inbox.processing_at = None
            inbox.next_attempt_at = None
            inbox.error_code = "channel.pairingChanged"
            await db.commit()
            return None
        text = str(inbox.payload.get("text") or "")
        if inbox.payload.get("type") in {"approval_action", "callback_action"}:
            try:
                token = await endpoint_token(db, endpoint)
            except TelegramServiceError:
                token = None
            callback_data = str(inbox.payload.get("callbackData") or "")
            callback_query_id = str(inbox.payload.get("callbackQueryId") or "")
            chat_id = str(inbox.payload["chatId"])
            source_message_id = str(inbox.payload.get("sourceMessageId") or "")
            if callback_data.startswith("q1:"):
                try:
                    question_id, interaction = decode_question_callback(callback_data)
                except ValueError:
                    response = _question_result_text(error_code="question.notFound")
                    inbox.status = "completed"
                    inbox.processing_at = None
                    await db.commit()
                    if token is not None:
                        try:
                            async with Bot(token=token) as bot:
                                await bot.answer_callback_query(
                                    callback_query_id=callback_query_id, text=response
                                )
                        except (InvalidToken, NetworkError, TelegramError, TimedOut):
                            logger.warning(
                                "Telegram question callback acknowledgement failed"
                            )
                    return None
                result, response = await _process_question_interaction(
                    db,
                    inbox,
                    endpoint,
                    question_id,
                    interaction,
                    source_message_id,
                )
                if token is not None:
                    if result is not None and result.changed:
                        reply_message_id = await _update_question_message(
                            token,
                            chat_id,
                            source_message_id,
                            result,
                            callback_query_id=callback_query_id,
                            response=response,
                        )
                        await _track_question_messages(
                            db,
                            question_id,
                            source_message_id,
                            reply_message_id,
                        )
                    else:
                        try:
                            async with Bot(token=token) as bot:
                                await bot.answer_callback_query(
                                    callback_query_id=callback_query_id, text=response
                                )
                        except (InvalidToken, NetworkError, TelegramError, TimedOut):
                            logger.warning(
                                "Telegram question callback acknowledgement failed"
                            )
                return None
            try:
                approval_id, decision = decode_approval_callback(callback_data)
            except ValueError:
                response = _approval_result_text(error_code="approval.notFound")
                inbox.status = "completed"
                inbox.processing_at = None
                await db.commit()
                if token is not None:
                    await _answer_approval_callback(
                        token,
                        callback_query_id,
                        chat_id,
                        source_message_id,
                        response,
                        remove_buttons=False,
                    )
                return None
            response, resolved_decision = await _process_approval_action(
                db,
                inbox,
                endpoint,
                approval_id,
                decision,
                source_external_message_id=source_message_id,
                reply_with_message=False,
            )
            if token is not None:
                await _answer_approval_callback(
                    token,
                    callback_query_id,
                    chat_id,
                    source_message_id,
                    response,
                    remove_buttons=resolved_decision is not None,
                )
            return None
        reply_to_message_id = str(inbox.payload.get("replyToMessageId") or "")
        if text.strip() and reply_to_message_id:
            question_outbox = await db.scalar(
                select(MessagingOutbox)
                .where(
                    MessagingOutbox.endpoint_id == endpoint.id,
                    MessagingOutbox.status == "delivered",
                    MessagingOutbox.deduplication_key.startswith("question:"),
                    MessagingOutbox.external_message_ids.contains(
                        [reply_to_message_id]
                    ),
                )
                .order_by(MessagingOutbox.created_at.desc())
                .limit(1)
            )
            try:
                question_envelope = (
                    parse_question_envelope(question_outbox.payload)
                    if question_outbox is not None
                    else None
                )
            except ValidationError:
                question_envelope = None
            if question_envelope is not None:
                main_message_id = _question_main_message_id(
                    question_outbox, question_envelope
                )
                try:
                    token = await endpoint_token(db, endpoint)
                except TelegramServiceError:
                    token = None
                result, _response = await _process_question_interaction(
                    db,
                    inbox,
                    endpoint,
                    question_envelope.question_id,
                    QuestionInteraction(action="text", text=text.strip()),
                    reply_to_message_id,
                )
                if (
                    token is not None
                    and result is not None
                    and main_message_id is not None
                ):
                    reply_message_id = await _update_question_message(
                        token,
                        str(inbox.payload["chatId"]),
                        main_message_id,
                        result,
                    )
                    if result.changed:
                        await _track_question_messages(
                            db,
                            question_envelope.question_id,
                            main_message_id,
                            reply_message_id,
                        )
                return None
        approval_command = parse_approval_command(text)
        if approval_command is not None:
            await _process_approval_action(
                db,
                inbox,
                endpoint,
                *approval_command,
                reply_with_message=True,
            )
            return None
        if text.split(" ", 1)[0].split("@", 1)[0].casefold() == "/approval":
            _enqueue_text(
                db,
                endpoint,
                destination=str(inbox.payload["chatId"]),
                key=f"approval-invalid:{inbox.id}",
                text="Der Freigabebefehl ist ungültig oder unvollständig.",
                inbox_id=inbox.id,
            )
            inbox.status = "completed"
            inbox.processing_at = None
            await db.commit()
            return None
        if await _command(db, inbox, endpoint, text):
            inbox.status = "completed"
            inbox.processing_at = None
            await db.commit()
            return None
        waiting_for_answer = (
            await db.scalar(
                select(AgentRun.id)
                .join(Message, Message.id == AgentRun.input_message_id)
                .where(
                    AgentRun.session_id == endpoint.current_session_id,
                    AgentRun.status == "waiting_input",
                    Message.source_endpoint_id == endpoint.id,
                )
                .limit(1)
            )
            if endpoint.current_session_id is not None
            else None
        )
        if waiting_for_answer is not None:
            _enqueue_text(
                db,
                endpoint,
                destination=str(inbox.payload["chatId"]),
                key=f"question-reply-required:{inbox.id}",
                text=(
                    "Bitte nutze das eingeblendete Antwortfeld oder Telegrams Funktion "
                    "„Antworten“ an der Fragen-Nachricht."
                ),
                inbox_id=inbox.id,
            )
            inbox.status = "completed"
            inbox.processing_at = None
            inbox.error_code = None
            await db.commit()
            return None
        existing_run = await db.get(AgentRun, inbox.id)
        if existing_run is not None:
            endpoint.current_session_id = existing_run.session_id
            inbox.session_id = existing_run.session_id
            inbox.input_message_id = existing_run.input_message_id
            inbox.agent_run_id = existing_run.id
            inbox.status = "completed"
            inbox.processing_at = None
            await db.commit()
            return existing_run.id
        try:
            attachments = await _download_media(
                db, endpoint, list(inbox.payload.get("media") or [])
            )
            content = text.strip()
            if len(content) > MESSAGE_CONTENT_MAX_LENGTH:
                raise TelegramServiceError("validation.tooLong")
            from mato_loop_backend.sessions.service import SessionServiceError, get_owned_session

            try:
                session = await get_owned_session(
                    db, endpoint.current_session_id, endpoint.user_id
                ) if endpoint.current_session_id else None
            except SessionServiceError as exc:
                raise TelegramServiceError(exc.code) from exc
            if session is None or session.user_id != endpoint.user_id or session.session_type != "chat":
                run = await _new_session(db, endpoint, content, attachments, inbox.id)
            else:
                run = await AgentRuntime.start(
                    db,
                    session,
                    content,
                    run_id=inbox.id,
                    attachments=attachments,
                    source_type="endpoint",
                    source_provider="telegram",
                    source_endpoint_id=endpoint.id,
                )
            allowed = await channel_provider_allowed(db, "telegram", lock=True)
            user = await db.scalar(
                select(UserAccount)
                .where(UserAccount.id == endpoint.user_id)
                .with_for_update()
            )
            endpoint = await db.scalar(
                select(MessagingEndpoint)
                .where(MessagingEndpoint.id == endpoint.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            inbox = await db.scalar(
                select(MessagingInbox)
                .where(MessagingInbox.id == inbox.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if endpoint is None or inbox is None:
                return None
            if (
                inbox.status != "processing"
                or user is None
                or not user.is_active
                or not allowed
                or endpoint.paired_chat_id != inbox.payload.get("chatId")
                or endpoint.paired_user_id != inbox.payload.get("userId")
            ):
                await db.rollback()
                return None
            endpoint.current_session_id = run.session_id
            inbox.session_id = run.session_id
            inbox.input_message_id = run.input_message_id
            inbox.agent_run_id = run.id
            inbox.status = "completed"
            inbox.processing_at = None
            inbox.error_code = None
            await db.commit()
            return run.id
        except (
            AgentRunError,
            AttachmentError,
            AttachmentProcessingError,
            InvalidToken,
            NetworkError,
            OSError,
            ProviderError,
            TelegramError,
            TelegramServiceError,
            TimedOut,
        ) as exc:
            await db.rollback()
            inbox = await db.get(MessagingInbox, inbox_id)
            endpoint = await db.get(MessagingEndpoint, inbox.endpoint_id) if inbox else None
            if inbox is None or endpoint is None:
                return None
            code = getattr(exc, "code", None) or getattr(exc, "public_code", None) or "channel.unavailable"
            inbox.processing_at = None
            inbox.error_code = code
            if code == "session.runAlreadyActive" and inbox.attempts < OUTBOX_MAX_ATTEMPTS:
                inbox.status = "pending"
                inbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=10)
            else:
                inbox.status = "failed"
                _enqueue_text(
                    db,
                    endpoint,
                    destination=inbox.payload["chatId"],
                    key=f"inbox-error:{inbox.id}",
                    text="Die Nachricht konnte nicht verarbeitet werden. Prüfe die Session in MatoLoop.",
                    inbox_id=inbox.id,
                )
            await db.commit()
            return None


async def _progress_target(run_id: UUID) -> tuple[str, str, int, int] | None:
    async with AsyncSessionFactory() as db:
        allowed = await channel_provider_allowed(db, "telegram", lock=True)
        row = (
            await db.execute(
                select(MessagingInbox, MessagingEndpoint, UserAccount, AgentRun)
                .join(MessagingEndpoint, MessagingEndpoint.id == MessagingInbox.endpoint_id)
                .join(UserAccount, UserAccount.id == MessagingEndpoint.user_id)
                .join(AgentRun, AgentRun.id == MessagingInbox.agent_run_id)
                .where(MessagingInbox.agent_run_id == run_id)
            )
        ).one_or_none()
        if row is None:
            await db.rollback()
            return None
        inbox, endpoint, user, run = row
        if (
            not allowed
            or run.status not in ("queued", "running", "waiting_approval", "waiting_input")
            or not user.is_active
            or not endpoint.enabled
            or endpoint.deleted_at is not None
            or endpoint.provider != "telegram"
            or endpoint.paired_chat_id != inbox.payload.get("chatId")
            or endpoint.paired_user_id != inbox.payload.get("userId")
        ):
            await db.rollback()
            return None
        try:
            chat_id = int(endpoint.paired_chat_id)
            draft_id = int(inbox.external_message_id or "0")
        except ValueError:
            await db.rollback()
            return None
        if draft_id == 0:
            draft_id = run_id.int % 2_147_483_647 or 1
        token = await endpoint_token(db, endpoint)
        status = run.status
        await db.rollback()
        return status, token, chat_id, draft_id


def _progress_activity(event: dict) -> str | None:
    event_type = event.get("type")
    if event_type == "run.phase" and event.get("phase") == "finalizing":
        return None
    if event_type == "run.phase" and event.get("phase") == "processing_tool":
        display = event.get("toolDisplay")
        if isinstance(display, dict):
            emoji, label, summary = (
                display.get("emoji"),
                display.get("label"),
                display.get("summary"),
            )
            if isinstance(emoji, str) and isinstance(label, str):
                return f"{emoji} {label}{f' · {summary}' if isinstance(summary, str) else ''}"
    supplied = event.get("activityLabel")
    supplied = supplied.strip() if isinstance(supplied, str) else ""
    if event_type == "run.started":
        return f"⚙️ {run_activity_label('preparing_context')}"
    if event_type == "run.phase":
        phase = event.get("phase")
        if not isinstance(phase, str):
            return None
        label = supplied or run_activity_label(
            phase,
            model_request=event.get("modelRequest"),
            provider_attempt=event.get("providerAttempt"),
            provider_attempt_total=event.get("providerAttemptTotal"),
            tool=event.get("tool"),
        )
        if label is None:
            return None
        icon = {
            "compacting_context": "🗜️",
            "waiting_approval": "🔐",
            "waiting_input": "❓",
            "reconnecting": "🔄",
        }.get(phase, "⚙️")
        return f"{icon} {label}"
    if event_type == "reasoning.updated":
        reasoning = event.get("reasoning")
        label = supplied or reasoning_activity_label(
            reasoning if isinstance(reasoning, dict) else None
        )
        return f"🧠 Nachdenken{f' · {label}' if label != 'Nachdenken' else ''}"
    if event_type == "approval.created":
        return f"🔐 {run_activity_label('waiting_approval')}"
    return None


def _progress_draft(activity: str, content: str) -> str:
    if content:
        return content
    return activity


def _retry_after_seconds(exc: RetryAfter) -> float:
    retry_after = exc.retry_after
    return (
        retry_after.total_seconds()
        if isinstance(retry_after, timedelta)
        else float(retry_after)
    )


async def _relay_run_progress(run_id: UUID) -> None:
    try:
        initial = await _progress_target(run_id)
        if initial is None:
            return
        _, token, chat_id, draft_id = initial
        content = ""
        activity = ""
        reasoning_label = ""
        resume_activity = ""
        pending_interactions: set[str] = set()
        draft_dirty = False
        next_draft_at = 0.0
        next_indicator_at = 0.0
        async def progress_events():
            async with aclosing(run_events.subscribe(run_id)) as stream:
                async for event in stream:
                    if event.get("type") == "run.snapshot":
                        yield {"type": "run.snapshot", "snapshotReplay": True}
                        for item in event.get("events", []):
                            yield {**item, "snapshotReplay": True}
                        yield {"type": "message.delta", "delta": event.get("content", "")}
                    else:
                        yield event

        events = progress_events()
        event_task = asyncio.create_task(anext(events))
        try:
            async with Bot(token=token) as bot:
                target = await _progress_target(run_id)
                if target is None:
                    return
                status, current_token, current_chat_id, current_draft_id = target
                if (
                    current_token != token
                    or current_chat_id != chat_id
                    or current_draft_id != draft_id
                ):
                    return
                next_indicator_at = (
                    asyncio.get_running_loop().time() + TYPING_REFRESH_SECONDS
                )
                if status not in {"waiting_approval", "waiting_input"}:
                    try:
                        await bot.send_chat_action(
                            chat_id=chat_id,
                            action=ChatAction.TYPING,
                        )
                    except RetryAfter as exc:
                        next_indicator_at = (
                            asyncio.get_running_loop().time()
                            + max(_retry_after_seconds(exc), TYPING_REFRESH_SECONDS)
                        )
                while True:
                    now = asyncio.get_running_loop().time()
                    deadlines = [next_indicator_at]
                    if draft_dirty:
                        deadlines.append(next_draft_at)
                    elif activity or content:
                        deadlines.append(next_draft_at + DRAFT_REFRESH_SECONDS)
                    deadline = min(deadlines)
                    done, _ = await asyncio.wait(
                        {event_task}, timeout=max(0.0, deadline - now)
                    )
                    if event_task in done:
                        try:
                            event = event_task.result()
                        except StopAsyncIteration:
                            return
                        event_task = asyncio.create_task(anext(events))
                        event_type = event.get("type")
                        interaction_finished = False
                        if event_type == "approval.created":
                            approval = event.get("approval")
                            if isinstance(approval, dict) and isinstance(approval.get("id"), str):
                                pending_interactions.add(f"approval:{approval['id']}")
                        elif event_type == "question.created":
                            question = event.get("question")
                            if isinstance(question, dict) and isinstance(question.get("id"), str):
                                pending_interactions.add(f"question:{question['id']}")
                        elif event_type in {"approval.resolved", "approval.expired"}:
                            approval_id = event.get("approvalId")
                            if isinstance(approval_id, str):
                                pending_interactions.discard(f"approval:{approval_id}")
                                interaction_finished = True
                        elif event_type in {
                            "question.answered",
                            "question.rejected",
                            "question.expired",
                        }:
                            question_id = event.get("questionId")
                            if isinstance(question_id, str):
                                pending_interactions.discard(f"question:{question_id}")
                                interaction_finished = True
                        if event_type == "run.snapshot":
                            draft_dirty = bool(activity or content)
                            content = activity = reasoning_label = resume_activity = ""
                            pending_interactions.clear()
                        elif event_type == "message.delta":
                            delta = event.get("delta")
                            if isinstance(delta, str) and delta:
                                content += delta
                                activity = ""
                                draft_dirty = True
                        elif event_type == "question.created":
                            resume_activity = resume_activity or activity
                            draft_dirty = bool(activity or content)
                            content = ""
                            activity = ""
                            reasoning_label = ""
                        elif interaction_finished and not pending_interactions:
                            if resume_activity:
                                activity = resume_activity
                                resume_activity = ""
                                draft_dirty = True
                            next_indicator_at = 0.0
                        elif (
                            event_type == "run.timing"
                            and event.get("activeDurationRunning") is True
                        ):
                            if resume_activity and not pending_interactions:
                                activity = resume_activity
                                resume_activity = ""
                                draft_dirty = True
                            next_indicator_at = 0.0
                        elif not content:
                            phase = event.get("phase")
                            if event_type == "approval.created":
                                resume_activity = resume_activity or activity
                                approval = event.get("approval")
                                details = (
                                    approval.get("details")
                                    if isinstance(approval, dict)
                                    else None
                                )
                                if not resume_activity:
                                    resume_activity = run_activity_label(
                                        "processing_tool",
                                        tool=(
                                            details.get("tool")
                                            if isinstance(details, dict)
                                            else None
                                        ),
                                    )
                                display = details.get("toolDisplay") if isinstance(details, dict) else None
                                if isinstance(display, dict) and not activity:
                                    resume_activity = _progress_activity(
                                        {
                                            "type": "run.phase",
                                            "phase": "processing_tool",
                                            "toolDisplay": display,
                                        }
                                    ) or resume_activity
                            if event_type == "approval.created" or (
                                event_type == "run.phase"
                                and phase in {"preparing_context", "processing_tool"}
                            ):
                                reasoning_label = ""
                            next_activity = _progress_activity(event)
                            if event_type == "reasoning.updated":
                                if next_activity and next_activity != "🧠 Nachdenken":
                                    reasoning_label = next_activity
                                next_activity = reasoning_label or next_activity
                            elif (
                                event_type == "run.phase"
                                and phase == "waiting_model"
                                and reasoning_label
                            ):
                                next_activity = reasoning_label
                            if event_type == "run.phase" and phase in {
                                "preparing_context",
                                "waiting_model",
                            } and activity:
                                next_activity = None
                            if event_type == "run.phase" and phase == "finalizing" and not content:
                                next_activity = ""
                            if next_activity is not None and next_activity != activity:
                                activity = next_activity
                                draft_dirty = True
                        if event_type == "message.completed":
                            message = event.get("message")
                            if isinstance(message, dict) and message.get("toolCalls"):
                                content = ""
                                tool = message["toolCalls"][0]
                                if isinstance(tool, dict):
                                    activity = _progress_activity(
                                        {
                                            "type": "run.phase",
                                            "phase": "processing_tool",
                                            "tool": tool.get("name"),
                                            "toolDisplay": tool.get("toolDisplay"),
                                        }
                                    ) or activity
                                draft_dirty = bool(activity)
                            elif isinstance(message, dict) and isinstance(message.get("content"), str):
                                completed_content = message["content"]
                                draft_dirty = bool(activity or completed_content != content)
                                content = completed_content
                                activity = ""
                        elif event_type in {"message.interrupted", "message.continued"}:
                            content = ""
                            reasoning_label = ""
                            draft_dirty = True
                        elif event_type in {
                            "run.completed",
                            "run.failed",
                            "run.cancelled",
                        }:
                            return
                        if event.get("snapshotReplay"):
                            continue
                    now = asyncio.get_running_loop().time()
                    has_draft = bool(activity or content)
                    if (
                        not draft_dirty
                        and has_draft
                        and now >= next_draft_at + DRAFT_REFRESH_SECONDS
                    ):
                        draft_dirty = True
                    send_draft = draft_dirty and now >= next_draft_at
                    send_typing = now >= next_indicator_at
                    if not send_draft and not send_typing:
                        continue
                    target = await _progress_target(run_id)
                    if target is None:
                        return
                    status, current_token, current_chat_id, current_draft_id = target
                    if (
                        current_token != token
                        or current_chat_id != chat_id
                        or current_draft_id != draft_id
                    ):
                        return
                    if pending_interactions or status in {"waiting_approval", "waiting_input"}:
                        if send_typing:
                            next_indicator_at = now + TYPING_REFRESH_SECONDS
                        send_typing = False
                    if send_draft:
                        parts = render_channel_text(
                            "telegram",
                            _progress_draft(activity, content) or DRAFT_CLEAR_TEXT,
                            content_format="markdown" if content else "plain",
                            partial=True,
                        )
                        part = parts[-1] if parts else RenderedChannelText("")
                        try:
                            await bot.send_message_draft(
                                chat_id=chat_id,
                                draft_id=draft_id,
                                text=part.text,
                                parse_mode=(
                                    ParseMode.HTML
                                    if part.syntax == TELEGRAM_HTML
                                    else None
                                ),
                            )
                        except RetryAfter as exc:
                            next_draft_at = now + max(
                                _retry_after_seconds(exc), DRAFT_UPDATE_SECONDS
                            )
                            continue
                        draft_dirty = False
                        next_draft_at = now + DRAFT_UPDATE_SECONDS
                    if send_typing:
                        try:
                            await bot.send_chat_action(
                                chat_id=chat_id,
                                action=ChatAction.TYPING,
                            )
                        except RetryAfter as exc:
                            next_indicator_at = now + max(
                                _retry_after_seconds(exc), TYPING_REFRESH_SECONDS
                            )
                        else:
                            next_indicator_at = now + TYPING_REFRESH_SECONDS
        finally:
            event_task.cancel()
            await asyncio.gather(event_task, return_exceptions=True)
            await events.aclose()
    except asyncio.CancelledError:
        raise
    except (
        InvalidToken,
        NetworkError,
        OSError,
        TelegramError,
        TelegramServiceError,
        TimedOut,
    ):
        logger.warning("Telegram progress relay stopped for run %s", run_id)
    except Exception:
        logger.exception("Telegram progress relay failed for run %s", run_id)


async def claim_inbox() -> UUID | None:
    async with AsyncSessionFactory() as db:
        now = datetime.now(UTC)
        inbox = await db.scalar(
            select(MessagingInbox)
            .join(MessagingEndpoint, MessagingEndpoint.id == MessagingInbox.endpoint_id)
            .join(UserAccount, UserAccount.id == MessagingEndpoint.user_id)
            .where(
                MessagingEndpoint.enabled.is_(True),
                MessagingEndpoint.provider == "telegram",
                MessagingEndpoint.deleted_at.is_(None),
                UserAccount.is_active.is_(True),
                or_(
                    MessagingInbox.status == "pending",
                    (
                        (MessagingInbox.status == "processing")
                        & (MessagingInbox.processing_at < now - PROCESSING_TIMEOUT)
                    ),
                ),
                or_(
                    MessagingInbox.next_attempt_at.is_(None),
                    MessagingInbox.next_attempt_at <= now,
                ),
            )
            .order_by(MessagingInbox.created_at, MessagingInbox.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if inbox is None:
            await db.rollback()
            return None
        inbox.status = "processing"
        inbox.processing_at = now
        inbox.attempts += 1
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            return None
        return inbox.id


async def reconcile_runs() -> int:
    async with AsyncSessionFactory() as db:
        delivery_allowed = await channel_provider_allowed(db, "telegram", lock=True)
        rows = list(
            (
                await db.execute(
                    select(MessagingInbox, MessagingEndpoint, AgentRun)
                    .join(MessagingEndpoint, MessagingEndpoint.id == MessagingInbox.endpoint_id)
                    .join(UserAccount, UserAccount.id == MessagingEndpoint.user_id)
                    .join(AgentRun, AgentRun.id == MessagingInbox.agent_run_id)
                    .where(
                        MessagingInbox.status == "completed",
                        MessagingEndpoint.provider == "telegram",
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
                else "Die Antwort wurde durch einen Neustart unterbrochen."
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
                    status="pending" if delivery_allowed else "failed",
                    error_code=None if delivery_allowed else "channel.providerDisabled",
                )
            )
            count += 1
        await db.commit()
        return count


async def _send_attachment(
    bot: Bot, destination: str, path: Path, kind: str, filename: str
) -> int:
    data = await asyncio.to_thread(Path(path).read_bytes)
    if kind == "image":
        sent = await bot.send_photo(chat_id=destination, photo=data, filename=filename)
    elif kind == "audio":
        sent = await bot.send_audio(chat_id=destination, audio=data, filename=filename)
    elif kind == "video":
        sent = await bot.send_video(chat_id=destination, video=data, filename=filename)
    else:
        sent = await bot.send_document(chat_id=destination, document=data, filename=filename)
    return sent.message_id


async def deliver_outbox(outbox_id: UUID) -> None:
    async with AsyncSessionFactory() as db:
        item = await db.get(MessagingOutbox, outbox_id)
        endpoint_hint = (
            await db.get(MessagingEndpoint, item.endpoint_id) if item else None
        )
        if item is None or endpoint_hint is None or item.status != "processing":
            return
        provider_allowed = await channel_provider_allowed(
            db, endpoint_hint.provider, lock=True
        )
        user = await db.scalar(
            select(UserAccount)
            .where(UserAccount.id == endpoint_hint.user_id)
            .with_for_update()
        )
        endpoint = await db.scalar(
            select(MessagingEndpoint)
            .where(MessagingEndpoint.id == endpoint_hint.id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        item = await db.scalar(
            select(MessagingOutbox)
            .where(MessagingOutbox.id == outbox_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )
        if item is None or endpoint is None or item.status != "processing":
            return
        active = user is not None and user.is_active
        pairing_valid = bool(
            item.payload.get("allowUnpaired")
            or endpoint.paired_chat_id == item.destination
        )
        allowed = (
            active
            and provider_allowed
            and endpoint.enabled
            and endpoint.provider == "telegram"
            and endpoint.deleted_at is None
            and pairing_valid
        )
        if not allowed:
            item.status = "failed"
            item.processing_at = None
            item.next_attempt_at = None
            item.error_code = (
                "channel.providerDisabled"
                if not provider_allowed
                else "channel.pairingChanged"
            )
            if item.automation_run_id is not None:
                automation_run = await db.get(AutomationRun, item.automation_run_id)
                if automation_run is not None and automation_run.delivery_status == "pending":
                    automation_run.delivery_attempts = item.attempts
                    if active:
                        automation_run.delivery_channel_type = "app"
                        automation_run.delivery_endpoint_id = None
                        automation_run.delivery_destination = None
                    else:
                        automation_run.delivery_status = "cancelled"
                        automation_run.delivery_next_attempt_at = None
            await db.commit()
            return
        try:
            token = await endpoint_token(db, endpoint)
            approval_payload = parse_approval_payload(item.payload)
            question_payload = parse_question_envelope(item.payload)
            reply_markup = None
            outbox_text = str(item.payload.get("text") or "")
            if approval_payload is not None:
                approval = await db.get(ApprovalRequest, approval_payload.approval_id)
                if (
                    approval is None
                    or approval.status != "pending"
                    or approval.session_id != approval_payload.session_id
                    or approval.run_id != approval_payload.run_id
                    or approval.user_id != endpoint.user_id
                ):
                    item.status = "failed"
                    item.processing_at = None
                    item.next_attempt_at = None
                    item.error_code = "approval.notFound"
                    await db.commit()
                    return
                reply_markup = InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                action.label,
                                callback_data=encode_approval_callback(
                                    approval_payload.approval_id, action.decision
                                ),
                            )
                        ]
                        for action in approval_payload.actions
                    ]
                )
            elif question_payload is not None:
                question_request = await db.get(
                    QuestionRequest, question_payload.question_id
                )
                question_session = await db.get(
                    ChatSession, question_payload.session_id
                )
                if (
                    question_request is None
                    or question_session is None
                    or question_request.status != "pending"
                    or question_request.session_id != question_payload.session_id
                    or question_request.run_id != question_payload.run_id
                    or question_session.user_id != endpoint.user_id
                ):
                    item.status = "failed"
                    item.processing_at = None
                    item.next_attempt_at = None
                    item.error_code = "question.notFound"
                    await db.commit()
                    return
                outbox_text = _question_prompt(question_payload)
                reply_markup = _question_markup(question_payload)
            attachments: list[tuple[Path, str, str]] = []
            if item.payload.get("includeAttachments") and item.source_message_id:
                source = await db.scalar(
                    select(Message)
                    .where(Message.id == item.source_message_id)
                    .options(selectinload(Message.attachments))
                )
                if source is not None:
                    attachment_service = AttachmentService()
                    for attachment in source.attachments:
                        path = await attachment_service.verified_path(
                            attachment, source.session_id
                        )
                        attachments.append(
                            (
                                path,
                                attachment.kind,
                                attachment.filename or "attachment",
                            )
                        )
            parts = render_channel_text(
                endpoint.provider,
                outbox_text,
                content_format=str(item.payload.get("contentFormat") or "plain"),
            )
            destination = item.destination
            attempt = item.attempts
            await db.rollback()
            message_ids = []
            async with Bot(token=token) as bot:
                for index, part in enumerate(parts):
                    sent = await bot.send_message(
                        chat_id=destination,
                        text=part.text,
                        parse_mode=(
                            ParseMode.HTML if part.syntax == TELEGRAM_HTML else None
                        ),
                        reply_markup=reply_markup if index == len(parts) - 1 else None,
                    )
                    message_ids.append(str(sent.message_id))
                if question_payload is not None:
                    message_ids = [message_ids[-1]]
                    reply_message_id = await _send_question_reply_prompt(
                        bot, destination, question_payload
                    )
                    if reply_message_id is not None:
                        message_ids.append(reply_message_id)
                for path, kind, filename in attachments:
                    message_ids.append(
                        str(
                            await _send_attachment(
                                bot, destination, path, kind, filename
                            )
                        )
                    )
            item = await db.scalar(
                select(MessagingOutbox)
                .where(MessagingOutbox.id == outbox_id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
            if item is None or item.status != "processing" or item.attempts != attempt:
                await db.rollback()
                return
            item.status = "delivered"
            item.external_message_ids = message_ids
            item.delivered_at = datetime.now(UTC)
            item.processing_at = None
            item.error_code = None
            if item.automation_run_id is not None:
                automation_run = await db.get(AutomationRun, item.automation_run_id)
                if automation_run is not None:
                    automation_run.delivery_status = "delivered"
                    automation_run.delivery_attempts = item.attempts
                    automation_run.delivery_next_attempt_at = None
            await db.commit()
        except ValidationError:
            await db.rollback()
            item = await db.get(MessagingOutbox, outbox_id)
            if item is not None:
                item.status = "failed"
                item.processing_at = None
                item.next_attempt_at = None
                item.error_code = "channel.invalidResponse"
                await db.commit()
        except RetryAfter as exc:
            await db.rollback()
            item = await db.get(MessagingOutbox, outbox_id)
            if item is not None:
                item.status = "pending"
                item.processing_at = None
                retry_after = exc.retry_after
                item.next_attempt_at = datetime.now(UTC) + (
                    retry_after
                    if isinstance(retry_after, timedelta)
                    else timedelta(seconds=float(retry_after))
                )
                item.error_code = "http.rateLimited"
                if item.automation_run_id is not None:
                    automation_run = await db.get(AutomationRun, item.automation_run_id)
                    if automation_run is not None:
                        automation_run.delivery_attempts = item.attempts
                        automation_run.delivery_next_attempt_at = item.next_attempt_at
                await db.commit()
        except (AttachmentError, InvalidToken, NetworkError, OSError, TelegramError, TimedOut):
            await db.rollback()
            item = await db.get(MessagingOutbox, outbox_id)
            if item is None:
                return
            item.processing_at = None
            item.error_code = "channel.unavailable"
            if item.attempts >= OUTBOX_MAX_ATTEMPTS:
                item.status = "failed"
                item.next_attempt_at = None
                if item.automation_run_id is not None:
                    automation_run = await db.get(AutomationRun, item.automation_run_id)
                    if automation_run is not None:
                        automation_run.delivery_status = "failed"
                        automation_run.delivery_attempts = item.attempts
                        automation_run.delivery_next_attempt_at = None
            else:
                item.status = "pending"
                item.next_attempt_at = datetime.now(UTC) + timedelta(seconds=30 * item.attempts)
                if item.automation_run_id is not None:
                    automation_run = await db.get(AutomationRun, item.automation_run_id)
                    if automation_run is not None:
                        automation_run.delivery_attempts = item.attempts
                        automation_run.delivery_next_attempt_at = item.next_attempt_at
            await db.commit()


async def claim_outbox() -> tuple[UUID, UUID | None] | None:
    async with AsyncSessionFactory() as db:
        now = datetime.now(UTC)
        item = await db.scalar(
            select(MessagingOutbox)
            .join(MessagingEndpoint, MessagingEndpoint.id == MessagingOutbox.endpoint_id)
            .join(UserAccount, UserAccount.id == MessagingEndpoint.user_id)
            .where(
                MessagingEndpoint.enabled.is_(True),
                MessagingEndpoint.provider == "telegram",
                MessagingEndpoint.deleted_at.is_(None),
                UserAccount.is_active.is_(True),
                or_(
                    MessagingOutbox.status == "pending",
                    (
                        (MessagingOutbox.status == "processing")
                        & (MessagingOutbox.processing_at < now - PROCESSING_TIMEOUT)
                    ),
                ),
                or_(
                    MessagingOutbox.next_attempt_at.is_(None),
                    MessagingOutbox.next_attempt_at <= now,
                ),
            )
            .order_by(MessagingOutbox.created_at, MessagingOutbox.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if item is None:
            await db.rollback()
            return None
        run_id = (
            await db.scalar(
                select(MessagingInbox.agent_run_id).where(
                    MessagingInbox.id == item.inbox_id
                )
            )
            if item.inbox_id is not None
            else None
        )
        item_id = item.id
        item.status = "processing"
        item.processing_at = now
        item.attempts += 1
        await db.commit()
        return item_id, run_id


class TelegramScheduler:
    _tasks: list[asyncio.Task] = []
    _progress_tasks: dict[UUID, asyncio.Task] = {}
    _owner = str(uuid4())

    @classmethod
    def start(cls) -> None:
        if not cls._tasks:
            cls._tasks = [
                asyncio.create_task(cls._poll_loop()),
                asyncio.create_task(cls._work_loop()),
            ]

    @classmethod
    async def stop(cls) -> None:
        tasks = [*cls._tasks, *cls._progress_tasks.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        cls._tasks = []
        cls._progress_tasks = {}

    @classmethod
    def _start_progress(cls, run_id: UUID) -> None:
        task = asyncio.create_task(_relay_run_progress(run_id))
        cls._progress_tasks[run_id] = task
        task.add_done_callback(
            lambda completed: cls._discard_progress(run_id, completed)
        )

    @classmethod
    def _discard_progress(cls, run_id: UUID, task: asyncio.Task) -> None:
        if cls._progress_tasks.get(run_id) is task:
            cls._progress_tasks.pop(run_id, None)

    @classmethod
    async def _stop_progress(cls, run_id: UUID | None) -> None:
        task = cls._progress_tasks.pop(run_id, None)
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    @classmethod
    async def _poll_loop(cls) -> None:
        while True:
            try:
                async with AsyncSessionFactory() as db:
                    allowed = await channel_provider_allowed(db, "telegram")
                    endpoint_ids = (
                        list(
                            (
                                await db.scalars(
                                    select(MessagingEndpoint.id)
                                    .join(
                                        UserAccount,
                                        UserAccount.id == MessagingEndpoint.user_id,
                                    )
                                    .where(
                                        MessagingEndpoint.provider == "telegram",
                                        MessagingEndpoint.enabled.is_(True),
                                        MessagingEndpoint.deleted_at.is_(None),
                                        UserAccount.is_active.is_(True),
                                    )
                                )
                            ).all()
                        )
                        if allowed
                        else []
                    )
                await asyncio.gather(*(_poll(endpoint_id, cls._owner) for endpoint_id in endpoint_ids))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram polling iteration failed")
            await asyncio.sleep(1)

    @classmethod
    async def _work_loop(cls) -> None:
        while True:
            try:
                async with AsyncSessionFactory() as db:
                    allowed = await channel_provider_allowed(db, "telegram")
                if allowed:
                    while inbox_id := await claim_inbox():
                        if run_id := await process_inbox(inbox_id):
                            cls._start_progress(run_id)
                await reconcile_runs()
                if allowed:
                    while claimed_outbox := await claim_outbox():
                        outbox_id, run_id = claimed_outbox
                        await cls._stop_progress(run_id)
                        await deliver_outbox(outbox_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Telegram delivery iteration failed")
            await asyncio.sleep(2)
