from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.personal_variables import (
    PERSONAL_VARIABLE_NAME_PATTERN,
    PersonalVariableError,
    create_personal_variable,
    get_personal_variable,
    list_personal_variables,
    personal_variable_payload,
    update_personal_variable,
)
from mato_loop_backend.security import require_user

router = APIRouter(prefix="/personal-variables", dependencies=[Depends(require_user)])


class PersonalVariableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120, pattern=PERSONAL_VARIABLE_NAME_PATTERN.pattern)
    value: str | None = None
    secret: bool

    @model_validator(mode="after")
    def value_is_present(self) -> PersonalVariableRequest:
        if self.value is None:
            raise ValueError("value is required")
        return self


class PersonalVariableUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120, pattern=PERSONAL_VARIABLE_NAME_PATTERN.pattern)
    value: str | None = None
    secret: bool


def _api_error(exc: PersonalVariableError) -> ApiError:
    status_code = {
        "personalVariable.notFound": 404,
        "personalVariable.valueInvalid": 422,
        "personalVariable.valueRequired": 422,
    }.get(exc.code, 409)
    return ApiError(
        status_code,
        exc.code,
        field=exc.field,
    )


@router.get("")
async def list_variables(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[dict[str, object]]:
    return [
        personal_variable_payload(variable)
        for variable in await list_personal_variables(db, user.id)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_variable(
    payload: PersonalVariableRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, object]:
    try:
        assert payload.value is not None
        variable = await create_personal_variable(
            db, user.id, name=payload.name, value=payload.value, secret=payload.secret
        )
        await db.commit()
        return personal_variable_payload(variable)
    except PersonalVariableError as exc:
        raise _api_error(exc) from exc


@router.put("/{variable_id}")
async def update_variable(
    variable_id: UUID,
    payload: PersonalVariableUpdateRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, object]:
    try:
        variable = await update_personal_variable(
            db,
            user.id,
            variable_id,
            name=payload.name,
            value=payload.value,
            secret=payload.secret,
        )
        await db.commit()
        return personal_variable_payload(variable)
    except PersonalVariableError as exc:
        raise _api_error(exc) from exc


@router.delete("/{variable_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variable(
    variable_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    variable = await get_personal_variable(db, user.id, variable_id, for_update=True)
    if variable is None:
        raise ApiError(404, "personalVariable.notFound")
    await db.delete(variable)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
