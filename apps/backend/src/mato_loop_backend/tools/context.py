from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.video_analysis import ResolvedVideoModel
from mato_loop_backend.execution import ExecutionClient
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.integrations.contracts import IntegrationResource
from mato_loop_backend.media.video_processing import VideoAnalysisMode
from mato_loop_backend.models import ChatSession
from mato_loop_backend.permissions.policy import PermissionDecision, PermissionRequest
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    AgentProfileUnavailable,
    IntegrationSettings,
    SkillDefinition,
    ToolSettings,
    TrustLevel,
    load_agent_profile,
    load_effective_integrations,
)
from mato_loop_backend.settings.effective import filter_agent_profile_integrations
from mato_loop_backend.skills import RunSkillState, SkillDraft
from mato_loop_backend.tools.arguments import (
    ProtectedQuestionResolution,
    QuestionRequestArguments,
    TaskArguments,
)
from mato_loop_backend.web.fetch import PdfVisionHandler


class AskHandler(Protocol):
    def __call__(
        self, request: PermissionRequest, *, check_only: bool = False,
    ) -> Awaitable[PermissionDecision]: ...


SkillDraftHandler = Callable[
    [str, str | None, SkillDefinition | None], Awaitable[SkillDraft]
]


QuestionHandler = Callable[
    [QuestionRequestArguments],
    Awaitable[
        dict[str, list[str]] | ProtectedQuestionResolution | Literal["rejected"] | None
    ],
]


VideoAnalysisHandler = Callable[
    [bytes, TrustLevel, str | None, VideoAnalysisMode, str | None],
    Awaitable[dict[str, Any]],
]


VideoAnalysisTargetResolver = Callable[[int], ResolvedVideoModel | None]


if TYPE_CHECKING:
    from mato_loop_backend.tools.contracts import ToolResult

TaskHandler = Callable[[TaskArguments], Awaitable["ToolResult"]]


ArgumentsEditedHandler = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class ToolContext:
    db: AsyncSession
    run_id: UUID
    session: ChatSession
    tool_call: ToolCall
    root_files: RootFileService
    integration_settings: IntegrationSettings
    ask_handler: AskHandler
    question_handler: QuestionHandler | None = None
    tool_settings: ToolSettings = field(default_factory=ToolSettings)
    pdf_vision_handler: PdfVisionHandler | None = None
    video_analysis_handler: VideoAnalysisHandler | None = None
    video_analysis_target: VideoAnalysisTargetResolver | None = None
    assistant_message_id: UUID | None = None
    memory_read_enabled: bool = False
    memory_write_enabled: bool = False
    memory_trust_level: TrustLevel = TrustLevel.PUBLIC
    memory_write_trust_level: TrustLevel = TrustLevel.PUBLIC
    excluded_memory_content_hashes: frozenset[str] = frozenset()
    bound_options: dict[str, Any] = field(default_factory=dict)
    model_id: UUID | None = None
    model_trust_level: TrustLevel = TrustLevel.PUBLIC
    agent_persona_id: UUID | None = None
    agent_profile_id: UUID | None = None
    bound_resource: IntegrationResource | None = None
    skill_state: RunSkillState = field(default_factory=RunSkillState)
    skill_draft_handler: SkillDraftHandler | None = None
    approval_resolution: dict[str, str] = field(default_factory=dict)
    user_denied: bool = False
    execution_client: ExecutionClient | None = None
    session_read_cursors: dict[UUID, UUID | None] = field(default_factory=dict)
    one_time_secrets: dict[UUID, str] = field(default_factory=dict)
    task_handler: TaskHandler | None = None
    arguments_edited_handler: ArgumentsEditedHandler | None = None

    async def current_integrations(self) -> IntegrationSettings:
        from mato_loop_backend.agent.subagents import integration_ceiling_ids

        settings = await load_effective_integrations(self.db, self.session.user_id)
        profile = self.tool_settings._agent_profile
        profile_id = self.agent_profile_id or self.session.agent_profile_id
        if profile_id is not None:
            try:
                profile = await load_agent_profile(self.db, self.session.user_id, profile_id)
            except AgentProfileUnavailable:
                return filter_agent_profile_integrations(settings, None, allowed_ids=())
        settings = filter_agent_profile_integrations(
            settings, profile,
            allowed_ids=(
                () if self.tool_settings._agent_profile_unavailable
                else integration_ceiling_ids(self.tool_settings._delegation_ceiling)
            ),
        )
        if any(item.adapter_type == "browser" for item in settings.instances):
            from mato_loop_backend.integrations.browser.adapter import (
                attached_browser_integration_id,
            )

            integration_id = attached_browser_integration_id(
                self.session.id,
                self.execution_client.agent_id if self.execution_client else None,
                self.session.user_id,
            )
            for item in settings.instances:
                if item.adapter_type == "browser":
                    item._browser_attached = item.id == integration_id
                    item._execution_operations = self.execution_client.operations if self.execution_client else frozenset()
                    if self.execution_client is None or item.execution_agent_id != self.execution_client.agent_id:
                        item.enabled = False
        return settings

    async def ask(self, request: PermissionRequest) -> PermissionDecision:
        if self.user_denied:
            return PermissionDecision.USER_DENIED
        decision = await self.ask_handler(request)
        self.user_denied = decision == PermissionDecision.USER_DENIED
        return decision

    async def ask_user(
        self, questions: QuestionRequestArguments
    ) -> (
        dict[str, list[str]]
        | ProtectedQuestionResolution
        | Literal["rejected"]
        | None
    ):
        if self.question_handler is None:
            return None
        return await self.question_handler(questions)
