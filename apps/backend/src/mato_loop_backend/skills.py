from __future__ import annotations

import json
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import Automation
from mato_loop_backend.settings import (
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
)
from mato_loop_backend.settings.models import normalize_skill_name, normalize_skill_text


class SkillError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=240)
    instructions: str = Field(min_length=1, max_length=8_000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> Any:
        return normalize_skill_name(value) if isinstance(value, str) else value

    @field_validator("description", "instructions", mode="before")
    @classmethod
    def validate_text(cls, value: Any, info: Any) -> Any:
        return normalize_skill_text(value, info.field_name) if isinstance(value, str) else value


class SkillEditPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, min_length=1, max_length=240)
    instructions: str | None = Field(default=None, min_length=1, max_length=8_000)

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("name must not be null")
        return normalize_skill_name(value) if isinstance(value, str) else value

    @field_validator("description", "instructions", mode="before")
    @classmethod
    def validate_text(cls, value: Any, info: Any) -> Any:
        if value is None:
            raise ValueError(f"{info.field_name} must not be null")
        return normalize_skill_text(value, info.field_name) if isinstance(value, str) else value

    @model_validator(mode="after")
    def require_change(self) -> SkillEditPatch:
        if self.name is None and self.description is None and self.instructions is None:
            raise ValueError("at least one skill field is required")
        return self


def serialize_skill_draft(draft: SkillDraft) -> str:
    return json.dumps(draft.model_dump(), ensure_ascii=False, indent=2) + "\n"


def parse_skill_draft(value: str | bytes) -> SkillDraft:
    try:
        return SkillDraft.model_validate(json.loads(value))
    except (JSONDecodeError, UnicodeDecodeError, TypeError, ValidationError) as exc:
        raise SkillError("skill.draftInvalid") from exc


@dataclass(slots=True)
class RunSkillState:
    catalog: dict[UUID, SkillDefinition] = field(default_factory=dict)
    active: SkillDefinition | None = None
    edit_completed: bool = False

    @classmethod
    def from_settings(cls, settings: SkillsSettings) -> RunSkillState:
        return cls({skill.id: skill.model_copy(deep=True) for skill in settings.skills})


class SkillService:
    @staticmethod
    async def list(db: AsyncSession, user_id: UUID) -> list[SkillDefinition]:
        settings = await SettingsStore.load(db, SkillsSettings, user_id=user_id)
        return settings.skills

    @staticmethod
    async def create(
        db: AsyncSession, user_id: UUID, draft: SkillDraft
    ) -> SkillDefinition:
        return await SkillService.create_definition(
            db, user_id, SkillDefinition(**draft.model_dump())
        )

    @staticmethod
    async def create_definition(
        db: AsyncSession, user_id: UUID, definition: SkillDefinition
    ) -> SkillDefinition:
        settings = await SettingsStore.load(
            db, SkillsSettings, user_id=user_id, for_update=True
        )
        SkillService._ensure_unique(settings, definition.name)
        if len(settings.skills) >= 20:
            raise SkillError("skill.limitReached")
        settings.skills.append(definition)
        await SettingsStore.save(db, settings, user_id=user_id)
        return definition

    @staticmethod
    async def replace(
        db: AsyncSession,
        user_id: UUID,
        skill_id: UUID,
        draft: SkillDraft,
        *,
        expected: SkillDefinition | None = None,
    ) -> SkillDefinition:
        settings = await SettingsStore.load(
            db, SkillsSettings, user_id=user_id, for_update=True
        )
        current = next((item for item in settings.skills if item.id == skill_id), None)
        if current is None:
            raise SkillError("skill.notFound")
        if expected is not None and current != expected:
            raise SkillError("skill.changed")
        SkillService._ensure_unique(settings, draft.name, exclude_id=skill_id)
        replacement = SkillDefinition(id=current.id, **draft.model_dump())
        settings.skills[settings.skills.index(current)] = replacement
        await SettingsStore.save(db, settings, user_id=user_id)
        return replacement

    @staticmethod
    async def delete(db: AsyncSession, user_id: UUID, skill_id: UUID) -> None:
        settings = await SettingsStore.load(
            db, SkillsSettings, user_id=user_id, for_update=True
        )
        referenced = await db.scalar(
            select(Automation.id).where(
                Automation.user_id == user_id, Automation.skill_id == skill_id
            )
        )
        if referenced is not None:
            raise SkillError("skill.inUse")
        current = next((item for item in settings.skills if item.id == skill_id), None)
        if current is None:
            raise SkillError("skill.notFound")
        settings.skills.remove(current)
        await SettingsStore.save(db, settings, user_id=user_id)

    @staticmethod
    def _ensure_unique(
        settings: SkillsSettings, name: str, *, exclude_id: UUID | None = None
    ) -> None:
        folded = name.casefold()
        if any(
            item.id != exclude_id and item.name.casefold() == folded
            for item in settings.skills
        ):
            raise SkillError("skill.duplicate")
