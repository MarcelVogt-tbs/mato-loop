from __future__ import annotations

import json
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any, Literal
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
    DEFAULT_SKILLS,
    InstanceSkillsSettings,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    load_effective_skills,
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
    def from_skills(cls, skills: list[SkillDefinition]) -> RunSkillState:
        return cls({skill.id: skill.model_copy(deep=True) for skill in skills if skill.enabled})


SkillScope = Literal["instance", "personal"]


class SkillService:
    @staticmethod
    async def list(db: AsyncSession, user_id: UUID) -> list[SkillDefinition]:
        return await load_effective_skills(db, user_id, enabled_only=True)

    @staticmethod
    async def list_scoped(
        db: AsyncSession, user_id: UUID, scope: SkillScope
    ) -> list[SkillDefinition]:
        settings, _ = await SkillService._load_scoped(db, user_id, scope)
        if scope == "instance":
            return settings.skills
        effective = await load_effective_skills(db, user_id)
        defaults = [skill for skill in effective if skill.id in DEFAULT_SKILLS]
        custom = [skill for skill in settings.skills if skill.id not in DEFAULT_SKILLS]
        return [*defaults, *custom]

    @staticmethod
    async def create(
        db: AsyncSession, user_id: UUID, draft: SkillDraft, scope: SkillScope = "personal"
    ) -> SkillDefinition:
        return await SkillService.create_definition(
            db, user_id, SkillDefinition(**draft.model_dump()), scope=scope
        )

    @staticmethod
    async def create_definition(
        db: AsyncSession, user_id: UUID, definition: SkillDefinition,
        *, scope: SkillScope = "personal"
    ) -> SkillDefinition:
        settings, owner_id = await SkillService._load_scoped(
            db, user_id, scope, for_update=True
        )
        SkillService._ensure_unique(settings, definition.name)
        if sum(skill.id not in DEFAULT_SKILLS for skill in settings.skills) >= 20:
            raise SkillError("skill.limitReached")
        settings.skills.append(definition)
        await SettingsStore.save(db, settings, user_id=owner_id)
        return definition

    @staticmethod
    async def replace(
        db: AsyncSession,
        user_id: UUID,
        skill_id: UUID,
        draft: SkillDraft,
        *,
        expected: SkillDefinition | None = None,
        scope: SkillScope = "personal",
    ) -> SkillDefinition:
        settings, owner_id = await SkillService._load_scoped(
            db, user_id, scope, for_update=True
        )
        current = next((item for item in settings.skills if item.id == skill_id), None)
        if current is None and scope == "personal" and skill_id in DEFAULT_SKILLS:
            current = await SkillService._default_base(db, skill_id)
            settings.skills.append(current)
        if current is None:
            raise SkillError("skill.notFound")
        if expected is not None and current.model_dump() != expected.model_dump():
            raise SkillError("skill.changed")
        SkillService._ensure_unique(settings, draft.name, exclude_id=skill_id)
        replacement = SkillDefinition(
            id=current.id, enabled=current.enabled, **draft.model_dump()
        )
        settings.skills[settings.skills.index(current)] = replacement
        await SettingsStore.save(db, settings, user_id=owner_id)
        return replacement

    @staticmethod
    async def delete(
        db: AsyncSession, user_id: UUID, skill_id: UUID,
        scope: SkillScope = "personal",
    ) -> None:
        if skill_id in DEFAULT_SKILLS:
            raise SkillError("skill.defaultProtected")
        settings, owner_id = await SkillService._load_scoped(
            db, user_id, scope, for_update=True
        )
        referenced = await db.scalar(
            select(Automation.id).where(
                *( [Automation.user_id == user_id] if scope == "personal" else [] ),
                Automation.skill_id == skill_id,
            )
        )
        if referenced is not None:
            raise SkillError("skill.inUse")
        current = next((item for item in settings.skills if item.id == skill_id), None)
        if current is None:
            raise SkillError("skill.notFound")
        settings.skills.remove(current)
        await SettingsStore.save(db, settings, user_id=owner_id)

    @staticmethod
    async def set_enabled(
        db: AsyncSession, user_id: UUID, skill_id: UUID, enabled: bool,
        scope: SkillScope = "personal",
    ) -> SkillDefinition:
        settings, owner_id = await SkillService._load_scoped(
            db, user_id, scope, for_update=True
        )
        skill = next((item for item in settings.skills if item.id == skill_id), None)
        if skill is None and scope == "personal" and skill_id in DEFAULT_SKILLS:
            skill = await SkillService._default_base(db, skill_id)
            settings.skills.append(skill)
        if skill is None:
            raise SkillError("skill.notFound")
        skill.enabled = enabled
        await SettingsStore.save(db, settings, user_id=owner_id)
        return skill

    @staticmethod
    async def reset(
        db: AsyncSession, user_id: UUID, skill_id: UUID,
        scope: SkillScope = "personal",
    ) -> SkillDefinition:
        default = DEFAULT_SKILLS.get(skill_id)
        if default is None:
            raise SkillError("skill.notFound")
        settings, owner_id = await SkillService._load_scoped(
            db, user_id, scope, for_update=True
        )
        current = next((item for item in settings.skills if item.id == skill_id), None)
        if current is None and scope == "personal":
            current = await SkillService._default_base(db, skill_id)
            settings.skills.append(current)
        if current is None:
            raise SkillError("skill.notFound")
        replacement = default.model_copy(deep=True)
        replacement.enabled = current.enabled
        settings.skills[settings.skills.index(current)] = replacement
        await SettingsStore.save(db, settings, user_id=owner_id)
        return replacement

    @staticmethod
    async def _load_scoped(
        db: AsyncSession, user_id: UUID, scope: SkillScope, *, for_update: bool = False
    ) -> tuple[InstanceSkillsSettings | SkillsSettings, UUID | None]:
        if scope == "instance":
            return await SettingsStore.load(
                db, InstanceSkillsSettings, for_update=for_update
            ), None
        return await SettingsStore.load(
            db, SkillsSettings, user_id=user_id, for_update=for_update
        ), user_id

    @staticmethod
    async def _default_base(db: AsyncSession, skill_id: UUID) -> SkillDefinition:
        instance = await SettingsStore.load(db, InstanceSkillsSettings)
        return next(
            (
                skill.model_copy(deep=True)
                for skill in instance.skills
                if skill.id == skill_id
            ),
            DEFAULT_SKILLS[skill_id].model_copy(deep=True),
        )

    @staticmethod
    def _ensure_unique(
        settings: InstanceSkillsSettings | SkillsSettings,
        name: str, *, exclude_id: UUID | None = None
    ) -> None:
        folded = name.casefold()
        if any(
            item.id != exclude_id and item.name.casefold() == folded
            for item in settings.skills
        ):
            raise SkillError("skill.duplicate")
