"""One-time inline-skill conversion; ordinary runtime reads only file packages."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models.setting import SettingRecord
from mato_loop_backend.settings import DEFAULT_SKILLS, InstanceSkillsSettings, SettingsStore
from mato_loop_backend.settings.models import SkillReference
from mato_loop_backend.skill_packages import (
    SkillError,
    SkillFile,
    SkillPackage,
    skill_md,
    skill_path,
)
from mato_loop_backend.skill_storage import SkillStorage
from mato_loop_backend.skills import default_package


async def initialize_skill_library(db: AsyncSession) -> None:
    """Publish complete directories before removing any inline source content."""
    await SettingsStore._lock_scope(db, "skills", None)
    records = list((await db.scalars(select(SettingRecord).where(SettingRecord.key.in_(("skills", "model-defaults", "tools"))).with_for_update())).all())
    for record in records:
        if record.key == "model-defaults":
            record.data = {key: value for key, value in record.data.items() if key not in {"skill_creation_model_id", "skillCreationModelId"}}
        elif record.key == "tools":
            data = dict(record.data)
            for key in ("tool_availability", "toolAvailability"):
                availability = dict(data.get(key, {}))
                if "build_skill" in availability:
                    availability["save_skill"] = availability.pop("build_skill") and availability.get("save_skill", True)
                    data[key] = availability
            record.data = data
    records = [record for record in records if record.key == "skills"]
    storage = SkillStorage()
    for skill_id in DEFAULT_SKILLS:
        await asyncio.to_thread(storage.publish, None, skill_id, default_package(skill_id))
    for record in records:
        references = []
        names: set[str] = set()
        for item in record.data.get("skills", []):
            if "instructions" not in item:
                reference = SkillReference.model_validate(item)
            else:
                skill_id = UUID(item["id"])
                ascii_name = unicodedata.normalize("NFKD", item["name"]).encode("ascii", "ignore").decode().lower()
                name = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")[:64].rstrip("-") or "skill"
                try:
                    skill_path(name)
                except SkillError:
                    name = f"skill-{name}"
                if name in names:
                    name = name[:27].rstrip("-") + "-" + skill_id.hex
                package = SkillPackage({"SKILL.md": SkillFile(skill_md(name, item["description"], item["instructions"]))})
                revision = await asyncio.to_thread(storage.publish, record.user_id, skill_id, package)
                reference = SkillReference(id=skill_id, name=name, revision=revision, enabled=item.get("enabled", True))
            names.add(reference.name)
            references.append(reference.model_dump(mode="json"))
        record.data = {**record.data, "skills": references}
    if not any(record.user_id is None for record in records):
        await SettingsStore.save(db, InstanceSkillsSettings())
    await db.commit()
    current = (await db.scalars(select(SettingRecord).where(SettingRecord.key == "skills"))).all()
    live = {(record.user_id, UUID(item["id"]), item["revision"]) for record in current for item in record.data.get("skills", [])}
    live.update((None, skill_id, default_package(skill_id).revision) for skill_id in DEFAULT_SKILLS)
    await asyncio.to_thread(storage.reconcile, live)
