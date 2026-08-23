from __future__ import annotations

import re
from pathlib import PurePath
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations.mail import (
    MAIL_CREDENTIAL_KIND,
    MailComposeArguments,
    MailConfig,
    binding_revision,
    client_for,
    locator_from_token,
    mail_error_code,
    message_payload,
    token_for,
)
from mato_loop_backend.integrations.mail_client import MailError, build_message
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import load_credential, read_credential, require_user
from mato_loop_backend.settings import (
    InstanceLimits,
    IntegrationInstance,
    SettingsStore,
    load_effective_integrations,
)

router = APIRouter(prefix="/mail", dependencies=[Depends(require_user)])
_CONTENT_TYPE = re.compile(
    r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$"
)


def _mailbox_role(name: str, flags: frozenset[str]) -> str | None:
    for role in ("inbox", "drafts", "sent", "trash", "junk", "archive"):
        if f"\\{role}" in flags or (role == "inbox" and name.casefold() == "inbox"):
            return role
    return None


def _api_error(error: MailError) -> ApiError:
    code = mail_error_code(error)
    if code == "integration.remoteNotFound":
        return ApiError(404, code)
    if code in {
        "integration.operationUnsupported",
        "integration.permissionDenied",
        "integration.staleReference",
    }:
        return ApiError(409, code)
    if code == "integration.responseTooLarge":
        return ApiError(413, code)
    if code == "tool.invalidArguments":
        return ApiError(422, code)
    return ApiError(502, code)


async def _account(
    db: AsyncSession, user: UserAccount, integration_id: UUID
) -> tuple[IntegrationInstance, object, str]:
    settings = await load_effective_integrations(db, user.id)
    account = next(
        (
            item
            for item in settings.instances
            if item.id == integration_id
            and item.enabled
            and item.integration_type == "mail"
            and item.adapter_type == "imap_smtp"
        ),
        None,
    )
    if account is None or account.credential_id is None:
        raise ApiError(404, "integration.notFound")
    record = await load_credential(
        db, account.credential_id, owner_id=account.owner_id
    )
    if record is None or record.kind != MAIL_CREDENTIAL_KIND:
        raise ApiError(502, "integration.authenticationFailed")
    try:
        credential = read_credential(record)
    except Exception as exc:
        raise ApiError(502, "integration.authenticationFailed") from exc
    if (
        set(credential) != {"password"}
        or not isinstance(credential["password"], str)
        or not credential["password"]
    ):
        raise ApiError(502, "integration.authenticationFailed")
    return account, client_for(account, credential["password"]), binding_revision(account)


async def _attachments(
    uploads: list[UploadFile] | None, limits: InstanceLimits
) -> tuple[tuple[str, str, bytes], ...]:
    uploads = uploads or []
    if len(uploads) > limits.max_attachments_per_message:
        for upload in uploads:
            await upload.close()
        raise ApiError(413, "attachment.tooLarge")
    result = []
    total = 0
    try:
        for upload in uploads:
            data = await upload.read(limits.max_upload_bytes + 1)
            total += len(data)
            if len(data) > limits.max_upload_bytes:
                raise ApiError(413, "integration.attachmentTooLarge")
            if total > limits.max_message_upload_bytes:
                raise ApiError(413, "attachment.tooLarge")
            filename = PurePath(upload.filename or "attachment").name
            filename = "".join(
                character for character in filename if ord(character) >= 32
            )[:500] or "attachment"
            content_type = (upload.content_type or "application/octet-stream").split(
                ";", 1
            )[0][:200]
            if _CONTENT_TYPE.fullmatch(content_type) is None:
                content_type = "application/octet-stream"
            result.append((filename, content_type, data))
    finally:
        for upload in uploads:
            await upload.close()
    return tuple(result)


async def _raw_message(
    account: IntegrationInstance,
    limits: InstanceLimits,
    *,
    to: list[str] | None,
    cc: list[str] | None,
    bcc: list[str] | None,
    subject: str,
    body: str,
    attachments: list[UploadFile] | None,
) -> bytes:
    try:
        compose = MailComposeArguments(
            integration_id=account.id,
            to=tuple(to or ()),
            cc=tuple(cc or ()),
            bcc=tuple(bcc or ()),
            subject=subject,
            body=body,
        )
    except ValidationError as exc:
        raise ApiError(422, "validation.invalid") from exc
    return build_message(
        from_address=MailConfig.model_validate(account.config).from_address,
        to=compose.to,
        cc=compose.cc,
        bcc=compose.bcc,
        subject=compose.subject,
        body=compose.body,
        attachments=await _attachments(attachments, limits),
    )


@router.get("/accounts")
async def list_accounts(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    settings = await load_effective_integrations(db, user.id)
    return {
        "accounts": [
            {
                "integrationId": str(item.id),
                "name": item.name,
                "fromAddress": item.config.get("from_address"),
            }
            for item in settings.instances
            if item.enabled
            and item.integration_type == "mail"
            and item.adapter_type == "imap_smtp"
        ]
    }


@router.get("/accounts/{integration_id}/mailboxes")
async def list_mailboxes(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    _instance, client, _revision = await _account(db, user, integration_id)
    try:
        mailboxes = await client.list_mailboxes()
    except MailError as exc:
        raise _api_error(exc) from exc
    return {
        "mailboxes": [
            {
                "key": mailbox.name,
                "name": mailbox.name,
                "label": mailbox.name,
                "role": _mailbox_role(mailbox.name, mailbox.flags),
                "flags": sorted(mailbox.flags),
            }
            for mailbox in mailboxes
        ]
    }


@router.get("/accounts/{integration_id}/messages")
async def list_messages(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    mailbox: Annotated[
        str, Query(alias="mailboxKey", min_length=1, max_length=1_000)
    ],
    query: Annotated[str | None, Query(min_length=1, max_length=500)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> dict:
    _instance, client, revision = await _account(db, user, integration_id)
    try:
        messages = await client.search(mailbox, query=query, limit=limit)
    except MailError as exc:
        raise _api_error(exc) from exc
    return {
        "messages": [
            {
                "messageToken": token_for(integration_id, revision, item.locator),
                "mailbox": item.locator.mailbox,
                "subject": item.subject,
                "from": item.sender,
                "to": list(item.recipients),
                "date": item.date.isoformat() if item.date is not None else None,
                "size": item.size,
                "flags": sorted(item.flags),
            }
            for item in messages
        ],
        "nextCursor": None,
    }


@router.get("/accounts/{integration_id}/messages/{message_token}")
async def get_message(
    integration_id: UUID,
    message_token: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    _instance, client, revision = await _account(db, user, integration_id)
    try:
        locator, _part = locator_from_token(message_token, integration_id, revision)
        message = await client.get_message(locator)
    except ValueError as exc:
        raise ApiError(409, "integration.staleReference") from exc
    except MailError as exc:
        raise _api_error(exc) from exc
    return {"message": message_payload(message, integration_id, revision)}


@router.post("/accounts/{integration_id}/drafts", status_code=status.HTTP_201_CREATED)
async def create_draft(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    to: Annotated[list[str] | None, Form()] = None,
    subject: Annotated[str, Form(max_length=1_000)] = "",
    body: Annotated[str, Form(max_length=100_000)] = "",
    cc: Annotated[list[str] | None, Form()] = None,
    bcc: Annotated[list[str] | None, Form()] = None,
    mailbox: Annotated[str | None, Form(max_length=1_000)] = None,
    attachments: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    account, client, revision = await _account(db, user, integration_id)
    limits = await SettingsStore.load(db, InstanceLimits)
    try:
        locator = await client.create_draft(
            await _raw_message(
                account,
                limits,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body=body,
                attachments=attachments,
            ),
            mailbox=mailbox,
        )
    except MailError as exc:
        raise _api_error(exc) from exc
    return {
        "messageToken": token_for(integration_id, revision, locator),
        "mailbox": locator.mailbox,
        "isDraft": True,
    }


@router.put("/accounts/{integration_id}/drafts/{message_token}")
async def update_draft(
    integration_id: UUID,
    message_token: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    to: Annotated[list[str] | None, Form()] = None,
    subject: Annotated[str, Form(max_length=1_000)] = "",
    body: Annotated[str, Form(max_length=100_000)] = "",
    cc: Annotated[list[str] | None, Form()] = None,
    bcc: Annotated[list[str] | None, Form()] = None,
    attachments: Annotated[list[UploadFile] | None, File()] = None,
) -> dict:
    account, client, revision = await _account(db, user, integration_id)
    limits = await SettingsStore.load(db, InstanceLimits)
    try:
        locator, _part = locator_from_token(message_token, integration_id, revision)
        replacement = await client.update_draft(
            locator,
            await _raw_message(
                account,
                limits,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body=body,
                attachments=attachments,
            ),
        )
    except ValueError as exc:
        raise ApiError(409, "integration.staleReference") from exc
    except MailError as exc:
        raise _api_error(exc) from exc
    return {
        "messageToken": token_for(integration_id, revision, replacement),
        "mailbox": replacement.mailbox,
        "isDraft": True,
    }


@router.delete(
    "/accounts/{integration_id}/drafts/{message_token}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_draft(
    integration_id: UUID,
    message_token: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    _instance, client, revision = await _account(db, user, integration_id)
    try:
        locator, _part = locator_from_token(message_token, integration_id, revision)
        await client.delete_draft(locator)
    except ValueError as exc:
        raise ApiError(409, "integration.staleReference") from exc
    except MailError as exc:
        raise _api_error(exc) from exc


@router.post("/accounts/{integration_id}/drafts/{message_token}/send")
async def send_draft(
    integration_id: UUID,
    message_token: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, bool]:
    _instance, client, revision = await _account(db, user, integration_id)
    try:
        locator, _part = locator_from_token(message_token, integration_id, revision)
        await client.send_draft(locator)
    except ValueError as exc:
        raise ApiError(409, "integration.staleReference") from exc
    except MailError as exc:
        raise _api_error(exc) from exc
    return {"sent": True}


@router.post("/accounts/{integration_id}/messages/send")
async def send_message(
    integration_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    to: Annotated[list[str], Form()],
    subject: Annotated[str, Form(max_length=1_000)] = "",
    body: Annotated[str, Form(max_length=100_000)] = "",
    cc: Annotated[list[str] | None, Form()] = None,
    bcc: Annotated[list[str] | None, Form()] = None,
    attachments: Annotated[list[UploadFile] | None, File()] = None,
) -> dict[str, bool]:
    account, client, _revision = await _account(db, user, integration_id)
    limits = await SettingsStore.load(db, InstanceLimits)
    try:
        await client.send(
            await _raw_message(
                account,
                limits,
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                body=body,
                attachments=attachments,
            )
        )
    except MailError as exc:
        raise _api_error(exc) from exc
    return {"sent": True}


@router.delete(
    "/accounts/{integration_id}/messages/{message_token}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_message(
    integration_id: UUID,
    message_token: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> None:
    _instance, client, revision = await _account(db, user, integration_id)
    try:
        locator, _part = locator_from_token(message_token, integration_id, revision)
        await client.delete_message(locator)
    except ValueError as exc:
        raise ApiError(409, "integration.staleReference") from exc
    except MailError as exc:
        raise _api_error(exc) from exc


@router.get("/accounts/{integration_id}/attachments/{attachment_token}")
async def download_attachment(
    integration_id: UUID,
    attachment_token: str,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    _instance, client, revision = await _account(db, user, integration_id)
    try:
        locator, part = locator_from_token(
            attachment_token, integration_id, revision, require_part=True
        )
        assert part is not None
        data, filename, _content_type = await client.get_attachment(locator, part)
    except ValueError as exc:
        raise ApiError(409, "integration.staleReference") from exc
    except MailError as exc:
        raise _api_error(exc) from exc
    return Response(
        data,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                'attachment; filename="download"; '
                f"filename*=UTF-8''{quote(filename, safe='')}"
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )
