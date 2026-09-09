from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from mato_loop_backend.api.errors import ApiError, register_error_handlers
from mato_loop_backend.api.routes.approvals import (
    ApprovalDecisionRequest,
    decide_approval,
    router,
)
from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.db.session import get_db_session
from mato_loop_backend.memory.service import MemoryService, normalize_content
from mato_loop_backend.models import (
    ApprovalRequest,
    ChatSession,
    MemoryAudience,
    MemoryCategory,
    MemoryDreamRun,
    MemoryEmbedding,
    MemoryRecord,
    MemorySourceMessage,
    Message,
    UserAccount,
)
from mato_loop_backend.security import require_user
from mato_loop_backend.settings import (
    MemorySettings,
    SettingsStore,
    ToolExecutionPolicy,
    TrustLevel,
)

EDIT = {
    "content": "  Owner's chosen memory  \n",
    "category": "goal",
    "audience": "public_only",
    "pinned": True,
}


@pytest.fixture
async def approval_api():
    user = UserAccount(id=uuid4(), username="test", password_hash="test")
    approval = ApprovalRequest(
        id=uuid4(),
        user_id=user.id,
        request_type="memory_change",
        action_kind="memory_update",
        status="pending",
        available_decisions=["approve", "deny"],
        payload={"after": {"content": "Original proposal"}},
    )
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = approval
    app = FastAPI()
    app.include_router(router, prefix="/api")
    register_error_handlers(app)
    app.dependency_overrides[require_user] = lambda: user
    app.dependency_overrides[get_db_session] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield SimpleNamespace(
            client=client, db=db, user=user, approval=approval,
            url=f"/api/approvals/{approval.id}/decisions",
        )


@pytest.mark.parametrize(
    ("edit", "code", "field"),
    [
        ({**EDIT, "content": " \n\t "}, "validation.tooShort", "content"),
        ({**EDIT, "content": "x" * 20_001}, "validation.tooLong", "content"),
        ({**EDIT, "content": None}, "validation.invalidType", "content"),
        ({**EDIT, "category": "secret-invalid-category"}, "validation.invalidChoice", "category"),
        ({**EDIT, "audience": "secret-invalid-audience"}, "validation.invalidChoice", "audience"),
        ({**EDIT, "pinned": "true"}, "validation.invalidType", "pinned"),
        ({**EDIT, "pinned": None}, "validation.invalidType", "pinned"),
        ({**EDIT, "targetMemories": []}, "validation.invalid", "targetMemories"),
        ([], "validation.invalid", "memory_edit"),
        *[
            ({key: value for key, value in EDIT.items() if key != missing},
             "validation.required", missing)
            for missing in EDIT
        ],
    ],
)
async def test_edit_input_errors_are_safe_and_do_not_resolve(approval_api, edit, code, field):
    response = await approval_api.client.post(
        approval_api.url, json={"decision": "approve", "memory_edit": edit}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["field"] == field
    assert "secret-invalid" not in response.text
    assert "input" not in response.json()["error"]
    approval_api.db.get.assert_not_awaited()
    approval_api.db.commit.assert_not_awaited()
    assert approval_api.approval.status == "pending"


@pytest.mark.parametrize(
    ("request_type", "action", "decision"),
    [
        ("memory_change", "memory_update", "deny"),
        ("memory_change", "memory_merge", "allow_once"),
        ("memory_change", "memory_update", "allow_session"),
        ("memory_change", "memory_remove", "approve"),
        ("memory_change", "unknown", "approve"),
        ("agent_action", "session_read", "allow_once"),
        ("agent_action", "memory_update", "approve"),
        ("agent_action", "terminal_command", "deny"),
    ],
)
async def test_inappropriate_edits_are_rejected_centrally(
    approval_api, request_type, action, decision
):
    approval_api.approval.request_type = request_type
    approval_api.approval.action_kind = action
    original = deepcopy(approval_api.approval.payload)

    response = await approval_api.client.post(
        approval_api.url, json={"decision": decision, "memory_edit": EDIT}
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation.invalid"
    assert response.json()["error"]["field"] == "memory_edit"
    assert approval_api.approval.status == "pending"
    assert approval_api.approval.payload == original
    approval_api.db.commit.assert_not_awaited()
    approval_api.db.add.assert_not_called()


@pytest.mark.parametrize("outcome", ["applied", "target_changed", "policy_changed"])
async def test_owner_edit_is_recorded_only_after_success(approval_api, monkeypatch, outcome):
    original = deepcopy(approval_api.approval.payload)

    async def apply(_db, *, approval, owner_id, memory_edit):
        assert approval.payload == original
        assert owner_id == approval_api.user.id
        assert memory_edit.content == EDIT["content"].strip()
        return outcome

    monkeypatch.setattr(MemoryService, "apply_approved_change", apply)
    response = await approval_api.client.post(
        approval_api.url, json={"decision": "approve", "memory_edit": EDIT}
    )

    assert response.status_code == (200 if outcome == "applied" else 409)
    expected = deepcopy(original)
    if outcome == "applied":
        expected["ownerEdit"] = {**EDIT, "content": EDIT["content"].strip()}
    assert approval_api.approval.payload == expected
    approval_api.db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    ("state", "code"),
    [("foreign_owner", "approval.notFound"), ("approved", "approval.alreadyDecided")],
)
async def test_edits_do_not_bypass_approval_ownership_or_status(approval_api, state, code):
    if state == "foreign_owner":
        approval_api.approval.user_id = uuid4()
    else:
        approval_api.approval.status = state
    response = await approval_api.client.post(
        approval_api.url, json={"decision": "approve", "memory_edit": EDIT}
    )

    assert response.status_code == (404 if state == "foreign_owner" else 409)
    assert response.json()["error"]["code"] == code
    assert "ownerEdit" not in approval_api.approval.payload
    approval_api.db.commit.assert_not_awaited()


def test_edit_content_is_trimmed_before_length_validation():
    request = ApprovalDecisionRequest.model_validate(
        {"decision": "approve", "memory_edit": {**EDIT, "content": " " + "x" * 20_000 + " "}}
    )
    assert request.memory_edit.content == "x" * 20_000
    assert ApprovalDecisionRequest(decision="approve").memory_edit is None


@pytest.fixture
async def proposal(request):
    if not request.config.getoption("--memory-db"):
        pytest.skip("requires the migrated development PostgreSQL database")
    engine = create_async_engine(get_runtime_config().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            # Connection close rolls back test data even when the route commits.
            await connection.begin()
            async with AsyncSession(
                connection, join_transaction_mode="create_savepoint", expire_on_commit=False
            ) as db:
                user = UserAccount(username=f"approval-edit-{uuid4().hex}", password_hash="test")
                db.add(user)
                await db.flush()
                session = ChatSession(user_id=user.id, title="Source", memory_mode="full")
                db.add(session)
                await db.flush()
                evidence = Message(session_id=session.id, role="user", content="Direct evidence")
                old_evidence = Message(session_id=session.id, role="user", content="Prior evidence")
                db.add_all([evidence, old_evidence])
                await db.flush()
                run = MemoryDreamRun(
                    session_id=session.id, status="completed", trigger="manual",
                    source_from_message_id=evidence.id, source_to_message_id=evidence.id,
                )
                db.add(run)
                await db.flush()
                targets = []
                for index in range(2 if request.param == "merge" else 1):
                    target = await MemoryService.create_manual(
                        db, owner_id=user.id, content=f"Original memory {index}",
                        category=MemoryCategory.FACT, audience=MemoryAudience.PRIVATE_ONLY,
                    )
                    target.pinned = index == 1
                    db.add(MemorySourceMessage(memory_id=target.id, message_id=old_evidence.id))
                    targets.append(target)
                await SettingsStore.save(
                    db, MemorySettings(dreaming_update_policy="ask", dreaming_remove_policy="ask"),
                    user_id=user.id,
                )
                approval = await MemoryService.request_dreaming_approval(
                    db, action=request.param, owner_id=user.id,
                    source_session_id=session.id, source_dream_run_id=run.id, targets=targets,
                    after_content="Original proposal", after_category=MemoryCategory.PREFERENCE,
                    trust_level=TrustLevel.PRIVATE, readable_by=TrustLevel.PRIVATE,
                    evidence_message_ids=[evidence.id],
                    update_policy=ToolExecutionPolicy.ASK, remove_policy=ToolExecutionPolicy.ASK,
                )
                await db.commit()
                yield SimpleNamespace(
                    db=db, user=user, session=session, run=run, evidence=evidence,
                    old_evidence=old_evidence, targets=targets, approval=approval,
                )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("proposal", ["update", "merge"], indirect=True)
@pytest.mark.parametrize("audience,pinned", [("public_only", True), ("both", False), ("private_only", False)])
async def test_edited_approval_persists_result_evidence_index_and_counters(proposal, audience, pinned):
    p = proposal
    original = deepcopy(p.approval.payload)
    owner_id = p.user.id
    target_ids = [item.id for item in p.targets]
    edit = {**EDIT, "audience": audience, "pinned": pinned}
    before = [(item.content, item.revision) for item in p.targets]
    request = ApprovalDecisionRequest.model_validate({"decision": "approve", "memory_edit": edit})
    for item in p.targets:
        await p.db.refresh(item)
    assert [(item.content, item.revision) for item in p.targets] == before
    assert p.approval.status == "pending"
    assert p.approval.payload == original

    result = await decide_approval(p.approval.id, request, p.user, p.db)

    p.db.expire_all()
    memories = list((await p.db.scalars(
        select(MemoryRecord).where(MemoryRecord.user_id == owner_id)
    )).all())
    await p.db.refresh(p.user)
    assert len(memories) == 1
    memory = memories[0]
    assert memory.id == min(target_ids, key=str)
    assert (memory.content, memory.category, memory.audience, memory.pinned, memory.revision) == (
        EDIT["content"].strip(), "goal", audience, pinned, 2,
    )
    assert memory.normalized_content_hash == normalize_content(EDIT["content"])[1]
    await p.db.refresh(p.run)
    await p.db.refresh(p.approval)
    await p.db.refresh(p.evidence)
    await p.db.refresh(p.old_evidence)
    assert memory.source_type == "dreaming"
    assert memory.source_dream_run_id == p.run.id
    linked = set((await p.db.scalars(
        select(MemorySourceMessage.message_id).where(MemorySourceMessage.memory_id == memory.id)
    )).all())
    assert linked == {p.evidence.id, p.old_evidence.id}
    embedding = await p.db.get(MemoryEmbedding, memory.id)
    assert embedding.content_hash == memory.normalized_content_hash
    assert embedding.status == "queued"
    assert p.run.updated_memory_count == 1
    assert p.run.removed_memory_count == len(p.targets) - 1
    assert result == {"status": "approved", "decision": "approve"}
    assert p.approval.status == "approved"
    assert p.approval.payload == {**original, "ownerEdit": {**edit, "content": edit["content"].strip()}}
    with pytest.raises(ApiError, match="approval.alreadyDecided"):
        await decide_approval(p.approval.id, request, p.user, p.db)


@pytest.mark.parametrize("proposal", ["update", "merge"], indirect=True)
@pytest.mark.parametrize("decision,edit", [("approve", {}), ("approve", {"memory_edit": None}), ("deny", {})])
async def test_unedited_approval_preserves_defaults(proposal, decision, edit):
    p = proposal
    original = deepcopy(p.approval.payload)
    target_ids = [item.id for item in p.targets]
    await decide_approval(
        p.approval.id, ApprovalDecisionRequest(decision=decision, **edit), p.user, p.db
    )
    memories = list((await p.db.scalars(
        select(MemoryRecord).where(MemoryRecord.user_id == p.user.id)
        .execution_options(populate_existing=True)
    )).all())
    assert p.approval.payload == original
    if decision == "deny":
        assert {item.id for item in memories} == set(target_ids)
        assert all(item.revision == 1 and item.content.startswith("Original memory") for item in memories)
        assert p.run.updated_memory_count == p.run.removed_memory_count == 0
    else:
        assert len(memories) == 1
        assert memories[0].content == "Original proposal"
        assert memories[0].category == "preference"
        assert memories[0].audience == "private_only"
        assert memories[0].pinned == (len(target_ids) == 2)


@pytest.mark.parametrize("proposal", ["update", "merge"], indirect=True)
@pytest.mark.parametrize(
    "stale", ["revision", "content", "category", "audience", "pinned", "evidence", "source", "policy", "proposal_audience"]
)
async def test_owner_edit_cannot_bypass_original_validation(proposal, stale):
    p = proposal
    target = p.targets[0]
    if stale == "revision":
        target.revision += 1
    elif stale == "content":
        target.content = "Changed outside approval"
        target.normalized_content_hash = normalize_content(target.content)[1]
    elif stale == "category":
        target.category = "context"
    elif stale == "audience":
        target.audience = "both"
    elif stale == "pinned":
        target.pinned = not target.pinned
    elif stale == "evidence":
        p.db.add(MemorySourceMessage(memory_id=target.id, message_id=p.evidence.id))
    elif stale == "source":
        p.evidence.generation_status = "interrupted"
    elif stale == "policy":
        await SettingsStore.save(
            p.db, MemorySettings(dreaming_update_policy="auto"), user_id=p.user.id
        )
    else:
        p.approval.payload = {
            **p.approval.payload, "after": {**p.approval.payload["after"], "audience": "public_only"},
        }
    await p.db.commit()
    original = deepcopy(p.approval.payload)
    before = [(item.id, item.content, item.category, item.audience, item.pinned, item.revision) for item in p.targets]

    with pytest.raises(ApiError) as error:
        await decide_approval(
            p.approval.id,
            ApprovalDecisionRequest.model_validate({"decision": "approve", "memory_edit": EDIT}),
            p.user, p.db,
        )

    assert error.value.code == ("approval.policyChanged" if stale == "policy" else "approval.targetChanged")
    for item in p.targets:
        await p.db.refresh(item)
    await p.db.refresh(p.approval)
    await p.db.refresh(p.run)
    assert [(item.id, item.content, item.category, item.audience, item.pinned, item.revision) for item in p.targets] == before
    assert p.approval.status == "expired"
    assert p.approval.payload == original
    assert p.run.updated_memory_count == p.run.removed_memory_count == 0


@pytest.mark.parametrize("proposal", ["update", "merge"], indirect=True)
@pytest.mark.parametrize("audience", list(MemoryAudience))
@pytest.mark.parametrize("race", [False, True])
async def test_edited_duplicates_are_rejected_across_final_audiences(
    proposal, audience, race, monkeypatch
):
    p = proposal
    duplicate = await MemoryService.create_manual(
        p.db, owner_id=p.user.id, content=EDIT["content"].upper(),
        category=MemoryCategory.FACT, audience=audience,
    )
    await p.db.commit()
    original = deepcopy(p.approval.payload)
    if race:
        duplicate_lookup = MemoryService._duplicate_by_hash
        checked = False

        async def miss_first_lookup(*args, **kwargs):
            nonlocal checked
            if not checked:
                checked = True
                return None
            return await duplicate_lookup(*args, **kwargs)

        monkeypatch.setattr(MemoryService, "_duplicate_by_hash", miss_first_lookup)
    with pytest.raises(ApiError) as error:
        await decide_approval(
            p.approval.id,
            ApprovalDecisionRequest.model_validate({"decision": "approve", "memory_edit": EDIT}),
            p.user, p.db,
        )

    assert error.value.code == "approval.targetChanged"
    for item in [*p.targets, duplicate]:
        await p.db.refresh(item)
        assert item.revision == 1
    await p.db.refresh(p.approval)
    await p.db.refresh(p.run)
    assert p.approval.status == "expired"
    assert p.approval.payload == original
    assert p.run.updated_memory_count == p.run.removed_memory_count == 0
