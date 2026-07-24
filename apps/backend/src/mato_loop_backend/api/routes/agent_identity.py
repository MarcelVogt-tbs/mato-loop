from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import AgentIdentitySettings, SettingsStore
from mato_loop_backend.settings.models import normalize_agent_display_name

router = APIRouter(prefix="/agent-identity", dependencies=[Depends(require_user)])


class AgentIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        validation_alias=AliasChoices("displayName", "display_name"),
    )
    instructions: str | None = Field(default=None, max_length=16_000)

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("display_name must not be null")
        return normalize_agent_display_name(value)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("instructions must not be null")
        return value.strip()

    @model_validator(mode="after")
    def require_change(self) -> AgentIdentityRequest:
        if not self.model_fields_set:
            raise ValueError("at least one identity field is required")
        return self


def agent_identity_payload(settings: AgentIdentitySettings) -> dict[str, str]:
    return {
        "displayName": settings.display_name,
        "instructions": settings.instructions,
    }


@router.get("")
async def get_agent_identity(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    settings = await SettingsStore.load(db, AgentIdentitySettings, user_id=user.id)
    return agent_identity_payload(settings)


@router.patch("")
async def update_agent_identity(
    payload: AgentIdentityRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, str]:
    current = await SettingsStore.load(
        db, AgentIdentitySettings, user_id=user.id, for_update=True
    )
    values = current.model_dump()
    if "display_name" in payload.model_fields_set:
        values["display_name"] = payload.display_name
    if "instructions" in payload.model_fields_set:
        values["instructions"] = payload.instructions
    settings = AgentIdentitySettings.model_validate(values)
    await SettingsStore.save(db, settings, user_id=user.id)
    await db.commit()
    return agent_identity_payload(settings)
