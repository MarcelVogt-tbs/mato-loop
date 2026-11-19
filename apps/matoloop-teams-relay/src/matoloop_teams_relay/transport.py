from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from random import uniform
from time import monotonic
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from httpx2 import HTTPStatusError, RequestError
from matoloop_teams_protocol import MAX_FILE_BYTES, Blob, Delivery, Event, Identity, StreamPreview
from matoloop_teams_protocol.onboarding import pairing_card
from microsoft_teams.api import (
    AdaptiveCardActionMessageResponse,
    AdaptiveCardInvokeActivity,
    Attachment,
    FileConsentInvokeActivity,
    InstalledActivity,
    MessageActivity,
    MessageActivityInput,
    StreamInfoEntity,
    TypingActivityInput,
)
from microsoft_teams.apps import ActivityContext, App

from .config import RelayConfig
from .spool import Record, Spool, SpoolError
from .welcome_queue import WELCOME_TTL, WelcomeJob, WelcomeQueue

logger = logging.getLogger(__name__)
FILE_HOSTS = ("sharepoint.com", "1drv.com", "onedrive.com")
IMAGE_HOSTS = ("asm.skype.com", "teams.microsoft.com")
SERVICE_HOSTS = ("smba.trafficmanager.net", "smba.infra.teams.microsoft.com")
WELCOME_MAX_ATTEMPTS = 3


@dataclass
class ReplyStream:
    source: str
    started: float
    id: str | None = None
    text: str = ""
    sequence: int = 0
    updated: float = 0
    informative: bool = True


def trusted_url(value: str, hosts: tuple[str, ...]) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
        or not any(host == suffix or host.endswith("." + suffix) for suffix in hosts)
    ):
        raise SpoolError("relay.invalidDestination", 422)
    return value


def identity_key(identity: Identity) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"{identity.tenant_id}:{identity.app_id}:{identity.user_id}:{identity.conversation_id}",
    )


def welcome_retry_delay(retry_after: str, attempts: int) -> float:
    try:
        delay = int(retry_after)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(retry_after) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            delay = 0
    return min(WELCOME_TTL, max(2**attempts, delay)) + uniform(0, 1)


async def download(url: str, *, token: str | None = None) -> bytes:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
        async with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()
            content = bytearray()
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > MAX_FILE_BYTES:
                    raise SpoolError("attachment.tooLarge", 413)
    if not content:
        raise SpoolError("relay.emptyFile", 422)
    return bytes(content)


class TeamsTransport:
    def __init__(
        self, bot: App, spool: Spool, config: RelayConfig, welcomes: WelcomeQueue | None = None,
    ) -> None:
        self.bot, self.spool, self.config = bot, spool, config
        self.welcomes = welcomes
        self.tasks: set[asyncio.Task] = set()
        self.send_lock = asyncio.Lock()
        self.streams: dict[UUID, ReplyStream] = {}
        self.closed_streams: dict[tuple[UUID, str], float] = {}
        bot.on_message(self.on_message)
        bot.on_install_add(self.on_install_add)
        bot.on_card_action_execute(self.on_card)
        bot.on_file_consent(self.on_file_consent)

    def identity(self, activity) -> Identity:
        raw = activity.model_dump(mode="json", by_alias=True)
        tenant = (raw.get("channelData") or {}).get("tenant", {}).get("id")
        conversation = raw.get("conversation") or {}
        sender = raw.get("from") or {}
        if (
            raw.get("channelId") != "msteams"
            or conversation.get("conversationType") != "personal"
            or tenant != str(self.config.tenant_id)
        ):
            raise SpoolError("relay.scopeDenied", 403)
        return Identity(
            tenant_id=tenant,
            app_id=self.config.app_id,
            user_id=sender.get("aadObjectId"),
            conversation_id=conversation.get("id"),
            display_name=(sender.get("name") or "")[:160],
        )

    def remember(self, identity: Identity, activity) -> None:
        service_url = trusted_url(activity.service_url or "", SERVICE_HOSTS)
        self.spool.remember_conversation(
            identity_key(identity),
            {
                "identity": identity.model_dump(mode="json"),
                "service_url": service_url,
            },
        )

    async def on_install_add(self, ctx: ActivityContext[InstalledActivity]) -> None:
        activity = ctx.activity
        raw = activity.model_dump(mode="json", by_alias=True)
        conversation = raw.get("conversation") or {}
        tenant = ((raw.get("channelData") or {}).get("tenant") or {}).get("id")
        if (
            raw.get("channelId") != "msteams"
            or raw.get("action") != "add"
            or conversation.get("conversationType") != "personal"
            or tenant != str(self.config.tenant_id)
            or not conversation.get("id")
            or len(conversation["id"]) > 1024
            or not activity.id
            or len(activity.service_url or "") > 2048
        ):
            return
        service_url = trusted_url(activity.service_url or "", SERVICE_HOSTS)
        welcome_id = uuid5(
            NAMESPACE_URL, f"welcome:{self.config.app_id}:{tenant}:{conversation['id']}:{activity.id}",
        )
        # Older relays may have already attempted this welcome.
        if self.spool.records.get(welcome_id) is not None:
            return
        if self.welcomes is not None and not self.welcomes.enqueue(
            welcome_id, conversation["id"], service_url,
        ):
            logger.warning("Teams welcome queue is full; install event was not accepted")
            raise SpoolError("relay.welcomeCapacity", 503)

    async def send_welcome(self, queue: WelcomeQueue, job: WelcomeJob) -> None:
        message = MessageActivityInput(text="Willkommen!").add_attachments(
            Attachment.model_validate({
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": pairing_card(self.config.pairing_origin, welcome=True),
            })
        )
        try:
            async with asyncio.timeout(15):
                await self.bot.send(job.conversation_id, message, service_url=job.service_url)
        except HTTPStatusError as exc:
            status = exc.response.status_code
            if status == 429 and job.attempts < WELCOME_MAX_ATTEMPTS and queue.retry(
                job, welcome_retry_delay(exc.response.headers.get("Retry-After", ""), job.attempts),
            ):
                return
            queue.finish(job, "failed" if 400 <= status < 500 and status != 408 else "unknown")
            logger.warning("Teams welcome send ended with HTTP %s", status)
        except (RequestError, RuntimeError, TimeoutError) as exc:
            queue.finish(job, "unknown")
            logger.warning("Teams welcome delivery could not be confirmed (%s)", type(exc).__name__)
        else:
            queue.finish(job, "sent")

    async def run_welcomes(self, queue: WelcomeQueue) -> None:
        while True:
            try:
                job = queue.claim()
                if job:
                    await self.send_welcome(queue, job)
            except sqlite3.Error:
                logger.error("Teams welcome queue database operation failed", exc_info=False)
                await asyncio.sleep(5)
                continue
            await asyncio.sleep(0.1 if job else 1)

    async def on_message(self, ctx: ActivityContext[MessageActivity]) -> None:
        identity = self.identity(ctx.activity)
        self.remember(identity, ctx.activity)
        if not self.spool.online:
            await ctx.send(
                "MatoLoop ist gerade offline. Die Nachricht wird nicht später ausgeführt."
            )
            return
        event_id = uuid5(identity_key(identity), str(ctx.activity.id))
        if event_id in self.spool.records:
            return
        received = datetime.now(UTC)
        generation = self.spool.generation
        try:
            blobs = await self.incoming_files(ctx, event_id)
            # Another callback may have persisted this event or its receipt during the download.
            if event_id in self.spool.records:
                return
            if (
                not self.spool.online
                or self.spool.generation != generation
                or datetime.now(UTC) >= received + timedelta(seconds=30)
            ):
                raise SpoolError("relay.offline", 503)
            now = datetime.now(UTC)
            event = Event(
                id=event_id,
                identity=identity,
                generation=self.spool.generation,
                received_at=received,
                expires_at=received + timedelta(seconds=30),
                kind="message",
                text=ctx.activity.text or "",
                message_id=str(ctx.activity.id),
                blobs=blobs,
            )
            self.spool.save(
                Record(
                    id=event.id,
                    kind="event",
                    status="handoff",
                    expires_at=now + timedelta(hours=24),
                    payload=event.model_dump(mode="json"),
                )
            )
        except (SpoolError, httpx.HTTPError, OSError):
            logger.warning("Teams incoming attachment or handoff failed", exc_info=False)
            await ctx.send(
                "Die Nachricht konnte nicht an MatoLoop übergeben werden. Bitte prüfe die Verbindung und Dateigröße."
            )

    async def incoming_files(
        self, ctx: ActivityContext[MessageActivity], event_id: UUID
    ) -> list[Blob]:
        attachments = ctx.activity.attachments or []
        if len(attachments) > 10:
            raise SpoolError("validation.tooManyItems", 422)
        blobs = []
        for index, item in enumerate(attachments):
            raw = item.model_dump(mode="json", by_alias=True)
            content_type = raw.get("contentType") or ""
            content = raw.get("content") or {}
            name = (raw.get("name") or f"teams-image-{index}.png")[:255]
            if content_type == "application/vnd.microsoft.teams.file.download.info":
                url = trusted_url(content.get("downloadUrl", ""), FILE_HOSTS)
                data = await download(url)
                mime = "application/octet-stream"
            elif content_type.startswith("image/"):
                url = trusted_url(raw.get("contentUrl", ""), IMAGE_HOSTS)
                token = await self.bot.token_provider.get_app_token()
                if token is None:
                    raise SpoolError("relay.authenticationFailed", 502)
                data = await download(url, token=str(token))
                mime = content_type
            elif content_type == "text/html":
                continue
            else:
                raise SpoolError("relay.unsupportedAttachment", 422)
            if sum(blob.size for blob in blobs) + len(data) > MAX_FILE_BYTES:
                raise SpoolError("attachment.tooLarge", 413)
            blob = Blob(
                id=uuid5(event_id, str(index)), filename=name, mime_type=mime, size=len(data)
            )
            self.spool.put_blob(blob.id, data, event_id)
            blobs.append(blob)
        return blobs

    async def on_card(
        self, ctx: ActivityContext[AdaptiveCardInvokeActivity]
    ) -> AdaptiveCardActionMessageResponse:
        identity = self.identity(ctx.activity)
        self.remember(identity, ctx.activity)
        if not self.spool.online:
            return AdaptiveCardActionMessageResponse(
                value="MatoLoop ist offline. Die Entscheidung wurde nicht übernommen."
            )
        event_id = uuid5(identity_key(identity), str(ctx.activity.id))
        if event_id not in self.spool.records:
            event = Event(
                id=event_id,
                identity=identity,
                generation=self.spool.generation,
                received_at=datetime.now(UTC),
                expires_at=datetime.now(UTC) + timedelta(seconds=30),
                kind="action",
                message_id=str(ctx.activity.id),
                source_message_id=ctx.activity.reply_to_id,
                action=ctx.activity.value.action.data if ctx.activity.value else {},
            )
            try:
                self.spool.save(
                    Record(
                        id=event_id,
                        kind="event",
                        status="handoff",
                        expires_at=datetime.now(UTC) + timedelta(hours=24),
                        payload=event.model_dump(mode="json"),
                    )
                )
            except (SpoolError, OSError):
                logger.warning("Teams card handoff failed", exc_info=False)
                return AdaptiveCardActionMessageResponse(
                    value="Die Entscheidung konnte nicht an MatoLoop übergeben werden. Bitte prüfe die Verbindung."
                )
        return AdaptiveCardActionMessageResponse(value="Wird in MatoLoop geprüft.")

    async def preview(self, value: StreamPreview) -> bool:
        key = identity_key(value.identity)
        async with self.send_lock:
            now = monotonic()
            self.streams = {
                key: state for key, state in self.streams.items() if now - state.started < 3600
            }
            self.closed_streams = {
                pair: timestamp for pair, timestamp in self.closed_streams.items()
                if now - timestamp < 600
            }
            if (key, value.source_message_id) in self.closed_streams:
                return False
            conversation = self.spool.get(key, "conversation")
            source = self.spool.get(uuid5(key, value.source_message_id), "event")
            if source.status != "admitted":
                raise SpoolError("relay.bindingChanged")
            state = self.streams.get(key)
            if state is not None and state.source != value.source_message_id and now - state.started >= 600:
                state = None
            if state is None:
                if key not in self.streams and len(self.streams) >= 128:
                    return False
                state = self.streams[key] = ReplyStream(value.source_message_id, now)
            elif state.source != value.source_message_id or not state.id:
                return False
            if now - state.started >= 540 or (
                not state.informative and (value.informative or not value.text.startswith(state.text))
            ):
                return False
            if (
                (value.text == state.text and value.informative == state.informative)
                or (state.id and now - state.updated < 1)
            ):
                return True
            state.sequence += 1
            # App.send chooses PUT when an activity has an ID; streaming requires POST.
            activity = TypingActivityInput(
                text=value.text, text_format="extendedmarkdown",
                reply_to_id=value.source_message_id,
                entities=[StreamInfoEntity(
                    stream_id=state.id,
                    stream_type="informative" if value.informative else "streaming",
                    stream_sequence=state.sequence,
                )],
            )
            try:
                sent = await self.bot.send(
                    value.identity.conversation_id, activity,
                    service_url=str(conversation.payload["service_url"]),
                )
            except Exception:
                logger.warning("Microsoft preview update was not confirmed", exc_info=False)
                return False
            state.id = state.id or sent.id
            state.text, state.updated = value.text, monotonic()
            state.informative = value.informative
            return bool(state.id)

    async def send(self, delivery: Delivery):
        async with self.send_lock:
            previous = self.spool.records.get(delivery.id)
            fingerprint = hashlib.sha256(delivery.model_dump_json().encode()).hexdigest()
            if previous is not None:
                if (
                    previous.kind != "delivery"
                    or previous.payload.get("fingerprint") != fingerprint
                ):
                    raise SpoolError("relay.idConflict")
                return self.spool.delivery_result(previous)
            if (
                delivery.identity.tenant_id != self.config.tenant_id
                or delivery.identity.app_id != self.config.app_id
            ):
                raise SpoolError("relay.scopeDenied", 403)
            conversation = self.spool.get(identity_key(delivery.identity), "conversation")
            if identity_key(
                Identity.model_validate(conversation.payload["identity"])
            ) != identity_key(delivery.identity):
                raise SpoolError("relay.bindingChanged")
            activity = MessageActivityInput(text=delivery.text, text_format=delivery.text_format)
            key = identity_key(delivery.identity)
            stream = self.streams.get(key) if delivery.source_message_id else None
            if (
                delivery.kind != "message" or stream is None
                or stream.source != delivery.source_message_id
            ):
                stream = None
            if stream and stream.id:
                activity.reply_to_id = stream.source
                if delivery.card and not delivery.text:
                    activity.text = "MatoLoop wartet auf deine Eingabe."
                if monotonic() - stream.started < 540 and (
                    stream.informative or (activity.text or "").startswith(stream.text)
                ):
                    activity.add_entity(StreamInfoEntity(stream_id=stream.id, stream_type="final"))
                else:
                    activity.id = stream.id
                activity.text_format = "extendedmarkdown"
            if delivery.kind == "typing":
                activity = TypingActivityInput()
            elif delivery.kind == "file":
                if (
                    delivery.blob is None
                    or self.spool.blob(delivery.blob.id, delivery.id).stat().st_size
                    != delivery.blob.size
                ):
                    raise SpoolError("relay.blobUnavailable", 422)
                inline = (
                    delivery.blob.mime_type in {"image/png", "image/jpeg", "image/gif"}
                    and delivery.blob.size <= 1_000_000
                )
                if inline:
                    data = self.spool.blob(delivery.blob.id, delivery.id).read_bytes()
                    # The backend validates image dimensions before selecting an inline MIME type.
                    activity = MessageActivityInput().add_attachments(
                        Attachment.model_validate(
                            {
                                "contentType": delivery.blob.mime_type,
                                "name": delivery.blob.filename,
                                "contentUrl": f"data:{delivery.blob.mime_type};base64,{base64.b64encode(data).decode('ascii')}",
                            }
                        )
                    )
                else:
                    activity = MessageActivityInput().add_attachments(
                        Attachment.model_validate(
                            {
                                "contentType": "application/vnd.microsoft.teams.card.file.consent",
                                "name": delivery.blob.filename,
                                "content": {
                                    "description": "Datei aus deiner MatoLoop-Antwort",
                                    "sizeInBytes": delivery.blob.size,
                                    "acceptContext": {"deliveryId": str(delivery.id)},
                                    "declineContext": {"deliveryId": str(delivery.id)},
                                },
                            }
                        )
                    )
            elif delivery.card is not None:
                activity = activity.add_attachments(
                    Attachment.model_validate(
                        {
                            "contentType": "application/vnd.microsoft.card.adaptive",
                            "content": delivery.card,
                        }
                    )
                )
            if delivery.kind == "typing":
                active_stream = self.streams.get(key)
                if not (
                    active_stream and active_stream.id and monotonic() - active_stream.started < 600
                ):
                    await self.bot.send(
                        delivery.identity.conversation_id,
                        activity,
                        service_url=str(conversation.payload["service_url"]),
                    )
                return self.spool.delivery_result(
                    Record(
                        id=delivery.id,
                        kind="delivery",
                        status="sent",
                        expires_at=datetime.now(UTC),
                    )
                )
            record = Record(
                id=delivery.id,
                kind="delivery",
                status="sending",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
                payload={"fingerprint": fingerprint, "delivery": delivery.model_dump(mode="json")},
            )
            self.spool.save(record)
            service_url = str(conversation.payload["service_url"])
            used_stream = bool(stream and stream.id)
            try:
                try:
                    sent = await self.bot.send(
                        delivery.identity.conversation_id, activity, service_url=service_url,
                    )
                except HTTPStatusError as exc:
                    status = exc.response.status_code
                    if not used_stream or status not in {400, 403, 404}:
                        raise
                    logger.warning("Microsoft rejected stream completion (HTTP %s); sending plain reply", status)
                    fallback = activity.model_copy(update={
                        "id": None,
                        "entities": None,
                        "text_format": delivery.text_format,
                    })
                    sent = await self.bot.send(
                        delivery.identity.conversation_id, fallback, service_url=service_url,
                    )
                    used_stream = False
            except Exception:
                record.status = "unknown"
                self.spool.save(record)
                logger.error("Microsoft delivery outcome is unknown", exc_info=False)
                raise SpoolError("relay.deliveryUnknown", 502) from None
            if stream is not None:
                self.streams.pop(key, None)
            if delivery.kind == "message" and delivery.source_message_id:
                if len(self.closed_streams) >= 512:
                    self.closed_streams.pop(next(iter(self.closed_streams)))
                self.closed_streams[key, delivery.source_message_id] = monotonic()
            message_id = sent.id or (stream.id if used_stream and stream else None)
            record.message_ids = [str(message_id)] if message_id else []
            record.status = "offered" if delivery.kind == "file" and not inline else "sent"
            if record.status == "sent":
                record.payload = {"fingerprint": fingerprint}
            self.spool.save(record)
            return self.spool.delivery_result(record)

    async def on_file_consent(self, ctx: ActivityContext[FileConsentInvokeActivity]) -> None:
        identity = self.identity(ctx.activity)
        raw = ctx.activity.model_dump(mode="json", by_alias=True)
        value = raw.get("value") or {}
        context = value.get("context") or {}
        try:
            if not self.spool.online:
                raise SpoolError("relay.offline", 503)
            record = self.spool.get(UUID(str(context.get("deliveryId"))), "delivery")
            if record.status != "offered" or ctx.activity.reply_to_id not in record.message_ids:
                raise SpoolError("relay.bindingChanged")
            delivery = Delivery.model_validate(record.payload["delivery"])
            if (
                identity_key(delivery.identity) != identity_key(identity)
                or record.status != "offered"
                or delivery.blob is None
            ):
                raise SpoolError("relay.bindingChanged")
            if value.get("action") == "decline":
                record.status = "declined"
                record.payload = {"fingerprint": record.payload["fingerprint"]}
                self.spool.save(record)
                return
            if value.get("action") != "accept":
                raise SpoolError("relay.invalidConsent", 422)
            info = value.get("uploadInfo") or {}
            trusted_url(info.get("uploadUrl", ""), FILE_HOSTS)
            trusted_url(info.get("contentUrl", ""), FILE_HOSTS)
            record.payload = {**record.payload, "uploadInfo": info}
            record.status = "pending"
            self.spool.save(record)
            # MatoLoop revalidates the current owner binding before authorizing the upload.
        except (ValueError, SpoolError):
            logger.warning("Teams file consent rejected", exc_info=False)
            await ctx.send("Das Dateiangebot ist abgelaufen oder nicht mehr gültig.")

    def authorize_upload(self, delivery_id: UUID):
        record = self.spool.get(delivery_id, "delivery")
        if record.status != "pending":
            return self.spool.delivery_result(record)
        delivery = Delivery.model_validate(record.payload["delivery"])
        if delivery.kind != "file" or delivery.blob is None:
            raise SpoolError("relay.invalidConsent", 422)
        self.spool.blob(delivery.blob.id, delivery.id)
        record.status = "sending"
        self.spool.save(record)
        task = asyncio.create_task(self.upload_file(record, delivery, record.payload["uploadInfo"]))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.spool.delivery_result(record)

    async def upload_file(self, record: Record, delivery: Delivery, info: dict) -> None:
        try:
            if delivery.blob is None:
                raise SpoolError("relay.blobUnavailable", 422)
            data = self.spool.blob(delivery.blob.id, delivery.id).read_bytes()
            async with httpx.AsyncClient(follow_redirects=False, timeout=60) as client:
                response = await client.put(
                    info["uploadUrl"],
                    content=data,
                    headers={
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(len(data)),
                        "Content-Range": f"bytes 0-{len(data) - 1}/{len(data)}",
                    },
                )
                if response.status_code not in (200, 201):
                    raise SpoolError("relay.fileUploadFailed", 502)
            conversation = self.spool.get(identity_key(delivery.identity), "conversation")
            card = MessageActivityInput().add_attachments(
                Attachment.model_validate(
                    {
                        "contentType": "application/vnd.microsoft.teams.card.file.info",
                        "contentUrl": info["contentUrl"],
                        "name": delivery.blob.filename,
                        "content": {"uniqueId": info["uniqueId"], "fileType": info["fileType"]},
                    }
                )
            )
            sent = await self.bot.send(
                delivery.identity.conversation_id,
                card,
                service_url=str(conversation.payload["service_url"]),
            )
            record.status = "sent"
            record.message_ids.append(str(sent.id))
        except Exception:
            record.status = "unknown"
            logger.error("Teams file transfer failed; outcome requires inspection", exc_info=False)
            try:
                conversation = self.spool.get(identity_key(delivery.identity), "conversation")
                await self.bot.send(
                    delivery.identity.conversation_id,
                    MessageActivityInput(
                        text="Die Dateiübertragung konnte nicht bestätigt werden. Prüfe Teams und die ursprüngliche Antwort in MatoLoop."
                    ),
                    service_url=str(conversation.payload["service_url"]),
                )
            except Exception:
                logger.error(
                    "Teams file failure notification could not be delivered", exc_info=False
                )
        record.payload = {"fingerprint": record.payload["fingerprint"]}
        self.spool.save(record)

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def expire_handoffs(self) -> None:
        self.spool.pending()
        uncertain = [
            record
            for record in self.spool.records.values()
            if record.kind == "event"
            and record.status == "uncertain"
            and uuid5(record.id, "uncertain") not in self.spool.records
        ]
        for record in uncertain[:20]:
            notification_id = uuid5(record.id, "uncertain")
            event = Event.model_validate(record.payload)
            try:
                await self.send(
                    Delivery(
                        id=notification_id,
                        identity=event.identity,
                        kind="message",
                        text="Die Übergabe an MatoLoop konnte nicht bestätigt werden. Prüfe die Session in MatoLoop, bevor du die Nachricht erneut sendest.",
                    )
                )
            except (SpoolError, OSError):
                logger.warning(
                    "Uncertain handoff notification could not be confirmed", exc_info=False
                )
