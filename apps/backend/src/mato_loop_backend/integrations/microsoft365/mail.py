from __future__ import annotations

import hashlib
import json
import logging
from email.message import EmailMessage
from urllib.parse import quote

from pydantic import ValidationError

from mato_loop_backend.integrations.mail.adapter import (
    MailComposeArguments,
    MailSendMessageArguments,
)
from mato_loop_backend.integrations.mail.send import MailSendResult, send_once, send_status
from mato_loop_backend.integrations.microsoft365 import adapter
from mato_loop_backend.integrations.microsoft365.client import (
    MAX_ATTACHMENT_BYTES,
    SUMMARY_FIELDS,
    Attachment,
    Folder,
    GraphClient,
    Message,
    Microsoft365Config,
    Microsoft365Error,
)
from mato_loop_backend.integrations.microsoft365.composition import (
    MAX_OUTGOING_BYTES,
    message_payload,
)
from mato_loop_backend.settings import InstanceLimits, IntegrationInstance

logger = logging.getLogger(__name__)


def _validate_uploads(payload: dict, limits: InstanceLimits) -> None:
    files = payload.get("attachments", ())
    sizes = [
        len(item["contentBytes"]) // 4 * 3 - (len(item["contentBytes"]) - len(item["contentBytes"].rstrip("=")))
        for item in files
    ]
    if (
        sum(sizes) > min(limits.max_message_upload_bytes, MAX_OUTGOING_BYTES)
        or sum(not item.get("isInline", False) for item in files) > limits.max_attachments_per_message
        or any(size > limits.max_upload_bytes for item, size in zip(files, sizes, strict=True) if not item.get("isInline"))
    ):
        raise Microsoft365Error("integration.attachmentTooLarge")


class Microsoft365Mailbox:
    def __init__(self, instance: IntegrationInstance, client: GraphClient) -> None:
        self.instance = instance
        self.client = client

    def require(self, feature: str) -> Microsoft365Config:
        config = Microsoft365Config.model_validate(self.instance.config)
        if not getattr(config, feature):
            raise Microsoft365Error("microsoft365.featureDisabled")
        return config

    async def _page(self, arguments, kind, path, model, params):
        binding = adapter._query_binding(arguments)
        cursor = adapter._decode(arguments.cursor, self.instance, kind, binding) if arguments.cursor else None
        values, next_link = await self.client.page(path, model, params=params, cursor=cursor)
        return values, adapter._encode(self.instance, kind, next_link, binding) if next_link else None

    async def mailboxes(self, parent: str | None, cursor: str | None) -> dict:
        arguments = adapter.FolderArguments(
            integration_id=self.instance.id, parent_folder_id=parent, cursor=cursor, limit=100,
        )
        path = "/me/mailFolders"
        if parent:
            path += "/" + quote(parent, safe="") + "/childFolders"
        fields = "id,displayName,totalItemCount,unreadItemCount,childFolderCount"
        folders, next_cursor = await self._page(arguments, "folders", path, Folder, {"$top": 100, "$select": fields})
        inbox = None
        if parent is None:
            try:
                inbox = Folder.model_validate(await self.client.get("/me/mailFolders/inbox", params={"$select": fields}))
            except ValidationError as exc:
                raise Microsoft365Error("integration.invalidResponse") from exc
            folders = ([inbox] if cursor is None else []) + [folder for folder in folders if folder.id != inbox.id]
        return {
            "mailboxes": [
                {
                    "key": folder.id, "name": folder.displayName, "label": folder.displayName,
                    "role": "inbox" if inbox and folder.id == inbox.id else None,
                    "unreadCount": folder.unreadItemCount, "childFolderCount": folder.childFolderCount,
                }
                for folder in folders
            ],
            "nextCursor": next_cursor,
        }

    def _message(self, message: Message) -> dict:
        return {
            **adapter._message_payload(message, self.instance),
            "unread": not message.isRead,
        }

    async def messages(self, mailbox: str, query: str | None, limit: int, cursor: str | None) -> dict:
        arguments = adapter.SearchArguments(
            integration_id=self.instance.id, folder_id=mailbox, query=query, limit=limit, cursor=cursor,
        )
        params = {"$top": limit, "$select": SUMMARY_FIELDS}
        if query:
            params["$search"] = '"' + query + '"'
        else:
            params["$orderby"] = "receivedDateTime desc"
        messages, next_cursor = await self._page(
            arguments, "search", "/me/mailFolders/" + quote(mailbox, safe="") + "/messages", Message, params,
        )
        return {"messages": [self._message(message) for message in messages], "nextCursor": next_cursor}

    async def attachments(self, token: str, cursor: str | None = None) -> dict:
        message_id = adapter._decode(token, self.instance, "message")
        arguments = adapter.MessageArguments(
            integration_id=self.instance.id, message_token=token, view="attachments", limit=100, cursor=cursor,
        )
        attachments, next_cursor = await self._page(
            arguments, "attachments", "/me/messages/" + quote(message_id, safe="") + "/attachments",
            Attachment, {"$top": 100, "$select": "id,name,contentType,size,isInline"},
        )
        values = []
        for item in attachments:
            supported = item.type == "#microsoft.graph.fileAttachment" and item.size <= MAX_ATTACHMENT_BYTES
            values.append({
                "name": item.name, "sizeBytes": item.size, "downloadSupported": supported,
                "isInline": item.isInline,
                "attachmentToken": adapter._encode(
                    self.instance, "attachment",
                    adapter.AttachmentReference(message_id=message_id, attachment_id=item.id).model_dump_json(),
                ),
            })
        return {"attachments": values, "nextAttachmentCursor": next_cursor}

    async def message(self, token: str) -> dict:
        message = await self.client.message(adapter._decode(token, self.instance, "message"))
        result = {**self._message(message), "messageToken": token, **await self.attachments(token)}
        if message.isDraft:
            original = await self.client.message(message.id, original_body=True)
            result.update(
                version=original.changeKey,
                editBody=original.body.content[:100_000],
                editBodyFormat=original.body.contentType,
                bodyTruncated=len(original.body.content) > 100_000,
            )
        return {"message": result}

    async def update_draft(
        self, token: str, version: str, *, changes: dict, attachments=(),
        retained_tokens: list[str] | None = None, limits: InstanceLimits,
    ) -> dict:
        self.require("allow_drafts")
        message, existing = await self.inspect(token, draft_only=True)
        if not version or message.changeKey != version:
            raise Microsoft365Error("integration.staleReference")
        tokens = {item["attachmentToken"]: item for item in existing}
        if retained_tokens is not None and (len(set(retained_tokens)) != len(retained_tokens) or set(retained_tokens) - tokens.keys()):
            raise Microsoft365Error("integration.staleReference")
        retained = [
            item for item in existing
            if item["isInline"] or retained_tokens is None or item["attachmentToken"] in retained_tokens
        ]
        if (
            len([item for item in retained if not item["isInline"]]) + len(attachments) > limits.max_attachments_per_message
            or sum(item["sizeBytes"] for item in retained) + sum(len(item[2]) for item in attachments)
            > min(limits.max_message_upload_bytes, MAX_OUTGOING_BYTES)
            or any(len(item[2]) > min(limits.max_upload_bytes, MAX_OUTGOING_BYTES) for item in attachments)
        ):
            raise Microsoft365Error("integration.attachmentTooLarge")
        files = message_payload(to=(), subject="", body="", attachments=attachments).get("attachments", ())
        allowed = {"to", "cc", "bcc", "subject", "body", "body_format"}
        if set(changes) - allowed:
            raise Microsoft365Error("tool.invalidArguments")
        arguments = MailComposeArguments(
            integration_id=self.instance.id,
            **{name: value for name, value in changes.items() if name not in {"body_format"}},
        )
        payload = {
            field: [{"emailAddress": {"address": address}} for address in getattr(arguments, name)]
            for name, field in (("to", "toRecipients"), ("cc", "ccRecipients"), ("bcc", "bccRecipients"))
            if name in changes
        }
        if "subject" in changes:
            payload["subject"] = arguments.subject
        if "body" in changes:
            body_format = changes.get("body_format", "text")
            if body_format not in {"text", "html"}:
                raise Microsoft365Error("tool.invalidArguments")
            original = await self.client.message(message.id, original_body=True)
            if original.changeKey != version:
                raise Microsoft365Error("integration.staleReference")
            if original.body.contentType == "html" and body_format != "html":
                raise Microsoft365Error("integration.operationUnsupported")
            payload["body"] = {"contentType": body_format, "content": arguments.body}
        try:
            if payload:
                await self.client.update_draft(message.id, payload)
            for attachment in files:
                await self.client.add_attachment(message.id, attachment)
            for item in existing:
                if item not in retained:
                    reference = adapter._attachment_reference(item["attachmentToken"], self.instance)
                    await self.client.delete_attachment(message.id, reference.attachment_id)
            return await self.message(token)
        except Microsoft365Error as exc:
            # Multiple Graph effects cannot be rolled back or safely replayed.
            logger.warning("Microsoft 365 draft update may be partial: %s", exc.code)
            raise Microsoft365Error("integration.outcomeUnknown") from exc

    async def create_draft(
        self, to, subject: str, body: str, reply_to: str | None, *, cc=(), bcc=(), attachments=(),
        limits: InstanceLimits | None = None,
    ) -> dict:
        config = self.require("allow_drafts")
        arguments = MailComposeArguments(
            integration_id=self.instance.id, to=to, cc=cc, bcc=bcc, subject=subject, body=body,
        )
        payload = message_payload(
            to=arguments.to, cc=arguments.cc, bcc=arguments.bcc, subject=arguments.subject,
            body=arguments.body, signature=config.signature_html, attachments=attachments,
        )
        _validate_uploads(payload, limits or InstanceLimits())
        if reply_to:
            arguments = adapter.DraftArguments(integration_id=self.instance.id, message_token=reply_to, body=body)
            message_id = adapter._decode(arguments.message_token, self.instance, "message")
            draft = await self.client.reply_draft(message_id, arguments.body, message=payload)
        else:
            draft = await self.client.new_draft(arguments.to, arguments.subject, arguments.body, message=payload)
        return {**self._message(draft), "isDraft": True}

    async def inspect(self, token: str, *, draft_only: bool = False) -> tuple[Message, list[dict]]:
        message = await self.client.message(adapter._decode(token, self.instance, "message"))
        if not message.changeKey or (draft_only and not message.isDraft):
            raise Microsoft365Error("integration.staleReference")
        if not draft_only:
            return message, []
        attachments = []
        cursor = None
        while True:
            page = await self.attachments(token, cursor)
            attachments.extend(page["attachments"])
            cursor = page["nextAttachmentCursor"]
            if len(attachments) > 100:
                raise Microsoft365Error("integration.responseTooLarge")
            if not cursor:
                break
        return message, attachments

    async def send_message(
        self, user_id, key: str, *, to, cc=(), bcc=(), subject: str, body: str, attachments=(),
        limits: InstanceLimits | None = None,
    ) -> MailSendResult:
        config = self.require("allow_send")
        arguments = MailSendMessageArguments(
            integration_id=self.instance.id, to=to, cc=cc, bcc=bcc, subject=subject, body=body,
        )
        payload = message_payload(
            to=arguments.to, cc=arguments.cc, bcc=arguments.bcc, subject=arguments.subject,
            body=arguments.body, signature=config.signature_html, attachments=attachments,
        )
        _validate_uploads(payload, limits or InstanceLimits())
        return await send_once(
            user_id=user_id, integration_id=self.instance.id, idempotency_key=key,
            raw=_send_identity(payload), client=_Sender(self.client, payload=payload),
        )

    async def send_draft(self, user_id, key: str, token: str, version: str) -> MailSendResult:
        self.require("allow_send")
        message_id = adapter._decode(token, self.instance, "message")
        reference = "graph:" + message_id
        completed = await send_status(user_id, self.instance.id, key, draft_reference=reference)
        if completed is not None:
            return completed
        message = await self.client.message(message_id)
        if not version or message.changeKey != version or not message.isDraft:
            raise Microsoft365Error("integration.staleReference")
        return await send_once(
            user_id=user_id, integration_id=self.instance.id, idempotency_key=key,
            raw=_send_identity({"draft": message_id, "version": version}),
            client=_Sender(self.client, draft_id=message_id), draft_reference=reference,
        )

    async def delete(self, token: str, version: str, *, draft_only: bool = False) -> None:
        self.require("allow_delete")
        message = await self.client.message(adapter._decode(token, self.instance, "message"))
        if not version or message.changeKey != version or (draft_only and not message.isDraft):
            raise Microsoft365Error("integration.staleReference")
        await self.client.delete_message(message.id)

    async def attachment(self, token: str) -> tuple[bytes, str]:
        reference = adapter._attachment_reference(token, self.instance)
        path = "/me/messages/" + quote(reference.message_id, safe="") + "/attachments/" + quote(reference.attachment_id, safe="")
        try:
            metadata = Attachment.model_validate(await self.client.get(path, params={"$select": "id,name,contentType,size,isInline"}))
        except ValidationError as exc:
            raise Microsoft365Error("integration.invalidResponse") from exc
        return await self.client.download(reference.message_id, reference.attachment_id), metadata.name


def _send_identity(payload: dict) -> bytes:
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(sort_keys=True, separators=(",", ":"), ensure_ascii=True).iterencode(payload):
        digest.update(chunk.encode())
    message = EmailMessage()
    message.set_content(digest.hexdigest())
    return message.as_bytes()


class _Sender:
    def __init__(self, client: GraphClient, *, payload: dict | None = None, draft_id: str | None = None):
        self.client, self.payload, self.draft_id = client, payload, draft_id

    async def send(self, _raw: bytes) -> None:
        if self.draft_id is not None:
            await self.client.send_draft(self.draft_id)
        else:
            assert self.payload is not None
            await self.client.send_message(self.payload)
