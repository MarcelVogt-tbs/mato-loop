"""Startup package conversion and conservative updates of bundled files."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import unicodedata
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models.setting import SettingRecord
from mato_loop_backend.settings import (
    DEFAULT_SKILLS,
    WRITING_STYLE_SKILL_ID,
    InstanceSkillsSettings,
    SettingsStore,
)
from mato_loop_backend.settings.models import SkillReference
from mato_loop_backend.skill_packages import (
    SkillError,
    SkillFile,
    SkillPackage,
    bundled_file_hashes,
    parse_skill_md,
    skill_md,
    skill_path,
)
from mato_loop_backend.skill_storage import SkillStorage
from mato_loop_backend.skills import default_package

logger = logging.getLogger(__name__)


def update_bundled_package(reference: SkillReference, package: SkillPackage) -> tuple[SkillReference, SkillPackage]:
    """Replace only recognized shipped content; never merge user-edited instructions."""
    target = default_package(reference.id)
    if package.name != target.name:
        return reference, package
    baseline = reference.bundled_files
    if not baseline:
        if all(package.files.get(path) == entry for path, entry in target.files.items()):
            return reference.model_copy(update={"bundled_files": bundled_file_hashes(target)}), package
        if reference.id != WRITING_STYLE_SKILL_ID:
            return reference, package
        metadata, instructions = parse_skill_md(package.files["SKILL.md"].content)
        prefix, separator, examples = instructions.partition("\n## Writing examples\n")
        if not separator or hashlib.sha256(prefix.encode()).hexdigest() != "b35651c4a8df38fcb2085207a8422f440127470d08489726042fb9be51f43124":
            return reference, package
        path = "references/writing-examples.md"
        if any(existing.casefold() == path or existing.casefold().startswith(path + "/") or path.startswith(existing.casefold() + "/") for existing in package.files):
            return reference, package
        # Preserve custom frontmatter and every other resource. The sample text is not rewritten.
        entry = SkillFile(
            skill_md(package.name, metadata["description"], DEFAULT_SKILLS[reference.id].instructions, metadata),
            package.files["SKILL.md"].executable,
        )
        files = {**package.files, "SKILL.md": entry}
        if examples.strip() != "No personal examples stored yet. Replace this sentence when adding the first example.":
            files[path] = SkillFile(package.files["SKILL.md"].content.partition(b"\n## Writing examples\n")[2])
        package = SkillPackage(files)
        baseline = bundled_file_hashes(target)
    else:
        files = dict(package.files)
        current_hashes, target_hashes = bundled_file_hashes(package), bundled_file_hashes(target)
        baseline = dict(baseline)
        for path in baseline.keys() | target.files.keys():
            if path in baseline:
                if current_hashes.get(path) != baseline[path]:
                    continue
            elif any(existing.casefold() == path.casefold() or existing.casefold().startswith(path.casefold() + "/") or path.casefold().startswith(existing.casefold() + "/") for existing in files):
                continue
            if path in target.files:
                files[path] = target.files[path]
                baseline[path] = target_hashes[path]
            else:
                files.pop(path, None)
                baseline.pop(path)
        package = SkillPackage(files)
    return reference.model_copy(update={"revision": package.revision, "bundled_files": baseline}), package


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
            if reference.id in DEFAULT_SKILLS:
                package = await asyncio.to_thread(storage.read, record.user_id, reference.id, reference.revision, reference.name)
                try:
                    updated, package = update_bundled_package(reference, package)
                except SkillError as exc:
                    if exc.code not in {"file.tooLarge", "skill.draftInvalid"}:
                        raise
                    logger.warning("Skipping invalid bundled skill update for %s: %s; keeping original package", reference.id, exc.code)
                    updated = reference
                if updated.revision != reference.revision:
                    await asyncio.to_thread(storage.publish, record.user_id, reference.id, package)
                reference = updated
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
