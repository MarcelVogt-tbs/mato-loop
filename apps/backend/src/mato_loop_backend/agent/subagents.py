from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.error_contract import error_payload
from mato_loop_backend.events import run_events
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.models import AgentRun, ChatSession, Message, WebSearchPolicy
from mato_loop_backend.settings import (
    AgentProfileUnavailable,
    IntegrationSettings,
    ToolSettings,
    TrustLevel,
    load_agent_profile_tools,
    load_effective_integrations,
)
from mato_loop_backend.settings.effective import filter_agent_profile_integrations
from mato_loop_backend.settings.models import AgentProfilePermissions
from mato_loop_backend.tools.arguments import MAX_ACTIVE_SUBAGENTS, TaskArguments
from mato_loop_backend.tools.contracts import ToolResult

if TYPE_CHECKING:
    from mato_loop_backend.agent.runtime import AgentRuntime

MAX_RESULT_CHARACTERS = 12_000


@dataclass
class ChildTask:
    session_id: UUID
    run_id: UUID
    title: str
    task: asyncio.Task[None]
    reported_instruction_wait: asyncio.Event | None = None


_children: dict[UUID, dict[UUID, ChildTask]] = {}


def has_children(run_id: UUID) -> bool:
    return bool(_children.get(run_id))


def parent_run_for_child(run_id: UUID) -> UUID | None:
    return next((parent_id for parent_id, children in _children.items() if run_id in children), None)


async def wait_for_children(
    runtime: type[AgentRuntime], run_id: UUID, children: list[ChildTask] | None = None,
) -> None:
    if children is None:
        children = list(_children.get(run_id, {}).values())
    wakeup = runtime._continuation_wakeups.setdefault(run_id, asyncio.Event())
    signal = asyncio.create_task(wakeup.wait())
    try:
        while pending := [child.task for child in children if not child.task.done()]:
            if run_id in runtime._pending_continuations:
                return
            await asyncio.wait([*pending, signal], return_when=asyncio.FIRST_COMPLETED)
    finally:
        signal.cancel()
        await asyncio.gather(signal, return_exceptions=True)


def forget_children(run_id: UUID, children: list[ChildTask]) -> None:
    pending = _children.get(run_id, {})
    for child in children:
        pending.pop(child.run_id, None)
        run_events.unregister_subagent(child.run_id)
    if not pending:
        _children.pop(run_id, None)


async def cancel_children(runtime: type[AgentRuntime], run_id: UUID) -> None:
    children = list(_children.get(run_id, {}).values())
    for child in children:
        if not child.task.done():
            runtime.cancel(child.run_id)
    if children:
        await asyncio.shield(asyncio.gather(*(child.task for child in children), return_exceptions=True))
        forget_children(run_id, children)


def permission_snapshot(
    settings: ToolSettings, tool_names: set[str],
    integration_settings: IntegrationSettings | None = None,
) -> dict[str, Any]:
    from mato_loop_backend.permissions.resolution import tool_default_policy
    from mato_loop_backend.tools.catalog import tool_registry
    from mato_loop_backend.tools.context_search import source_available

    integrations = integration_settings or IntegrationSettings()
    # Only permission data belongs here, never integration configuration or credentials.
    return {
        "permissions": settings.permissions.model_dump(mode="json"),
        "agentPermissions": (
            settings._agent_permissions.model_dump(mode="json")
            if settings._agent_permissions is not None else None
        ),
        "delegationEnabled": bool(
            settings._agent_profile and settings._agent_profile.delegation_enabled
        ),
        "sessionWritePaths": (
            settings._agent_profile.session_write_paths
            if settings._agent_profile is not None else None
        ),
        "temporaryFilesEnabled": settings._temporary_files_enabled,
        "shellPrefixes": settings.shell_prefixes.model_dump(mode="json"),
        "toolNames": sorted(tool_names - {"task"}),
        "contextSources": [source for source in ("memories", "sessions") if source_available(settings, source)],
        "integrationIds": sorted(str(item.id) for item in integrations.instances if item.enabled),
        "actionDefaults": {
            action.capability: tool_default_policy(settings, action.capability, action.default_policy(settings)).value
            for action in tool_registry.policy_actions(integrations)
        },
        "integrationDefaults": {
            str(instance.id): {
                action.capability: action.default_policy(settings).value
                for action in tool_registry.integration_resource_actions(instance)
            }
            for instance in integrations.instances if instance.enabled
        },
    }


def integration_ceiling_ids(snapshot: dict[str, Any] | None) -> list[str] | None:
    if snapshot is None:
        return None
    restrictions = []
    while snapshot is not None:
        ids = snapshot.get("integrationIds", [])
        restrictions.append({value for value in ids if isinstance(value, str)} if isinstance(ids, list) else set())
        snapshot = snapshot.get("offeredCeiling")
    return sorted(set.intersection(*restrictions))


def intersect_snapshots(*snapshots: dict[str, Any]) -> dict[str, Any]:
    layers = []
    for snapshot in snapshots:
        while snapshot is not None:
            layer = {key: value for key, value in snapshot.items() if key != "offeredCeiling"}
            if layer not in layers:
                layers.append(deepcopy(layer))
            snapshot = snapshot.get("offeredCeiling")
    result = layers[-1]
    for layer in reversed(layers[:-1]):
        result = {**layer, "offeredCeiling": result}
    return result


def session_write_paths_ceiling(snapshot: dict[str, Any] | None) -> list[str] | None:
    restrictions = []
    while snapshot is not None:
        paths = snapshot.get("sessionWritePaths")
        if paths is not None:
            restrictions.append(set(paths) if isinstance(paths, list) else set())
        snapshot = snapshot.get("offeredCeiling")
    return sorted(set.intersection(*restrictions)) if restrictions else None


def ceiling_settings(snapshot: dict[str, Any], trust: TrustLevel) -> ToolSettings:
    permissions = AgentProfilePermissions.model_validate(snapshot["permissions"])
    settings = ToolSettings(
        private_permissions=permissions,
        public_permissions=permissions,
        shell_prefixes=snapshot["shellPrefixes"],
    ).select_trust(trust)
    settings._frozen_action_defaults = snapshot["actionDefaults"]
    settings._temporary_files_enabled = snapshot.get("temporaryFilesEnabled", False) is True
    if snapshot["agentPermissions"] is not None:
        settings._agent_permissions = AgentProfilePermissions.model_validate(
            snapshot["agentPermissions"]
        )
    return apply_delegation_ceiling(settings, snapshot.get("offeredCeiling"))


def apply_delegation_ceiling(settings: ToolSettings, snapshot: dict[str, Any] | None) -> ToolSettings:
    settings._delegation_ceiling = snapshot
    settings._temporary_files_enabled &= temporary_files_ceiling(snapshot)
    if snapshot is not None:
        # Aggregate integrations enumerate scoped keys before checking each resource policy.
        # Retain those keys, not old values that could override stricter current defaults.
        ceiling = snapshot
        while ceiling is not None:
            for permissions in (ceiling["permissions"], ceiling["agentPermissions"] or {}):
                for integration_id, resources in permissions.get("resource_policies", {}).items():
                    current = settings.permissions.resource_policies.setdefault(integration_id, {})
                    for resource_key in resources:
                        current.setdefault(resource_key, {})
                for integration_id, resources in permissions.get("resource_availability", {}).items():
                    current = settings.permissions.resource_policies.setdefault(integration_id, {})
                    for resource_key in resources:
                        current.setdefault(resource_key, {})
            ceiling = ceiling.get("offeredCeiling")
    return settings


def temporary_files_ceiling(snapshot: dict[str, Any] | None) -> bool:
    while snapshot is not None:
        if snapshot.get("temporaryFilesEnabled") is not True:
            return False
        snapshot = snapshot.get("offeredCeiling")
    return True


async def delegation_snapshot(
    db: AsyncSession, run: AgentRun, session: ChatSession,
) -> dict[str, Any] | None:
    if session.parent_session_id is None:
        return None
    message = await db.get(Message, run.input_message_id) if run.input_message_id else None
    metadata = (message.provider_data or {}).get("_matoloop", {}) if message else {}
    snapshot = metadata.get("delegationCeiling")
    if not isinstance(snapshot, dict):
        from mato_loop_backend.agent.run_contracts import AgentRunError

        raise AgentRunError("tool.unavailable")
    return snapshot


async def permission_origin(db: AsyncSession, session: ChatSession) -> ChatSession:
    if session.parent_session_id is None:
        return session
    parent = await db.get(ChatSession, session.parent_session_id)
    if parent is None or parent.user_id != session.user_id or parent.parent_session_id is not None:
        from mato_loop_backend.agent.run_contracts import AgentRunError

        raise AgentRunError("session.notFound")
    return parent


def provenance(session: ChatSession) -> dict[str, str]:
    return (
        {"subagentSessionId": str(session.id), "subagentTitle": session.title}
        if session.parent_session_id is not None else {}
    )


async def run_tasks(
    runtime: type[AgentRuntime],
    db: AsyncSession,
    parent: AgentRun,
    session: ChatSession,
    arguments: TaskArguments,
    *,
    tool_call_id: str,
    tool_settings: ToolSettings,
    tool_names: set[str],
    integration_settings: IntegrationSettings | None = None,
    max_tool_calls: int | None,
) -> ToolResult:
    if session.parent_session_id is not None or (
        tool_settings._agent_profile is None
        or not tool_settings._agent_profile.delegation_enabled
    ):
        return ToolResult.error("tool.unavailable")
    offered_snapshot = permission_snapshot(tool_settings, tool_names, integration_settings)
    input_message = await db.get(Message, parent.input_message_id) if parent.input_message_id else None
    input_metadata = (input_message.provider_data or {}).get("_matoloop", {}) if input_message else {}
    active_file = deepcopy(input_metadata.get("activeFile"))
    # Flush dirty parent input/tool metadata and release locks before children can ask for grants.
    commit = asyncio.create_task(db.commit())
    try:
        await asyncio.shield(commit)
    except asyncio.CancelledError:
        await commit
        raise

    children: list[ChildTask] = []
    corrections: set[UUID] = set()
    stopping: set[UUID] = set()

    async def start_children() -> ToolResult | None:
        async with AsyncSessionFactory() as child_db:
            current_tools, profile = await load_agent_profile_tools(
                child_db, session.user_id, parent.agent_profile_id or session.agent_profile_id,
                TrustLevel(parent.trust_level),
            )
            if not current_tools.tool_availability.get("task", True):
                raise AgentProfileUnavailable
            if profile is None or not profile.delegation_enabled:
                raise AgentProfileUnavailable
            current_integrations = filter_agent_profile_integrations(
                await load_effective_integrations(child_db, session.user_id), profile,
                allowed_ids=integration_ceiling_ids(offered_snapshot),
            )
            snapshot = permission_snapshot(
                current_tools,
                {name for name in tool_names if current_tools.tool_availability.get(name, True)},
                current_integrations,
            )
            # Keep both policy layers: scoped overrides and shell prefixes cannot widen the old offer.
            snapshot = intersect_snapshots(snapshot, offered_snapshot)
            # Serialize admission for the whole main session, including resumed children.
            await child_db.execute(select(ChatSession.id).where(ChatSession.id == session.id).with_for_update())
            targets = []
            for task in arguments.tasks:
                child_session = previous = None
                if task.session_id is not None:
                    child_session = await child_db.get(ChatSession, task.session_id)
                    if (child_session is None or child_session.user_id != session.user_id
                            or child_session.parent_session_id != session.id):
                        return ToolResult.error("session.notFound")
                    previous = await child_db.scalar(select(AgentRun).where(
                        AgentRun.session_id == child_session.id,
                        AgentRun.parent_run_id.is_(None),
                    ).order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).limit(1))
                    if previous is None:
                        return ToolResult.error("session.runInactive")
                active = previous is not None and previous.status in {"queued", "running", "waiting_approval", "waiting_input"}
                if active and previous.id not in _children.get(parent.id, {}):
                    return ToolResult.error("session.runInactive")
                if task.stop and not active:
                    return ToolResult.error("session.runInactive")
                targets.append((task, child_session, previous, active))
            count = await child_db.scalar(select(func.count()).select_from(AgentRun).join(
                ChatSession, ChatSession.id == AgentRun.session_id,
            ).where(
                ChatSession.parent_session_id == session.id,
                ChatSession.user_id == session.user_id,
                AgentRun.status.in_({"queued", "running", "waiting_approval", "waiting_input"}),
            )) or 0
            available = max(0, MAX_ACTIVE_SUBAGENTS - count)
            if sum(not active for _, _, _, active in targets) > available:
                return ToolResult.error("agent.subagentLimitReached", params={
                    "available": available, "maximum": MAX_ACTIVE_SUBAGENTS,
                })
            next_index = (await child_db.scalar(select(func.max(ChatSession.spawn_index)).where(
                ChatSession.spawned_by_run_id == parent.id,
            )))
            next_index = 0 if next_index is None else next_index + 1
            prepared = []
            selected_runs = []
            created_sessions = []
            reservations = []
            committed = False
            from mato_loop_backend.settings import apply_session_ai, load_effective_ai

            session_ai = apply_session_ai(await load_effective_ai(child_db, session.user_id), session, parent.agent_profile_id or session.agent_profile_id)
            storage = SessionStorage()
            try:
                for task, _child_session, previous, active in targets:
                    if active and not task.stop:
                        reservation = runtime.reserve_continuation(previous.id, queued=previous.status == "queued")
                        if reservation is None:
                            return ToolResult.error("session.runInactive")
                        reservations.append((previous.id, reservation))
                for task, child_session, previous, active in targets:
                    if task.stop:
                        stopping.add(previous.id)
                        selected_runs.append(previous.id)
                        continue
                    child_run_id = uuid4()
                    child_snapshot = deepcopy(snapshot)
                    if child_session is None:
                        child_session = ChatSession(
                            id=uuid4(), user_id=session.user_id,
                            session_type="chat", title=task.description,
                            title_generation_status="skipped", project_id=session.project_id,
                            parent_session_id=session.id, spawned_by_run_id=parent.id,
                            spawned_by_tool_call_id=tool_call_id, spawn_index=next_index,
                            agent_profile_id=parent.agent_profile_id or session.agent_profile_id,
                            model_config_id=parent.model_config_id,
                            trust_level=session.trust_level,
                            reasoning_effort=session_ai.reasoning_effort,
                            preferred_execution_agent_id=parent.execution_agent_id,
                            current_directory=parent.current_directory_snapshot,
                            memory_mode="disabled",
                            web_search_policy=(
                                WebSearchPolicy.DISABLED.value
                                if session.web_search_policy == WebSearchPolicy.DISABLED
                                else WebSearchPolicy.AVAILABLE.value
                            ),
                        )
                        child_db.add(child_session)
                        next_index += 1
                        await child_db.flush()
                        created_sessions.append(child_session)
                        await asyncio.to_thread(
                            storage.provision, child_session.id, child_session.project_id,
                            user_id=child_session.user_id,
                        )
                    else:
                        old_ceiling = await delegation_snapshot(child_db, previous, child_session)
                        child_snapshot["toolNames"] = sorted(set(child_snapshot["toolNames"]) & set(old_ceiling["toolNames"]))
                        child_snapshot = intersect_snapshots(child_snapshot, old_ceiling)
                        child_snapshot["integrationIds"] = integration_ceiling_ids(child_snapshot)
                    message = Message(
                        id=uuid4(), session_id=child_session.id, role="user", content=task.prompt,
                        trust_level=previous.trust_level if previous else parent.trust_level, source_type="app",
                        provider_data={"_matoloop": {
                            "delegationCeiling": child_snapshot,
                            "delegationSource": {
                                "sessionId": str(session.id), "runId": str(parent.id), "toolCallId": tool_call_id,
                            },
                            "maxToolCallsByRun": {str(child_run_id): max_tool_calls},
                            **({"continuationRunId": str(previous.id)} if active else {}),
                            **({"activeFile": deepcopy(active_file)} if active_file is not None else {}),
                        }},
                    )
                    child_db.add(message)
                    if active:
                        corrections.add(previous.id)
                        selected_runs.append(previous.id)
                        continue
                    source = previous or parent
                    child = AgentRun(
                        id=child_run_id, session_id=child_session.id, parent_run_id=None,
                        automation_origin=bool(parent.automation_origin),
                        input_message_id=message.id, status="queued",
                        agent_profile_id=child_session.agent_profile_id,
                        model_config_id=source.model_config_id, model_name=source.model_name,
                        trust_level=source.trust_level,
                        pseudonymization_enabled=source.pseudonymization_enabled,
                        execution_agent_id=source.execution_agent_id,
                        execution_agent_revision=source.execution_agent_revision,
                        current_directory_snapshot=source.current_directory_snapshot,
                        execution_operations=list(source.execution_operations or []),
                    )
                    prepared.append((child_session, child))
                    selected_runs.append(child.id)
                    await child_db.flush()
                    child_db.add(child)
                await child_db.commit()
                committed = True
            except BaseException:
                await child_db.rollback()
                for child_session in created_sessions:
                    await asyncio.to_thread(
                        storage.remove_session, child_session.id, child_session.project_id
                    )
                raise
            finally:
                for child_id, reservation in reservations:
                    runtime.finish_continuation(child_id, reservation, accepted=committed)
            # No await between the durable checkpoint and registering every scheduled child.
            for child_session, child in prepared:
                run_events.register_subagent(child.id, parent.id, {
                    "sessionId": str(child_session.id), "runId": str(child.id),
                    "title": child_session.title, "status": "queued",
                    "spawnIndex": child_session.spawn_index, "toolCallId": tool_call_id,
                })
                tracked = ChildTask(child_session.id, child.id, child_session.title, runtime._schedule_run(child))
                _children.setdefault(parent.id, {})[child.id] = tracked
            children.extend(_children[parent.id][run_id] for run_id in selected_runs)
            for child in children:
                if child.run_id in stopping:
                    runtime.cancel(child.run_id)

    startup = asyncio.create_task(start_children())
    try:
        try:
            error = await asyncio.shield(startup)
        except asyncio.CancelledError:
            await startup
            raise
        if error is not None:
            return error
        if stopping:
            await asyncio.shield(asyncio.gather(*(child.task for child in children if child.run_id in stopping), return_exceptions=True))
        if not corrections:
            await wait_for_children(runtime, parent.id, children)
        result = await task_results(children)
        payload = json.loads(result.content)
        if corrections:
            for item in payload["tasks"]:
                if UUID(item["runId"]) in corrections:
                    item["messageDelivered"] = True
        completed = {UUID(item["runId"]) for item in payload["tasks"] if item["status"] in {"completed", "failed", "cancelled"}}
        forget_children(parent.id, [child for child in children if child.run_id in completed])
        return ToolResult.json(payload, metadata=result.metadata)
    except AgentProfileUnavailable:
        return ToolResult.error("agentProfile.selectedUnavailable")
    except BaseException:
        await cancel_children(runtime, parent.id)
        raise


async def task_results(children: list[ChildTask]) -> ToolResult:
    from mato_loop_backend.agent.runtime import AgentRuntime

    results = []
    private_data_used = False
    async with AsyncSessionFactory() as result_db:
        for tracked in children:
            session_id, run_id, title = tracked.session_id, tracked.run_id, tracked.title
            child = await result_db.get(AgentRun, run_id)
            child_session = await result_db.get(ChatSession, session_id)
            private_data_used |= bool(child_session and child_session.private_integration_data_used)
            status = child.status if child is not None else "failed"
            message = (
                await result_db.get(Message, child.result_message_id)
                if child is not None and child.result_message_id and status in {"completed", "failed", "cancelled"} else None
            )
            content = message.content if message is not None else ""
            metadata = (message.provider_data or {}).get("_matoloop", {}) if message else {}
            cause = (child.error if child is not None else "agent.failed") or metadata.get("completionCause")
            if status in {"failed", "cancelled"} and not cause:
                cause = "agent.failed"
            evidence = []
            if cause:
                tools = list((await result_db.scalars(
                    select(Message).where(
                        Message.session_id == session_id,
                        Message.role == "tool",
                        func.coalesce(Message.provider_data["_matoloop"]["errorCode"].as_string(), "").not_in((
                            "agent.toolCallLimitReached", "agent.outputLimitReached",
                            "agent.repeatedToolCall", "tool.skippedAfterUserDenial",
                        )),
                    ).order_by(Message.created_at.desc(), Message.id.desc()).limit(6)
                )).all())
                for tool in reversed(tools):
                    tool_metadata = (tool.provider_data or {}).get("_matoloop", {})
                    evidence.append({
                        "messageId": str(tool.id), "toolCallId": tool.tool_call_id,
                        "tool": tool_metadata.get("toolName"),
                        "result": tool.content[:1_200], "truncated": len(tool.content) > 1_200,
                        "fileActions": tool_metadata.get("fileActions", [])[:6],
                        "errorCode": tool_metadata.get("errorCode"),
                    })
            results.append({
                "sessionId": str(session_id), "runId": str(run_id), "description": title,
                "status": status,
                "resultMessageId": str(message.id) if message is not None else None,
                "result": content[:MAX_RESULT_CHARACTERS],
                "truncated": len(content) > MAX_RESULT_CHARACTERS,
                "errorCode": cause,
                **({"awaitingInstruction": True} if run_id in AgentRuntime._instruction_waits else {}),
                **({"cause": error_payload(cause)["error"], "lastToolResults": evidence} if cause else {}),
            })
            tracked.reported_instruction_wait = AgentRuntime._instruction_waits.get(run_id)
    return ToolResult.json({"tasks": results}, metadata={"privateIntegrationData": private_data_used})


async def deliver_updates(db: AsyncSession, parent: AgentRun, session: ChatSession) -> None:
    from mato_loop_backend.agent.runtime import AgentRuntime

    children = [child for child in _children.get(parent.id, {}).values() if child.task.done() or (
        (instruction := AgentRuntime._instruction_waits.get(child.run_id)) is not None
        and instruction is not child.reported_instruction_wait
    )]
    if not children:
        return
    completed = [child for child in children if child.task.done()]
    result = await task_results(children)
    if result.metadata["privateIntegrationData"]:
        session.private_integration_data_used = True
    message = Message(
        id=uuid4(), session_id=session.id, role="assistant",
        content="Aktualisierungen delegierter Aufgaben sind eingetroffen.",
        trust_level=parent.trust_level, created_at=datetime.now(UTC), attachments=[],
        provider_data={"_matoloop": {
            "origin": "host", "runId": str(parent.id),
            "agentProfileId": str(parent.agent_profile_id) if parent.agent_profile_id else None,
            "continuationSegment": True, "subagentResults": json.loads(result.content),
        }},
    )
    db.add(message)
    await db.commit()
    forget_children(parent.id, completed)
    from mato_loop_backend.sessions.serialization import message_payload

    await run_events.publish(parent.id, {"type": "message.completed", "message": message_payload(message)})
