import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import httpx
import pytest
from matoloop_teams_protocol import Delivery, Event, Identity
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.routes import teams_channels
from mato_loop_backend.messaging import teams
from mato_loop_backend.messaging.formatting import render_channel_text
from mato_loop_backend.messaging.questions import (
    MessagingQuestionEnvelope,
    QuestionInteraction,
    decode_question_callback,
)
from mato_loop_backend.messaging.teams_cards import (
    card_revision,
    interaction_card,
    session_list_card,
)
from mato_loop_backend.messaging.teams_client import TeamsRelayClient, TeamsServiceError
from mato_loop_backend.models import (
    ChatSession,
    MessagingEndpoint,
    MessagingInbox,
    MessagingOutbox,
    UserAccount,
)
from mato_loop_backend.settings.models import TeamsChannelSettings
from mato_loop_backend.tools.arguments import QuestionItem, QuestionOption


def identity():
    return Identity(tenant_id=uuid4(), app_id=uuid4(), user_id=uuid4(), conversation_id="personal")


@pytest.mark.parametrize("admitted", [False, True])
async def test_expired_handoff_only_reconciles_previously_committed_admission(
    monkeypatch, admitted
):
    who = identity()
    event = Event(
        id=uuid4(),
        identity=who,
        generation=uuid4(),
        kind="message",
        message_id="message",
        received_at=datetime.now(UTC) - timedelta(minutes=2),
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = MessagingInbox(payload={"event": str(event.id)}) if admitted else None
    monkeypatch.setattr(teams, "AsyncSessionFactory", lambda: nullcontext(db))
    result = await teams.ingest_event(
        event,
        uuid4(),
        TeamsChannelSettings(app_id=who.app_id, tenant_id=who.tenant_id),
    )
    assert result is admitted
    db.execute.assert_not_awaited()


async def test_lost_relay_receipt_never_blindly_resends_message():
    requests = []

    def handle(request):
        requests.append(request.method)
        return httpx.Response(404)

    async with httpx.AsyncClient(
        base_url="https://relay.example/", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(TeamsServiceError, match="channel.deliveryUnknown"):
            await TeamsRelayClient(client).deliver(
                Delivery(id=uuid4(), identity=identity(), kind="message", text="Reply"),
                recovering=True,
            )
    assert requests == ["GET"]


async def test_failed_delivery_post_preserves_unknown_outcome():
    def handle(request):
        assert request.method == "POST"
        return httpx.Response(502)

    async with httpx.AsyncClient(
        base_url="https://relay.example/", transport=httpx.MockTransport(handle)
    ) as client:
        with pytest.raises(TeamsServiceError, match="channel.deliveryUnknown"):
            await TeamsRelayClient(client).deliver(
                Delivery(id=uuid4(), identity=identity(), kind="message", text="Reply")
            )


async def test_failed_teams_answer_creates_a_notice_in_matoloop(monkeypatch):
    who = identity()
    endpoint = MessagingEndpoint(id=uuid4(), user_id=uuid4(), channel_state={"bindingId": "paired"})
    inbox = MessagingInbox(
        id=uuid4(), session_id=uuid4(), agent_run_id=uuid4(),
        payload={"bindingId": "paired", "chatId": who.conversation_id},
    )
    item = MessagingOutbox(
        id=uuid4(), endpoint_id=endpoint.id, inbox_id=inbox.id, status="processing",
        destination=who.conversation_id, deduplication_key=f"run:{inbox.agent_run_id}",
        attempts=1, payload={"text": "Antwort"},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.side_effect = [item, inbox, item, endpoint, inbox]
    db.scalar.return_value = inbox.session_id
    monkeypatch.setattr(teams, "AsyncSessionFactory", lambda: nullcontext(db))
    monkeypatch.setattr(teams, "available_endpoint", AsyncMock(return_value=endpoint))
    monkeypatch.setattr(teams, "bound_identity", lambda *_: who)
    create_notice = AsyncMock()
    monkeypatch.setattr(teams.NotificationService, "create", create_notice)
    client = AsyncMock(spec=TeamsRelayClient)
    client.deliver.side_effect = TeamsServiceError("channel.deliveryUnknown")

    await teams.deliver_outbox(item.id, TeamsChannelSettings(), client)

    notice = create_notice.await_args.args[1]
    assert (item.status, item.error_code, notice.user_id, notice.session_id) == (
        "failed", "channel.deliveryUnknown", endpoint.user_id, inbox.session_id,
    )


async def test_disabling_streaming_keeps_existing_pairings(monkeypatch):
    who = identity()
    settings = TeamsChannelSettings(
        relay_url="https://relay.example", app_id=who.app_id, tenant_id=who.tenant_id,
        credential_id=uuid4(), revision="existing",
    )
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(teams_channels, "lock_channel_provider", AsyncMock())
    monkeypatch.setattr(teams_channels.SettingsStore, "load", AsyncMock(return_value=settings))
    save = AsyncMock()
    monkeypatch.setattr(teams_channels.SettingsStore, "save", save)

    payload = await teams_channels.update_configuration(
        teams_channels.TeamsConfigurationRequest(
            relayUrl=settings.relay_url, appId=who.app_id, tenantId=who.tenant_id,
            streamingEnabled=False,
        ),
        UserAccount(), db,
    )

    assert payload["streamingEnabled"] is False
    assert settings.revision == "existing"
    db.scalars.assert_not_awaited()
    save.assert_awaited_once_with(db, settings)


async def test_old_card_cannot_approve_after_same_account_is_paired_again(monkeypatch):
    who = identity()
    item = MessagingOutbox(
        id=uuid4(),
        endpoint_id=uuid4(),
        status="delivered",
        destination=who.conversation_id,
        external_message_ids=["card"],
        payload={"runId": str(uuid4())},
    )
    event = Event(
        id=uuid4(),
        identity=who,
        generation=uuid4(),
        kind="action",
        message_id="click",
        source_message_id="card",
        received_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        action={
            "outboxId": str(item.id),
            "revision": card_revision(item.payload),
            "callback": "a1:old:o",
        },
    )
    endpoint = MessagingEndpoint(id=item.endpoint_id, channel_state={"bindingId": "new-pairing"})
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = item
    db.scalar.return_value = MessagingInbox(
        status="completed", payload={"bindingId": "old-pairing"}
    )
    decide = AsyncMock()
    monkeypatch.setattr(teams, "resolve_approval_action", decide)
    with pytest.raises(TeamsServiceError, match="channel.pairingChanged"):
        await teams.process_action(db, endpoint, MessagingInbox(id=uuid4()), event)
    decide.assert_not_awaited()


async def test_teams_help_queues_a_readable_command_card(monkeypatch):
    who = identity()
    now = datetime.now(UTC)
    event = Event(
        id=uuid4(), identity=who, generation=uuid4(), kind="message",
        message_id="help", text="/help", received_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    endpoint = MessagingEndpoint(id=uuid4(), channel_state={"bindingId": "paired"})
    inbox = MessagingInbox(
        id=event.id, endpoint_id=endpoint.id, status="processing",
        payload={"bindingId": "paired", "envelope": event.model_dump(mode="json")},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = inbox
    monkeypatch.setattr(teams, "AsyncSessionFactory", lambda: nullcontext(db))
    monkeypatch.setattr(teams, "available_endpoint", AsyncMock(return_value=endpoint))

    await teams.process_inbox(event.id, TeamsChannelSettings(), AsyncMock())

    card = db.add.call_args.args[0].payload["card"]
    command_rows = {
        row["inlines"][0]["text"] for row in card["body"] if row["type"] == "RichTextBlock"
    }
    assert {"/new", "/use <ID>", "/git-review"} <= command_rows


@pytest.mark.parametrize("question_type", ["single_choice", "multiple_choice"])
def test_teams_choice_cards_avoid_per_selection_confirmation(question_type):
    question_id = uuid4()
    envelope = MessagingQuestionEnvelope(
        kind="question",
        version=1,
        questionId=question_id,
        sessionId=uuid4(),
        runId=uuid4(),
        questions=[
            QuestionItem(
                id="choice",
                header="Auswahl",
                question="Welche Option?",
                type=question_type,
                options=[QuestionOption(label="A"), QuestionOption(label="B")],
                allow_custom=False,
            )
        ],
    )
    item = MessagingOutbox(id=uuid4(), payload=envelope.model_dump(mode="json", by_alias=True))
    card = interaction_card(item)
    assert card["body"][0]["text"] == "Deine Antwort ist nötig"
    choices = [block["actions"] for block in card["body"] if block["type"] == "ActionSet"]
    if question_type == "multiple_choice":
        assert not choices
        field = next(block for block in card["body"] if block["type"] == "Input.ChoiceSet")
        assert field["isMultiSelect"] and field["id"] == "answer"
        assert card["actions"][0]["data"]["callback"] == f"qchoices:{question_id}"
        return
    assert [[button["title"] for button in row] for row in choices] == [["A", "B"]]
    button = choices[0][0]
    assert [action["title"] for action in card["actions"]] == ["Ablehnen"]
    assert decode_question_callback(button["data"]["callback"]) == (
        question_id,
        QuestionInteraction(action="select", optionIndex=0),
    )


async def test_teams_session_picker_switches_chat_in_one_click(monkeypatch):
    who = identity()
    now = datetime.now(UTC)
    event = Event(
        id=uuid4(), identity=who, generation=uuid4(), kind="message",
        message_id="list", text="/sessions", received_at=now,
        expires_at=now + timedelta(seconds=30),
    )
    endpoint = MessagingEndpoint(id=uuid4(), user_id=uuid4(), channel_state={"bindingId": "paired"})
    inbox = MessagingInbox(
        id=event.id, endpoint_id=endpoint.id, status="processing",
        payload={"bindingId": "paired", "envelope": event.model_dump(mode="json")},
    )
    sessions = [ChatSession(id=uuid4(), title=f"Chat {index}") for index in range(8)]
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = inbox
    latest = AsyncMock(return_value=sessions)
    monkeypatch.setattr(teams, "AsyncSessionFactory", lambda: nullcontext(db))
    monkeypatch.setattr(teams, "available_endpoint", AsyncMock(return_value=endpoint))
    monkeypatch.setattr(teams, "recent_chat_sessions", latest)
    await teams.process_inbox(event.id, TeamsChannelSettings(), AsyncMock())

    latest.assert_awaited_once_with(db, endpoint, limit=8)
    item = db.add.call_args.args[0]
    item.id, item.status, item.external_message_ids = uuid4(), "delivered", ["card"]
    card = session_list_card(item)
    buttons = [row["actions"][0] for row in card["body"] if row["type"] == "ActionSet"]
    assert [button["title"] for button in buttons] == [session.title for session in sessions]

    click = Event(
        id=uuid4(), identity=who, generation=event.generation, kind="action",
        message_id="click", source_message_id="card", action=buttons[-1]["data"],
        received_at=now, expires_at=now + timedelta(seconds=30),
    )
    db.get.side_effect = [item, inbox, item, inbox]
    db.scalars.return_value.all = MagicMock(return_value=[sessions[-1]])
    forged = click.model_copy(update={"action": {**click.action, "callback": f"session:{uuid4()}"}})
    with pytest.raises(TeamsServiceError, match="channel.pairingChanged"):
        await teams.process_action(db, endpoint, MessagingInbox(id=forged.id), forged)
    await teams.process_action(db, endpoint, MessagingInbox(id=click.id), click)
    assert endpoint.current_session_id == sessions[-1].id


def test_teams_approval_card_flags_that_the_user_needs_to_act():
    item = MessagingOutbox(id=uuid4(), payload={
        "kind": "approval", "version": 1, "text": "Datei speichern?",
        "approvalId": str(uuid4()), "sessionId": str(uuid4()), "runId": str(uuid4()),
        "actions": [{"decision": "deny", "label": "Ablehnen"}],
    })
    assert interaction_card(item)["body"][0]["text"] == "Deine Freigabe ist nötig"


async def test_deleted_endpoint_releases_the_unique_external_identity(monkeypatch):
    user = UserAccount(id=uuid4())
    item = MessagingEndpoint(id=uuid4(), external_identity="tenant:user")
    db = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(teams_channels, "owned", AsyncMock(return_value=item))
    monkeypatch.setattr(teams_channels, "stop_endpoint_work", AsyncMock())
    await teams_channels.remove_endpoint(item.id, user, db)
    assert item.external_identity != "tenant:user"


async def test_polling_recovers_when_database_cannot_record_the_failure(monkeypatch):
    settings = AsyncMock(side_effect=[SQLAlchemyError(), None, asyncio.CancelledError()])
    monkeypatch.setattr(teams.TeamsScheduler, "settings", settings)
    monkeypatch.setattr(teams, "AsyncSessionFactory", MagicMock(side_effect=SQLAlchemyError()))
    monkeypatch.setattr(teams.asyncio, "sleep", AsyncMock())
    with pytest.raises(asyncio.CancelledError):
        await teams.TeamsScheduler.poll_loop()
    assert settings.await_count == 3


def test_long_teams_code_blocks_stay_bounded_and_keep_complete_content():
    code = "    print('hello')\n" * 800
    parts = render_channel_text("teams", f"```python\n{code}```", content_format="markdown")
    assert (
        "".join(part.text.removeprefix("```python\n").removesuffix("\n```") for part in parts)
        == code
    )
    assert max(len(part.text.encode()) for part in parts) < 20_000
