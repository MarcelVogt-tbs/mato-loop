from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4, uuid5

import pytest
from fastapi.testclient import TestClient
from httpx2 import HTTPStatusError, Request, Response
from matoloop_teams_protocol import MAX_BODY_BYTES, Blob, Delivery, Event, Identity, StreamPreview
from microsoft_teams.api import InstalledActivity
from microsoft_teams.apps import App

from matoloop_teams_relay.app import create_app
from matoloop_teams_relay.config import RelayConfig
from matoloop_teams_relay.spool import Record, Spool, SpoolError
from matoloop_teams_relay.transport import TeamsTransport, identity_key, trusted_url


@pytest.fixture
def spool(tmp_path):
    value = Spool(tmp_path, max_bytes=1024 * 1024, max_records=20)
    yield value
    value.close()


def event(spool):
    return Event(
        id=uuid4(),
        identity=Identity(
            tenant_id=uuid4(), app_id=uuid4(), user_id=uuid4(), conversation_id="personal"
        ),
        generation=spool.lease(uuid4()),
        received_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
        kind="message",
        text="Hello",
        message_id="external-1",
    )


def test_admission_receipt_is_idempotent_and_drops_content(spool):
    item = event(spool)
    spool.save(
        Record(
            id=item.id,
            kind="event",
            status="handoff",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            payload=item.model_dump(mode="json"),
        )
    )
    spool.receipt(item.id, item.generation, "admitted")
    spool.receipt(item.id, item.generation, "admitted")
    assert spool.get(item.id, "event").payload == {"generation": str(item.generation)}


def test_restart_never_reexecutes_an_unacknowledged_handoff(tmp_path):
    first = Spool(tmp_path, max_bytes=1024 * 1024, max_records=20)
    item = event(first)
    first.save(
        Record(
            id=item.id,
            kind="event",
            status="handoff",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            payload=item.model_dump(mode="json"),
        )
    )
    first.close()
    recovered = Spool(tmp_path, max_bytes=1024 * 1024, max_records=20)
    try:
        recovered.lease(uuid4())
        assert recovered.pending()[0].generation != recovered.generation
    finally:
        recovered.close()


def test_only_one_receiver_can_admit_work(spool):
    spool.lease(uuid4())
    with pytest.raises(SpoolError, match="receiverBusy"):
        spool.lease(uuid4())


@pytest.mark.parametrize("scope, matching_tenant, send", [
    ("personal", True, True), ("channel", True, False), ("personal", False, False),
])
async def test_install_welcome_is_personal_and_deduplicated(spool, tmp_path, scope, matching_tenant, send):
    config = RelayConfig(
        app_id=uuid4(), tenant_id=uuid4(),
        client_secret_file=tmp_path / "secret", relay_token_file=tmp_path / "token",
        matoloop_url="https://chat.example.org",
    )
    bot = MagicMock(spec=App)
    bot.send = AsyncMock()
    TeamsTransport(bot, spool, config)
    activity = InstalledActivity.model_validate({
        "type": "installationUpdate", "id": "install", "action": "add", "channelId": "msteams",
        "conversation": {"id": "personal-chat", "conversationType": scope},
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "channelData": {"tenant": {"id": str(config.tenant_id if matching_tenant else uuid4())}},
        "from": {"id": "installer"}, "recipient": {"id": "bot"},
    })
    handler = bot.on_install_add.call_args.args[0]
    await handler(SimpleNamespace(activity=activity))
    await handler(SimpleNamespace(activity=activity))
    if send:
        bot.send.assert_awaited_once()
        conversation, message = bot.send.await_args.args
        assert conversation == "personal-chat"
        assert message.attachments[0].content["actions"][0]["url"] == (
            "https://chat.example.org/settings/channels?pair=teams"
        )
    else:
        bot.send.assert_not_awaited()


def test_oversized_action_cannot_block_the_poll_batch(spool):
    item = event(spool).model_copy(
        update={"kind": "action", "action": {"value": "a" * MAX_BODY_BYTES}}
    )
    with pytest.raises(SpoolError, match="eventTooLarge"):
        spool.save(
            Record(
                id=item.id,
                kind="event",
                status="handoff",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
                payload=item.model_dump(mode="json"),
            )
        )


@pytest.mark.parametrize(
    "url",
    [
        "https://contoso.sharepoint.com.evil.example/file",
        "http://contoso.sharepoint.com/file",
        "https://user@contoso.sharepoint.com/file",
        "https://127.0.0.1/file",
        "https://contoso.sharepoint.com:8443/file",
    ],
)
def test_file_urls_cannot_escape_microsoft_hosts(url):
    with pytest.raises(SpoolError, match="invalidDestination"):
        trusted_url(url, ("sharepoint.com",))


def test_relay_requires_authentication_and_microsoft_callback_auth(tmp_path):
    secret = tmp_path / "client-secret"
    token = tmp_path / "relay-token"
    secret.write_text("test-only-client-secret")
    token.write_text("a" * 43)
    app = create_app(
        RelayConfig(
            app_id=uuid4(),
            tenant_id=uuid4(),
            client_secret_file=secret,
            relay_token_file=token,
            spool_dir=tmp_path / "spool",
        )
    )
    with TestClient(app) as client:
        assert [
            client.get("/relay/v1/status").status_code,
            client.post("/api/messages", json={}).status_code,
            client.get(
                "/relay/v1/status", headers={"Authorization": "Bearer " + "a" * 43}
            ).status_code,
        ] == [401, 401, 200]


async def test_unknown_microsoft_send_is_not_repeated(spool, tmp_path):
    who = Identity(tenant_id=uuid4(), app_id=uuid4(), user_id=uuid4(), conversation_id="personal")
    spool.remember_conversation(
        identity_key(who),
        {
            "identity": who.model_dump(mode="json"),
            "service_url": "https://smba.trafficmanager.net/emea/",
        },
    )
    bot = MagicMock()
    bot.send = AsyncMock(side_effect=TimeoutError)
    transport = TeamsTransport(
        bot,
        spool,
        RelayConfig(
            tenant_id=who.tenant_id,
            app_id=who.app_id,
            client_secret_file=tmp_path / "secret",
            relay_token_file=tmp_path / "token",
        ),
    )
    value = Delivery(id=uuid4(), identity=who, kind="message", text="Reply", text_format="markdown")
    with pytest.raises(SpoolError, match="deliveryUnknown"):
        await transport.send(value)
    assert (await transport.send(value)).status == "unknown"
    bot.send.assert_awaited_once()
    assert bot.send.await_args.args[1].text_format == "markdown"


@pytest.mark.parametrize("reject_final", [False, True])
async def test_stream_updates_and_final_answer_share_the_teams_message(
    spool, tmp_path, monkeypatch, reject_final,
):
    who = event(spool).identity
    key = identity_key(who)
    spool.remember_conversation(key, {
        "identity": who.model_dump(mode="json"),
        "service_url": "https://smba.trafficmanager.net/emea/",
    })
    spool.save(Record(id=uuid5(key, "incoming"), kind="event", status="admitted",
                      expires_at=datetime.now(UTC) + timedelta(hours=1)))
    bot = MagicMock()
    bot.send = AsyncMock(return_value=SimpleNamespace(id="stream-id"))
    transport = TeamsTransport(bot, spool, RelayConfig(
        tenant_id=who.tenant_id, app_id=who.app_id,
        client_secret_file=tmp_path / "secret", relay_token_file=tmp_path / "token",
    ))
    clock = 0.0
    monkeypatch.setattr("matoloop_teams_relay.transport.monotonic", lambda: clock)
    assert await transport.preview(StreamPreview(
        identity=who, source_message_id="incoming", text="Datei lesen", informative=True,
    ))
    clock = 2.0
    assert await transport.preview(StreamPreview(
        identity=who, source_message_id="incoming", text="Hello",
    ))
    await transport.send(Delivery(id=uuid4(), identity=who, kind="typing"))
    if reject_final:
        request = Request("POST", "https://smba.trafficmanager.net/emea/")
        bot.send.side_effect = [
            HTTPStatusError("Stream rejected", request=request, response=Response(403)),
            SimpleNamespace(id="plain-reply"),
        ]
    result = await transport.send(Delivery(
        id=uuid4(), identity=who, kind="message", text="Hello there", source_message_id="incoming",
    ))
    status, opening, final, *plain = [
        call.args[1].model_dump(mode="json", by_alias=True, exclude_none=True)
        for call in bot.send.await_args_list
    ]
    assert status["replyToId"] == opening["replyToId"] == final["replyToId"] == "incoming"
    assert status["entities"] == [
        {"type": "streaminfo", "streamType": "informative", "streamSequence": 1},
    ]
    assert opening["entities"] == [
        {"type": "streaminfo", "streamId": "stream-id", "streamType": "streaming",
         "streamSequence": 2},
    ]
    assert all("id" not in activity for activity in (status, opening, final))
    assert final["entities"] == [
        {"type": "streaminfo", "streamType": "final", "streamId": "stream-id"},
    ]
    if reject_final:
        assert [(a.get("id"), a.get("entities"), a["text"]) for a in plain] == [
            (None, None, "Hello there"),
        ]
        assert result.message_ids == ["plain-reply"]
    else:
        assert not plain


async def test_notified_handoffs_do_not_starve_later_uncertainty_notifications(spool, tmp_path):
    spool.max_records = 100
    original = event(spool)
    for index in range(21):
        item = original.model_copy(update={"id": uuid4()})
        spool.save(
            Record(
                id=item.id,
                kind="event",
                status="uncertain",
                expires_at=datetime.now(UTC) + timedelta(hours=24),
                payload=item.model_dump(mode="json"),
            )
        )
        if index < 20:
            spool.save(
                Record(
                    id=uuid5(item.id, "uncertain"),
                    kind="delivery",
                    status="sent",
                    expires_at=datetime.now(UTC) + timedelta(hours=24),
                )
            )
    transport = TeamsTransport(
        MagicMock(),
        spool,
        RelayConfig(
            tenant_id=item.identity.tenant_id,
            app_id=item.identity.app_id,
            client_secret_file=tmp_path / "secret",
            relay_token_file=tmp_path / "token",
        ),
    )
    transport.send = AsyncMock()
    await transport.expire_handoffs()
    assert [call.args[0].id for call in transport.send.await_args_list] == [
        uuid5(item.id, "uncertain")
    ]


async def test_file_consent_waits_for_backend_reauthorization(spool, tmp_path):
    who = Identity(tenant_id=uuid4(), app_id=uuid4(), user_id=uuid4(), conversation_id="personal")
    spool.lease(uuid4())
    blob = Blob(id=uuid4(), filename="answer.txt", mime_type="text/plain", size=4)
    value = Delivery(id=uuid4(), identity=who, kind="file", blob=blob)
    spool.put_blob(blob.id, b"file", value.id)
    spool.save(
        Record(
            id=value.id,
            kind="delivery",
            status="offered",
            message_ids=["offer"],
            expires_at=datetime.now(UTC) + timedelta(minutes=30),
            payload={"delivery": value.model_dump(mode="json"), "fingerprint": "test"},
        )
    )
    bot = MagicMock()
    bot.send = AsyncMock()
    transport = TeamsTransport(
        bot,
        spool,
        RelayConfig(
            tenant_id=who.tenant_id,
            app_id=who.app_id,
            client_secret_file=tmp_path / "secret",
            relay_token_file=tmp_path / "token",
        ),
    )
    raw = {
        "channelId": "msteams",
        "channelData": {"tenant": {"id": str(who.tenant_id)}},
        "conversation": {"conversationType": "personal", "id": who.conversation_id},
        "from": {"aadObjectId": str(who.user_id)},
        "value": {
            "action": "accept",
            "context": {"deliveryId": str(value.id)},
            "uploadInfo": {
                "uploadUrl": "https://contoso.sharepoint.com/upload",
                "contentUrl": "https://contoso.sharepoint.com/file",
                "uniqueId": "file",
                "fileType": "txt",
            },
        },
    }
    activity = SimpleNamespace(model_dump=lambda **_: raw, reply_to_id="offer")
    await transport.on_file_consent(SimpleNamespace(activity=activity, send=AsyncMock()))
    assert spool.get(value.id, "delivery").status == "pending" and not transport.tasks
