from __future__ import annotations

import asyncio
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.files.service import RootFileError, RootFileService
from mato_loop_backend.models import ApprovalRequest, ChatSession, UserAccount
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    DEFAULT_SKILLS,
    InstanceSkillsSettings,
    SettingsStore,
    SkillDefinition,
)
from mato_loop_backend.skill_packages import (
    MAX_FILE_BYTES,
    MAX_PACKAGE_BYTES,
    MAX_SKILL_BYTES,
    SkillFile,
    SkillPackage,
    parse_skill_md,
    skill_path,
)
from mato_loop_backend.skill_resources import read_draft
from mato_loop_backend.skills import (
    SkillError,
    SkillScope,
    SkillService,
    default_package,
)

router = APIRouter(prefix="/skills", dependencies=[Depends(require_user)])

_ERROR_STATUS = {
    "skill.duplicate": status.HTTP_409_CONFLICT,
    "skill.limitReached": status.HTTP_409_CONFLICT,
    "skill.notFound": status.HTTP_404_NOT_FOUND,
    "skill.inUse": status.HTTP_409_CONFLICT,
    "skill.defaultProtected": status.HTTP_409_CONFLICT,
    "skill.personalDisabled": status.HTTP_409_CONFLICT,
    "skill.changed": status.HTTP_409_CONFLICT,
    "skill.nameInvalid": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "skill.unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "file.tooLarge": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "file.invalidPath": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "auth.adminRequired": status.HTTP_403_FORBIDDEN,
}


class SkillCatalogItem(BaseModel):
    model_config = ConfigDict(
        alias_generator=lambda value: value.split("_")[0]
        + "".join(part.capitalize() for part in value.split("_")[1:]),
        populate_by_name=True,
    )

    id: UUID
    name: str
    description: str
    instructions: str
    enabled: bool
    scope: SkillScope
    is_default: bool
    modified: bool
    document: str
    revision: str
    resource_paths: list[str]


class SkillDocumentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document: str = Field(max_length=MAX_SKILL_BYTES)
    expected_revision: str | None = Field(default=None, alias="expectedRevision", pattern=r"^[a-f0-9]{64}$")


class SkillListResponse(BaseModel):
    items: list[SkillCatalogItem]


class SkillEnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class SkillSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    personal_skills_enabled: StrictBool = Field(alias="personalSkillsEnabled")


def _raise_api_error(exc: SkillError) -> None:
    code = exc.code if exc.code in _ERROR_STATUS else "skill.draftInvalid"
    raise ApiError(_ERROR_STATUS.get(code, status.HTTP_422_UNPROCESSABLE_ENTITY), code) from exc


def _require_scope(user: UserAccount, scope: SkillScope) -> None:
    if scope == "instance" and not user.is_admin:
        raise ApiError(status.HTTP_403_FORBIDDEN, "auth.adminRequired")


def _payload(skill: SkillDefinition, scope: SkillScope) -> SkillCatalogItem:
    default = DEFAULT_SKILLS.get(skill.id)
    return SkillCatalogItem(
        **skill.model_dump(),
        scope=scope,
        is_default=default is not None,
        modified=default is not None and skill.revision != default_package(skill.id).revision,
    )


@router.get("", response_model=SkillListResponse)
async def list_skills(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope | None, Query()] = None,
) -> SkillListResponse:
    if scope is not None:
        _require_scope(user, scope)
        skills = await SkillService.list_scoped(db, user.id, scope)
        return SkillListResponse(items=[_payload(skill, scope) for skill in skills])
    skills = await SkillService.list(db, user.id)
    return SkillListResponse(items=[
        _payload(skill, "personal" if skill.owner_id == user.id else "instance")
        for skill in skills
    ])


@router.get("/settings", response_model=SkillSettingsRequest)
async def get_skill_settings(
    _user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillSettingsRequest:
    settings = await SettingsStore.load(db, InstanceSkillsSettings)
    return SkillSettingsRequest(personal_skills_enabled=settings.personal_skills_enabled)


@router.put("/settings", response_model=SkillSettingsRequest)
async def update_skill_settings(
    payload: SkillSettingsRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> SkillSettingsRequest:
    _require_scope(user, "instance")
    settings = await SettingsStore.load(db, InstanceSkillsSettings, for_update=True)
    settings.personal_skills_enabled = payload.personal_skills_enabled
    await SettingsStore.save(db, settings)
    await db.commit()
    return payload


@router.post("/import", response_model=SkillCatalogItem, status_code=status.HTTP_201_CREATED)
async def import_skill(
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
    scope: SkillScope = "personal",
    skill_id: UUID | None = None,
    expected_revision: str | None = None,
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        content = await file.read(MAX_PACKAGE_BYTES + 256 * 1024 + 1)
        package = await asyncio.to_thread(SkillPackage.import_file, content, archive=(file.filename or "").lower().endswith(".zip"))
        skill = await SkillService.save_package(db, user.id, skill_id or uuid4(), package, scope=scope, create=skill_id is None, expected_revision=expected_revision)
    except SkillError as exc:
        _raise_api_error(exc)
    finally:
        await file.close()
    await db.commit()
    return _payload(skill, scope)


@router.get("/drafts/{approval_id}")
async def review_skill_draft(
    approval_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict:
    approval = await db.get(ApprovalRequest, approval_id)
    session = await db.get(ChatSession, approval.session_id) if approval else None
    if session is None or session.user_id != user.id or approval.status != "pending":
        raise ApiError(404, "skill.notFound")
    details = approval.payload or {}
    draft = details.get("skillDraft")
    if not draft or approval.action_kind not in {"build_skill", "edit_skill"}:
        raise ApiError(404, "skill.notFound")
    try:
        package = await asyncio.to_thread(read_draft, RootFileService(session.id, project_id=session.project_id), draft["path"], draft["executablePaths"])
        metadata, instructions = parse_skill_md(package.files["SKILL.md"].content)
    except (SkillError, RootFileError) as exc:
        _raise_api_error(SkillError(exc.code))
    return {"revision": package.revision, "name": metadata["name"], "description": metadata["description"], "instructions": instructions, "files": sorted(package.files), "scope": details["skillPreview"]["scope"]}


async def _read_package(db: AsyncSession, user: UserAccount, skill_id: UUID, scope: SkillScope) -> SkillPackage:
    skill = await SkillService.get(db, user.id, skill_id, scope)
    if scope == "instance" and not user.is_admin and not skill.enabled:
        raise SkillError("skill.notFound")
    return await SkillService.package(skill)


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: SkillScope = "personal",
) -> Response:
    try:
        package = await _read_package(db, user, skill_id, scope)
        content = await asyncio.to_thread(package.archive)
    except SkillError as exc:
        _raise_api_error(exc)
    return Response(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{package.name}.zip"'})


@router.get("/{skill_id}/files")
async def read_skill_file(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    scope: SkillScope = "personal",
) -> Response:
    try:
        package = await _read_package(db, user, skill_id, scope)
        entry = package.files.get(skill_path(path))
        if entry is None:
            raise SkillError("skill.notFound")
    except SkillError as exc:
        _raise_api_error(exc)
    return Response(entry.content, media_type="application/octet-stream", headers={"ETag": f'"{package.revision}"', "X-Skill-Executable": str(entry.executable).lower()})


@router.put("/{skill_id}/files", response_model=SkillCatalogItem)
async def write_skill_file(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    file: Annotated[UploadFile, File()],
    path: str,
    expected_revision: str,
    executable: bool = False,
    scope: SkillScope = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        package = await _read_package(db, user, skill_id, scope)
        entry = SkillFile(await file.read(MAX_FILE_BYTES + 1), executable)
        updated = SkillPackage({**package.files, skill_path(path): entry})
        skill = await SkillService.save_package(db, user.id, skill_id, updated, scope=scope, expected_revision=expected_revision)
    except SkillError as exc:
        _raise_api_error(exc)
    finally:
        await file.close()
    await db.commit()
    return _payload(skill, scope)


@router.delete("/{skill_id}/files", response_model=SkillCatalogItem)
async def delete_skill_file(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    path: str,
    expected_revision: str,
    scope: SkillScope = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        package = await _read_package(db, user, skill_id, scope)
        path = skill_path(path)
        if path == "SKILL.md" or path not in package.files:
            raise SkillError("skill.draftInvalid")
        updated = SkillPackage({key: value for key, value in package.files.items() if key != path})
        skill = await SkillService.save_package(db, user.id, skill_id, updated, scope=scope, expected_revision=expected_revision)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.post("", response_model=SkillCatalogItem, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillDocumentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.save_package(db, user.id, uuid4(), SkillPackage({"SKILL.md": SkillFile(payload.document.encode())}), scope=scope, create=True)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.put("/{skill_id}", response_model=SkillCatalogItem)
async def replace_skill(
    skill_id: UUID,
    payload: SkillDocumentRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        current = await SkillService.get(db, user.id, skill_id, scope)
        package = await SkillService.package(current)
        package = SkillPackage({**package.files, "SKILL.md": SkillFile(payload.document.encode())})
        skill = await SkillService.save_package(db, user.id, skill_id, package, scope=scope, expected_revision=payload.expected_revision)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.patch("/{skill_id}/enabled", response_model=SkillCatalogItem)
async def set_skill_enabled(
    skill_id: UUID,
    payload: SkillEnabledRequest,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.set_enabled(db, user.id, skill_id, payload.enabled, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.post("/{skill_id}/reset", response_model=SkillCatalogItem)
async def reset_skill(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> SkillCatalogItem:
    _require_scope(user, scope)
    try:
        skill = await SkillService.reset(db, user.id, skill_id, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
    return _payload(skill, scope)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    user: Annotated[UserAccount, Depends(require_user)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    scope: Annotated[SkillScope, Query()] = "personal",
) -> None:
    _require_scope(user, scope)
    try:
        await SkillService.delete(db, user.id, skill_id, scope)
    except SkillError as exc:
        _raise_api_error(exc)
    await db.commit()
