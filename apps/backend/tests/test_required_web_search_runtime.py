import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import mato_loop_backend.agent.runtime as runtime_module
import mato_loop_backend.agent.video_analysis as video_analysis
from mato_loop_backend.agent.context import ContextManager, ContextProjection, message_dict
from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
)
from mato_loop_backend.agent.runtime import (
    AgentRunError,
    AgentRuntime,
    ApprovalResolution,
    _linked_research_source_ids,
    _VisibleResponse,
)
from mato_loop_backend.agent.tools import (
    ToolAttachment,
    ToolFileAction,
    ToolRegistry,
    ToolResult,
    tool_registry,
)
from mato_loop_backend.events import run_events
from mato_loop_backend.models import (
    AgentRun,
    ChatSession,
    Message,
    MessageAttachment,
    WebSearchPolicy,
)
from mato_loop_backend.providers.base import ProviderResult, ToolCall
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.session_storage import SessionStorage
from mato_loop_backend.settings import (
    AgentIdentitySettings,
    AgentRuntimeSettings,
    AiSettings,
    InstanceLimits,
    IntegrationInstance,
    IntegrationSettings,
    MemorySettings,
    SettingsStore,
    SkillDefinition,
    SkillsSettings,
    ToolSettings,
    UserProfileSettings,
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
    skills: SkillsSettings | None = None,
    skill_creation_model: bool = False,
    streamed_contents: list[str | None] | None = None,
    integration_settings: IntegrationSettings | None = None,
    execution_agent_id=None,
    execution_operations: tuple[str, ...] = (),
    user_image: bool = False,
    user_image_count: int = 1,
    downloaded_image: bool = False,
    user_content: str = "Aktuelle Frage",
    user_trust_level: TrustLevel | None = None,
    reasoning_effort: str | None = None,
    model_id: str = "local-model",
):
    provider = LlmProviderConfig(
        name="Local",
        adapter_type="openai-compatible",
        config={"base_url": "https://llm.example.test/v1"},
    )
    model = ModelConfig(
        provider_id=provider.id,
        model_id=model_id,
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
        reasoning_effort=reasoning_effort,
    )
    run = AgentRun(
        id=uuid4(),
        session_id=session.id,
        status="running",
        execution_agent_id=execution_agent_id,
        execution_operations=list(execution_operations),
    )
    user = Message(
        id=uuid4(),
        session_id=session.id,
        role="user",
        content=user_content,
        trust_level=user_trust_level.value if user_trust_level is not None else None,
    )
    records = [user]
    if user_image:
        user.attachments = [
            MessageAttachment(
                id=uuid4(),
                message_id=user.id,
                position=position,
                kind="image",
                category="user_upload",
                purpose="attachment",
                mime_type="image/png",
                size_bytes=100,
                sha256="a" * 64,
                file_path=f"attachments/{uuid4()}/reference.png",
                filename=f"reference-{position}.png",
            )
            for position in range(user_image_count)
        ]
    if downloaded_image:
        tool_message = Message(
            id=uuid4(),
            session_id=session.id,
            role="tool",
            content='{"attachments":[{"filename":"drawing-page-2.jpg","pageNumber":2}]}',
            tool_call_id="fetch",
            trust_level=TrustLevel.PUBLIC.value,
        )
        tool_message.attachments = [
            MessageAttachment(
                id=uuid4(),
                message_id=tool_message.id,
                position=0,
                kind="image",
                category="downloaded",
                purpose="artifact",
                mime_type="image/jpeg",
                size_bytes=100,
                sha256="b" * 64,
                file_root_id=session.id,
                file_id=uuid4(),
                file_revision=1,
                file_path="drawing-page-2.jpg",
                filename="drawing-page-2.jpg",
                metadata_json={"pageNumber": 2},
            )
        ]
        records.append(tool_message)
    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = session
    if execution_agent_id is not None:
        db.scalars.return_value = SimpleNamespace(all=lambda: [execution_agent_id])

    async def load_settings(_db, settings_type, **_kwargs):
        return {
            AiSettings: ai_settings,
            AgentIdentitySettings: AgentIdentitySettings(),
            AgentRuntimeSettings: runtime_settings,
            InstanceLimits: InstanceLimits(),
            MemorySettings: MemorySettings(enabled=False),
            IntegrationSettings: integration_settings or IntegrationSettings(),
            SkillsSettings: skills or SkillsSettings(),
            ToolSettings: ToolSettings(),
            UserProfileSettings: UserProfileSettings(),
        }[settings_type]

    projections = []

    async def build_context(*_args, **kwargs):
        projections.append(kwargs)
        return ContextProjection(
            messages=[
                {"role": "system", "content": kwargs["system_prompt"]},
                *[
                    message_dict(record, include_attachment_markers=False)
                    for record in records
                ],
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
    monkeypatch.setattr(AgentRuntime, "_records", AsyncMock(return_value=records))
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


def _tool_messages(db) -> list[Message]:
    return [
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message) and call.args[0].role == "tool"
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


async def test_session_reasoning_effort_reaches_primary_model_request(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [ProviderResult(content="Antwort", finish_reason="stop")],
        search_policy=WebSearchPolicy.AVAILABLE,
        reasoning_effort="low",
    )

    await AgentRuntime._execute_loop(db, run)

    assert requests[0]["reasoning_effort"] == "low"


async def test_runtime_replays_exact_tool_calls_without_persisting_them(monkeypatch) -> None:
    arguments = {
        "root": "files",
        "path": "note.txt",
        "oldText": "private old text",
        "newText": "private new text",
    }
    db, run, _requests, projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[ToolCall("edit", "edit_file", arguments)],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Fertig.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(projections) == 2
    assert list(projections[1]["live_tool_calls"].values()) == [
        [{"id": "edit", "name": "edit_file", "arguments": arguments}]
    ]
    persisted_arguments = _assistant_messages(db)[0].tool_calls[0]["arguments"]
    assert persisted_arguments == {
        "root": "files",
        "path": "note.txt",
        "oldText": {"characters": 16, "redacted": True},
        "newText": {"characters": 16, "redacted": True},
    }


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
                            "path": "cube.scad",
                            "content": "cube(1);\n",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="export",
                        name="render_openscad",
                        arguments={
                            "root": "session",
                            "path": "cube.scad",
                            "output": "stl",
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
                            "path": "cube.scad",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="wrong-review",
                        name="read",
                        arguments={
                            "root": "session",
                            "path": "other.scad",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="review",
                        name="read",
                        arguments={
                            "root": "session",
                            "path": "cube.scad",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Fertig gerendert.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == [
        "auto",
        "required",
        "required",
        "required",
        "required",
        "required",
        "auto",
    ]
    assert "has not been rendered successfully" in projections[1]["system_prompt"]
    assert "must be reviewed before finalizing" in projections[4]["system_prompt"]
    assert not any(
        "cube(1)" in message.content for message in _assistant_messages(db)
    )
    assert _assistant_messages(db)[-1].content == "Fertig gerendert."


async def test_execution_agent_unavailable_stops_before_model_retry(monkeypatch) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="render",
                        name="render_openscad",
                        arguments={
                            "root": "session",
                            "path": "model.scad",
                            "output": "preview",
                        },
                    )
                ],
                finish_reason="tool_calls",
            )
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
        tool_result=ToolResult.error("executionAgent.unavailable"),
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "executionAgent.unavailable"
    assert len(requests) == 1
    assert tool_registry._execute_definition.await_count == 1


async def test_openscad_contact_sheet_is_required_for_active_vision_model(monkeypatch) -> None:
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
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="write",
                        name="write_file",
                        arguments={
                            "root": "session",
                            "path": "model.scad",
                            "content": "cube(1);",
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
                            "path": "model.scad",
                            "output": "preview",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=json.dumps(
                    {
                        "verdict": "pass",
                        "findings": ["No defect."],
                        "uncertainty": ["Rear geometry is hidden."],
                    }
                ),
                finish_reason="stop",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="review",
                        name="read",
                        arguments={
                            "root": "session",
                            "path": "model.scad",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Visuell verglichen.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        max_steps=2,
        active_supports_image_input=True,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
        user_image=True,
        downloaded_image=True,
    )
    generated_id = uuid4()

    class FakeAttachmentService:
        def __init__(self, **_kwargs):
            pass

        async def create_attachment(self, _db, _message, _data, _mime_type, **kwargs):
            return SimpleNamespace(id=generated_id, kind=kwargs["kind"])

        async def discard(self, _attachment, _session_id):
            return None

        async def read_image(self, attachment, _session_id):
            return (attachment.filename or "image").encode()

    monkeypatch.setattr(runtime_module, "AttachmentService", FakeAttachmentService)
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            side_effect=[
                ToolResult.json({"written": "model.scad"}),
                ToolResult(
                    json.dumps(
                        {
                            "output": "preview",
                            "previewLayout": ["isometric", "front", "right", "top"],
                            "geometryMetadata": {"triangleCount": 12},
                        }
                    ),
                    {},
                    attachments=(
                        ToolAttachment(b"png", "image/png", "model-views.png"),
                    ),
                ),
                ToolResult.json({"content": "cube(1);"}),
            ]
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "local-model",
        "local-model",
        "local-model",
        "local-model",
    ]
    verifier = requests[2]
    assert verifier["tools"] == []
    assert verifier["tool_choice"] == "none"
    assert verifier["max_output_tokens"] == 512
    assert [image.data for image in verifier["image_inputs"]] == [
        b"png",
        b"reference-0.png",
        b"drawing-page-2.jpg",
    ]
    verifier_prompt = verifier["messages"][0]["content"]
    assert '"verdict":"pass|fail|uncertain"' in verifier_prompt
    assert "Pass requires an empty uncertainty list" in verifier_prompt
    assert "Do not infer hidden geometry" in verifier_prompt
    verifier_data = json.loads(verifier["messages"][1]["content"])
    assert verifier_data == {
        "imageOrder": {"contactSheet": 1, "referenceImages": [2, 3]},
        "recentRequests": [
            {
                "messageId": verifier_data["recentRequests"][0]["messageId"],
                "content": "Aktuelle Frage",
            }
        ],
        "previewLayout": ["isometric", "front", "right", "top"],
        "geometryMetadata": {"triangleCount": 12},
        "referenceImages": [
            {"image": 2, "filename": "reference-0.png"},
            {
                "image": 3,
                "filename": "drawing-page-2.jpg",
                "pageNumber": 2,
            },
        ],
    }
    assert requests[3]["required_image_ids"] == (str(generated_id),)
    assert projections[2]["required_image_ids"] == (str(generated_id),)
    render_message = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
        and call.args[0].role == "tool"
        and call.args[0].tool_call_id == "render"
    )
    assert json.loads(render_message.content)["visualVerification"] == {
        "status": "completed",
        "scope": "visible_geometry",
        "verdict": "uncertain",
        "findings": ["No defect."],
        "uncertainty": ["Rear geometry is hidden."],
        "referenceImageCount": 2,
    }
    assert _assistant_messages(db)[-1].content == "Visuell verglichen."


@pytest.mark.parametrize("vision_fallback", [False, True])
async def test_text_model_marks_or_runs_openscad_visual_verification(
    monkeypatch, vision_fallback: bool
) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    results = [
        ProviderResult(
            tool_calls=[
                ToolCall(
                    id="write",
                    name="write_file",
                    arguments={
                        "root": "session",
                        "path": "model.scad",
                        "content": "cube(1);",
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
                    arguments={"root": "session", "path": "model.scad"},
                )
            ],
            finish_reason="tool_calls",
        ),
    ]
    if vision_fallback:
        results.append(
            ProviderResult(
                content=json.dumps(
                    {
                        "verdict": "pass",
                        "findings": ["Geometry matches."],
                        "uncertainty": [],
                    }
                ),
                finish_reason="stop",
            )
        )
    results.append(
        ProviderResult(
            tool_calls=[
                ToolCall(
                    id="review",
                    name="read",
                    arguments={
                        "root": "session",
                        "path": "model.scad",
                    },
                )
            ],
            finish_reason="tool_calls",
        )
    )
    results.append(ProviderResult(content="Fertig.", finish_reason="stop"))
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        results,
        search_policy=WebSearchPolicy.AVAILABLE,
        max_steps=3,
        vision_fallback=vision_fallback,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
        user_image=True,
        user_image_count=8,
        user_content="x" * 5_000,
    )
    generated_id = uuid4()

    class FakeAttachmentService:
        def __init__(self, **_kwargs):
            pass

        async def create_attachment(self, _db, _message, _data, _mime_type, **kwargs):
            return SimpleNamespace(id=generated_id, kind=kwargs["kind"])

        async def discard(self, _attachment, _session_id):
            return None

        async def read_image(self, attachment, _session_id):
            return f"reference-{attachment.position}".encode()

    monkeypatch.setattr(runtime_module, "AttachmentService", FakeAttachmentService)
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            side_effect=[
                ToolResult.json({"written": "model.scad"}),
                ToolResult(
                    json.dumps(
                        {
                            "output": "preview",
                            "previewLayout": ["isometric", "front", "right", "top"],
                            "geometryMetadata": {"triangleCount": 6},
                        }
                    ),
                    {},
                    attachments=(ToolAttachment(b"png", "image/png", "model-views.png"),),
                ),
                ToolResult.json({"content": "cube(1);"}),
            ]
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == (
        ["local-model", "local-model", "vision-model", "local-model", "local-model"]
        if vision_fallback
        else ["local-model", "local-model", "local-model", "local-model"]
    )
    if vision_fallback:
        assert [image.data for image in requests[2]["image_inputs"]] == [
            b"png",
            *(f"reference-{position}".encode() for position in range(7)),
        ]
        verifier_data = json.loads(requests[2]["messages"][1]["content"])
        assert verifier_data["imageOrder"] == {
            "contactSheet": 1,
            "referenceImages": [2, 3, 4, 5, 6, 7, 8],
        }
        assert len(verifier_data["recentRequests"][0]["content"]) == 4_000
        assert verifier_data["previewLayout"] == [
            "isometric",
            "front",
            "right",
            "top",
        ]
        assert verifier_data["geometryMetadata"] == {"triangleCount": 6}
        assert verifier_data["referenceImages"] == [
            {"image": position + 2, "filename": f"reference-{position}.png"}
            for position in range(7)
        ]
    tool_message = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
        and call.args[0].role == "tool"
        and call.args[0].tool_call_id == "render"
    )
    verification = json.loads(tool_message.content)["visualVerification"]
    assert verification == (
        {
            "status": "completed",
            "scope": "visible_geometry",
            "verdict": "pass",
            "findings": ["Geometry matches."],
            "uncertainty": [],
            "referenceImageCount": 7,
        }
        if vision_fallback
        else {"status": "unavailable"}
    )


async def test_private_openscad_source_uses_public_active_vision_model(monkeypatch) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="render",
                        name="render_openscad",
                        arguments={"root": "session", "path": "model.scad"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                content=json.dumps(
                    {
                        "verdict": "pass",
                        "findings": ["None visible."],
                        "uncertainty": [],
                    }
                )
            ),
            ProviderResult(content="Fertig.", finish_reason="stop"),
        ],
        max_steps=2,
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=True,
        vision_fallback=True,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
        user_trust_level=TrustLevel.PRIVATE,
    )

    class FakeAttachmentService:
        def __init__(self, **_kwargs):
            pass

        async def create_attachment(self, _db, _message, _data, _mime_type, **kwargs):
            return SimpleNamespace(id=uuid4(), kind=kwargs["kind"])

        async def discard(self, _attachment, _session_id):
            return None

    monkeypatch.setattr(runtime_module, "AttachmentService", FakeAttachmentService)
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            return_value=ToolResult(
                '{"output":"preview"}',
                {},
                attachments=(ToolAttachment(b"png", "image/png", "model-views.png"),),
            )
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "local-model",
        "local-model",
    ]
    assert requests[1]["target_trust_level"] == TrustLevel.PUBLIC
    assert requests[1]["image_inputs"][0].trust_level == TrustLevel.PRIVATE.value


async def test_failed_active_openscad_verifier_does_not_fallback_and_counts_budget(
    monkeypatch,
) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    db, run, requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [],
        max_steps=2,
        max_model_requests=3,
        search_policy=WebSearchPolicy.AVAILABLE,
        active_supports_image_input=True,
        vision_fallback=True,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
    )
    generated_id = uuid4()

    class FakeAttachmentService:
        def __init__(self, **_kwargs):
            pass

        async def create_attachment(self, _db, _message, _data, _mime_type, **kwargs):
            return SimpleNamespace(id=generated_id, kind=kwargs["kind"])

        async def discard(self, _attachment, _session_id):
            return None

    async def complete(_db, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            return ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="render",
                        name="render_openscad",
                        arguments={"root": "session", "path": "model.scad"},
                    )
                ],
                finish_reason="tool_calls",
            )
        if len(requests) == 2:
            raise RuntimeError("vision unavailable")
        return ProviderResult(content="Fertig.", finish_reason="stop")

    monkeypatch.setattr(runtime_module, "AttachmentService", FakeAttachmentService)
    monkeypatch.setattr(ProviderService, "complete", staticmethod(complete))
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            return_value=ToolResult(
                '{"output":"preview"}',
                {},
                attachments=(ToolAttachment(b"png", "image/png", "model-views.png"),),
            )
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "local-model",
        "local-model",
    ]
    assert requests[1]["tool_choice"] == "none"
    assert requests[2]["required_image_ids"] == (str(generated_id),)
    assert [
        event["modelRequest"]
        for event in events
        if event.get("type") == "run.phase" and event.get("phase") == "waiting_model"
    ] == [1, 2, 3]
    render_message = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
        and call.args[0].role == "tool"
        and call.args[0].tool_call_id == "render"
    )
    assert json.loads(render_message.content)["visualVerification"] == {
        "status": "unavailable"
    }


async def test_each_successful_openscad_preview_runs_a_verifier(monkeypatch) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="render-1",
                        name="render_openscad",
                        arguments={"root": "session", "path": "model.scad"},
                    )
                ]
            ),
            ProviderResult(
                content=json.dumps(
                    {
                        "verdict": "pass",
                        "findings": ["First inspection."],
                        "uncertainty": [],
                    }
                )
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="render-2",
                        name="render_openscad",
                        arguments={"root": "session", "path": "model.scad"},
                    )
                ]
            ),
            ProviderResult(content="Second inspection"),
            ProviderResult(content="Fertig.", finish_reason="stop"),
        ],
        max_steps=3,
        search_policy=WebSearchPolicy.AVAILABLE,
        vision_fallback=True,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
    )

    class FakeAttachmentService:
        def __init__(self, **_kwargs):
            pass

        async def create_attachment(self, _db, _message, _data, _mime_type, **kwargs):
            return SimpleNamespace(id=uuid4(), kind=kwargs["kind"])

        async def discard(self, _attachment, _session_id):
            return None

    monkeypatch.setattr(runtime_module, "AttachmentService", FakeAttachmentService)
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            side_effect=[
                ToolResult(
                    '{"output":"preview"}',
                    {},
                    attachments=(ToolAttachment(b"one", "image/png", "one.png"),),
                ),
                ToolResult(
                    '{"output":"preview"}',
                    {},
                    attachments=(ToolAttachment(b"two", "image/png", "two.png"),),
                ),
            ]
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "vision-model",
        "local-model",
        "vision-model",
        "local-model",
    ]
    assert [request["image_inputs"][0].data for request in (requests[1], requests[3])] == [
        b"one",
        b"two",
    ]
    verifications = [
        json.loads(call.args[0].content)["visualVerification"]
        for call in db.add.call_args_list
        if isinstance(call.args[0], Message)
        and call.args[0].role == "tool"
        and call.args[0].tool_call_id in {"render-1", "render-2"}
    ]
    assert verifications == [
        {
            "status": "completed",
            "scope": "visible_geometry",
            "verdict": "pass",
            "findings": ["First inspection."],
            "uncertainty": [],
            "referenceImageCount": 0,
        },
        {"status": "unavailable"},
    ]


async def test_openscad_stl_and_failed_preview_do_not_run_verifier(monkeypatch) -> None:
    agent_id = uuid4()
    integration = IntegrationInstance(
        name="OpenSCAD",
        integration_type="openscad",
        adapter_type="openscad",
        execution_agent_id=agent_id,
        config={"binary_path": "/usr/bin/openscad"},
    )
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="stl",
                        name="render_openscad",
                        arguments={
                            "root": "session",
                            "path": "model.scad",
                            "output": "stl",
                        },
                    ),
                    ToolCall(
                        id="failed-preview",
                        name="render_openscad",
                        arguments={"root": "session", "path": "model.scad"},
                    ),
                ]
            ),
            ProviderResult(content="Fertig.", finish_reason="stop"),
        ],
        max_steps=2,
        search_policy=WebSearchPolicy.AVAILABLE,
        vision_fallback=True,
        integration_settings=IntegrationSettings(instances=[integration]),
        execution_agent_id=agent_id,
        execution_operations=("integration.openscad.render", "cache.export"),
    )
    monkeypatch.setattr(
        tool_registry,
        "_execute_definition",
        AsyncMock(
            side_effect=[
                ToolResult.json({"output": "stl"}),
                ToolResult.error("tool.executionFailed"),
            ]
        ),
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["model"].model_id for request in requests] == [
        "local-model",
        "local-model",
    ]
    assert all(request["tool_choice"] != "none" for request in requests)


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
        "edit_skill",
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
    monkeypatch, tmp_path, caplog
) -> None:
    caplog.set_level(logging.WARNING, logger=runtime_module.__name__)
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
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="invalid-draft",
                        name="return_skill_draft",
                        arguments={
                            "name": "API checks",
                            "description": "x" * 241,
                            "instructions": "Validate the API response.",
                        },
                    )
                ]
            ),
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
    assert "terms likely to appear in matching requests" in builder_request[
        "messages"
    ][0]["content"]
    assert [message["role"] for message in repair_request["messages"]] == [
        "system",
        "user",
        "user",
    ]
    assert repair_request["messages"][2]["content"].startswith(
        "The output was invalid: description: string_too_long (max_length=240)."
    )
    assert "description 1-240 characters" in repair_request["messages"][2]["content"]
    assert "validation=description: string_too_long (max_length=240)" in caplog.text
    assert "x" * 241 not in caplog.text
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


async def test_skill_editor_sends_current_skill_and_merges_partial_patch(
    monkeypatch, tmp_path
) -> None:
    skill = SkillDefinition(
        name="Songfinder",
        description="Findet Songs.",
        instructions="Suche breit und prüfe Quellen.",
    )
    skills = SkillsSettings(skills=[skill])
    original_execute = ToolRegistry._execute_definition
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="edit-skill",
                        name="edit_skill",
                        arguments={
                            "skill_id": str(skill.id),
                            "brief": "Ändere nur die Einsatzbeschreibung.",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        id="draft",
                        name="return_skill_edit",
                        arguments={
                            "description": "Immer bei einer Song-Suche verwenden."
                        },
                    )
                ]
            ),
            ProviderResult(content="Skill geändert.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        skills=skills,
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
        ),
    )
    monkeypatch.setattr(SettingsStore, "save", AsyncMock())
    monkeypatch.setattr(
        AgentRuntime,
        "_authorize",
        AsyncMock(return_value=PermissionDecision.ALLOW),
    )

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 3
    builder_request = requests[1]
    assert [tool["name"] for tool in builder_request["tools"]] == [
        "return_skill_edit"
    ]
    builder_payload = json.loads(builder_request["messages"][1]["content"])
    assert builder_payload["currentSkill"] == {
        "name": "Songfinder",
        "description": "Findet Songs.",
        "instructions": "Suche breit und prüfe Quellen.",
    }
    assert builder_payload["brief"] == "Ändere nur die Einsatzbeschreibung."
    assert {tool["name"] for tool in requests[2]["tools"]}.isdisjoint(
        {"load_skill", "edit_skill"}
    )
    assert skills.skills == [
        SkillDefinition(
            id=skill.id,
            name=skill.name,
            description="Immer bei einer Song-Suche verwenden.",
            instructions=skill.instructions,
        )
    ]


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
    action = ToolFileAction(
        "write",
        "workspace",
        None,
        "/home/user/project/report.txt",
        "report.txt",
        7,
        execution_agent_id,
        "/home/user/project",
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
                            "path": "report.txt",
                            "content": "report",
                        },
                    )
                ]
            ),
            ProviderResult(content="Fertig", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        tool_result=ToolResult.json(
            {"root": "session", "written": "report.txt"},
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
                    "path": "/home/user/project/report.txt",
                    "filename": "report.txt",
                    "sizeBytes": 7,
                    "executionAgentId": execution_agent_id,
                    "currentDirectory": "/home/user/project",
                }
            ]
        }
    }
    assert "download" not in str(tool_message.provider_data).casefold()


async def test_cancelled_dispatched_workspace_write_stops_without_waiting(
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
    )
    run.execution_agent_id = uuid4()
    run.current_directory_snapshot = "/home/user/project"
    run.execution_operations = ["filesystem.upsert"]
    dispatched = asyncio.Event()

    async def request(
        _agent_id,
        _operation,
        _arguments,
        **_kwargs,
    ):
        dispatched.set()
        await asyncio.Future()

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

    task = asyncio.create_task(AgentRuntime._execute_loop(db, run))
    await dispatched.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not any(
        isinstance(call.args[0], Message) and call.args[0].role == "tool"
        for call in db.add.call_args_list
    )
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


@pytest.mark.parametrize(
    ("model_id", "expected_efforts"),
    [
        ("Qwen3.8-27B-GGUF-UD-Q4_K_XL", ["low", "none"]),
        ("local-model", ["low", "low"]),
    ],
)
async def test_empty_response_repair_disables_supported_reasoning(
    monkeypatch, model_id, expected_efforts
) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(content="", finish_reason="stop"),
            ProviderResult(content="Reparierte Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        reasoning_effort="low",
        model_id=model_id,
    )

    await AgentRuntime._execute_loop(db, run)

    assert [request["reasoning_effort"] for request in requests] == expected_efforts


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
                        {
                            "root": "session",
                            "path": "note.txt",
                            "content": "Hallo",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Die Datei wurde erfolgreich erstellt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
        tool_result=ToolResult.error("tool.invalidArguments", field="path"),
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "tool.invalidArguments"
    assert "Die Datei wurde erfolgreich erstellt." not in {
        message.content for message in _assistant_messages(db)
    }
    assert all(event["type"] != "message.delta" for event in events)


async def test_user_denial_skips_the_remaining_tool_batch(monkeypatch) -> None:
    db, run, _requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        "write-denied",
                        "write_file",
                        {"root": "session", "path": "note.txt", "content": "Hallo"},
                    ),
                    ToolCall(
                        "delete-skipped",
                        "delete_path",
                        {"root": "session", "path": "old.txt", "recursive": False},
                    ),
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Die Aktion wurde abgelehnt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )
    monkeypatch.setattr(
        PermissionPolicy, "evaluate", AsyncMock(return_value=PermissionDecision.ASK)
    )
    request_approval = AsyncMock(return_value=ApprovalResolution(decision="deny"))
    monkeypatch.setattr(
        AgentRuntime,
        "_request_approval",
        request_approval,
    )

    async def execute(_call, context, _tool):
        request = PermissionRequest(
            capability="filesystem.write",
            resource_type="path",
            resource_scope="note.txt",
            action_kind="write_file",
            risk_level="medium",
            title="Datei schreiben?",
            summary="Datei schreiben",
        )
        decision = await context.ask(request)
        assert decision == PermissionDecision.USER_DENIED
        assert await context.ask(request) == PermissionDecision.USER_DENIED
        return ToolResult.error("permission.denied")

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert tool_registry._execute_definition.await_count == 1
    request_approval.assert_awaited_once()
    assert [message.tool_call_id for message in _tool_messages(db)] == [
        "write-denied",
        "delete-skipped",
    ]
    assert [json.loads(message.content)["error"]["code"] for message in _tool_messages(db)] == [
        "permission.userDenied",
        "tool.skippedAfterUserDenial",
    ]
    assert _assistant_messages(db)[-1].content == "Die Aktion wurde abgelehnt."


async def test_user_denial_resolves_required_search(monkeypatch) -> None:
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[_search_call("search-denied", "Aktuelle Frage")],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Die Suche wurde abgelehnt.", finish_reason="stop"),
        ],
    )
    monkeypatch.setattr(
        PermissionPolicy, "evaluate", AsyncMock(return_value=PermissionDecision.ASK)
    )
    monkeypatch.setattr(
        AgentRuntime,
        "_request_approval",
        AsyncMock(return_value=ApprovalResolution(decision="deny")),
    )

    async def execute(_call, context, _tool):
        await context.ask(
            PermissionRequest(
                capability="network.search",
                resource_type="network_service",
                resource_scope="configured_search",
                action_kind="web_search",
                risk_level="medium",
                title="Websuche?",
                summary="Web durchsuchen",
            )
        )
        return ToolResult.error("permission.denied")

    tool_registry._execute_definition.side_effect = execute

    await AgentRuntime._execute_loop(db, run)

    assert [request["tool_choice"] for request in requests] == ["required", "auto"]
    assert json.loads(_tool_messages(db)[0].content)["error"]["code"] == (
        "permission.userDenied"
    )
    assert _assistant_messages(db)[-1].content == "Die Suche wurde abgelehnt."


async def test_successful_mutation_correction_releases_the_final_response(monkeypatch) -> None:
    db, run, _requests, _projections, events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[
                    ToolCall(
                        "write-failed",
                        "write_file",
                        {
                            "root": "session",
                            "path": "wrong.txt",
                            "content": "Hallo",
                        },
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
                        {
                            "root": "session",
                            "path": "note.txt",
                            "content": "Hallo",
                        },
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Die Datei wurde erstellt.", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("tool.invalidArguments", field="path"),
        ToolResult.json({"written": "note.txt"}),
    ]

    await AgentRuntime._execute_loop(db, run)

    persisted = [message.content for message in _assistant_messages(db)]
    assert "Schon erledigt." not in persisted
    assert persisted[-1] == "Die Datei wurde erstellt."
    assert [event.get("delta") for event in events].count("Die Datei wurde erstellt.") == 1


async def test_repeated_failed_tool_call_gets_one_correction_turn(monkeypatch) -> None:
    failed = {"url": "https://example.test/missing"}
    corrected = {"url": "https://example.test/current"}
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[ToolCall("fetch-failed", "webfetch", failed)],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[ToolCall("fetch-repeated", "webfetch", failed)],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[ToolCall("fetch-corrected", "webfetch", corrected)],
                finish_reason="tool_calls",
            ),
            ProviderResult(content="Korrigierte Antwort", finish_reason="stop"),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )
    tool_registry._execute_definition.side_effect = [
        ToolResult.error("webfetch.requestFailed", params={"statusCode": 404}),
        ToolResult.json({"content": "Aktuelle Quelle"}),
    ]

    await AgentRuntime._execute_loop(db, run)

    assert len(requests) == 4
    assert [
        call.args[0].arguments
        for call in tool_registry._execute_definition.await_args_list
    ] == [failed, corrected]
    assert [
        json.loads(message.content).get("error", {}).get("code")
        for message in _tool_messages(db)
    ] == ["webfetch.requestFailed", "agent.repeatedToolCall", None]
    assert _assistant_messages(db)[-1].content == "Korrigierte Antwort"


async def test_second_repeated_failed_tool_call_ends_the_run(monkeypatch) -> None:
    arguments = {"url": "https://example.test/missing"}
    db, run, requests, _projections, _events = await _runtime_setup(
        monkeypatch,
        [
            ProviderResult(
                tool_calls=[ToolCall("fetch-failed", "webfetch", arguments)],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[ToolCall("fetch-repeated", "webfetch", arguments)],
                finish_reason="tool_calls",
            ),
            ProviderResult(
                tool_calls=[ToolCall("fetch-repeated-again", "webfetch", arguments)],
                finish_reason="tool_calls",
            ),
        ],
        search_policy=WebSearchPolicy.AVAILABLE,
    )
    tool_registry._execute_definition.return_value = ToolResult.error(
        "webfetch.requestFailed", params={"statusCode": 404}
    )

    with pytest.raises(AgentRunError) as exc_info:
        await AgentRuntime._execute_loop(db, run)

    assert exc_info.value.code == "agent.repeatedToolCall"
    assert len(requests) == 3
    tool_registry._execute_definition.assert_awaited_once()


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
    db, run, requests, projections, _ = await _runtime_setup(
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
    assert {"web_search", "webfetch", "fetch_transcript"} <= {
        tool["name"] for tool in requests[1]["tools"]
    }
    assert requests[0]["tools"] == requests[1]["tools"] == requests[2]["tools"]
    assert projections[0]["system_prompt"] == projections[1]["system_prompt"]
    assert projections[1]["system_prompt"] == projections[2]["system_prompt"]
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
    assert requests[0]["tools"] == requests[1]["tools"] == requests[2]["tools"]
    assert projections[0]["system_prompt"] == projections[1]["system_prompt"]
    assert projections[1]["system_prompt"] == projections[2]["system_prompt"]
    assert "Research budget limit: 8 calls this turn" in projections[2]["system_prompt"]


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
                        arguments={"root": "session", "path": "info.txt"},
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
    assert "Tool budget: 1 calls" in projections[0][
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
