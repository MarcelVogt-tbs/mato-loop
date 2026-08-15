import asyncio
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.agent.runtime as runtime_module
import mato_loop_backend.agent.video_analysis as video_analysis
from mato_loop_backend.agent.context import ContextManager, ContextProjection
from mato_loop_backend.agent.permissions import PermissionDecision
from mato_loop_backend.agent.runtime import (
    AgentRunError,
    AgentRuntime,
    _linked_research_source_ids,
    _VisibleResponse,
)
from mato_loop_backend.agent.tools import (
    ToolFileAction,
    ToolRegistry,
    ToolResult,
    tool_registry,
)
from mato_loop_backend.events import run_events
from mato_loop_backend.execution.protocol import ResponseMessage
from mato_loop_backend.models import AgentRun, ChatSession, Message, WebSearchPolicy
from mato_loop_backend.providers.base import ProviderResult, ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    AgentRuntimeSettings,
    AiSettings,
    IntegrationInstance,
    IntegrationSettings,
    MemorySettings,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    ToolSettings,
)
from mato_loop_backend.settings.models import LlmProviderConfig, ModelConfig, TrustLevel
from mato_loop_backend.video_processing import MediaInfo, VideoFrame
from mato_loop_backend.workspace import RootFileService

SOURCE_ID = "src-test"
SOURCE_URL = "https://example.test/source"
SOURCE_TITLE = "Quelle"
SOURCE_CITATION = f"[{SOURCE_TITLE}]({SOURCE_URL})"


def _search_call(call_id: str, query: str) -> ToolCall:
    return ToolCall(id=call_id, name="web_search", arguments={"query": query})


def _search_result(*, empty: bool = False) -> ToolResult:
    if empty:
        return ToolResult.json([], metadata={"resultCount": 0, "sources": []})
    source = {
        "sourceId": SOURCE_ID,
        "title": SOURCE_TITLE,
        "url": SOURCE_URL,
        "kind": "search",
    }
    return ToolResult.json(
        [{**source, "snippet": "Aktuelle Information"}],
        metadata={"resultCount": 1, "sources": [source]},
    )


async def _runtime_setup(
    monkeypatch,
    results: list[ProviderResult],
    *,
    max_steps: int = 10,
    max_tool_calls: int = 20,
    max_research_tool_calls: int = 8,
    max_model_requests: int = 10,
    search_policy: WebSearchPolicy = WebSearchPolicy.REQUIRED,
    active_supports_image_input: bool = False,
    vision_fallback: bool = False,
    vision_fallback_private: bool = False,
    tool_result: ToolResult | None = None,
    workspace_access: str = "read-only",
    skills: SkillsSettings | None = None,
    skill_creation_model: bool = False,
    streamed_contents: list[str | None] | None = None,
    integration_settings: IntegrationSettings | None = None,
    execution_agent_id=None,
    execution_operations: tuple[str, ...] = (),
):
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "https://llm.example.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id="local-model",
        supports_image_input=active_supports_image_input,
    )
    vision_model = (
        ModelConfig(
            provider_id=provider.id,
            model_id="vision-model",
            supports_image_input=True,
            supports_tools=False,
            trust_level=(TrustLevel.PRIVATE if vision_fallback_private else None),
        )
        if vision_fallback
        else None
    )
    builder_model = (
        ModelConfig(provider_id=provider.id, model_id="skill-builder")
        if skill_creation_model
        else None
    )
    ai_settings = AiSettings(
        providers=[provider],
        models=[item for item in (model, vision_model, builder_model) if item is not None],
        default_model_id=model.id,
        vision_model_id=vision_model.id if vision_model is not None else None,
        skill_creation_model_id=builder_model.id if builder_model is not None else None,
    )
    runtime_settings = AgentRuntimeSettings(
        max_steps=max_steps,
        max_model_requests=max_model_requests,
        max_tool_calls=max_tool_calls,
        max_research_tool_calls=max_research_tool_calls,
    )
    session = ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        model_config_id=model.id,
        memory_mode="disabled",
        web_search_policy=search_policy.value,
        workspace_access=workspace_access,
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        execution_agent_id=execution_agent_id,
        execution_operations=list(execution_operations),
    )
    user = Message(id=uuid4(), session_id=session.id, role="user", content="Aktuelle Frage")
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            AiSettings: ai_settings,
            AgentIdentitySettings: AgentIdentitySettings(),
            AgentRuntimeSettings: runtime_settings,
            MemorySettings: MemorySettings(enabled=False),
            IntegrationSettings: integration_settings or IntegrationSettings(),
            SkillsSettings: skills or SkillsSettings(),
            ToolSettings: ToolSettings(),
        }[settings_type]

    projections = []

    async def build_context(*_args, **kwargs):
        projections.append(kwargs)
        return ContextProjection(
            messages=[
                {"role": "system", "content": kwargs["system_prompt"]},
                {"role": "user", "content": user.content},
            ],
            estimated_input_tokens=100,
            input_budget_tokens=1_000,
        )

    requests = []
    queue = list(results)
    streamed_queue = list(streamed_contents) if streamed_contents is not None else None

    async def complete(_db, **kwargs):
        requests.append(kwargs)
        result = queue.pop(0)
        streamed_content = (
            streamed_queue.pop(0) if streamed_queue is not None else result.content
        )
        if streamed_content and kwargs.get("on_delta"):
            await kwargs["on_delta"](streamed_content)
        return result

    events = []

    async def publish(_run_id, event):
        events.append(event)

    monkeypatch.setattr(SettingsStore, "load", load_settings)
    monkeypatch.setattr(AgentRuntime, "_records", AsyncMock(return_value=[user]))
    monkeypatch.setattr(ContextManager, "build", build_context)
    monkeypatch.setattr(ProviderService, "complete", staticmethod(complete))
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(return_value=tool_result or _search_result()),
    )
    monkeypatch.setattr(run_events, "publish", publish)
    return db, run, requests, projections, events


def _assistant_messages(db) -> list[Message]:
    return [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "assistant"
    ]


async def test_completed_response_persists_canonical_markdown(monkeypatch) -> None:
    wrapped = "````markdown\n# Document\n\n```mermaid\ngraph TD\nA --> B\n```\n````"
    expected = "# Document\n\n```mermaid\ngraph TD\nA --> B\n```\n"
    db, run, _requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content=wrapped, finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert _assistant_messages(db)[0].content == expected
    completed = next(event for event in events if event.get("type") == "message.completed")
    assert completed["message"]["content"] == expected


async def test_generated_openscad_source_requires_a_successful_render(monkeypatch) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    db, run, requests, projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(content="```scad\ncube(1);\n```", finish_reason="stop"),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="write",
                        name="write_file",
                        arguments={
                            "root": "session",
                            "path": "files/cube.scad",
                            "content": "cube(1);\n",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="render",
                        name="render_openscad",
                        arguments={
                            "root": "session",
                            "path": "files/cube.scad",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Fertig gerendert.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        workspace_access="read-write",
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "auto",
        "required",
        "required",
        "auto",
    ]
    assert "has not been rendered successfully" in projections[1]["system_prompt"]
    assert not any(
        "cube(1)" in message.content for message in _assistant_messages(db)
    )
    assert _assistant_messages(db)[-1].content == "Fertig gerendert."


async def test_provider_retry_publishes_waiting_model_phase(monkeypatch) -> None:
    db, run, requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="Fertig", finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)
    events.clear()
    await requests[0]["on_retry"](2, 3, 503, "provider.requestFailed")

    started_at = events[0].pop("startedAt")
    assert isinstance(started_at, str)
    assert events == [
        {
            "type": "run.phase",
            "phase": "waiting_model",
            "modelRequest": 1,
            "providerAttempt": 2,
            "providerAttemptTotal": 3,
        }
    ]


async def test_required_search_loads_matching_skill_before_search(monkeypatch) -> None:
    skill = SkillDefinition(
        name="Current research",
        description="Use for current web research.",
        instructions="Search official sources first.",
    )
    db, run, requests, projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="load-skill",
                        name="load_skill",
                        arguments={"skill_id": str(skill.id)},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[_search_call("search-after-skill", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content=f"Antwort {SOURCE_CITATION}", finish_reason="stop"),
        ],
        skills=SkillsSettings(skills=[skill]),
    )

    async def execute(call, context, bound_tool):
        if call.name == "load_skill":
            return await ToolRegistry._execute_definition(
                tool_registry, call, context, bound_tool
            )
        return _search_result()

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert {tool["name"] for tool in requests[0]["tools"]} == {
        "load_skill",
        "web_search",
    }
    assert [tool["name"] for tool in requests[1]["tools"]] == ["web_search"]
    assert skill.instructions not in projections[0]["system_prompt"]
    assert skill.instructions in projections[1]["system_prompt"]


async def test_stale_skill_loads_after_activation_are_not_executed_or_persisted(
    monkeypatch,
) -> None:
    skill = SkillDefinition(
        name="Current research",
        description="Use for current web research.",
        instructions="Search official sources first.",
    )
    stale = ToolCall(
        id="stale-load",
        name="load_skill",
        arguments={"skill_id": str(skill.id)},
    )
    db, run, requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="initial-load",
                        name="load_skill",
                        arguments={"skill_id": str(skill.id)},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(tool_calls=[stale], finish_reason="tool_calls"),
            ProviderResult(tool_calls=[stale], finish_reason="tool_calls"),
            ProviderResult(tool_calls=[stale], finish_reason="tool_calls"),
        ],
        skills=SkillsSettings(skills=[skill]),
    )

    async def execute(call, context, bound_tool):
        return await ToolRegistry._execute_definition(
            tool_registry, call, context, bound_tool
        )

    tool_registry._execute_definition.side_effect = execute

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.requiredWebSearchMissing"
    assert len(requests) == 4
    tool_registry._execute_definition.assert_awaited_once()
    persisted = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
    ]
    assert [message.role for message in persisted] == ["assistant", "tool"]
    assert "stale-load" not in str([message.tool_calls for message in persisted])
    assert all(event.get("toolCallId") != "stale-load" for event in events)


async def test_invalid_skill_loads_consume_required_search_retries(monkeypatch) -> None:
    skill = SkillDefinition(
        name="Current research",
        description="Use for current web research.",
        instructions="Search official sources first.",
    )
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id=f"invalid-load-{index}",
                        name="load_skill",
                        arguments={"skill_id": str(uuid4())},
                    )
                ],
                finish_reason="tool_calls",
            )
            for index in range(3)
        ],
        skills=SkillsSettings(skills=[skill]),
    )

    async def execute(call, context, bound_tool):
        return await ToolRegistry._execute_definition(
            tool_registry, call, context, bound_tool
        )

    tool_registry._execute_definition.side_effect = execute

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.requiredWebSearchMissing"
    assert len(requests) == 3
    assert tool_registry._execute_definition.await_count == 3


async def test_load_skill_is_rejected_when_called_with_another_tool(monkeypatch) -> None:
    skill = SkillDefinition(
        name="Current research",
        description="Use for current web research.",
        instructions="Search official sources first.",
    )
    db, run, _requests, projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="load-skill",
                        name="load_skill",
                        arguments={"skill_id": str(skill.id)},
                    ),
                    _search_call("parallel-search", "Aktuelle Frage"),
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Ohne Skill fortgesetzt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        skills=SkillsSettings(skills=[skill]),
    )

    await AgentRuntime._execute_loop(db, run)

    tool_messages = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert len(tool_messages) == 2
    assert all("skill.loadMustBeExclusive" in item.content for item in tool_messages)
    assert skill.instructions not in projections[1]["system_prompt"]
    tool_registry._execute_definition.assert_not_awaited()


async def test_skill_builder_is_isolated_and_confirms_session_model_fallback(
    monkeypatch, tmp_path
) -> None:
    skills = SkillsSettings()
    original_execute = ToolRegistry._execute_definition
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="build-skill",
                        name="build_skill",
                        arguments={
                            "brief": "Create a reusable API validation workflow.",
                            "name_hint": "API checks",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="invalid"),
            ProviderResult(content="still invalid"),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="draft",
                        name="return_skill_draft",
                        arguments={
                            "name": "API checks",
                            "description": "Use when an API response must be validated.",
                            "instructions": "Call the available API tool and validate its response.",
                        },
                    )
                ]
            ),
            ProviderResult(content="Skill erstellt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        skills=skills,
        skill_creation_model=True,
    )

    async def execute(call, context, bound_tool):
        return await original_execute(tool_registry, call, context, bound_tool)

    tool_registry._execute_definition.side_effect = execute
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(run.session_id)
    monkeypatch.setattr(
        runtime_module,
        "RootFileService",
        lambda session_id, **kwargs: RootFileService(
            session_id,
            session_storage=storage,
            workspace_home=tmp_path,
            working_directory=kwargs.get("working_directory"),
        ),
    )
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(
        AgentRuntime,
        "_authorize",
        AsyncMock(return_value=PermissionDecision.ALLOW),
    )
    async def confirm_fallback(*_args, **_kwargs):
        assert len(requests) == 3
        return {"skillModelFallback": ["Sessionmodell verwenden"]}

    request_question = AsyncMock(side_effect=confirm_fallback)
    monkeypatch.setattr(AgentRuntime, "_request_question", request_question)

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "skill-builder",
        "skill-builder",
        "local-model",
        "local-model",
    ]
    builder_request, repair_request, fallback_request = requests[1:4]
    assert all(
        [tool["name"] for tool in request["tools"]] == ["return_skill_draft"]
        for request in (builder_request, repair_request, fallback_request)
    )
    assert [message["role"] for message in builder_request["messages"]] == [
        "system",
        "user",
    ]
    assert [message["role"] for message in repair_request["messages"]] == [
        "system",
        "user",
        "user",
    ]
    assert repair_request["messages"][2]["content"].startswith(
        "The output was invalid."
    )
    assert [message["role"] for message in fallback_request["messages"]] == [
        "system",
        "user",
    ]
    builder_payload = json.loads(builder_request["messages"][1]["content"])
    assert builder_payload["currentRequest"] == "Aktuelle Frage"
    assert builder_payload["brief"] == "Create a reusable API validation workflow."
    assert builder_payload["nameHint"] == "API checks"
    assert builder_payload["effectiveTools"] == requests[0]["tools"]
    assert set(builder_payload) == {
        "currentRequest",
        "brief",
        "nameHint",
        "effectiveTools",
    }
    request_question.assert_awaited_once()
    assert [skill.name for skill in skills.skills] == ["API checks"]
    SettingsStore.save.assert_awaited_once()


async def test_skill_builder_uses_only_one_structured_repair_across_fallback(
    monkeypatch,
) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="build-skill",
                        name="build_skill",
                        arguments={"brief": "Create an API workflow."},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="invalid"),
            ProviderResult(content="still invalid"),
            ProviderResult(content="fallback invalid"),
            ProviderResult(content="Unbestätigte Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        skill_creation_model=True,
    )
    original_execute = ToolRegistry._execute_definition

    async def execute(call, context, bound_tool):
        return await original_execute(tool_registry, call, context, bound_tool)

    tool_registry._execute_definition.side_effect = execute
    monkeypatch.setattr(
        AgentRuntime,
        "_request_question",
        AsyncMock(return_value={"skillModelFallback": ["Sessionmodell verwenden"]}),
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "skill.creationModelFailed"
    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "skill-builder",
        "skill-builder",
        "local-model",
        "local-model",
    ]
    assert [message["role"] for message in requests[3]["messages"]] == [
        "system",
        "user",
    ]


async def test_failed_skill_builder_request_consumes_model_budget(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [],
        max_model_requests=2,
        search_policy=WebSearchPolicy.AVAILABLE,
        skill_creation_model=True,
    )
    main_result = ProviderResult(
        tool_calls=[
            ToolCall(
                id="build-skill",
                name="build_skill",
                arguments={"brief": "Create an API workflow."},
            )
        ],
        finish_reason="tool_calls",
    )

    async def complete(_db, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return main_result
        raise RuntimeError("builder unavailable")

    original_execute = ToolRegistry._execute_definition

    async def execute(call, context, bound_tool):
        return await original_execute(tool_registry, call, context, bound_tool)

    monkeypatch.setattr(ProviderService, "complete", staticmethod(complete))
    tool_registry._execute_definition.side_effect = execute
    question = AsyncMock(
        return_value={"skillModelFallback": ["Sessionmodell verwenden"]}
    )
    monkeypatch.setattr(AgentRuntime, "_request_question", question)

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.modelRequestLimitReached"
    assert len(requests) == 2
    question.assert_awaited_once()


async def test_runtime_persists_typed_file_actions_on_tool_results(monkeypatch) -> None:
    execution_agent_id = str(uuid4())
    root_id = str(uuid4())
    action = ToolFileAction(
        "write",
        "workspace",
        root_id,
        "files/report.txt",
        "report.txt",
        7,
        execution_agent_id,
        root_id,
        "a" * 64,
        "persisted/project",
    )
    db, run, *_ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={
                            "root": "session",
                            "path": "files/report.txt",
                            "content": "report",
                        },
                    )
                ]
            ),
            ProviderResult(content="Fertig", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        workspace_access="read-write",
        tool_result=ToolResult.json(
            {"root": "session", "written": "files/report.txt"},
            file_actions=(action,),
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    tool_message = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    )
    assert tool_message.provider_data == {
        "_matoloop": {
            "fileActions": [
                {
                    "operation": "write",
                    "root": "workspace",
                    "rootIdentity": action.root_identity,
                    "path": "files/report.txt",
                    "filename": "report.txt",
                    "sizeBytes": 7,
                    "executionAgentId": execution_agent_id,
                    "rootId": root_id,
                    "rootRevision": "a" * 64,
                    "workingDirectory": "persisted/project",
                }
            ]
        }
    }
    assert "download" not in str(tool_message.provider_data).casefold()


async def test_cancelled_dispatched_workspace_write_commits_before_run_stops(
    monkeypatch,
) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="write-1",
                        name="write_file",
                        arguments={
                            "root": "workspace",
                            "path": "a.txt",
                            "content": "x",
                        },
                    )
                ]
            ),
            ProviderResult(content="Must not be requested", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        workspace_access="read-write",
    )
    run.execution_agent_id = uuid4()
    run.execution_root_id = uuid4()
    run.execution_root_revision = "a" * 64
    run.working_directory_snapshot = "project"
    run.execution_operations = ["workspace.write"]
    dispatched = asyncio.Event()
    release_response = asyncio.Event()
    timeline = []

    async def request(
        _agent_id,
        _operation,
        _arguments,
        *,
        on_cancellation_deferred,
        **_kwargs,
    ):
        dispatched.set()
        try:
            await release_response.wait()
        except asyncio.CancelledError:
            on_cancellation_deferred()
            await release_response.wait()
        return ResponseMessage.model_validate(
            {
                "type": "response",
                "id": uuid4(),
                "ok": True,
                "result": {"path": "a.txt", "sha256": "b" * 64, "size": 1},
            }
        )

    async def commit() -> None:
        if any(
            isinstance(call.args[0], Message) and call.args[0].role == "tool"
            for call in db.add.call_args_list
        ) and "tool committed" not in timeline:
            timeline.append("tool committed")

    original_execute = ToolRegistry._execute_definition

    async def execute(call, context, bound_tool):
        return await original_execute(tool_registry, call, context, bound_tool)

    monkeypatch.setattr(
        "mato_loop_backend.execution.client.execution_agent_connections.request",
        request,
    )
    monkeypatch.setattr(
        AgentRuntime, "_authorize", AsyncMock(return_value=PermissionDecision.ALLOW)
    )
    tool_registry._execute_definition.side_effect = execute
    db.commit.side_effect = commit

    task = asyncio.create_task(AgentRuntime._execute_loop(db, run))
    await dispatched.wait()
    task.cancel()
    await asyncio.sleep(0)
    release_response.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    timeline.append("run cancelled")

    tool_message = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    )
    assert timeline == ["tool committed", "run cancelled"]
    assert tool_message.tool_call_id == "write-1"
    assert tool_message.provider_data["_matoloop"]["fileActions"] == [
        {
            "operation": "write",
            "root": "workspace",
            "rootIdentity": str(run.execution_root_id),
            "path": "a.txt",
            "filename": "a.txt",
            "sizeBytes": 1,
            "executionAgentId": str(run.execution_agent_id),
            "rootId": str(run.execution_root_id),
            "rootRevision": run.execution_root_revision,
            "workingDirectory": "project",
        }
    ]
    assert len(requests) == 1


def test_linked_research_sources_ignore_hidden_and_foreign_links() -> None:
    parenthesized_url = "https://example.test/wiki/Product_(series)"
    content = (
        f"[Beleg]({parenthesized_url})\n"
        "[Andere Quelle](https://other.example/source)\n"
        f"`[Versteckt]({SOURCE_URL})`"
    )

    assert _linked_research_source_ids(
        content,
        {"parenthesized": parenthesized_url, SOURCE_ID: SOURCE_URL},
    ) == ["parenthesized"]


def test_linked_research_sources_support_autolinks_but_not_images() -> None:
    image_url = "https://example.test/image"
    escaped_url = "https://example.test/escaped"
    hidden_url = "https://example.test/hidden"
    content = (
        f"<{SOURCE_URL}>\n"
        f"![Bild]({image_url})\n"
        f"\\[Kein Link]({escaped_url})\n"
        f"<!-- [Versteckt]({hidden_url})"
    )

    assert _linked_research_source_ids(
        content,
        {
            SOURCE_ID: SOURCE_URL,
            "image": image_url,
            "escaped": escaped_url,
            "hidden": hidden_url,
        },
    ) == [SOURCE_ID]


def test_linked_research_sources_match_rendered_destinations_not_link_labels() -> None:
    other_url = "https://other.example/source"
    content = (
        f"[{SOURCE_URL}]({other_url}) "
        f"[Quelle mit Titel]({SOURCE_URL} \"Offiziell\")"
    )

    assert _linked_research_source_ids(
        content,
        {SOURCE_ID: SOURCE_URL, "other": other_url},
    ) == ["other", SOURCE_ID]


async def test_available_can_answer_without_research(monkeypatch) -> None:
    answer = "Antwort aus stabilem Wissen"
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content=answer, finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 1
    assert requests[0]["tool_choice"] == "auto"
    assert requests[0]["prompt_cache_key"] == str(run.session_id)
    assert any(event.get("delta") == answer for event in events)
    final = _assistant_messages(db)[0]
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "available",
        "used": False,
        "linkedSources": [],
    }


async def test_required_search_transitions_to_the_shared_streaming_loop(monkeypatch) -> None:
    answer = f"Belegte Antwort {SOURCE_CITATION}"
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Ich recherchiere.",
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                provider_data={"chat_reasoning_details": [{"text": "Intern"}]},
                finish_reason="tool_calls",
            ),
            ProviderResult(content=answer, finish_reason="stop"),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["required", "auto"]
    assert [tool["name"] for tool in requests[0]["tools"]] == ["web_search"]
    assert {tool["name"] for tool in requests[1]["tools"]} >= {
        "read",
        "web_search",
    }
    assert "Mandatory web research is active" in projections[0]["system_prompt"]
    assert "Mandatory web research is active" not in projections[1]["system_prompt"]
    assert "Web research contract:" in projections[0]["system_prompt"]
    assert "Web research contract:" in projections[1]["system_prompt"]
    persisted_contents = [message.content for message in _assistant_messages(db)]
    assert "Ich recherchiere." not in persisted_contents
    assert answer in persisted_contents
    assert all(event.get("delta") != "Ich recherchiere." for event in events)
    assert any(event.get("delta") == answer for event in events)
    final = next(message for message in _assistant_messages(db) if not message.tool_calls)
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "required",
        "used": True,
        "linkedSources": [{"title": SOURCE_TITLE, "url": SOURCE_URL}],
    }
    completed = next(
        event
        for event in events
        if event["type"] == "message.completed" and not event["message"]["toolCalls"]
    )
    assert completed["message"]["research"] == final.provider_data["_matoloop"][
        "research"
    ]


async def test_empty_response_after_search_is_repaired(monkeypatch) -> None:
    answer = f"Belegte Antwort {SOURCE_CITATION}"
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="", finish_reason="stop"),
            ProviderResult(content=answer, finish_reason="stop"),
        ],
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert "previous model turn returned neither" not in projections[1]["system_prompt"]
    assert "previous model turn returned neither" in projections[2]["system_prompt"]
    final_messages = [message for message in _assistant_messages(db) if not message.tool_calls]
    assert [message.content for message in final_messages] == [answer]
    assert any(event.get("delta") == answer for event in events)


async def test_empty_response_repair_can_cross_the_step_limit(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(content="", finish_reason="stop"),
            ProviderResult(content="Reparierte Antwort", finish_reason="stop"),
        ],
        max_steps=1,
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 2
    assert _assistant_messages(db)[0].content == "Reparierte Antwort"


async def test_repeated_empty_response_fails_without_persisting_it(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="", finish_reason="stop"),
            ProviderResult(content="", finish_reason="stop"),
        ],
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.emptyResponse"
    assert len(requests) == 3
    assert not [message for message in _assistant_messages(db) if not message.tool_calls]


async def test_streamed_text_is_persisted_when_final_result_is_empty(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="", finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
        streamed_contents=["Gestreamte Antwort"],
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 1
    assert _assistant_messages(db)[0].content == "Gestreamte Antwort"


async def test_empty_output_at_length_limit_is_not_repaired(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="", finish_reason="length")],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.outputLimitReached"
    assert len(requests) == 1


async def test_empty_required_search_satisfies_the_gate(monkeypatch) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-empty", "Sehr seltenes Thema")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Keine aktuellen Treffer gefunden.", finish_reason="stop"
            ),
        ],
    )
    tool_registry._execute_definition.return_value = _search_result(empty=True)

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["required", "auto"]
    assert any(
        event.get("delta") == "Keine aktuellen Treffer gefunden." for event in events
    )
    final = next(message for message in _assistant_messages(db) if not message.tool_calls)
    assert final.provider_data["_matoloop"]["research"] == {
        "policy": "required",
        "used": True,
        "linkedSources": [],
    }


async def test_required_search_retries_hidden_after_tool_failure(monkeypatch) -> None:
    db, run, requests, projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                content="Unbelegte Antwort",
                tool_calls=[_search_call("search-failed", "Fehlerhafte Suche")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Zweiter Versuch",
                tool_calls=[_search_call("search-ok", "Neu formulierte Suche")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Normale Antwort", finish_reason="stop"),
        ],
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("integration.unavailable"),
        _search_result(),
    ]

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "required",
        "required",
        "auto",
    ]
    assert "previous attempt did not complete" in projections[1]["system_prompt"]
    persisted_contents = [message.content for message in _assistant_messages(db)]
    assert "Unbelegte Antwort" not in persisted_contents
    assert "Zweiter Versuch" not in persisted_contents
    assert all(
        event.get("delta") not in {"Unbelegte Antwort", "Zweiter Versuch"}
        for event in events
    )


async def test_failed_mutation_blocks_an_unconfirmed_final_response(monkeypatch) -> None:
    db, run, _requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        "write-failed",
                        "write_file",
                        {"root": "session", "path": "files/note.txt", "content": "Hallo"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Die Datei wurde erfolgreich erstellt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        workspace_access="read-write",
        tool_result=ToolResult.error("tool.invalidArguments", field="path"),
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "tool.invalidArguments"
    assert "Die Datei wurde erfolgreich erstellt." not in {
        message.content for message in _assistant_messages(db)
    }
    assert all(event["type"] != "message.delta" for event in events)


async def test_successful_mutation_correction_releases_the_final_response(monkeypatch) -> None:
    db, run, _requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        "write-failed",
                        "write_file",
                        {"root": "session", "path": "files/wrong.txt", "content": "Hallo"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content="Schon erledigt.",
                tool_calls=[
                    ToolCall(
                        "write-corrected",
                        "write_file",
                        {"root": "session", "path": "files/note.txt", "content": "Hallo"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Die Datei wurde erstellt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        workspace_access="read-write",
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("tool.invalidArguments", field="path"),
        ToolResult.json({"written": "files/note.txt"}),
    ]

    await AgentRuntime._execute_loop(db, run)

    persisted = [message.content for message in _assistant_messages(db)]
    assert "Schon erledigt." not in persisted
    assert persisted[-1] == "Die Datei wurde erstellt."
    assert [event.get("delta") for event in events].count("Die Datei wurde erstellt.") == 1


async def test_directory_change_allows_retrying_a_failed_workspace_call(monkeypatch) -> None:
    workspace_read = {"root": "workspace", "path": "README.md"}
    db, run, _requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[ToolCall("read-failed", "read", workspace_read)],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        "change-directory",
                        "change_directory",
                        {"path": "/workspace/drastic-backup"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[ToolCall("read-retried", "read", workspace_read)],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Repository gelesen.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        execution_agent_id=uuid4(),
        execution_operations=("filesystem.resolve",),
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("file.notFound"),
        ToolResult.json({"currentDirectory": "/workspace/drastic-backup"}),
        ToolResult("README", {}),
    ]

    await AgentRuntime._execute_loop(db, run)

    assert [
        call.args[0].name for call in tool_registry._execute_definition.await_args_list
    ] == ["read", "change_directory", "read"]
    assert _assistant_messages(db)[-1].content == "Repository gelesen."


async def test_available_uses_the_shared_research_budget(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[_search_call("search-2", "Weitere Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Budgetierte Antwort", finish_reason="stop"),
        ],
        max_research_tool_calls=1,
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert tool_registry._execute_definition.await_count == 1
    assert "web_search" not in {tool["name"] for tool in requests[1]["tools"]}
    assert "webfetch" not in {tool["name"] for tool in requests[1]["tools"]}
    tool_messages = [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert any("agent.researchBudgetReached" in message.content for message in tool_messages)


async def test_repeated_successful_research_call_does_not_hit_the_network(monkeypatch) -> None:
    db, run, requests, projections, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[_search_call("search-2", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Antwort nach Duplikat", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert tool_registry._execute_definition.await_count == 1
    tool_contents = [
        call.args[0].content
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
    ]
    assert any('"alreadyCompleted": true' in content for content in tool_contents)
    assert "web_search" not in {tool["name"] for tool in requests[2]["tools"]}
    assert "Research tools are unavailable for the rest of this turn" in projections[2][
        "system_prompt"
    ]
    assert "7 remaining" not in projections[2]["system_prompt"]


async def test_tool_call_limit_applies_after_required_gate(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="read-1",
                        name="read",
                        arguments={"root": "session", "path": "files/info.txt"},
                    )
                ],
                finish_reason="tool_calls",
            ),
        ],
        max_tool_calls=1,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.toolCallLimitReached"
    assert len(requests) == 2
    assert requests[1]["tools"] == []
    assert tool_registry._execute_definition.await_count == 1


async def test_exact_tool_call_limit_allows_tool_free_final_response(monkeypatch) -> None:
    answer = f"Abschluss aus bestätigten Ergebnissen {SOURCE_CITATION}"
    db, run, requests, projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-1", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content=answer, finish_reason="stop"),
        ],
        max_steps=1,
        max_tool_calls=1,
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 2
    assert requests[1]["tools"] == []
    assert "Tool budget limit for this run: 1" in projections[0][
        "system_prompt"
    ]
    assert "Tool budget exhausted. No tools are available." in projections[1][
        "system_prompt"
    ]
    assert _assistant_messages(db)[-1].content == answer


async def test_oversized_tool_batch_executes_nothing(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    _search_call("search-1", "Erste Frage"),
                    _search_call("search-2", "Zweite Frage"),
                ],
                finish_reason="tool_calls",
            )
        ],
        max_tool_calls=1,
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.toolCallLimitReached"
    assert len(requests) == 1
    assert not _assistant_messages(db)
    tool_registry._execute_definition.assert_not_awaited()


async def test_required_search_stops_after_two_semantic_retries(monkeypatch) -> None:
    db, run, requests, _, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(content="Antwort 1", finish_reason="stop"),
            ProviderResult(content="Antwort 2", finish_reason="stop"),
            ProviderResult(content="Antwort 3", finish_reason="stop"),
        ],
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.requiredWebSearchMissing"
    assert len(requests) == 3
    assert db.add.call_count == 0
    assert all(event["type"] != "message.delta" for event in events)


@pytest.mark.parametrize(
    ("active_supports_image_input", "expected_model"),
    [(True, "local-model"), (False, "vision-model")],
)
async def test_pdf_vision_prefers_active_model_then_configured_fallback(
    monkeypatch, active_supports_image_input: bool, expected_model: str
) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="fetch-pdf",
                        name="webfetch",
                        arguments={"url": "https://example.test/scan.pdf"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content='[{"page":1,"text":"Scan text"}]'),
            ProviderResult(content="Fertige Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=active_supports_image_input,
        vision_fallback=True,
    )

    async def execute(_call, context, _tool):
        assert context.pdf_vision_handler is not None
        extracted = await context.pdf_vision_handler([(1, b"jpeg")], False)
        return ToolResult.json({"content": extracted[1]})

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    assert requests[1]["model"].model_id == expected_model
    assert requests[1]["tool_choice"] == "none"
    assert requests[1]["image_inputs"][0].message_index == 1
    assert requests[1]["image_inputs"][0].data == b"jpeg"


async def test_private_pdf_pages_use_public_active_vision_model(monkeypatch) -> None:
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="fetch-pdf",
                        name="webfetch",
                        arguments={"url": "http://private.test/scan.pdf"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content='[{"page":1,"text":"Private scan text"}]'),
            ProviderResult(content="Fertige Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=True,
        vision_fallback=True,
        vision_fallback_private=True,
    )

    async def execute(_call, context, _tool):
        assert context.pdf_vision_handler is not None
        extracted = await context.pdf_vision_handler([(1, b"jpeg")], True)
        return ToolResult.json({"content": extracted[1]})

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert requests[1]["model"].model_id == "local-model"
    assert requests[1]["target_trust_level"] == TrustLevel.PUBLIC
    assert requests[1]["image_inputs"][0].trust_level == TrustLevel.PRIVATE.value


async def test_video_frames_are_analyzed_in_provider_sized_batches(monkeypatch) -> None:
    frames = [VideoFrame(index * 1_000, f"frame-{index}".encode()) for index in range(8)]
    monkeypatch.setattr(
        video_analysis,
        "probe_video",
        lambda _data: MediaInfo("mp4", "video/mp4", 8_000, 640, 360, 0, None),
    )
    monkeypatch.setattr(
        video_analysis, "extract_video_frames", lambda _data, **_kwargs: frames
    )
    db, run, requests, _, _ = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="analyze-video",
                        name="analyze_video",
                        arguments={"root": "session", "path": "attachments/video.mp4"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Frames 0 bis 3"),
            ProviderResult(content="Frames 4 bis 7"),
            ProviderResult(content="Fertige Anleitung", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=True,
    )

    async def execute(_call, context, _tool):
        assert context.video_analysis_handler is not None
        result = await context.video_analysis_handler(
            b"video", TrustLevel.PUBLIC, None, "overview", None
        )
        assert [
            (segment["startMs"], segment["endMs"])
            for segment in result["visualSegments"]
        ] == [(0, 3_500), (3_500, 8_000)]
        return ToolResult.json(result)

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    image_requests = [request for request in requests if request.get("image_inputs")]
    assert [len(request["image_inputs"]) for request in image_requests] == [4, 4]
    assert all(
        {image.message_index for image in request["image_inputs"]} == {1}
        for request in image_requests
    )
    assert all(
        len([message for message in request["messages"] if message["role"] == "user"])
        == 1
        for request in image_requests
    )
    assert all(request["max_output_tokens"] == 1_024 for request in image_requests)
    assert all(
        "at most 300 words" in request["messages"][0]["content"]
        for request in image_requests
    )
    assert [
        image.data for request in image_requests for image in request["image_inputs"]
    ] == [frame.data for frame in frames]


async def test_cancel_does_not_capture_required_gate_text(monkeypatch) -> None:
    db, run, _, _, events = await _runtime_setup(monkeypatch, [])
    visible_response = _VisibleResponse()

    async def cancel_during_gate(_db, **kwargs):
        await kwargs["on_delta"]("Nicht freigegebener Entwurf")
        raise asyncio.CancelledError

    monkeypatch.setattr(
        ProviderService,
        "complete",
        staticmethod(cancel_during_gate),
    )

    with pytest.raises(asyncio.CancelledError):
        await AgentRuntime._execute_loop(db, run, visible_response)

    assert visible_response.content == ""
    assert all(event["type"] != "message.delta" for event in events)
