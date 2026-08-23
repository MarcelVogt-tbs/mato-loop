from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.request_body_limit import _request_limit
from mato_loop_backend.api.routes.integrations import (
    IntegrationAccessRequest,
    get_integration_access,
    update_integration_access,
)
from mato_loop_backend.integrations.mail import (
    MAIL_ADAPTER,
    MAIL_CREDENTIAL_KIND,
    MailConfig,
    MailCreateDraftArguments,
    MailSendMessageArguments,
    MailTokenArguments,
    binding_revision,
    locator_from_token,
    token_for,
)
from mato_loop_backend.integrations.mail_client import (
    MailClient,
    MailError,
    MessageLocator,
    build_message,
    parse_message,
)
from mato_loop_backend.integrations.service import MailIntegrationService
from mato_loop_backend.security import CredentialCipher
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
)


def _account() -> IntegrationInstance:
    return IntegrationInstance(
        name="Mail",
        integration_type="mail",
        adapter_type="imap_smtp",
        config={
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_security": "tls",
            "smtp_host": "smtp.example.com",
            "smtp_port": 587,
            "smtp_security": "starttls",
            "username": "user@example.com",
            "from_address": "user@example.com",
        },
    )


def _client() -> MailClient:
    return MailClient(
        **MailConfig.model_validate(_account().config).model_dump(), password="secret"
    )


def test_mail_config_and_adapter_allow_only_encrypted_transports_and_account_policies() -> None:
    with pytest.raises(ValidationError):
        MailConfig.model_validate(_account().config | {"imap_security": "plain"})
    with pytest.raises(ValidationError):
        MailConfig.model_validate(_account().config | {"smtp_security": "plain"})

    actions = {action.capability: action for action in MAIL_ADAPTER.resource_actions}
    assert MAIL_ADAPTER.resource_loader is None
    assert set(actions) == {"mail.read", "mail.drafts", "mail.send", "mail.delete"}
    assert actions["mail.read"].allowed_policies == tuple(ToolExecutionPolicy)
    assert actions["mail.drafts"].allowed_policies == tuple(ToolExecutionPolicy)
    assert actions["mail.send"].allowed_policies == (
        ToolExecutionPolicy.DISABLED,
        ToolExecutionPolicy.ASK,
    )
    assert actions["mail.delete"].allowed_policies == (
        ToolExecutionPolicy.DISABLED,
        ToolExecutionPolicy.ASK,
    )
    read_tools = {
        "mail_list_accounts",
        "mail_list_folders",
        "mail_search",
        "mail_get_message",
    }
    assert all(
        tool.releases_private_data == (tool.name in read_tools)
        for tool in MAIL_ADAPTER.shared_tools
    )
    delete_draft = next(tool for tool in MAIL_ADAPTER.shared_tools if tool.name == "mail_delete_draft")
    assert delete_draft.actions[0].capability == "mail.delete"
    account = _account()
    context = SimpleNamespace(
        bound_options={
            "integration_id": str(account.id),
            "binding_revision": binding_revision(account),
        },
        integration_settings=IntegrationSettings(instances=[account]),
    )
    request = delete_draft.authorization_factory(
        MailTokenArguments(integration_id=account.id, message_token="opaque"), context
    )[0]
    assert request.available_decisions == ("allow_once", "deny")


def test_mail_drafts_allow_missing_recipients_but_sending_does_not() -> None:
    integration_id = uuid4()
    assert MailCreateDraftArguments(integration_id=integration_id).to == ()
    with pytest.raises(ValidationError):
        MailSendMessageArguments(integration_id=integration_id)


def test_html_only_mail_is_reduced_to_safe_text() -> None:
    message = parse_message(
        b"Content-Type: text/html\r\n\r\n<h1>Hello</h1><script>bad()</script><p>World</p>",
        MessageLocator("INBOX", 1, 1),
    )
    assert message.body_format == "text"
    assert "Hello" in message.body
    assert "World" in message.body
    assert "bad" not in message.body
    assert "<h1>" not in message.body


async def test_mail_service_stores_only_encrypted_password_credential(monkeypatch) -> None:
    settings = IntegrationSettings()
    credential = SimpleNamespace(id=uuid4())
    store_credential = AsyncMock(return_value=credential)
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(
        "mato_loop_backend.integrations.service.store_credential", store_credential
    )
    db = AsyncMock(spec=AsyncSession)

    result = await MailIntegrationService.create(
        db,
        name="Mail",
        config=dict(_account().config),
        password="secret",
        enabled=True,
    )

    assert len(result.instances) == 1
    assert "password" not in result.instances[0].config
    assert store_credential.await_args.kwargs == {
        "owner_id": None,
        "kind": MAIL_CREDENTIAL_KIND,
        "label": "Mail",
        "payload": {"password": "secret"},
    }
    db.commit.assert_awaited_once()


async def test_action_only_integration_access_reads_and_updates_without_resources(
    monkeypatch,
) -> None:
    account = _account()
    integrations = IntegrationSettings(instances=[account])
    tools = ToolSettings()

    async def load(_db, settings_type, **_kwargs):
        return integrations if settings_type is IntegrationSettings else tools

    monkeypatch.setattr(SettingsStore, "load", AsyncMock(side_effect=load))
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    db = AsyncMock(spec=AsyncSession)

    access = await get_integration_access(account.id, db)
    assert access["resources"] == []
    assert {item["capability"] for item in access["actions"]} == {
        "mail.read",
        "mail.drafts",
        "mail.send",
        "mail.delete",
    }

    updated = await update_integration_access(
        account.id,
        IntegrationAccessRequest(
            policies={"mail.read": ToolExecutionPolicy.AUTO}, resources={}
        ),
        db,
    )
    assert tools.integration_policies[str(account.id)] == {
        "mail.read": ToolExecutionPolicy.AUTO
    }
    read = next(item for item in updated["actions"] if item["capability"] == "mail.read")
    assert read["effectiveIntegrationPolicy"] == "auto"


def test_message_and_attachment_tokens_are_opaque_and_revision_bound() -> None:
    raw = build_message(
        from_address="user@example.com",
        to=("other@example.com",),
        subject="Bericht",
        body="Vertraulicher Inhalt",
        attachments=(("report.bin", "application/octet-stream", b"\x00\x01"),),
    )
    locator = MessageLocator("INBOX", 17, 42)
    message = parse_message(raw, locator)
    account = _account()
    revision = binding_revision(account)

    assert message.body.startswith("Vertraulicher Inhalt")
    assert message.attachments[0].filename == "report.bin"
    token = token_for(account.id, revision, locator, part=message.attachments[0].part)
    assert "INBOX" not in token
    decoded = CredentialCipher().decrypt(token.encode("ascii"))
    assert decoded["mailbox"] == "INBOX"
    assert decoded["uidvalidity"] == 17
    assert decoded["uid"] == 42

    resolved, part = locator_from_token(
        token, account.id, revision, require_part=True
    )
    assert resolved == locator
    assert part == message.attachments[0].part
    changed = account.model_copy(deep=True)
    changed.config["imap_host"] = "new.example.com"
    with pytest.raises(ValueError, match="binding mismatch"):
        locator_from_token(
            token, account.id, binding_revision(changed), require_part=True
        )


async def test_imap_delete_uses_uid_expunge_and_never_global_expunge(monkeypatch) -> None:
    calls = []

    class FakeImap:
        capabilities = (b"IMAP4rev1", b"UIDPLUS")

        def list(self):
            return "OK", [b'(\\HasNoChildren) "/" "INBOX"']

        def select(self, mailbox, readonly=False):
            calls.append(("SELECT", mailbox, readonly))
            return "OK", [b"1"]

        def response(self, name):
            assert name == "UIDVALIDITY"
            return "OK", [b"17"]

        def uid(self, *arguments):
            calls.append(arguments)
            return "OK", [b""]

        def expunge(self):
            raise AssertionError("global EXPUNGE must not be called")

    client = _client()

    @contextmanager
    def imap():
        yield FakeImap()

    monkeypatch.setattr(client, "_imap", imap)
    await client.delete_message(MessageLocator("INBOX", 17, 42))

    assert ("STORE", "42", "+FLAGS.SILENT", "(\\Deleted)") in calls
    assert ("EXPUNGE", "42") in calls


async def test_imap_rejects_control_characters_before_select(monkeypatch) -> None:
    client = _client()

    @contextmanager
    def imap():
        raise AssertionError("invalid mailbox must be rejected before opening IMAP")
        yield

    monkeypatch.setattr(client, "_imap", imap)
    with pytest.raises(MailError) as error:
        await client.search("INBOX\r\nLOGOUT")
    assert error.value.code == "mail.invalidMessage"


async def test_imap_rejects_oversized_message_before_fetching_body(monkeypatch) -> None:
    body_fetched = False

    class FakeImap:
        def select(self, _mailbox, readonly=False):
            assert readonly
            return "OK", [b"1"]

        def response(self, _name):
            return "OK", [b"17"]

        def uid(self, _command, _uid, fields):
            nonlocal body_fetched
            if fields == "(UID FLAGS RFC822.SIZE)":
                return "OK", [b"1 (UID 42 FLAGS () RFC822.SIZE 999999999)"]
            body_fetched = True
            return "OK", []

    client = _client()

    @contextmanager
    def imap():
        yield FakeImap()

    monkeypatch.setattr(client, "_imap", imap)
    with pytest.raises(MailError) as error:
        await client.get_message(MessageLocator("INBOX", 17, 42))
    assert error.value.code == "mail.responseTooLarge"
    assert not body_fetched


async def test_smtp_disconnect_is_outcome_unknown_and_is_not_retried(monkeypatch) -> None:
    attempts = 0
    sent_from = None

    class FakeSmtp:
        def send_message(self, message, *_args, **_kwargs):
            nonlocal attempts, sent_from
            attempts += 1
            sent_from = str(message["From"])
            raise OSError("connection lost after DATA")

    client = _client()

    @contextmanager
    def smtp():
        yield FakeSmtp()

    monkeypatch.setattr(client, "_smtp", smtp)
    raw = build_message(
        from_address="forged@example.com",
        to=("other@example.com",),
        subject="Test",
        body="Body",
    )

    with pytest.raises(MailError) as error:
        await client.send(raw)
    assert error.value.code == "mail.outcomeUnknown"
    assert attempts == 1
    assert sent_from == "user@example.com"


def test_mail_multipart_routes_have_request_body_limits() -> None:
    for method, path in (
        ("POST", f"/api/mail/accounts/{uuid4()}/drafts"),
        ("PUT", f"/api/mail/accounts/{uuid4()}/drafts/token"),
        ("POST", f"/api/mail/accounts/{uuid4()}/messages/send"),
    ):
        assert _request_limit({"type": "http", "method": method, "path": path})
