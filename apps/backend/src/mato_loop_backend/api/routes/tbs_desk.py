from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.api.integration_access import _load_scoped_integrations
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.integrations.tbs_desk.adapter import (
    TBS_DESK_CREDENTIAL_KIND,
    TbsDeskConfig,
    TbsDeskIntegrationService,
    TbsSupportState,
    binding_revision,
    read_ticket,
    set_support_state,
    support_state,
)
from mato_loop_backend.integrations.tbs_desk.client import TbsDeskClient, TbsDeskError
from mato_loop_backend.models import ChatSession, QuestionRequest, UserAccount
from mato_loop_backend.security import load_credential, read_credential, require_user
from mato_loop_backend.settings import IntegrationInstance, load_effective_integrations

router = APIRouter(dependencies=[Depends(require_user)])
Db = Annotated[AsyncSession, Depends(get_db_session)]
User = Annotated[UserAccount, Depends(require_user)]


class TbsDeskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(default="tbs.Desk", min_length=1, max_length=160)
    base_url: HttpUrl = Field(validation_alias=AliasChoices("baseUrl", "base_url"))
    username: str = Field(min_length=1, max_length=320)
    password: str | None = Field(default=None, min_length=1, max_length=10_000)
    enabled: bool = True

    def config(self) -> TbsDeskConfig:
        return TbsDeskConfig(base_url=self.base_url, username=self.username)


class TbsDeskCreateRequest(TbsDeskRequest):
    password: str = Field(min_length=1, max_length=10_000)


def _payload(settings) -> dict:
    accounts = [
        item for item in settings.instances
        if item.integration_type == "support" and item.adapter_type == "tbs-desk"
    ]
    return {
        "configured": bool(accounts),
        "accounts": [{
            "id": str(item.id),
            "name": item.name,
            "integrationType": "support",
            "providerType": "tbs-desk",
            "enabled": item.enabled,
            "hasCredential": item.credential_id is not None,
            "config": {
                "baseUrl": item.config.get("base_url"),
                "username": item.config.get("username"),
            },
        } for item in accounts],
    }


def _api_error(exc: TbsDeskError) -> ApiError:
    return ApiError(
        404 if exc.code in {"integration.notFound", "session.notFound"}
        else 409 if exc.code.startswith("tbsDesk.") else 502,
        exc.code,
    )


@router.get("/integrations/support/tbs-desk")
async def get_tbs_desk(db: Db, user: User) -> dict:
    settings, _ = await _load_scoped_integrations(
        db, "personal", user, adapter_type="tbs-desk"
    )
    return _payload(settings)


@router.post("/integrations/support/tbs-desk", status_code=status.HTTP_201_CREATED)
async def create_tbs_desk(payload: TbsDeskCreateRequest, db: Db, user: User) -> dict:
    await _load_scoped_integrations(
        db, "personal", user, mutation=True, adapter_type="tbs-desk"
    )
    config = payload.config()
    settings = await TbsDeskIntegrationService.create(
        db, owner_id=user.id, name=payload.name, base_url=str(config.base_url),
        username=config.username, password=payload.password, enabled=payload.enabled,
    )
    return _payload(settings)


@router.put("/integrations/support/tbs-desk/{integration_id}")
async def update_tbs_desk(
    integration_id: UUID, payload: TbsDeskRequest, db: Db, user: User
) -> dict:
    await _load_scoped_integrations(
        db, "personal", user, mutation=True, adapter_type="tbs-desk"
    )
    config = payload.config()
    try:
        settings = await TbsDeskIntegrationService.update(
            db, integration_id, owner_id=user.id, name=payload.name,
            base_url=str(config.base_url), username=config.username,
            password=payload.password, enabled=payload.enabled,
        )
    except TbsDeskError as exc:
        raise _api_error(exc) from exc
    return _payload(settings)


@router.delete(
    "/integrations/support/tbs-desk/{integration_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_tbs_desk(integration_id: UUID, db: Db, user: User) -> None:
    await _load_scoped_integrations(db, "personal", user, mutation=True)
    try:
        await TbsDeskIntegrationService.delete(db, integration_id, owner_id=user.id)
    except TbsDeskError as exc:
        raise _api_error(exc) from exc


async def _owned_account(db: AsyncSession, user: UserAccount, integration_id: UUID):
    settings, _ = await _load_scoped_integrations(
        db, "personal", user, adapter_type="tbs-desk"
    )
    account = TbsDeskIntegrationService.find(settings, integration_id)
    if account is None:
        raise ApiError(404, "integration.notFound")
    return account


async def _client(db: AsyncSession, account: IntegrationInstance) -> TbsDeskClient:
    if account.credential_id is None:
        raise TbsDeskError("integration.authenticationFailed")
    record = await load_credential(db, account.credential_id, owner_id=account.owner_id)
    if record is None or record.kind != TBS_DESK_CREDENTIAL_KIND:
        raise TbsDeskError("integration.authenticationFailed")
    try:
        credential = read_credential(record)
    except Exception as exc:
        raise TbsDeskError("integration.authenticationFailed") from exc
    if (
        not isinstance(credential, dict)
        or set(credential) != {"password"}
        or not isinstance(credential["password"], str)
    ):
        raise TbsDeskError("integration.authenticationFailed")
    config = TbsDeskConfig.model_validate(account.config)
    return TbsDeskClient(str(config.base_url), config.username, credential["password"])


@router.post("/integrations/support/tbs-desk/{integration_id}/test")
async def test_tbs_desk(integration_id: UUID, db: Db, user: User) -> dict:
    account = await _owned_account(db, user, integration_id)
    try:
        async with await _client(db, account) as client:
            await client.authenticate()
    except TbsDeskError as exc:
        raise _api_error(exc) from exc
    return {"status": "ok"}


async def _session(db: AsyncSession, user_id: UUID, session_id: UUID) -> ChatSession:
    session = await db.scalar(select(ChatSession).where(
        ChatSession.id == session_id, ChatSession.user_id == user_id,
    ))
    if session is None:
        raise ApiError(404, "session.notFound")
    return session


async def _effective_account(
    db: AsyncSession, user_id: UUID, integration_id: UUID | None = None
) -> IntegrationInstance | None:
    settings = await load_effective_integrations(db, user_id)
    accounts = [item for item in settings.instances if item.enabled
                and item.owner_id == user_id and item.integration_type == "support"
                and item.adapter_type == "tbs-desk"
                and (integration_id is None or item.id == integration_id)]
    return accounts[0] if len(accounts) == 1 else None


@router.get("/tbs-desk/sessions/{session_id}")
async def get_tbs_desk_state(session_id: UUID, db: Db, user: User) -> dict:
    session = await _session(db, user.id, session_id)
    try:
        state = support_state(session)
    except TbsDeskError as exc:
        raise _api_error(exc) from exc
    account = await _effective_account(
        db, user.id, state.integration_id if state is not None else None
    )
    if account is None:
        return {
            "available": False, "integrationId": None, "candidates": [],
            "selectedCustomer": None, "tickets": [], "questionId": None,
        }
    if state is None or state.binding_revision != binding_revision(account):
        state = TbsSupportState(
            integration_id=account.id,
            binding_revision=binding_revision(account),
        )
    question = await db.scalar(
        select(QuestionRequest)
        .where(QuestionRequest.session_id == session.id, QuestionRequest.status == "pending")
        .order_by(QuestionRequest.created_at.desc())
    )
    question_id = None
    if question is not None and any(
        item.get("id") == "tbs_customer"
        for item in question.questions
    ):
        question_id = str(question.id)
    return {
        "available": True,
        **state.model_dump(mode="json", by_alias=True),
        "questionId": question_id,
    }


@router.post("/tbs-desk/sessions/{session_id}/clear-customer")
async def clear_tbs_desk_customer(session_id: UUID, db: Db, user: User) -> dict:
    session = await _session(db, user.id, session_id)
    try:
        state = support_state(session)
    except TbsDeskError as exc:
        raise _api_error(exc) from exc
    account = await _effective_account(
        db, user.id, state.integration_id if state is not None else None
    )
    if account is None:
        raise ApiError(409, "integration.unavailable")
    empty_state = TbsSupportState(
        integration_id=account.id,
        binding_revision=binding_revision(account),
    )
    set_support_state(session, empty_state)
    await db.commit()
    return {
        "available": True,
        **empty_state.model_dump(mode="json", by_alias=True),
        "questionId": None,
    }


@router.get("/tbs-desk/sessions/{session_id}/tickets/{ticket_id}")
async def get_tbs_desk_ticket(
    session_id: UUID, ticket_id: Annotated[int, Path(ge=1)], db: Db, user: User
) -> dict:
    session = await _session(db, user.id, session_id)
    try:
        state = support_state(session)
    except TbsDeskError as exc:
        raise _api_error(exc) from exc
    if (
        state is None
        or state.selected_customer is None
        or state.selected_customer.erp_id is None
    ):
        raise ApiError(409, "tbsDesk.customerRequired")
    account = await _effective_account(db, user.id, state.integration_id)
    if account is None:
        raise ApiError(409, "integration.unavailable")
    if state.binding_revision != binding_revision(account):
        raise ApiError(409, "tbsDesk.customerRequired")
    try:
        async with await _client(db, account) as client:
            ticket = await read_ticket(client, ticket_id, state.selected_customer.erp_id)
    except TbsDeskError as exc:
        raise _api_error(exc) from exc
    return ticket.model_dump(mode="json")
