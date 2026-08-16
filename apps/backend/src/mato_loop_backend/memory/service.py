from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load, selectinload

from mato_loop_backend.memory.indexing import MemoryIndexService
from mato_loop_backend.models import (
    ApprovalRequest,
    ChatSession,
    MemoryCategory,
    MemoryDreamRun,
    MemoryMode,
    MemoryRecord,
    MemorySourceMessage,
    MemorySourceType,
    Message,
)
from mato_loop_backend.settings import (
    MemorySettings,
    SettingsStore,
    ToolExecutionPolicy,
    TrustLevel,
)

_MAX_LEXICAL_TERMS = 20
_MAX_LEXICAL_TERM_CHARACTERS = 256
_MAX_PINNED_AUTO = 5
_MEMORY_APPROVAL_SCHEMA_VERSION = 1


class MemoryApprovalTarget(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    id: UUID
    revision: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=20_000)
    category: MemoryCategory
    trust_level: TrustLevel = Field(
        validation_alias=AliasChoices("trustLevel", "trust_level"),
        serialization_alias="trustLevel",
    )
    pinned: bool
    evidence_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        validation_alias=AliasChoices("evidenceHash", "evidence_hash"),
        serialization_alias="evidenceHash",
    )


class MemoryApprovalAfter(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    content: str = Field(min_length=1, max_length=20_000)
    category: MemoryCategory
    trust_level: TrustLevel = Field(
        validation_alias=AliasChoices("trustLevel", "trust_level"),
        serialization_alias="trustLevel",
    )
    pinned: bool


class MemoryApprovalPayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(
        validation_alias=AliasChoices("schemaVersion", "schema_version"),
        serialization_alias="schemaVersion",
    )
    target_memories: list[MemoryApprovalTarget] = Field(
        min_length=1,
        max_length=50,
        validation_alias=AliasChoices("targetMemories", "target_memories"),
        serialization_alias="targetMemories",
    )
    after: MemoryApprovalAfter | None = None
    requested_pin: bool = Field(
        validation_alias=AliasChoices("requestedPin", "requested_pin"),
        serialization_alias="requestedPin",
    )
    pin_kind: Literal[
        "preferred_name",
        "form_of_address",
        "primary_residence",
        "pronouns",
        "primary_language",
    ] | None = Field(
        default=None,
        validation_alias=AliasChoices("pinKind", "pin_kind"),
        serialization_alias="pinKind",
    )
    source_dream_run_id: UUID = Field(
        validation_alias=AliasChoices("sourceDreamRunId", "source_dream_run_id"),
        serialization_alias="sourceDreamRunId",
    )
    source_session_id: UUID = Field(
        validation_alias=AliasChoices("sourceSessionId", "source_session_id"),
        serialization_alias="sourceSessionId",
    )
    evidence_message_ids: list[UUID] = Field(
        min_length=1,
        max_length=100,
        validation_alias=AliasChoices("evidenceMessageIds", "evidence_message_ids"),
        serialization_alias="evidenceMessageIds",
    )
    trust_level: TrustLevel = Field(
        validation_alias=AliasChoices("trustLevel", "trust_level"),
        serialization_alias="trustLevel",
    )
    readable_by: TrustLevel = Field(
        validation_alias=AliasChoices("readableBy", "readable_by"),
        serialization_alias="readableBy",
    )
    action_policy: ToolExecutionPolicy = Field(
        validation_alias=AliasChoices("actionPolicy", "action_policy"),
        serialization_alias="actionPolicy",
    )
    pin_policy: ToolExecutionPolicy = Field(
        validation_alias=AliasChoices("pinPolicy", "pin_policy"),
        serialization_alias="pinPolicy",
    )

    @model_validator(mode="after")
    def validate_pin(self) -> MemoryApprovalPayload:
        if self.requested_pin != (self.pin_kind is not None):
            raise ValueError("pinKind is required exactly when requestedPin is true")
        if len(self.target_memories) != len({item.id for item in self.target_memories}):
            raise ValueError("targetMemories must be unique")
        if len(self.evidence_message_ids) != len(set(self.evidence_message_ids)):
            raise ValueError("evidenceMessageIds must be unique")
        return self


def normalize_content(content: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", content.strip()).casefold()
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def memory_lexical_terms(content: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in re.findall(r"\w+", content.casefold()):
        if len(term) > _MAX_LEXICAL_TERM_CHARACTERS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) == _MAX_LEXICAL_TERMS:
            break
    return terms


def memory_reference_options() -> tuple[Load, Load]:
    return (
        selectinload(MemoryRecord.source_session),
        selectinload(MemoryRecord.source_message).load_only(Message.created_at),
    )


def memory_reference_timestamps(item: MemoryRecord) -> dict[str, str | None]:
    return {
        "sourceMessageAt": (
            item.source_message.created_at.isoformat()
            if item.source_message and item.source_message.created_at
            else None
        ),
        "memoryCreatedAt": item.created_at.isoformat() if item.created_at else None,
        "memoryUpdatedAt": item.updated_at.isoformat() if item.updated_at else None,
    }


@dataclass(frozen=True, slots=True)
class MemoryList:
    items: list[MemoryRecord]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class MemoryMutation:
    item: MemoryRecord | None
    created: bool = False
    updated: bool = False
    duplicate: bool = False
    conflict: bool = False


@dataclass(frozen=True, slots=True)
class MemoryScope:
    total: int
    pinned: int


class MemoryDuplicateError(Exception):
    pass


class MemoryService:
    @staticmethod
    async def create_manual(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
    ) -> MemoryRecord:
        _, content_hash = normalize_content(content)
        if await MemoryService._duplicate_by_hash(db, content_hash) is not None:
            raise MemoryDuplicateError
        try:
            async with db.begin_nested():
                item = await MemoryService._create(
                    db,
                    content=content,
                    category=category,
                    source_type=MemorySourceType.MANUAL,
                    trust_level=trust_level,
                )
                return item
        except IntegrityError as exc:
            if await MemoryService._duplicate_by_hash(db, content_hash) is None:
                raise
            raise MemoryDuplicateError from exc

    @staticmethod
    async def create_tool(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        session_id: UUID,
        message_id: UUID,
        run_id: UUID,
        tool_call_id: str,
    ) -> MemoryMutation:
        existing = await db.scalar(
            select(MemoryRecord).where(
                MemoryRecord.source_type == MemorySourceType.TOOL_CALL.value,
                MemoryRecord.source_run_id == run_id,
                MemoryRecord.source_tool_call_id == tool_call_id,
            )
        )
        if existing is not None:
            return MemoryMutation(existing, created=True)
        _, content_hash = normalize_content(content)
        duplicate = await MemoryService._duplicate_by_hash(db, content_hash)
        if duplicate is not None:
            return MemoryMutation(duplicate, duplicate=True)
        try:
            async with db.begin_nested():
                item = await MemoryService._create(
                    db,
                    content=content,
                    category=category,
                    source_type=MemorySourceType.TOOL_CALL,
                    trust_level=trust_level,
                    session_id=session_id,
                    message_id=message_id,
                    run_id=run_id,
                    tool_call_id=tool_call_id,
                    evidence_message_ids=[message_id],
                )
                return MemoryMutation(item, created=True)
        except IntegrityError:
            replay = await db.scalar(
                select(MemoryRecord).where(
                    MemoryRecord.source_type == MemorySourceType.TOOL_CALL.value,
                    MemoryRecord.source_run_id == run_id,
                    MemoryRecord.source_tool_call_id == tool_call_id,
                )
            )
            if replay is not None:
                return MemoryMutation(replay, created=True)
            duplicate = await MemoryService._duplicate_by_hash(db, content_hash)
            if duplicate is None:
                raise
            return MemoryMutation(duplicate, duplicate=True)

    @staticmethod
    async def update_tool(
        db: AsyncSession,
        *,
        memory_id: UUID,
        expected_revision: int,
        content: str,
        category: MemoryCategory,
        readable_by: TrustLevel,
        write_trust_level: TrustLevel,
        session_id: UUID,
        evidence_message_id: UUID,
        run_id: UUID,
        tool_call_id: str,
    ) -> MemoryMutation:
        item = await MemoryService._get_for_automatic_update(
            db, memory_id, readable_by=readable_by
        )
        if item is None:
            return MemoryMutation(None)
        if item.revision != expected_revision:
            return MemoryMutation(item, conflict=True)
        target_trust = (
            TrustLevel.PRIVATE
            if write_trust_level == TrustLevel.PRIVATE
            else TrustLevel(item.trust_level)
        )
        try:
            await MemoryService.update(
                db,
                item,
                content=content,
                category=category,
                trust_level=target_trust,
                evidence_message_ids=[evidence_message_id],
                source_type=MemorySourceType.TOOL_CALL,
                source_session_id=session_id,
                source_message_id=evidence_message_id,
                source_run_id=run_id,
                source_tool_call_id=tool_call_id,
            )
        except MemoryDuplicateError:
            _, content_hash = normalize_content(content)
            duplicate = await MemoryService._duplicate_by_hash(
                db, content_hash, exclude_id=item.id, for_update=True
            )
            if duplicate is None:
                raise
            source_evidence = list(
                (
                    await db.scalars(
                        select(MemorySourceMessage.message_id).where(
                            MemorySourceMessage.memory_id == item.id
                        )
                    )
                ).all()
            )
            if item.source_message_id is not None:
                source_evidence.append(item.source_message_id)
            source_evidence.append(evidence_message_id)
            merged_trust = (
                TrustLevel.PRIVATE
                if TrustLevel.PRIVATE.value
                in {item.trust_level, duplicate.trust_level, target_trust.value}
                else TrustLevel.PUBLIC
            )
            await MemoryService._merge_automatic_metadata(
                db,
                duplicate,
                trust_level=merged_trust,
                evidence_message_ids=source_evidence,
            )
            duplicate.source_type = MemorySourceType.TOOL_CALL.value
            duplicate.source_session_id = session_id
            duplicate.source_message_id = evidence_message_id
            duplicate.source_run_id = run_id
            duplicate.source_dream_run_id = None
            duplicate.source_tool_call_id = tool_call_id
            await db.delete(item)
            await db.flush()
            return MemoryMutation(duplicate, updated=True)
        return MemoryMutation(item, updated=True)

    @staticmethod
    async def create_dreaming(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        session_id: UUID,
        run_id: UUID,
        evidence_message_ids: list[UUID],
    ) -> MemoryMutation:
        _, content_hash = normalize_content(content)
        existing = await MemoryService._duplicate_by_hash(
            db, content_hash, for_update=True
        )
        if existing is not None:
            await MemoryService._merge_automatic_metadata(
                db,
                existing,
                trust_level=trust_level,
                evidence_message_ids=evidence_message_ids,
            )
            return MemoryMutation(existing, duplicate=True)
        try:
            async with db.begin_nested():
                item = await MemoryService._create(
                    db,
                    content=content,
                    category=category,
                    source_type=MemorySourceType.DREAMING,
                    trust_level=trust_level,
                    session_id=session_id,
                    message_id=(evidence_message_ids[0] if evidence_message_ids else None),
                    dream_run_id=run_id,
                    evidence_message_ids=evidence_message_ids,
                )
                return MemoryMutation(item, created=True)
        except IntegrityError:
            duplicate = await MemoryService._duplicate_by_hash(
                db, content_hash, for_update=True
            )
            if duplicate is None:
                raise
            await MemoryService._merge_automatic_metadata(
                db,
                duplicate,
                trust_level=trust_level,
                evidence_message_ids=evidence_message_ids,
            )
            return MemoryMutation(duplicate, duplicate=True)

    @staticmethod
    async def update_dreaming(
        db: AsyncSession,
        *,
        memory_id: UUID,
        expected_revision: int,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        session_id: UUID,
        dream_run_id: UUID,
        evidence_message_ids: list[UUID],
    ) -> MemoryMutation:
        item = await MemoryService._get_for_automatic_update(
            db, memory_id, readable_by=trust_level
        )
        if item is None:
            return MemoryMutation(None)
        if item.revision != expected_revision:
            return MemoryMutation(item, conflict=True)
        target_trust = (
            TrustLevel.PRIVATE
            if trust_level == TrustLevel.PRIVATE
            else TrustLevel(item.trust_level)
        )
        try:
            await MemoryService.update(
                db,
                item,
                content=content,
                category=category,
                trust_level=target_trust,
                evidence_message_ids=evidence_message_ids,
                source_type=MemorySourceType.DREAMING,
                source_session_id=session_id,
                source_message_id=(evidence_message_ids[0] if evidence_message_ids else None),
                source_dream_run_id=dream_run_id,
            )
        except MemoryDuplicateError:
            return MemoryMutation(item, conflict=True)
        return MemoryMutation(item, updated=True)

    @staticmethod
    async def merge_dreaming(
        db: AsyncSession,
        *,
        memory_ids: list[UUID],
        expected_revisions: dict[UUID, int],
        expected_trust: dict[UUID, TrustLevel],
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        readable_by: TrustLevel,
        session_id: UUID,
        dream_run_id: UUID,
        evidence_message_ids: list[UUID],
        pin: bool = False,
    ) -> MemoryMutation:
        unique_ids = sorted(set(memory_ids), key=str)
        if len(unique_ids) < 2 or len(unique_ids) > 50:
            return MemoryMutation(None, conflict=True)
        items = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(MemoryRecord.id.in_(unique_ids))
                    .order_by(MemoryRecord.id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            ).all()
        )
        if {item.id for item in items} != set(unique_ids) or any(
            item.revision != expected_revisions.get(item.id)
            or TrustLevel(item.trust_level) != expected_trust.get(item.id)
            or (
                readable_by == TrustLevel.PUBLIC
                and item.trust_level != TrustLevel.PUBLIC.value
            )
            for item in items
        ):
            return MemoryMutation(None, conflict=True)

        cleaned = content.strip()
        if not cleaned:
            raise ValueError("memory content must not be empty")
        _, content_hash = normalize_content(cleaned)
        matching = [item for item in items if item.normalized_content_hash == content_hash]
        survivor = min(matching or items, key=lambda item: str(item.id))
        duplicate = await MemoryService._duplicate_by_hash(
            db, content_hash, exclude_id=survivor.id, for_update=True
        )
        if duplicate is not None and duplicate.id not in set(unique_ids):
            return MemoryMutation(survivor, conflict=True)

        linked_ids = list(
            (
                await db.scalars(
                    select(MemorySourceMessage.message_id).where(
                        MemorySourceMessage.memory_id.in_(unique_ids)
                    )
                )
            ).all()
        )
        all_evidence = [
            *linked_ids,
            *(item.source_message_id for item in items if item.source_message_id is not None),
            *evidence_message_ids,
        ]
        content_changed = survivor.normalized_content_hash != content_hash
        survivor.content = cleaned
        survivor.normalized_content_hash = content_hash
        survivor.category = category.value
        survivor.trust_level = (
            TrustLevel.PRIVATE.value
            if trust_level == TrustLevel.PRIVATE
            or any(item.trust_level == TrustLevel.PRIVATE.value for item in items)
            else TrustLevel.PUBLIC.value
        )
        survivor.pinned = pin or any(item.pinned for item in items)
        survivor.source_type = MemorySourceType.DREAMING.value
        survivor.source_session_id = session_id
        survivor.source_message_id = evidence_message_ids[0] if evidence_message_ids else None
        survivor.source_run_id = None
        survivor.source_dream_run_id = dream_run_id
        survivor.source_tool_call_id = None
        survivor.revision = (survivor.revision or 1) + 1
        await db.flush()
        await MemoryService._add_evidence(db, survivor.id, all_evidence)
        if content_changed:
            await MemoryIndexService.enqueue(db, survivor)
        for item in items:
            if item.id != survivor.id:
                await db.delete(item)
        await db.flush()
        return MemoryMutation(survivor, updated=True)

    @staticmethod
    async def keep_dreaming(
        db: AsyncSession,
        *,
        memory_id: UUID,
        expected_revision: int,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID],
    ) -> MemoryMutation:
        item = await MemoryService._get_for_automatic_update(
            db, memory_id, readable_by=trust_level
        )
        if item is None:
            return MemoryMutation(None)
        if item.revision != expected_revision:
            return MemoryMutation(item, conflict=True)
        await MemoryService._merge_automatic_metadata(
            db,
            item,
            trust_level=trust_level,
            evidence_message_ids=evidence_message_ids,
        )
        return MemoryMutation(item)

    @staticmethod
    async def reconciliation_candidates(
        db: AsyncSession,
        *,
        candidates: list[tuple[str, MemoryCategory]],
        readable_by: TrustLevel,
        max_items_per_candidate: int = 5,
        max_characters: int = 40_000,
    ) -> list[list[MemoryRecord]]:
        trust_filters = []
        if readable_by == TrustLevel.PUBLIC:
            trust_filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        result: list[list[MemoryRecord]] = []
        used_characters = 0
        for content, _category in candidates:
            selected: dict[UUID, MemoryRecord] = {}
            _, content_hash = normalize_content(content)
            exact = await db.scalar(
                select(MemoryRecord).where(
                    MemoryRecord.normalized_content_hash == content_hash,
                    *trust_filters,
                ).options(*memory_reference_options())
            )
            if exact is not None:
                result.append([exact])
                continue

            terms = memory_lexical_terms(content)
            if terms:
                tsquery = func.to_tsquery("simple", " | ".join(terms))
                lexical = list(
                    (
                        await db.scalars(
                            select(MemoryRecord)
                            .options(*memory_reference_options())
                            .where(
                                MemoryRecord.search_vector.op("@@")(tsquery),
                                *trust_filters,
                            )
                            .order_by(
                                func.ts_rank_cd(MemoryRecord.search_vector, tsquery).desc(),
                                MemoryRecord.updated_at.desc(),
                            )
                            .limit(5)
                        )
                    ).all()
                )
                selected.update((item.id, item) for item in lexical)
            local: list[MemoryRecord] = []
            for item in selected.values():
                if len(local) >= max_items_per_candidate:
                    break
                item_characters = len(item.content) + 200
                if used_characters + item_characters > max_characters:
                    continue
                local.append(item)
                used_characters += item_characters
            result.append(local)
        return result

    @staticmethod
    async def _evidence_hash(db: AsyncSession, item: MemoryRecord) -> str:
        message_ids = list(
            (
                await db.scalars(
                    select(MemorySourceMessage.message_id).where(
                        MemorySourceMessage.memory_id == item.id
                    )
                )
            ).all()
        )
        if item.source_message_id is not None:
            message_ids.append(item.source_message_id)
        serialized = "\n".join(sorted({str(message_id) for message_id in message_ids}))
        return hashlib.sha256(serialized.encode("ascii")).hexdigest()

    @staticmethod
    async def _approval_target(
        db: AsyncSession, item: MemoryRecord
    ) -> MemoryApprovalTarget:
        return MemoryApprovalTarget(
            id=item.id,
            revision=item.revision,
            content=item.content,
            category=MemoryCategory(item.category),
            trustLevel=TrustLevel(item.trust_level),
            pinned=item.pinned,
            evidenceHash=await MemoryService._evidence_hash(db, item),
        )

    @staticmethod
    async def request_dreaming_approval(
        db: AsyncSession,
        *,
        action: Literal["update", "merge", "pin"],
        user_id: UUID,
        source_session_id: UUID,
        source_dream_run_id: UUID,
        targets: list[MemoryRecord],
        after_content: str | None,
        after_category: MemoryCategory | None,
        trust_level: TrustLevel,
        readable_by: TrustLevel,
        evidence_message_ids: list[UUID],
        requested_pin: bool,
        pin_kind: str | None,
        action_policy: ToolExecutionPolicy,
        pin_policy: ToolExecutionPolicy,
    ) -> ApprovalRequest:
        ordered_targets = sorted(targets, key=lambda item: str(item.id))
        snapshots = [
            await MemoryService._approval_target(db, item) for item in ordered_targets
        ]
        resulting_trust = (
            TrustLevel.PRIVATE
            if trust_level == TrustLevel.PRIVATE
            or any(item.trust_level == TrustLevel.PRIVATE.value for item in ordered_targets)
            else TrustLevel.PUBLIC
        )
        resulting_pinned = requested_pin or any(item.pinned for item in ordered_targets)
        if requested_pin and (
            after_category
            or MemoryCategory(ordered_targets[0].category)
        ) != MemoryCategory.IDENTITY:
            raise ValueError("pin suggestions require an identity result")
        payload = MemoryApprovalPayload(
            schemaVersion=_MEMORY_APPROVAL_SCHEMA_VERSION,
            targetMemories=snapshots,
            after=(
                MemoryApprovalAfter(
                    content=after_content,
                    category=after_category,
                    trustLevel=resulting_trust,
                    pinned=resulting_pinned,
                )
                if after_content is not None and after_category is not None
                else None
            ),
            requestedPin=requested_pin,
            pinKind=pin_kind,
            sourceDreamRunId=source_dream_run_id,
            sourceSessionId=source_session_id,
            evidenceMessageIds=list(dict.fromkeys(evidence_message_ids)),
            trustLevel=trust_level,
            readableBy=readable_by,
            actionPolicy=action_policy,
            pinPolicy=pin_policy,
        )
        action_kind = f"memory_{action}"
        target_key = [(str(item.id), item.revision) for item in snapshots]
        pending = list(
            (
                await db.scalars(
                    select(ApprovalRequest).where(
                        ApprovalRequest.user_id == user_id,
                        ApprovalRequest.request_type == "memory_change",
                        ApprovalRequest.action_kind == action_kind,
                        ApprovalRequest.status == "pending",
                    )
                )
            ).all()
        )
        serialized_payload = payload.model_dump(by_alias=True, mode="json", exclude_none=True)
        for existing in pending:
            try:
                existing_payload = MemoryApprovalPayload.model_validate(existing.payload)
            except ValidationError:
                continue
            if (
                [
                    (str(item.id), item.revision)
                    for item in existing_payload.target_memories
                ]
                == target_key
                and existing_payload.model_dump(by_alias=True, mode="json", exclude_none=True)
                == serialized_payload
            ):
                return existing

        labels = {
            "update": ("Erinnerung ändern", "Dreaming schlägt eine Aktualisierung vor."),
            "merge": (
                "Erinnerungen zusammenführen",
                "Dreaming schlägt eine Zusammenführung vor.",
            ),
            "pin": (
                "Erinnerung immer berücksichtigen",
                "Dreaming schlägt vor, diese Erinnerung immer zu berücksichtigen.",
            ),
        }
        title, summary = labels[action]
        approval = ApprovalRequest(
            request_type="memory_change",
            user_id=user_id,
            session_id=source_session_id,
            run_id=None,
            action_kind=action_kind,
            risk_level="high" if action == "merge" else "medium",
            title=title,
            reason="Von Dreaming vorgeschlagene Memory-Änderung",
            summary=summary,
            capability=f"memory.{action}",
            resource_type="memory",
            resource_scope=",".join(str(item.id) for item in snapshots),
            payload=serialized_payload,
            available_decisions=["approve", "deny"],
        )
        db.add(approval)
        await db.flush()
        return approval

    @staticmethod
    async def auto_pin(db: AsyncSession, item: MemoryRecord) -> bool:
        if item.pinned:
            return True
        await db.execute(select(func.pg_advisory_xact_lock(1_836_642_671)))
        pinned = int(
            await db.scalar(
                select(func.count(MemoryRecord.id)).where(MemoryRecord.pinned.is_(True))
            )
            or 0
        )
        if pinned >= _MAX_PINNED_AUTO:
            return False
        await MemoryService.set_pinned(db, item, pinned=True)
        return True

    @staticmethod
    async def _valid_source_evidence(
        db: AsyncSession, payload: MemoryApprovalPayload
    ) -> bool:
        messages = list(
            (
                await db.scalars(
                    select(Message).where(Message.id.in_(payload.evidence_message_ids))
                )
            ).all()
        )
        return (
            len(messages) == len(set(payload.evidence_message_ids))
            and any(item.role == "user" for item in messages)
            and all(
                item.session_id == payload.source_session_id
                and item.role in {"user", "assistant"}
                and item.content != ""
                and item.error_code is None
                and item.generation_status == "completed"
                for item in messages
            )
        )

    @staticmethod
    async def apply_approved_change(
        db: AsyncSession, approval: ApprovalRequest, user_id: UUID
    ) -> Literal["applied", "target_changed", "policy_changed"]:
        if approval.user_id != user_id or approval.request_type != "memory_change":
            return "target_changed"
        try:
            payload = MemoryApprovalPayload.model_validate(approval.payload)
        except ValidationError:
            return "target_changed"
        action = {
            "memory_update": "update",
            "memory_merge": "merge",
            "memory_pin": "pin",
        }.get(approval.action_kind)
        if action is None:
            return "target_changed"
        session = await db.get(
            ChatSession, payload.source_session_id, with_for_update=True
        )
        dream_run = await db.get(MemoryDreamRun, payload.source_dream_run_id)
        if (
            session is None
            or session.user_id != user_id
            or dream_run is None
            or dream_run.session_id != session.id
            or not await MemoryService._valid_source_evidence(db, payload)
        ):
            return "target_changed"

        settings = await SettingsStore.load(
            db, MemorySettings, user_id=user_id, for_update=True
        )
        if not settings.enabled or session.memory_mode != MemoryMode.FULL.value:
            return "policy_changed"
        current_action_policy = {
            "update": settings.dreaming_update_policy,
            "merge": settings.dreaming_merge_policy,
            "pin": settings.dreaming_pin_policy,
        }[action]
        if (
            current_action_policy != ToolExecutionPolicy.ASK
            or current_action_policy != payload.action_policy
            or (
                payload.requested_pin
                and (
                    settings.dreaming_pin_policy == ToolExecutionPolicy.DISABLED
                    or settings.dreaming_pin_policy != payload.pin_policy
                )
            )
        ):
            return "policy_changed"

        target_ids = sorted(
            {item.id for item in payload.target_memories}, key=str
        )
        targets = list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(MemoryRecord.id.in_(target_ids))
                    .order_by(MemoryRecord.id)
                    .execution_options(populate_existing=True)
                    .with_for_update()
                )
            ).all()
        )
        snapshots = {item.id: item for item in payload.target_memories}
        if len(targets) != len(target_ids):
            return "target_changed"
        for item in targets:
            snapshot = snapshots[item.id]
            if (
                item.revision != snapshot.revision
                or item.content != snapshot.content
                or item.category != snapshot.category.value
                or item.trust_level != snapshot.trust_level.value
                or item.pinned != snapshot.pinned
                or (
                    payload.readable_by == TrustLevel.PUBLIC
                    and item.trust_level != TrustLevel.PUBLIC.value
                )
                or await MemoryService._evidence_hash(db, item)
                != snapshot.evidence_hash
            ):
                return "target_changed"

        if action == "pin":
            if (
                not payload.requested_pin
                or len(targets) != 1
                or targets[0].category != MemoryCategory.IDENTITY.value
            ):
                return "target_changed"
            await MemoryService.set_pinned(db, targets[0], pinned=True)
            return "applied"

        if payload.after is None or (
            payload.requested_pin
            and payload.after.category != MemoryCategory.IDENTITY
        ):
            return "target_changed"
        expected_trust = (
            TrustLevel.PRIVATE
            if payload.trust_level == TrustLevel.PRIVATE
            or any(item.trust_level == TrustLevel.PRIVATE.value for item in targets)
            else TrustLevel.PUBLIC
        )
        if (
            payload.after.trust_level != expected_trust
            or payload.after.pinned
            != (payload.requested_pin or any(item.pinned for item in targets))
        ):
            return "target_changed"
        if action == "update":
            if len(targets) != 1:
                return "target_changed"
            mutation = await MemoryService.update_dreaming(
                db,
                memory_id=targets[0].id,
                expected_revision=targets[0].revision,
                content=payload.after.content,
                category=payload.after.category,
                trust_level=payload.trust_level,
                session_id=session.id,
                dream_run_id=dream_run.id,
                evidence_message_ids=payload.evidence_message_ids,
            )
            if mutation.item is None or mutation.conflict:
                return "target_changed"
            if payload.requested_pin:
                await MemoryService.set_pinned(db, mutation.item, pinned=True)
            return "applied"

        mutation = await MemoryService.merge_dreaming(
            db,
            memory_ids=target_ids,
            expected_revisions={item.id: item.revision for item in targets},
            expected_trust={item.id: TrustLevel(item.trust_level) for item in targets},
            content=payload.after.content,
            category=payload.after.category,
            trust_level=payload.trust_level,
            readable_by=payload.readable_by,
            session_id=session.id,
            dream_run_id=dream_run.id,
            evidence_message_ids=payload.evidence_message_ids,
            pin=payload.requested_pin,
        )
        return "applied" if mutation.item is not None and not mutation.conflict else "target_changed"

    @staticmethod
    async def _create(
        db: AsyncSession,
        *,
        content: str,
        category: MemoryCategory,
        source_type: MemorySourceType,
        trust_level: TrustLevel,
        session_id: UUID | None = None,
        message_id: UUID | None = None,
        run_id: UUID | None = None,
        dream_run_id: UUID | None = None,
        tool_call_id: str | None = None,
        evidence_message_ids: list[UUID] | None = None,
    ) -> MemoryRecord:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("memory content must not be empty")
        _, content_hash = normalize_content(cleaned)
        item = MemoryRecord(
            source_session_id=session_id,
            source_message_id=message_id,
            source_run_id=run_id,
            source_dream_run_id=dream_run_id,
            source_tool_call_id=tool_call_id,
            source_type=source_type.value,
            pinned=False,
            trust_level=trust_level.value,
            category=category.value,
            content=cleaned,
            normalized_content_hash=content_hash,
            seen_at=datetime.now(UTC) if source_type == MemorySourceType.MANUAL else None,
        )
        db.add(item)
        await db.flush()
        await MemoryService._add_evidence(db, item.id, evidence_message_ids or [])
        await MemoryIndexService.enqueue(db, item)
        return item

    @staticmethod
    async def _duplicate_by_hash(
        db: AsyncSession,
        content_hash: str,
        *,
        exclude_id: UUID | None = None,
        for_update: bool = False,
    ) -> MemoryRecord | None:
        statement = select(MemoryRecord).where(
            MemoryRecord.normalized_content_hash == content_hash
        )
        if exclude_id is not None:
            statement = statement.where(MemoryRecord.id != exclude_id)
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return await db.scalar(statement)

    @staticmethod
    async def _get_for_automatic_update(
        db: AsyncSession,
        memory_id: UUID,
        *,
        readable_by: TrustLevel,
    ) -> MemoryRecord | None:
        statement = select(MemoryRecord).where(MemoryRecord.id == memory_id)
        if readable_by == TrustLevel.PUBLIC:
            statement = statement.where(
                MemoryRecord.trust_level == TrustLevel.PUBLIC.value
            )
        return await db.scalar(
            statement.execution_options(populate_existing=True).with_for_update()
        )

    @staticmethod
    async def _add_evidence(
        db: AsyncSession, memory_id: UUID, message_ids: list[UUID]
    ) -> None:
        unique_ids = list(dict.fromkeys(message_ids))
        if not unique_ids:
            return
        await db.execute(
            postgresql_insert(MemorySourceMessage)
            .values(
                [
                    {"memory_id": memory_id, "message_id": message_id}
                    for message_id in unique_ids
                ]
            )
            .on_conflict_do_nothing(index_elements=["memory_id", "message_id"])
        )

    @staticmethod
    async def _merge_automatic_metadata(
        db: AsyncSession,
        item: MemoryRecord,
        *,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID],
    ) -> None:
        if trust_level == TrustLevel.PRIVATE:
            item.trust_level = TrustLevel.PRIVATE.value
        item.revision = (item.revision or 1) + 1
        await MemoryService._add_evidence(db, item.id, evidence_message_ids)
        await db.flush()

    @staticmethod
    async def get(
        db: AsyncSession, memory_id: UUID, *, for_update: bool = False
    ) -> MemoryRecord | None:
        statement = (
            select(MemoryRecord)
            .where(MemoryRecord.id == memory_id)
            .options(
                selectinload(MemoryRecord.source_session),
                selectinload(MemoryRecord.source_messages),
            )
        )
        if for_update:
            statement = statement.execution_options(populate_existing=True).with_for_update()
        return await db.scalar(statement)

    @staticmethod
    async def update(
        db: AsyncSession,
        item: MemoryRecord,
        *,
        content: str,
        category: MemoryCategory,
        trust_level: TrustLevel,
        evidence_message_ids: list[UUID] | None = None,
        source_type: MemorySourceType | None = None,
        source_session_id: UUID | None = None,
        source_message_id: UUID | None = None,
        source_run_id: UUID | None = None,
        source_dream_run_id: UUID | None = None,
        source_tool_call_id: str | None = None,
        pinned: bool | None = None,
    ) -> MemoryRecord:
        cleaned = content.strip()
        if not cleaned:
            raise ValueError("memory content must not be empty")
        _, content_hash = normalize_content(cleaned)
        content_changed = content_hash != item.normalized_content_hash
        if await MemoryService._duplicate_by_hash(
            db, content_hash, exclude_id=item.id
        ) is not None:
            raise MemoryDuplicateError
        item_id = item.id
        try:
            async with db.begin_nested():
                item.content = cleaned
                item.category = category.value
                item.trust_level = trust_level.value
                if pinned is not None:
                    item.pinned = pinned
                item.normalized_content_hash = content_hash
                item.revision = (item.revision or 1) + 1
                if content_changed:
                    if source_type is not None:
                        item.source_type = source_type.value
                        item.source_session_id = source_session_id
                        item.source_message_id = source_message_id
                        item.source_run_id = source_run_id
                        item.source_dream_run_id = source_dream_run_id
                        item.source_tool_call_id = source_tool_call_id
                await db.flush()
                await MemoryService._add_evidence(
                    db, item.id, evidence_message_ids or []
                )
                if content_changed:
                    await MemoryIndexService.enqueue(db, item)
                await db.flush()
        except IntegrityError as exc:
            if await MemoryService._duplicate_by_hash(
                db, content_hash, exclude_id=item_id
            ) is None:
                raise
            raise MemoryDuplicateError from exc
        return item

    @staticmethod
    async def delete(db: AsyncSession, item: MemoryRecord) -> None:
        await db.delete(item)
        await db.flush()

    @staticmethod
    async def mark_seen(db: AsyncSession, memory_ids: list[UUID]) -> int:
        unique_ids = list(dict.fromkeys(memory_ids))
        if not unique_ids:
            return 0
        result = await db.scalars(
            update(MemoryRecord)
            .where(
                MemoryRecord.id.in_(unique_ids),
                MemoryRecord.seen_at.is_(None),
            )
            .values(
                seen_at=func.now(),
                updated_at=MemoryRecord.updated_at,
            )
            .returning(MemoryRecord.id)
        )
        return len(result.all())

    @staticmethod
    async def list(
        db: AsyncSession,
        *,
        query: str | None = None,
        source_type: MemorySourceType | None = None,
        category: MemoryCategory | None = None,
        trust_level: TrustLevel | None = None,
        pinned: bool | None = None,
        readable_by: TrustLevel | None = None,
        cursor_updated_at: datetime | None = None,
        cursor_id: UUID | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> MemoryList:
        if (cursor_updated_at is None) != (cursor_id is None):
            raise ValueError("both cursor fields are required together")
        filters = []
        if source_type is not None:
            filters.append(MemoryRecord.source_type == source_type.value)
        if category is not None:
            filters.append(MemoryRecord.category == category.value)
        if trust_level is not None:
            filters.append(MemoryRecord.trust_level == trust_level.value)
        if pinned is not None:
            filters.append(MemoryRecord.pinned.is_(pinned))
        if readable_by == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        cleaned_query = (query or "").strip()
        rank = None
        if cleaned_query:
            tsquery = func.plainto_tsquery(
                "simple", " ".join(memory_lexical_terms(cleaned_query))
            )
            filters.append(MemoryRecord.search_vector.op("@@")(tsquery))
            rank = func.ts_rank_cd(MemoryRecord.search_vector, tsquery)
        total = int(
            await db.scalar(select(func.count(MemoryRecord.id)).where(*filters)) or 0
        )
        if cursor_updated_at is not None and cursor_id is not None:
            filters.append(
                or_(
                    MemoryRecord.updated_at < cursor_updated_at,
                    and_(
                        MemoryRecord.updated_at == cursor_updated_at,
                        MemoryRecord.id < cursor_id,
                    ),
                )
            )
        statement = (
            select(MemoryRecord)
            .where(*filters)
            .options(
                *memory_reference_options(),
                selectinload(MemoryRecord.source_messages),
            )
        )
        if rank is not None:
            statement = statement.order_by(
                rank.desc(), MemoryRecord.updated_at.desc(), MemoryRecord.id.desc()
            )
        else:
            statement = statement.order_by(
                MemoryRecord.updated_at.desc(), MemoryRecord.id.desc()
            )
        items = list((await db.scalars(statement.offset(offset).limit(limit))).all())
        return MemoryList(items, total, offset, limit)

    @staticmethod
    async def scope(db: AsyncSession, *, readable_by: TrustLevel) -> MemoryScope:
        filters = []
        if readable_by == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        total, pinned = (
            await db.execute(
                select(
                    func.count(MemoryRecord.id),
                    func.count(MemoryRecord.id).filter(MemoryRecord.pinned.is_(True)),
                ).where(*filters)
            )
        ).one()
        return MemoryScope(int(total), int(pinned))

    @staticmethod
    async def pinned_context(
        db: AsyncSession,
        *,
        input_trust_level: TrustLevel,
    ) -> list[MemoryRecord]:
        filters = [MemoryRecord.pinned.is_(True)]
        if input_trust_level == TrustLevel.PUBLIC:
            filters.append(MemoryRecord.trust_level == TrustLevel.PUBLIC.value)
        return list(
            (
                await db.scalars(
                    select(MemoryRecord)
                    .where(*filters)
                    .options(*memory_reference_options())
                    .order_by(MemoryRecord.created_at, MemoryRecord.id)
                )
            ).all()
        )

    @staticmethod
    async def set_pinned(
        db: AsyncSession, item: MemoryRecord, *, pinned: bool
    ) -> MemoryRecord:
        if item.pinned != pinned:
            item.pinned = pinned
            item.revision = (item.revision or 1) + 1
            await db.flush()
        return item
