from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError

_MAX_RESPONSE_BYTES = 1_000_000


class TbsDeskError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TbsCustomer(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: int = Field(ge=1)
    erp_id: int | None = Field(
        default=None, validation_alias=AliasChoices("erp_id", "eb_id")
    )
    displayname: str = Field(min_length=1, max_length=500)


class TbsErpCustomer(BaseModel):
    model_config = ConfigDict(extra="ignore")

    erp_id: int = Field(ge=1)
    displayname: str = Field(min_length=1, max_length=500)
    contract: str | None = Field(default=None, max_length=500)
    zipcode: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=500)
    street: str | None = Field(default=None, max_length=500)


class TbsErpCustomerSearch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    customers: list[TbsErpCustomer] = Field(max_length=100)


class TbsTicketSummary(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=1)
    header: str = Field(default="", max_length=2_000)
    created: str | None = Field(default=None, max_length=100)
    open_from: str | None = Field(default=None, max_length=100)
    state: int | None = None
    priority: int | None = None
    entry_count: int | None = Field(default=None, ge=0)
    attachment_count: int | None = Field(default=None, ge=0)
    customer: TbsCustomer


class TbsTicketEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=1)
    text: str = Field(default="", max_length=100_000)
    starttime: str | None = Field(default=None, max_length=100)


class TbsTicketDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=1)
    customer_id: int = Field(ge=1)
    header: str = Field(default="", max_length=2_000)
    created: str | None = Field(default=None, max_length=100)
    state: int | None = None
    entries: list[TbsTicketEntry] = Field(default_factory=list, max_length=500)


class TbsDeskClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._client = client or httpx.AsyncClient(timeout=30, trust_env=False)
        self._owns_client = client is None
        self._token: str | None = None

    async def __aenter__(self) -> TbsDeskClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _json(
        self,
        method: str,
        path: str,
        *,
        params: list[tuple[str, str | int]] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
        authenticated: bool = True,
    ) -> Any | None:
        headers = {"Authorization": f"Bearer {self._token}"} if authenticated else {}
        request = self._client.build_request(
            method,
            f"{self.base_url}{path}",
            params=params,
            json=json_body,
            headers=headers,
        )
        try:
            response = await self._client.send(
                request, stream=True, follow_redirects=False
            )
            try:
                if allow_not_found and response.status_code == 404:
                    return None
                if response.status_code in {401, 403}:
                    raise TbsDeskError("integration.authenticationFailed")
                if response.is_redirect or response.status_code >= 400:
                    raise TbsDeskError("integration.unavailable")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise TbsDeskError("integration.responseTooLarge")
            finally:
                await response.aclose()
        except TbsDeskError:
            raise
        except httpx.HTTPError as exc:
            raise TbsDeskError("integration.unavailable") from exc
        try:
            return json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TbsDeskError("integration.invalidResponse") from exc

    async def authenticate(self) -> None:
        payload = await self._json(
            "POST",
            "/api/v2/auth/session",
            json_body={"username": self.username, "password": self.password},
            authenticated=False,
        )
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise TbsDeskError("integration.invalidResponse")
        self._token = token

    async def _ensure_authenticated(self) -> None:
        if self._token is None:
            await self.authenticate()

    async def active_call_customer(self) -> TbsCustomer | None:
        await self._ensure_authenticated()
        payload = await self._json(
            "GET", "/api/v2/phone/active_call_customer", allow_not_found=True
        )
        return self._validate(TbsCustomer, payload) if payload is not None else None

    async def search_customers(self, query: str) -> list[TbsErpCustomer]:
        await self._ensure_authenticated()
        payload = await self._json(
            "GET",
            "/api/v2/customer/search",
            params=[("q", query), ("source", "erp")],
        )
        return self._validate(TbsErpCustomerSearch, payload).customers

    async def search_tickets(
        self,
        erp_id: int,
        *,
        query: str | None,
        limit: int,
        ticket_id: int | None = None,
    ) -> list[TbsTicketSummary]:
        await self._ensure_authenticated()
        params: list[tuple[str, str | int]] = [
            ("external_id", erp_id),
            ("limit", limit),
        ]
        if ticket_id is not None:
            params.append(("ticket_id", ticket_id))
        if query:
            params.append(("header", query))
        payload = await self._json("GET", "/api/v2/ticket/filter", params=params)
        if not isinstance(payload, list) or len(payload) > limit:
            raise TbsDeskError("integration.invalidResponse")
        tickets = [self._validate(TbsTicketSummary, item) for item in payload]
        if any(ticket.customer.erp_id != erp_id for ticket in tickets):
            raise TbsDeskError("integration.invalidResponse")
        return tickets

    async def ticket(self, ticket_id: int) -> TbsTicketDetail:
        await self._ensure_authenticated()
        return self._validate(
            TbsTicketDetail,
            await self._json("GET", f"/api/v2/ticket/{ticket_id}"),
        )

    @staticmethod
    def _validate(model: type[BaseModel], payload: Any):
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise TbsDeskError("integration.invalidResponse") from exc
