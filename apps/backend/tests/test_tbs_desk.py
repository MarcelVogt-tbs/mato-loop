import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.routes.tbs_desk import (
    _payload,
    _session,
    get_tbs_desk_state,
)
from mato_loop_backend.integrations.registry import effective_integration_contributions
from mato_loop_backend.integrations.tbs_desk import (
    SelectCustomerArguments,
    TbsDeskConfig,
    TbsDeskIntegrationService,
    TbsSupportState,
    binding_revision,
    profile_id,
    read_ticket,
    set_support_state,
    support_state,
    tbs_desk_select_customer,
)
from mato_loop_backend.integrations.tbs_desk_client import (
    TbsCustomer,
    TbsDeskClient,
    TbsDeskError,
    TbsErpCustomer,
    TbsTicketDetail,
    TbsTicketSummary,
)
from mato_loop_backend.models import ChatSession
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    UserIntegrationSettings,
)


def test_config_allows_http_endpoint() -> None:
    config = TbsDeskConfig(
        base_url="http://192.168.1.30:9000", username="agent"
    )

    assert str(config.base_url) == "http://192.168.1.30:9000/"


async def test_client_authenticates_and_binds_ticket_search_to_customer() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/auth/session":
            assert json.loads(request.content) == {"username": "agent", "password": "secret"}
            return httpx.Response(200, json={"access_token": "token"})
        assert request.headers["Authorization"] == "Bearer token"
        if request.url.path == "/api/v2/customer/search":
            assert request.url.params["q"] == "Acme"
            assert request.url.params["source"] == "erp"
            return httpx.Response(200, json={
                "customers": [{
                    "erp_id": 123,
                    "displayname": "Acme",
                    "zipcode": "12345",
                    "city": "Berlin",
                }],
                "contact": None,
            })
        assert request.url.params.get_list("external_id") == ["123"]
        assert request.url.params.get_list("customer_id") == []
        assert request.url.params["header"] == "Drucker"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=[{
            "id": 7,
            "header": "Drucker",
            "customer": {"id": 42, "displayname": "Acme", "eb_id": 123},
        }])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        async with TbsDeskClient(
            "https://desk.example", "agent", "secret", client=http
        ) as client:
            customers = await client.search_customers("Acme")
            tickets = await client.search_tickets(123, query="Drucker", limit=5)

    assert customers[0].erp_id == 123
    assert customers[0].city == "Berlin"
    assert tickets[0].customer.id == 42
    assert len(requests) == 3


async def test_client_rejects_auth_redirects() -> None:
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(302, headers={"Location": "https://other.example"})
        )
    ) as http:
        async with TbsDeskClient(
            "https://desk.example", "agent", "secret", client=http
        ) as client:
            with pytest.raises(TbsDeskError, match="integration.unavailable"):
                await client.authenticate()


async def test_customer_selection_uses_only_persisted_erp_candidates(monkeypatch) -> None:
    owner_id = uuid4()
    instance = IntegrationInstance(
        name="Support", integration_type="support", adapter_type="tbs-desk",
        config={"base_url": "https://desk.example", "username": "agent"},
    )
    instance.owner_id = owner_id
    customers = [
        TbsErpCustomer(erp_id=101, displayname="Acme", city="Berlin"),
        TbsErpCustomer(erp_id=202, displayname="Acme West", city="Köln"),
    ]
    client = SimpleNamespace(
        active_call_customer=AsyncMock(),
        search_customers=AsyncMock(return_value=customers),
    )
    session = ChatSession(id=uuid4(), user_id=owner_id)

    @asynccontextmanager
    async def selected_client(_context):
        yield instance, client

    monkeypatch.setattr(
        "mato_loop_backend.integrations.tbs_desk.selected_client", selected_client
    )
    context = SimpleNamespace(
        session=session, db=SimpleNamespace(commit=AsyncMock())
    )

    search_result = await tbs_desk_select_customer(
        SelectCustomerArguments(query="Acme"), context
    )
    state = support_state(session)

    assert not search_result.is_error
    assert json.loads(search_result.content)["selectionRequired"] is True
    assert [item.id for item in state.candidates] == [101, 202]
    assert state.candidates[0].active_call is False
    assert state.candidates[0].city == "Berlin"
    assert state.candidates[1].city == "Köln"
    client.active_call_customer.assert_not_awaited()
    assert search_result.metadata["preparedReply"] == {
        "markdown": (
            "| Kundennummer | Kunde | Ort |\n"
            "|---|---|---|\n"
            "| [101](#reply-customer-101) | Acme | Berlin |\n"
            "| [202](#reply-customer-202) | Acme West | Köln |"
        ),
        "options": [
            {"id": "customer-101", "content": "Verwende ERP-Kunde 101."},
            {"id": "customer-202", "content": "Verwende ERP-Kunde 202."},
        ],
    }

    rejected = await tbs_desk_select_customer(
        SelectCustomerArguments(erp_id=999), context
    )
    assert rejected.is_error

    result = await tbs_desk_select_customer(
        SelectCustomerArguments(erp_id=202), context
    )

    assert not result.is_error
    assert support_state(session).selected_customer.erp_id == 202
    assert context.db.commit.await_count == 2


async def test_customer_selection_uses_active_call_only_without_query(monkeypatch) -> None:
    owner_id = uuid4()
    instance = IntegrationInstance(
        name="Support", integration_type="support", adapter_type="tbs-desk",
        config={"base_url": "https://desk.example", "username": "agent"},
    )
    instance.owner_id = owner_id
    client = SimpleNamespace(
        active_call_customer=AsyncMock(
            return_value=TbsCustomer(id=1, erp_id=101, displayname="Acme")
        ),
        search_customers=AsyncMock(),
    )
    session = ChatSession(id=uuid4(), user_id=owner_id)

    @asynccontextmanager
    async def selected_client(_context):
        yield instance, client

    monkeypatch.setattr(
        "mato_loop_backend.integrations.tbs_desk.selected_client", selected_client
    )
    context = SimpleNamespace(session=session, db=SimpleNamespace(commit=AsyncMock()))

    result = await tbs_desk_select_customer(SelectCustomerArguments(), context)

    assert json.loads(result.content)["selectionRequired"] is True
    assert support_state(session).candidates[0].active_call is True
    client.active_call_customer.assert_awaited_once()
    client.search_customers.assert_not_awaited()

    client.active_call_customer.return_value = None
    result = await tbs_desk_select_customer(SelectCustomerArguments(), context)

    assert json.loads(result.content)["nextAction"] == "askForCustomer"
    assert not result.is_error


async def test_ticket_detail_rejects_a_different_customer() -> None:
    client = SimpleNamespace(
        search_tickets=AsyncMock(return_value=[TbsTicketSummary(
            id=9,
            customer=TbsCustomer(id=42, erp_id=123, displayname="Acme"),
        )]),
        ticket=AsyncMock(return_value=TbsTicketDetail(
            id=9, customer_id=99, header="Foreign",
        )),
    )

    with pytest.raises(TbsDeskError, match="tbsDesk.ticketCustomerMismatch"):
        await read_ticket(client, 9, 123)

    client.search_tickets.assert_awaited_once_with(
        123, query=None, limit=1, ticket_id=9
    )


async def test_ticket_detail_does_not_fetch_unbound_ticket() -> None:
    client = SimpleNamespace(
        search_tickets=AsyncMock(return_value=[]),
        ticket=AsyncMock(),
    )

    with pytest.raises(TbsDeskError, match="tbsDesk.ticketCustomerMismatch"):
        await read_ticket(client, 9, 123)

    client.ticket.assert_not_awaited()


def test_profile_is_stable_and_only_visible_for_effective_enabled_integration() -> None:
    instance = IntegrationInstance(
        name="Support", integration_type="support", adapter_type="tbs-desk",
        config={"base_url": "https://desk.example", "username": "agent"},
    )

    profile = effective_integration_contributions(
        IntegrationSettings(instances=[instance])
    ).agent_profiles[0]

    assert profile.id == profile_id(instance.id)
    assert profile.editable is False
    assert profile.integrations_enabled_by_default is False
    assert profile.integration_availability == {str(instance.id): True}
    assert "An unfamiliar standalone word may be a customer name" in profile.instructions
    assert "do not reinterpret them as the support employee's personal issue" in profile.instructions
    assert "Distinguish documented observations from hypotheses" in profile.instructions
    instance.enabled = False
    assert not effective_integration_contributions(
        IntegrationSettings(instances=[instance])
    ).agent_profiles
    instance.enabled = True
    assert not effective_integration_contributions(IntegrationSettings(
        instances=[instance], adapter_availability={"tbs-desk": "disabled"},
    )).agent_profiles


async def test_configuration_stores_password_only_in_credential(monkeypatch) -> None:
    owner_id = uuid4()
    settings = UserIntegrationSettings()
    credential_id = uuid4()
    store_credential = AsyncMock(return_value=SimpleNamespace(id=credential_id))
    save = AsyncMock()
    monkeypatch.setattr(SettingsStore, "load", AsyncMock(return_value=settings))
    monkeypatch.setattr(SettingsStore, "save", save)
    monkeypatch.setattr(
        "mato_loop_backend.integrations.tbs_desk.store_credential", store_credential
    )
    db = AsyncMock(spec=AsyncSession)

    result = await TbsDeskIntegrationService.create(
        db, owner_id=owner_id, name="Desk", base_url="https://desk.example",
        username="agent", password="secret", enabled=True,
    )

    account = result.instances[0]
    assert account.config == {
        "base_url": "https://desk.example", "username": "agent"
    }
    assert "secret" not in json.dumps(_payload(result))
    store_credential.assert_awaited_once()
    assert store_credential.await_args.kwargs["payload"] == {"password": "secret"}
    assert account.credential_id == credential_id


async def test_runtime_session_lookup_is_owner_scoped() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = None

    with pytest.raises(ApiError, match="session.notFound"):
        await _session(db, uuid4(), uuid4())

    statement = db.scalar.await_args.args[0]
    assert "chat_sessions.user_id" in str(statement)


async def test_runtime_state_route_returns_only_owner_session_state(monkeypatch) -> None:
    owner_id = uuid4()
    account = IntegrationInstance(
        name="Support", integration_type="support", adapter_type="tbs-desk",
        config={"base_url": "https://desk.example", "username": "agent"},
    )
    account.owner_id = owner_id
    session = ChatSession(id=uuid4(), user_id=owner_id)
    revision = binding_revision(account)
    set_support_state(session, TbsSupportState(
        integration_id=account.id,
        binding_revision=revision,
    ))
    db = AsyncMock(spec=AsyncSession)
    db.scalar.side_effect = [session, None]
    monkeypatch.setattr(
        "mato_loop_backend.api.routes.tbs_desk.load_effective_integrations",
        AsyncMock(return_value=IntegrationSettings(instances=[account])),
    )

    result = await get_tbs_desk_state(
        session.id, db, SimpleNamespace(id=owner_id)
    )

    assert result == {
        "available": True,
        "integrationId": str(account.id),
        "bindingRevision": revision,
        "candidates": [],
        "selectedCustomer": None,
        "tickets": [],
        "questionId": None,
    }
