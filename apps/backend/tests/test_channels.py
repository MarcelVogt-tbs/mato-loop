import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import (
    CallbackQuery,
    Chat,
    Update,
    User,
)
from telegram import (
    Message as TelegramMessage,
)

from mato_loop_backend.api.routes import channels as routes
from mato_loop_backend.attachments import AttachmentInput
from mato_loop_backend.automations import AutomationService
from mato_loop_backend.messaging import telegram
from mato_loop_backend.messaging.approvals import (
    MessagingApprovalAction,
    MessagingApprovalPayload,
    decode_approval_callback,
)
from mato_loop_backend.messaging.questions import (
    MessagingQuestionEnvelope,
    QuestionInteraction,
    QuestionInteractionResult,
    decode_question_callback,
    encode_question_callback,
)
from mato_loop_backend.messaging.telegram import endpoint_payload
from mato_loop_backend.models import (
    AgentRun,
    ApprovalRequest,
    Automation,
    AutomationRun,
    ChatSession,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    QuestionRequest,
    UserAccount,
)
from mato_loop_backend.settings import ChannelSettings, InstanceLimits


def _admin() -> UserAccount:
    return UserAccount(
        id=uuid4(),
        username="admin",
        password_hash="hash",
        is_admin=True,
        is_active=True,
    )


async def test_admin_disables_provider_and_pending_delivery(monkeypatch) -> None:
    db = AsyncMock(spec=AsyncSession)
    settings = ChannelSettings()
    outbox = MessagingOutbox(
        endpoint_id=uuid4(),
        automation_run_id=uuid4(),
        destination="1",
        deduplication_key="test",
        payload={"text": "test"},
    )
    run = AutomationRun(
        automation_id=uuid4(),
        automation_revision=1,
        trigger="manual",
        status="completed",
        definition_snapshot={},
        delivery_status="pending",
    )
    run_rows = Mock()
    run_rows.all.return_value = [run]
    outbox_rows = Mock()
    outbox_rows.all.return_value = [outbox]
    db.scalars.side_effect = [run_rows, outbox_rows]
    load = AsyncMock(return_value=settings)
    save = AsyncMock()
    monkeypatch.setattr(routes.SettingsStore, "load", load)
    monkeypatch.setattr(routes.SettingsStore, "save", save)

    result = await routes.update_channel_provider(
        "telegram", routes.ChannelProviderRequest(allowed=False), _admin(), db
    )

    assert result["allowed"] is False
    assert settings.allowed_providers == set()
    assert outbox.status == "failed"
    assert outbox.error_code == "channel.providerDisabled"
    assert run.delivery_status == "pending"
    assert run.delivery_channel_type == "app"
    assert run.delivery_endpoint_id is None
    save.assert_awaited_once_with(db, settings)
    db.commit.assert_awaited_once()


def test_channel_endpoint_payload_has_no_integration_fields() -> None:
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        external_handle="private_bot",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    payload = endpoint_payload(endpoint)

    assert payload["provider"] == "telegram"
    assert payload["externalHandle"] == "private_bot"
    assert "integrationType" not in payload
    assert "providerType" not in payload


def test_telegram_normalizes_private_approval_callback() -> None:
    approval_id = uuid4()
    sender = User(id=456, first_name="Test", is_bot=False)
    chat = Chat(id=123, type="private")
    message = TelegramMessage(message_id=789, date=datetime.now(UTC), chat=chat)
    callback = CallbackQuery(
        id="callback-1",
        from_user=sender,
        chat_instance="instance",
        message=message,
        data=telegram.encode_approval_callback(approval_id, "deny"),
    )

    payload = telegram._normalized_update(Update(1, callback_query=callback))

    assert payload is not None
    assert payload["type"] == "callback_action"
    assert payload["chatId"] == "123"
    assert payload["userId"] == "456"
    assert payload["sourceMessageId"] == "789"
    assert decode_approval_callback(payload["callbackData"]) == (approval_id, "deny")


async def test_telegram_callback_resolves_through_shared_approval_service(
    monkeypatch,
) -> None:
    approval_id = uuid4()
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="1",
        payload={
            "type": "callback_action",
            "chatId": "789",
            "userId": "456",
            "callbackQueryId": "callback-1",
            "callbackData": telegram.encode_approval_callback(approval_id, "deny"),
            "sourceMessageId": "42",
            "text": "",
            "media": [],
        },
        status="processing",
    )
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [inbox, endpoint, inbox, endpoint]
    db.scalar.side_effect = [user, endpoint]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    resolve = AsyncMock(return_value={"status": "denied", "decision": "deny"})
    answer = AsyncMock()
    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "resolve_approval_action", resolve)
    monkeypatch.setattr(telegram, "_answer_approval_callback", answer)

    assert await telegram.process_inbox(inbox.id) is None

    resolve.assert_awaited_once_with(db, endpoint, approval_id, "deny", "42")
    answer.assert_awaited_once_with(
        "token",
        "callback-1",
        "789",
        "42",
        "Freigabe abgelehnt.",
        remove_buttons=True,
    )
    assert inbox.status == "completed"


async def test_telegram_callback_resolves_question_interaction(monkeypatch) -> None:
    question_id = uuid4()
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="1",
        payload={
            "type": "callback_action",
            "chatId": "789",
            "userId": "456",
            "callbackQueryId": "callback-1",
            "callbackData": encode_question_callback(
                question_id, QuestionInteraction(action="select", optionIndex=0)
            ),
            "sourceMessageId": "42",
            "text": "",
            "media": [],
        },
        status="processing",
    )
    envelope = MessagingQuestionEnvelope.model_validate(
        {
            "kind": "question",
            "version": 1,
            "questionId": question_id,
            "sessionId": uuid4(),
            "runId": uuid4(),
            "questions": [
                {
                    "id": "format",
                    "type": "single_choice",
                    "header": "Format",
                    "question": "Welches Format?",
                    "options": [{"label": "Kurz"}, {"label": "Ausführlich"}],
                }
            ],
        }
    )
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [inbox, endpoint, inbox, endpoint]
    db.scalar.side_effect = [user, endpoint]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    result = QuestionInteractionResult(
        status="pending", envelope=envelope, changed=True, finalized=False
    )
    async def process_interaction(*_args):
        inbox.status = "completed"
        return result, "Auswahl aktualisiert."

    process = AsyncMock(side_effect=process_interaction)
    update = AsyncMock(return_value=None)
    track = AsyncMock()
    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "_process_question_interaction", process)
    monkeypatch.setattr(telegram, "_update_question_message", update)
    monkeypatch.setattr(telegram, "_track_question_messages", track)

    assert await telegram.process_inbox(inbox.id) is None

    interaction = process.await_args.args[4]
    assert interaction.action == "select"
    assert interaction.option_index == 0
    assert process.await_args.args[5] == "42"
    update.assert_awaited_once_with(
        "token",
        "789",
        "42",
        result,
        callback_query_id="callback-1",
        response="Auswahl aktualisiert.",
    )
    track.assert_awaited_once_with(db, question_id, "42", None)
    assert inbox.status == "completed"


async def test_telegram_direct_reply_answers_question_text(monkeypatch) -> None:
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="1",
        payload={
            "type": "message",
            "chatId": "789",
            "userId": "456",
            "messageId": "43",
            "replyToMessageId": "42",
            "text": "Mein Format",
            "media": [],
        },
        status="processing",
    )
    source = MessagingOutbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        destination="789",
        deduplication_key=f"question:{uuid4()}",
        payload={},
        status="delivered",
        external_message_ids=["41", "42"],
    )
    envelope = MessagingQuestionEnvelope.model_validate(
        {
            "kind": "question",
            "version": 1,
            "questionId": uuid4(),
            "sessionId": uuid4(),
            "runId": uuid4(),
            "questions": [
                {
                    "id": "note",
                    "type": "free_text",
                    "header": "Hinweis",
                    "question": "Welcher Hinweis?",
                }
            ],
        }
    )
    source.payload = envelope.model_dump(mode="json", by_alias=True)
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [inbox, endpoint, inbox, endpoint]
    db.scalar.side_effect = [user, endpoint, source]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    result = QuestionInteractionResult(
        status="pending", envelope=envelope, changed=True, finalized=False
    )
    async def process_interaction(*_args):
        inbox.status = "completed"
        return result, "Auswahl aktualisiert."

    process = AsyncMock(side_effect=process_interaction)
    update = AsyncMock(return_value=None)
    track = AsyncMock()
    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "_process_question_interaction", process)
    monkeypatch.setattr(telegram, "_update_question_message", update)
    monkeypatch.setattr(telegram, "_track_question_messages", track)

    assert await telegram.process_inbox(inbox.id) is None

    interaction = process.await_args.args[4]
    assert interaction.action == "text"
    assert interaction.text == "Mein Format"
    assert process.await_args.args[5] == "42"
    update.assert_awaited_once_with("token", "789", "41", result)
    track.assert_awaited_once_with(db, envelope.question_id, "41", None)
    assert inbox.status == "completed"


async def test_telegram_force_reply_prompts_for_question_text() -> None:
    envelope = MessagingQuestionEnvelope.model_validate(
        {
            "kind": "question",
            "version": 1,
            "questionId": uuid4(),
            "sessionId": uuid4(),
            "runId": uuid4(),
            "questions": [
                {
                    "id": "note",
                    "type": "free_text",
                    "header": "Hinweis",
                    "question": "Welcher Hinweis?",
                    "placeholder": "Hier antworten",
                }
            ],
        }
    )
    bot = AsyncMock()
    bot.send_message.return_value = SimpleNamespace(message_id=43)

    assert await telegram._send_question_reply_prompt(bot, "789", envelope) == "43"

    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == "789"
    assert sent["text"] == "Antwort eingeben:"
    assert isinstance(sent["reply_markup"], telegram.ForceReply)
    assert sent["reply_markup"].input_field_placeholder == "Hier antworten"
    assert [
        button.text
        for row in telegram._question_markup(envelope).inline_keyboard
        for button in row
    ] == ["Ablehnen"]

    answered = MessagingQuestionEnvelope.model_validate(
        {
            **envelope.model_dump(mode="json", by_alias=True),
            "answers": {"note": ["Kaffee"]},
        }
    )
    bot.send_message.reset_mock()
    assert await telegram._send_question_reply_prompt(bot, "789", answered) is None
    bot.send_message.assert_not_awaited()
    assert [
        button.text
        for row in telegram._question_markup(answered).inline_keyboard
        for button in row
    ] == ["Antworten senden", "Ablehnen"]


async def test_telegram_unbound_text_during_question_gets_direct_guidance(
    monkeypatch,
) -> None:
    session_id = uuid4()
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
        current_session_id=session_id,
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="1",
        payload={
            "type": "message",
            "chatId": "789",
            "userId": "456",
            "messageId": "43",
            "replyToMessageId": None,
            "text": "Kaffee",
            "media": [],
        },
        status="processing",
        attempts=1,
    )
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [inbox, endpoint]
    db.scalar.side_effect = [user, endpoint, uuid4()]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )

    assert await telegram.process_inbox(inbox.id) is None

    guidance = db.add.call_args.args[0]
    assert guidance.payload["text"] == (
        "Bitte nutze das eingeblendete Antwortfeld oder Telegrams Funktion "
        "„Antworten“ an der Fragen-Nachricht."
    )
    assert inbox.status == "completed"
    assert inbox.attempts == 1


async def test_disabled_provider_falls_back_to_app(monkeypatch) -> None:
    endpoint_id = uuid4()
    endpoint = MessagingEndpoint(
        id=endpoint_id,
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="1",
        paired_chat_id="1",
    )
    automation = Automation(
        id=uuid4(),
        user_id=endpoint.user_id,
        execution_session_id=uuid4(),
        output_session_id=uuid4(),
        delivery_channel_type="endpoint",
        delivery_endpoint_id=endpoint_id,
        name="Test",
        task="Test",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = endpoint
    monkeypatch.setattr(
        "mato_loop_backend.messaging.policy.SettingsStore.load",
        AsyncMock(return_value=ChannelSettings(allowed_providers=set())),
    )

    result = await AutomationService.resolved_delivery_channel(db, automation)

    assert result == {
        "delivery_channel_type": "app",
        "delivery_endpoint_id": None,
        "delivery_destination": None,
    }


async def test_telegram_poll_releases_database_session_before_network(monkeypatch) -> None:
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = endpoint

    class SessionContext:
        closed = False

        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            self.closed = True

    session = SessionContext()

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_updates(self, **kwargs):
            assert session.closed is True
            assert kwargs["allowed_updates"] == ["message", "callback_query"]
            return ()

    ingest = AsyncMock()
    monkeypatch.setattr(telegram, "AsyncSessionFactory", lambda: session)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())
    monkeypatch.setattr(telegram, "_ingest_updates", ingest)

    await telegram._poll(endpoint.id, "worker")

    ingest.assert_awaited_once_with(endpoint.id, "worker", ())


async def test_telegram_probe_has_a_bounded_timeout(monkeypatch) -> None:
    class SlowBot:
        async def __aenter__(self):
            await asyncio.sleep(1)

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telegram, "PROBE_TIMEOUT_SECONDS", 0.001)
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: SlowBot())

    with pytest.raises(telegram.TelegramServiceError) as exc_info:
        await telegram.probe_bot("token")

    assert exc_info.value.code == "channel.unavailable"


async def test_telegram_connection_test_releases_database_before_probe(monkeypatch) -> None:
    owner_id = uuid4()
    endpoint = SimpleNamespace(
        credential_id=uuid4(),
        external_identity="123",
        external_handle=None,
    )
    rolled_back = False

    class User:
        @property
        def id(self):
            if rolled_back:
                raise AssertionError("user was accessed after rollback")
            return owner_id

    db = AsyncMock(spec=AsyncSession)

    async def rollback():
        nonlocal rolled_back
        rolled_back = True

    async def probe(_token):
        assert rolled_back is True
        return "123", "private_bot"

    db.rollback.side_effect = rollback
    monkeypatch.setattr(routes, "_ensure_available", AsyncMock())
    monkeypatch.setattr(routes, "_owned", AsyncMock(return_value=endpoint))
    monkeypatch.setattr(routes, "load_credential", AsyncMock(return_value=object()))
    monkeypatch.setattr(routes, "read_credential", Mock(return_value={"token": "token"}))
    monkeypatch.setattr(routes, "probe_bot", probe)

    assert await routes.test_telegram_endpoint(uuid4(), User(), db) == {"ok": True}
    assert endpoint.external_handle == "private_bot"
    db.commit.assert_awaited_once()


async def test_telegram_voice_uses_dictation_pipeline_without_attachment(
    monkeypatch,
) -> None:
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
    )
    source = b"telegram voice"
    attachment = AttachmentInput(
        data=source,
        mime_type="audio/ogg",
        filename="voice.ogg",
        kind="audio",
    )
    db = AsyncMock(spec=AsyncSession)

    class RemoteFile:
        async def download_as_bytearray(self):
            return bytearray(source)

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_file(self, _file_id):
            assert db.commit.await_count == 1
            return RemoteFile()

    tools = SimpleNamespace(
        tool_availability={"transcribe_audio": True},
        transcribe_audio=SimpleNamespace(timeout_seconds=30),
    )
    transcribe = AsyncMock(return_value=SimpleNamespace(text="  Gesprochener Text  "))
    monkeypatch.setattr(
        telegram.SettingsStore, "load", AsyncMock(return_value=InstanceLimits())
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())
    monkeypatch.setattr(telegram, "classify_attachment", Mock(return_value=attachment))
    monkeypatch.setattr(
        telegram,
        "process_attachment",
        Mock(return_value=SimpleNamespace(derived_files={"audio.wav": b"wav"})),
    )
    monkeypatch.setattr(telegram, "load_effective_tools", AsyncMock(return_value=tools))
    monkeypatch.setattr(telegram.MediaService, "transcribe_audio", transcribe)

    attachments, transcript = await telegram._download_media(
        db,
        endpoint,
        [
            {
                "fileId": "file-1",
                "fileSize": len(source),
                "filename": "voice.ogg",
                "mimeType": "audio/ogg",
                "kind": "voice",
            }
        ],
    )

    assert attachments == []
    assert transcript == "Gesprochener Text"
    transcribe.assert_awaited_once_with(
        db,
        audio=b"wav",
        language=None,
        timeout=30,
        user_id=endpoint.user_id,
    )


async def test_telegram_voice_transcript_becomes_plain_user_text(monkeypatch) -> None:
    session = ChatSession(
        id=uuid4(), user_id=uuid4(), title="Test", session_type="chat"
    )
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=session.user_id,
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
        current_session_id=session.id,
    )
    inbox = MessagingInbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        external_event_id="1",
        payload={
            "type": "message",
            "chatId": "789",
            "userId": "456",
            "text": "Bitte",
            "media": [{"kind": "voice"}],
        },
        status="processing",
    )
    user = UserAccount(
        id=session.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    run = AgentRun(id=inbox.id, session_id=session.id, input_message_id=uuid4())
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [inbox, endpoint, None, session]
    db.scalar.side_effect = [user, endpoint, None, user, endpoint, inbox]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    start = AsyncMock(return_value=run)
    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        telegram, "_download_media", AsyncMock(return_value=([], "gesprochener Text"))
    )
    monkeypatch.setattr(telegram.AgentRuntime, "start", start)

    assert await telegram.process_inbox(inbox.id) == run.id
    assert start.await_args.args[2] == "Bitte gesprochener Text"
    assert start.await_args.kwargs["attachments"] == []
    assert "Transkription" not in start.await_args.args[2]


async def test_telegram_progress_target_copies_state_before_rollback(monkeypatch) -> None:
    run_id = uuid4()
    rolled_back = False
    inbox = SimpleNamespace(
        payload={"chatId": "123", "userId": "456"},
        external_message_id="789",
    )
    endpoint = SimpleNamespace(
        paired_chat_id="123",
        paired_user_id="456",
        enabled=True,
        deleted_at=None,
        provider="telegram",
    )
    user = SimpleNamespace(is_active=True)

    class Run:
        @property
        def status(self):
            if rolled_back:
                raise AssertionError("run was accessed after rollback")
            return "running"

    rows = Mock()
    rows.one_or_none.return_value = (inbox, endpoint, user, Run())
    db = AsyncMock(spec=AsyncSession)
    db.execute.return_value = rows

    async def rollback():
        nonlocal rolled_back
        rolled_back = True

    db.rollback.side_effect = rollback

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))

    assert await telegram._progress_target(run_id) == (
        "running",
        "token",
        123,
        789,
    )


async def test_telegram_relays_typing_and_native_drafts(monkeypatch) -> None:
    run_id = uuid4()
    calls = []

    async def events():
        await asyncio.sleep(0.01)
        yield {
            "type": "reasoning.updated",
            "reasoning": {"kind": "content", "text": "secret reasoning"},
        }
        yield {
            "type": "reasoning.updated",
            "reasoning": {"kind": "summary", "text": "**Vorgehen prüfen**"},
        }
        yield {"type": "message.delta", "delta": "Zwischenstand"}
        yield {
            "type": "message.completed",
            "message": {"toolCalls": [{"name": "test"}]},
        }
        yield {
            "type": "run.phase",
            "phase": "processing_tool",
            "tool": "read_file",
            "toolLabel": "Datei lesen",
        }
        yield {
            "type": "tool.completed",
            "tool": "read_file",
            "toolLabel": "Datei lesen",
            "output": "secret output",
            "metadata": {"private": "secret metadata"},
        }
        yield {"type": "message.delta", "delta": "Antwort"}
        yield {"type": "run.phase", "phase": "finalizing"}
        yield {"type": "run.completed"}

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_chat_action(self, **kwargs):
            calls.append(("typing", kwargs))

        async def send_message_draft(self, **kwargs):
            calls.append(("draft", kwargs))

    monkeypatch.setattr(telegram, "TYPING_REFRESH_SECONDS", 0.005)
    monkeypatch.setattr(telegram, "DRAFT_UPDATE_SECONDS", 0)
    monkeypatch.setattr(
        telegram,
        "_progress_target",
        AsyncMock(return_value=("running", "token", 123, 456)),
    )
    monkeypatch.setattr(
        telegram.run_events,
        "subscribe",
        lambda _run_id: events(),
    )
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram._relay_run_progress(run_id)

    assert calls[0][0] == "typing"
    drafts = [payload["text"] for kind, payload in calls if kind == "draft"]
    assert drafts == [
        "Nachdenken",
        "Vorgehen prüfen",
        "Zwischenstand",
        "Tool-Aufrufe werden vorbereitet",
        "Datei wird gelesen",
        "Antwort",
    ]
    assert "secret" not in "".join(drafts)
    assert all(
        payload["chat_id"] == 123 and payload["draft_id"] == 456
        for kind, payload in calls
        if kind == "draft"
    )
    assert all(
        payload["parse_mode"] == telegram.ParseMode.HTML
        for kind, payload in calls
        if kind == "draft" and payload["text"]
    )


async def test_telegram_refreshes_unchanged_draft_before_expiry(monkeypatch) -> None:
    drafts = []

    async def events():
        yield {"type": "message.delta", "delta": "Antwort"}
        await asyncio.sleep(0.02)
        yield {"type": "run.completed"}

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_chat_action(self, **_kwargs):
            return None

        async def send_message_draft(self, **kwargs):
            drafts.append(kwargs["text"])

    monkeypatch.setattr(telegram, "DRAFT_UPDATE_SECONDS", 0)
    monkeypatch.setattr(telegram, "DRAFT_REFRESH_SECONDS", 0.005)
    monkeypatch.setattr(
        telegram,
        "_progress_target",
        AsyncMock(return_value=("running", "token", 123, 456)),
    )
    monkeypatch.setattr(
        telegram.run_events,
        "subscribe",
        lambda _run_id: events(),
    )
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram._relay_run_progress(uuid4())

    assert len(drafts) >= 2
    assert set(drafts) == {"Antwort"}


async def test_telegram_keeps_typing_active_while_draft_exists(monkeypatch) -> None:
    calls = []

    async def events():
        yield {"type": "message.delta", "delta": "Antwort"}
        await asyncio.sleep(0.025)
        yield {"type": "run.completed"}

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_chat_action(self, **_kwargs):
            calls.append("typing")

        async def send_message_draft(self, **_kwargs):
            calls.append("draft")

    monkeypatch.setattr(telegram, "TYPING_REFRESH_SECONDS", 0.005)
    monkeypatch.setattr(telegram, "DRAFT_UPDATE_SECONDS", 0)
    monkeypatch.setattr(
        telegram,
        "_progress_target",
        AsyncMock(return_value=("running", "token", 123, 456)),
    )
    monkeypatch.setattr(telegram.run_events, "subscribe", lambda _run_id: events())
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram._relay_run_progress(uuid4())

    assert "draft" in calls
    assert calls.count("typing") >= 3


async def test_telegram_keeps_approval_wait_visible_as_draft(monkeypatch) -> None:
    drafts = []

    async def events():
        yield {"type": "approval.created", "approval": {"summary": "secret"}}
        await asyncio.sleep(0.01)
        yield {"type": "run.completed"}

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_chat_action(self, **_kwargs):
            raise AssertionError("waiting approvals must not show typing")

        async def send_message_draft(self, **kwargs):
            drafts.append(kwargs["text"])

    monkeypatch.setattr(telegram, "DRAFT_UPDATE_SECONDS", 0)
    monkeypatch.setattr(
        telegram,
        "_progress_target",
        AsyncMock(return_value=("waiting_approval", "token", 123, 456)),
    )
    monkeypatch.setattr(telegram.run_events, "subscribe", lambda _run_id: events())
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram._relay_run_progress(uuid4())

    assert drafts == ["Wartet auf Freigabe"]


async def test_telegram_stops_progress_before_final_delivery(monkeypatch) -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()
    run_id = uuid4()

    async def relay(_run_id):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(telegram, "_relay_run_progress", relay)
    telegram.TelegramScheduler._progress_tasks = {}

    telegram.TelegramScheduler._start_progress(run_id)
    await started.wait()
    await telegram.TelegramScheduler._stop_progress(run_id)

    assert cancelled.is_set()
    assert run_id not in telegram.TelegramScheduler._progress_tasks


@pytest.mark.parametrize("with_inbox", [True, False])
async def test_telegram_claims_outbox_with_optional_linked_run(
    monkeypatch, with_inbox
) -> None:
    outbox_id = uuid4()
    inbox_id = uuid4() if with_inbox else None
    run_id = uuid4() if with_inbox else None
    item = MessagingOutbox(
        id=outbox_id,
        endpoint_id=uuid4(),
        inbox_id=inbox_id,
        destination="123",
        deduplication_key="claim-test",
        payload={"text": "test"},
        attempts=0,
    )
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [item, run_id] if with_inbox else [item]

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)

    assert await telegram.claim_outbox() == (outbox_id, run_id)
    assert item.status == "processing"
    assert item.attempts == 1
    assert item.processing_at is not None
    assert db.scalar.await_count == (2 if with_inbox else 1)
    db.commit.assert_awaited_once()


async def test_telegram_progress_revalidates_target_before_sending(monkeypatch) -> None:
    calls = []

    async def events():
        await asyncio.sleep(1)
        yield {"type": "run.completed"}

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_chat_action(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(
        telegram,
        "_progress_target",
        AsyncMock(side_effect=[("running", "token", 123, 456), None]),
    )
    monkeypatch.setattr(
        telegram.run_events,
        "subscribe",
        lambda _run_id: events(),
    )
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram._relay_run_progress(uuid4())

    assert calls == []


async def test_telegram_progress_failure_does_not_escape(monkeypatch) -> None:
    async def events():
        await asyncio.sleep(1)
        yield {"type": "run.completed"}

    class FailingBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_chat_action(self, **_kwargs):
            raise telegram.NetworkError("unavailable")

    monkeypatch.setattr(
        telegram,
        "_progress_target",
        AsyncMock(return_value=("running", "token", 123, 456)),
    )
    monkeypatch.setattr(
        telegram.run_events,
        "subscribe",
        lambda _run_id: events(),
    )
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FailingBot())

    await telegram._relay_run_progress(uuid4())


async def test_telegram_delivery_renders_markdown(monkeypatch) -> None:
    endpoint_id = uuid4()
    endpoint = MessagingEndpoint(
        id=endpoint_id,
        user_id=uuid4(),
        credential_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
    )
    item = MessagingOutbox(
        id=uuid4(),
        endpoint_id=endpoint_id,
        destination="789",
        deduplication_key="test",
        payload={"text": "**Antwort**", "contentFormat": "markdown"},
        status="processing",
    )
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [item, endpoint]
    db.scalar.side_effect = [user, endpoint, item, item]
    sent_messages = []

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_message(self, **kwargs):
            assert db.rollback.await_count == 1
            sent_messages.append(kwargs)
            return SimpleNamespace(message_id=1)

    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram.deliver_outbox(item.id)

    assert sent_messages == [
        {
            "chat_id": "789",
            "text": "<b>Antwort</b>",
            "parse_mode": telegram.ParseMode.HTML,
            "reply_markup": None,
        }
    ]
    assert item.status == "delivered"
    assert item.external_message_ids == ["1"]


async def test_telegram_delivery_renders_approval_as_native_buttons(monkeypatch) -> None:
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        credential_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
    )
    approval = ApprovalRequest(
        id=uuid4(),
        request_type="agent_action",
        user_id=endpoint.user_id,
        session_id=uuid4(),
        run_id=uuid4(),
        action_kind="file_write",
        risk_level="medium",
        title="Datei schreiben?",
        summary="Der Agent möchte eine Datei schreiben.",
        capability="filesystem.write",
        resource_type="file",
        resource_scope="/workspace/test.txt",
        payload={},
        available_decisions=["allow_once", "deny"],
        status="pending",
    )
    payload = MessagingApprovalPayload(
        kind="approval",
        version=1,
        text="Freigabe erforderlich\n\nDatei schreiben?",
        approvalId=approval.id,
        sessionId=approval.session_id,
        runId=approval.run_id,
        actions=[
            MessagingApprovalAction(
                decision="allow_once", label="Einmal erlauben"
            ),
            MessagingApprovalAction(decision="deny", label="Ablehnen"),
        ],
    )
    item = MessagingOutbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        destination="789",
        deduplication_key=f"approval:{approval.id}",
        payload=payload.model_dump(mode="json", by_alias=True),
        status="processing",
    )
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [item, endpoint, approval]
    db.scalar.side_effect = [user, endpoint, item, item]
    sent_messages = []

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_message(self, **kwargs):
            assert db.rollback.await_count == 1
            sent_messages.append(kwargs)
            return SimpleNamespace(message_id=42)

    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram.deliver_outbox(item.id)

    assert len(sent_messages) == 1
    sent = sent_messages[0]
    assert sent["text"] == "Freigabe erforderlich\n\nDatei schreiben?"
    assert "/approval" not in sent["text"]
    buttons = [row[0] for row in sent["reply_markup"].inline_keyboard]
    assert [button.text for button in buttons] == ["Einmal erlauben", "Ablehnen"]
    assert [decode_approval_callback(button.callback_data) for button in buttons] == [
        (approval.id, "allow_once"),
        (approval.id, "deny"),
    ]
    assert item.status == "delivered"


async def test_telegram_delivery_renders_question_as_native_buttons(monkeypatch) -> None:
    endpoint = MessagingEndpoint(
        id=uuid4(),
        user_id=uuid4(),
        credential_id=uuid4(),
        provider="telegram",
        name="Privater Bot",
        enabled=True,
        external_identity="123",
        paired_user_id="456",
        paired_chat_id="789",
    )
    request = QuestionRequest(
        id=uuid4(),
        session_id=uuid4(),
        run_id=uuid4(),
        tool_call_id="call-1",
        questions=[
            {
                "id": "format",
                "type": "single_choice",
                "header": "Format",
                "question": "Welches Format?",
                "options": [{"label": "Kurz"}, {"label": "Ausführlich"}],
                "allow_custom": True,
                "default_answers": [],
            }
        ],
        status="pending",
    )
    session = ChatSession(id=request.session_id, user_id=endpoint.user_id, title="Test")
    payload = MessagingQuestionEnvelope.model_validate(
        {
            "kind": "question",
            "version": 1,
            "questionId": request.id,
            "sessionId": request.session_id,
            "runId": request.run_id,
            "questions": request.questions,
        }
    )
    item = MessagingOutbox(
        id=uuid4(),
        endpoint_id=endpoint.id,
        destination="789",
        deduplication_key=f"question:{request.id}",
        payload=payload.model_dump(mode="json", by_alias=True),
        status="processing",
    )
    user = UserAccount(
        id=endpoint.user_id,
        username="user",
        password_hash="hash",
        is_active=True,
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [item, endpoint, request, session]
    db.scalar.side_effect = [user, endpoint, item, item]
    sent_messages = []

    class SessionContext:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *_args):
            return None

    class FakeBot:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def send_message(self, **kwargs):
            sent_messages.append(kwargs)
            return SimpleNamespace(message_id=42)

    monkeypatch.setattr(telegram, "AsyncSessionFactory", SessionContext)
    monkeypatch.setattr(
        telegram, "channel_provider_allowed", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(telegram, "endpoint_token", AsyncMock(return_value="token"))
    monkeypatch.setattr(telegram, "Bot", lambda **_kwargs: FakeBot())

    await telegram.deliver_outbox(item.id)

    assert len(sent_messages) == 1
    sent = sent_messages[0]
    assert sent["text"] == (
        "Frage 1 von 1: Format\n\nWelches Format?\n\nWähle eine Antwort aus."
    )
    buttons = sent["reply_markup"].inline_keyboard
    assert [button.text for row in buttons for button in row] == [
        "Kurz",
        "Ausführlich",
        "Eigene Antwort",
        "Antworten senden",
        "Ablehnen",
    ]
    decoded_question_id, interaction = decode_question_callback(
        buttons[0][0].callback_data
    )
    assert decoded_question_id == request.id
    assert interaction == QuestionInteraction(action="select", optionIndex=0)
    assert item.status == "delivered"
    assert item.external_message_ids == ["42"]
