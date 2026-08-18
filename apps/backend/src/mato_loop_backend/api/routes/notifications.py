from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import Notification, UserAccount
from mato_loop_backend.notifications import NotificationError, NotificationService
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/notifications", dependencies=[Depends(require_user)])


class SessionNotificationTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["session"]
    session_id: UUID = Field(alias="sessionId")


class AutomationNotificationTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["automationRun"]
    automation_id: UUID = Field(alias="automationId")
    run_id: UUID = Field(alias="runId")


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: str
    title: str
    body: str | None
    target: SessionNotificationTargetResponse | AutomationNotificationTargetResponse | None
    read_at: datetime | None = Field(alias="readAt")
    created_at: datetime = Field(alias="createdAt")


class NotificationListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NotificationResponse]
    next_cursor: str | None = Field(alias="nextCursor")
    unread_count: int = Field(alias="unreadCount")


class NotificationReadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notification: NotificationResponse
    unread_count: int = Field(alias="unreadCount")


class NotificationsReadAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_count: int = Field(alias="updatedCount")
    unread_count: int = Field(alias="unreadCount")


def _notification_payload(item: Notification) -> NotificationResponse:
    automation = item.automation_run.automation if item.automation_run is not None else None
    return NotificationResponse(
        id=item.id,
        kind=item.kind,
        title=item.title,
        body=item.body,
        target=(
            SessionNotificationTargetResponse(type="session", sessionId=item.session_id)
            if item.session_id is not None
            else AutomationNotificationTargetResponse(
                type="automationRun",
                automationId=automation.id,
                runId=item.automation_run_id,
            )
            if automation is not None and item.automation_run_id is not None
            else None
        ),
        readAt=item.read_at,
        createdAt=item.created_at,
    )


def _encode_cursor(user_id: UUID, created_at: datetime, notification_id: UUID) -> str:
    value = json.dumps(
        [str(user_id), created_at.isoformat(), str(notification_id)], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _decode_cursor(value: str, user_id: UUID) -> tuple[datetime, UUID]:
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        payload = json.loads(decoded)
        if not (
            isinstance(payload, list)
            and len(payload) == 3
            and all(isinstance(item, str) for item in payload)
            and UUID(payload[0]) == user_id
        ):
            raise ValueError
        created_at = datetime.fromisoformat(payload[1])
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(payload[2])
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError) as exc:
        raise ApiError(400, "validation.invalid", field="cursor") from exc


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=100)] = 20,
    cursor: str | None = None,
) -> NotificationListResponse:
    decoded_cursor = _decode_cursor(cursor, user.id) if cursor else None
    rows = await NotificationService.list_page(
        db, user.id, page_size=page_size, cursor=decoded_cursor
    )
    page = rows[:page_size]
    return NotificationListResponse(
        items=[_notification_payload(item) for item in page],
        nextCursor=(
            _encode_cursor(user.id, page[-1].created_at, page[-1].id)
            if len(rows) > page_size
            else None
        ),
        unreadCount=await NotificationService.unread_count(db, user.id),
    )


@router.post("/read-all", response_model=NotificationsReadAllResponse)
async def mark_all_notifications_read(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NotificationsReadAllResponse:
    updated_count = await NotificationService.mark_all_read(db, user.id)
    unread_count = await NotificationService.unread_count(db, user.id)
    await db.commit()
    return NotificationsReadAllResponse(
        updatedCount=updated_count,
        unreadCount=unread_count,
    )


@router.post("/{notification_id}/read", response_model=NotificationReadResponse)
async def mark_notification_read(
    notification_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> NotificationReadResponse:
    try:
        item = await NotificationService.mark_read(db, user.id, notification_id)
    except NotificationError as exc:
        raise ApiError(404, exc.code) from exc
    unread_count = await NotificationService.unread_count(db, user.id)
    await db.commit()
    return NotificationReadResponse(
        notification=_notification_payload(item),
        unreadCount=unread_count,
    )
