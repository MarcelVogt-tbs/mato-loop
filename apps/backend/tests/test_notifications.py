from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.notifications import (
    _decode_cursor,
    _encode_cursor,
    list_notifications,
    mark_all_notifications_read,
    mark_notification_presence,
    mark_notification_read,
)
from mato_loop_backend.models import Notification, UserAccount
from mato_loop_backend.notifications import (
    NotificationDraft,
    NotificationError,
    NotificationService,
)


def _user() -> UserAccount:
    return UserAccount(id=uuid4(), username="test", password_hash="hash")


def _notification(user_id, *, created_at: datetime | None = None) -> Notification:
    return Notification(
        id=uuid4(),
        user_id=user_id,
        kind="automation.completed",
        title="Automation abgeschlossen",
        body="Der Lauf wurde abgeschlossen.",
        deduplication_key=f"automation-run:{uuid4()}:completed",
        created_at=created_at or datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
    )


async def test_mark_notification_presence_is_user_scoped(monkeypatch) -> None:
    from mato_loop_backend.api.routes import notifications as notifications_route

    user = _user()
    mark = MagicMock()
    monkeypatch.setattr(notifications_route, "mark_user_present", mark)

    response = await mark_notification_presence(user)

    assert response.status_code == 204
    mark.assert_called_once_with(user.id)


def test_notification_model_preserves_history_when_session_is_deleted() -> None:
    foreign_keys = {column.name: next(iter(column.foreign_keys)) for column in Notification.__table__.columns if column.foreign_keys}

    assert foreign_keys["user_id"].ondelete == "CASCADE"
    assert foreign_keys["session_id"].ondelete == "SET NULL"
    assert any(
        constraint.name == "uq_notifications_user_deduplication_key"
        for constraint in Notification.__table__.constraints
    )


def test_notification_draft_normalizes_text_and_rejects_blanks() -> None:
    draft = NotificationDraft(
        user_id=uuid4(),
        kind="automation.completed",
        title="  Fertig  ",
        body="  Ergebnis  ",
        deduplication_key="  run:1  ",
    )

    assert (draft.title, draft.body, draft.deduplication_key) == (
        "Fertig",
        "Ergebnis",
        "run:1",
    )
    with pytest.raises(ValidationError):
        NotificationDraft(
            user_id=uuid4(),
            kind="automation.completed",
            title="   ",
            deduplication_key="run:2",
        )


async def test_notification_creation_is_idempotent_and_does_not_commit() -> None:
    created_id = uuid4()
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = created_id
    draft = NotificationDraft(
        user_id=uuid4(),
        kind="automation.completed",
        title="Fertig",
        deduplication_key="automation-run:1:completed",
    )

    result = await NotificationService.create(db, draft)

    assert result == created_id
    statement = db.scalar.await_args.args[0]
    sql = str(statement)
    assert "INSERT INTO notifications" in sql
    assert "ON CONFLICT ON CONSTRAINT uq_notifications_user_deduplication_key DO NOTHING" in sql
    db.commit.assert_not_awaited()


async def test_notification_creation_rejects_foreign_session_target() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None
    draft = NotificationDraft(
        user_id=uuid4(),
        session_id=uuid4(),
        kind="automation.completed",
        title="Fertig",
        deduplication_key="automation-run:1:completed",
    )

    with pytest.raises(NotificationError, match="notification.targetNotFound"):
        await NotificationService.create(db, draft)

    db.commit.assert_not_awaited()


async def test_notification_page_is_owner_scoped_and_uses_full_keyset() -> None:
    user_id = uuid4()
    cursor_time = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    cursor_id = uuid4()
    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    db = AsyncMock(spec=AsyncSession)
    db.scalars.return_value = scalar_result

    await NotificationService.list_page(
        db, user_id, page_size=20, cursor=(cursor_time, cursor_id)
    )

    statement = db.scalars.await_args.args[0]
    sql = str(statement)
    assert "notifications.user_id =" in sql
    assert "notifications.created_at <" in sql
    assert "notifications.created_at =" in sql
    assert "notifications.id <" in sql
    assert "ORDER BY notifications.created_at DESC, notifications.id DESC" in sql
    assert 21 in statement.compile().params.values()


async def test_read_mutations_lock_the_user_before_notification_state() -> None:
    user = _user()
    item = _notification(user.id)
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [user.id, item]

    result = await NotificationService.mark_read(db, user.id, item.id)

    assert result is item
    assert item.read_at is not None
    first_statement, second_statement = [call.args[0] for call in db.scalar.await_args_list]
    assert "FROM user_accounts" in str(first_statement)
    assert "FOR UPDATE" in str(first_statement)
    assert "FROM notifications" in str(second_statement)
    assert "FOR UPDATE" in str(second_statement)


def test_notification_cursor_is_owner_bound() -> None:
    user_id = uuid4()
    created_at = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
    notification_id = uuid4()
    cursor = _encode_cursor(user_id, created_at, notification_id)

    assert _decode_cursor(cursor, user_id) == (created_at, notification_id)
    with pytest.raises(ApiError) as exc_info:
        _decode_cursor(cursor, uuid4())

    assert exc_info.value.code == "validation.invalid"
    assert exc_info.value.field == "cursor"


async def test_notification_list_returns_cursor_target_and_global_unread_count(monkeypatch) -> None:
    user = _user()
    first = _notification(user.id)
    first.session_id = uuid4()
    overflow = _notification(
        user.id, created_at=datetime(2026, 8, 13, 8, 0, tzinfo=UTC)
    )
    monkeypatch.setattr(
        NotificationService, "list_page", AsyncMock(return_value=[first, overflow])
    )
    monkeypatch.setattr(NotificationService, "unread_count", AsyncMock(return_value=7))

    result = await list_notifications(user, AsyncMock(spec=AsyncSession), page_size=1)
    payload = result.model_dump(mode="json", by_alias=True)

    assert payload["unreadCount"] == 7
    assert payload["nextCursor"]
    assert payload["items"][0]["target"] == {
        "type": "session",
        "sessionId": str(first.session_id),
    }
    assert "deduplicationKey" not in payload["items"][0]


async def test_mark_read_is_owner_scoped_idempotent_and_committed(monkeypatch) -> None:
    user = _user()
    item = _notification(user.id)
    item.read_at = datetime(2026, 8, 14, 9, 0, tzinfo=UTC)
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(NotificationService, "mark_read", AsyncMock(return_value=item))
    monkeypatch.setattr(NotificationService, "unread_count", AsyncMock(return_value=2))

    result = await mark_notification_read(item.id, user, db)

    assert result.unread_count == 2
    NotificationService.mark_read.assert_awaited_once_with(db, user.id, item.id)
    db.commit.assert_awaited_once()


async def test_mark_all_read_commits_and_returns_authoritative_count(monkeypatch) -> None:
    user = _user()
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(NotificationService, "mark_all_read", AsyncMock(return_value=4))
    monkeypatch.setattr(NotificationService, "unread_count", AsyncMock(return_value=1))

    result = await mark_all_notifications_read(user, db)

    assert result.updated_count == 4
    assert result.unread_count == 1
    db.commit.assert_awaited_once()
