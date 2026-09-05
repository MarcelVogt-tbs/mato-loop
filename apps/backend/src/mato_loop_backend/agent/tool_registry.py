from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.agent.file_roots import (
    internal_file_roots,
    model_file_root_schema,
    model_file_roots,
)
from mato_loop_backend.agent.permissions import (
    PermissionDecision,
    PermissionRequest,
)
from mato_loop_backend.agent.tool_arguments import QuestionArguments
from mato_loop_backend.agent.tool_contracts import ToolAction
from mato_loop_backend.agent.video_analysis import (
    ResolvedVideoModel,
    video_analysis_available,
)
from mato_loop_backend.attachments import AttachmentError
from mato_loop_backend.error_contract import error_payload
from mato_loop_backend.execution import ExecutionClient, ExecutionClientError
from mato_loop_backend.integrations import (
    PdfVisionHandler,
    SearchIntegrationError,
    WebFetchError,
    effective_search_providers,
)
from mato_loop_backend.integrations.contracts import IntegrationResource, ToolContribution
from mato_loop_backend.integrations.registry import (
    effective_integration_contributions,
    installed_integration_tools,
    integration_adapter,
)
from mato_loop_backend.models import ChatSession
from mato_loop_backend.providers.base import ProviderError, ToolCall
from mato_loop_backend.providers.media import available_media_capabilities
from mato_loop_backend.security import load_credential, read_credential
from mato_loop_backend.settings import (
    AgentProfileUnavailable,
    IntegrationInstance,
    IntegrationSettings,
    SkillDefinition,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
    load_agent_profile_tools,
    load_effective_ai,
    load_effective_integrations,
)
from mato_loop_backend.skills import RunSkillState, SkillDraft
from mato_loop_backend.video_processing import VideoAnalysisMode
from mato_loop_backend.workspace import RootFileError, RootFileService

logger = logging.getLogger("mato_loop_backend.agent.tools")

AskHandler = Callable[[PermissionRequest], Awaitable[PermissionDecision]]
SkillDraftHandler = Callable[
    [str, str | None, SkillDefinition | None], Awaitable[SkillDraft]
]
QuestionHandler = Callable[
    [QuestionArguments], Awaitable[dict[str, list[str]] | Literal["rejected"] | None]
]
VideoAnalysisHandler = Callable[
    [bytes, TrustLevel, str | None, VideoAnalysisMode, str | None],
    Awaitable[dict[str, Any]],
]
VideoAnalysisTargetResolver = Callable[[int], ResolvedVideoModel | None]
TaskHandler = Callable[[str], Awaitable["ToolResult"]]
PolicyResolver = Callable[..., ToolExecutionPolicy]
CapabilityResolver = Callable[..., set[str]]


def _model_revision(value: BaseModel) -> str:
    return json.dumps(
        value.model_dump(mode="json"),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def _provider_revision(value: str | None, settings: IntegrationSettings) -> str | None:
    if value is None or ":" not in value:
        return None
    integration_id = value.split(":", 1)[1]
    instance = next(
        (item for item in settings.instances if str(item.id) == integration_id), None
    )
    return _model_revision(instance) if instance is not None else None


@dataclass(slots=True)
class ToolAttachment:
    data: bytes
    mime_type: str
    filename: str
    kind: Literal["image", "audio", "file"] = "image"
    category: Literal["generated", "downloaded"] = "generated"
    duration_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    file_root_id: UUID | None = None
    file_id: UUID | None = None
    file_revision: int | None = None
    file_path: str | None = None


@dataclass(frozen=True, slots=True)
class ToolFileAction:
    operation: Literal["write", "copy", "move"]
    root: Literal["session", "workspace"]
    root_identity: str | None
    path: str
    filename: str
    size_bytes: int
    execution_agent_id: str | None = None
    current_directory: str | None = None


@dataclass(slots=True)
class ToolResult:
    content: str
    metadata: dict[str, Any]
    is_error: bool = False
    attachments: tuple[ToolAttachment, ...] = ()
    file_actions: tuple[ToolFileAction, ...] = ()
    trust_level: TrustLevel | None = None

    @classmethod
    def json(
        cls,
        value: Any,
        *,
        metadata: dict[str, Any] | None = None,
        file_actions: tuple[ToolFileAction, ...] = (),
        trust_level: TrustLevel | None = None,
    ) -> ToolResult:
        return cls(
            json.dumps(model_file_roots(value), ensure_ascii=False),
            metadata or {},
            file_actions=file_actions,
            trust_level=trust_level,
        )

    @classmethod
    def error(
        cls,
        code: str,
        *,
        field: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ToolResult:
        payload = error_payload(
            code, field=field, params=params, fallback_code="tool.executionFailed"
        )
        descriptor = payload["error"]
        result = cls.json(payload, metadata={"errorCode": descriptor["code"]})
        result.is_error = True
        return result


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
    task_handler: TaskHandler | None = None

    async def ask(self, request: PermissionRequest) -> PermissionDecision:
        if self.user_denied:
            return PermissionDecision.USER_DENIED
        decision = await self.ask_handler(request)
        self.user_denied = decision == PermissionDecision.USER_DENIED
        return decision

    async def ask_user(
        self, questions: QuestionArguments
    ) -> dict[str, list[str]] | Literal["rejected"] | None:
        if self.question_handler is None:
            return None
        return await self.question_handler(questions)


TOOL_GROUPS = (
    {"id": "filesystem", "label": "Dateisystem", "icon": "folder"},
    {"id": "process", "label": "Prozesse", "icon": "terminal"},
    {"id": "web", "label": "Web", "icon": "language"},
    {"id": "media", "label": "Medien", "icon": "perm_media"},
    {"id": "sessions", "label": "Sessions", "icon": "forum"},
    {"id": "automations", "label": "Automationen", "icon": "schedule"},
    {"id": "memory", "label": "Memory", "icon": "psychology"},
    {"id": "agent", "label": "Agent", "icon": "smart_toy"},
    {"id": "coding", "label": "Coding", "icon": "code"},
    {"id": "integrations", "label": "Integrationen", "icon": "extension"},
)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[BaseModel]
    handler: Callable[[Any, ToolContext], Awaitable[ToolResult]]
    tool_type: Literal["backend", "computer", "shared"] = "backend"
    requires_search: bool = False
    requires_webfetch: bool = False
    requires_memory: bool = False
    requires_memory_write: bool = False
    required_capability: str | None = None
    required_operation: str | None = None
    group: str = "agent"
    label: str | None = None
    icon: str = "build"
    actions: tuple[ToolAction, ...] = ()
    alternative_action_groups: tuple[frozenset[str], ...] = ()
    instance_argument: str | None = None
    resource_argument: str | None = None
    releases_private_data: bool = False
    authorization_factory: (
        Callable[[Any, ToolContext], tuple[PermissionRequest, ...]] | None
    ) = None
    prompt_fragments: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        schema = self.arguments_model.model_json_schema()
        _remove_schema_titles(schema)
        model_file_root_schema(schema)
        return {"name": self.name, "description": self.description, "parameters": schema}

    def validate_arguments(self, value: object) -> BaseModel:
        return self.arguments_model.model_validate(internal_file_roots(value))

    def actions_satisfied(self, capabilities: set[str]) -> bool:
        declared = {action.capability for action in self.actions}
        alternatives = frozenset().union(*self.alternative_action_groups)
        return (
            capabilities <= declared
            and declared - alternatives <= capabilities
            and all(capabilities & group for group in self.alternative_action_groups)
        )


def _actions_enabled(
    definition: ToolDefinition,
    tool_settings: ToolSettings,
    policy_resolver: PolicyResolver,
) -> bool:
    if not tool_settings.tool_availability.get(definition.name, True):
        return False
    enabled = {
        action.capability
        for action in definition.actions
        if policy_resolver(
            tool_settings,
            action.capability,
            action.default_policy,
            include_scoped=True,
        )
        != ToolExecutionPolicy.DISABLED
    }
    return definition.actions_satisfied(enabled)


def _remove_schema_titles(value: Any) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("title"), str):
            value.pop("title")
        for item in value.values():
            _remove_schema_titles(item)
    elif isinstance(value, list):
        for item in value:
            _remove_schema_titles(item)


@dataclass(frozen=True, slots=True)
class BoundTool:
    definition: ToolDefinition
    source_type: str = "builtin"
    source_id: str | None = None
    source_revision: str | None = None
    integration_revisions: tuple[tuple[str, str], ...] = ()
    selected_options: dict[str, str | None] = field(default_factory=dict)
    default_arguments: dict[str, str] = field(default_factory=dict)

    def schema(self) -> dict[str, Any]:
        schema = self.definition.schema()
        parameters = schema["parameters"]
        instance_argument = self.definition.instance_argument
        if instance_argument is not None and self.integration_revisions:
            parameters["properties"][instance_argument]["enum"] = [
                integration_id for integration_id, _revision in self.integration_revisions
            ]
        if not self.default_arguments:
            return schema
        parameters["required"] = [
            name
            for name in parameters.get("required", [])
            if name not in self.default_arguments
        ]
        return schema


def _tool_prompt_fragments(tools: dict[str, BoundTool]) -> tuple[str, ...]:
    fragments = [
        fragment
        for tool in tools.values()
        for fragment in tool.definition.prompt_fragments
    ]
    if any(
        tools[name].default_arguments
        for name in ("calendar_query_events", "calendar_create_event")
        if name in tools
    ):
        fragments.append(
            "A default calendar is configured. For it, call calendar_query_events or "
            "calendar_create_event directly and omit both integration_id and calendar_key. "
            "For another calendar, include both."
        )
    if any(
        tools[name].default_arguments
        for name in ("tasks_query", "tasks_create", "tasks_create_many")
        if name in tools
    ):
        fragments.append(
            "A default task list is configured. For it, call tasks_query, tasks_create, or "
            "tasks_create_many directly and omit both integration_id and task_list_key. For "
            "another task list, include both."
        )
    if {
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
    } & tools.keys():
        fragments.append(
            "For event mutations, copy eventToken, integrationId, and calendarKey from one returned "
            "event. A mutation succeeds only with a confirming event or deleted=true. Present localTiming "
            "exactly; otherwise convert canonical timestamps once to the system-prompt timezone."
        )
    if {"tasks_update", "tasks_set_completed", "tasks_delete"} & tools.keys():
        fragments.append(
            "For task mutations, copy taskToken, integrationId, and taskListKey from one returned "
            "task. Success requires a confirming task or deleted=true."
        )
    return tuple(fragments)


@dataclass(frozen=True, slots=True)
class EffectiveToolSet:
    registry: ToolRegistry
    tools: dict[str, BoundTool]
    prompt_fragments: tuple[str, ...] = ()

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self.tools.values()]

    def only(self, *names: str) -> EffectiveToolSet:
        selected = {name: self.tools[name] for name in names if name in self.tools}
        return EffectiveToolSet(self.registry, selected, _tool_prompt_fragments(selected))

    def without(self, *names: str) -> EffectiveToolSet:
        excluded = set(names)
        selected = {
            name: tool for name, tool in self.tools.items() if name not in excluded
        }
        return EffectiveToolSet(self.registry, selected, _tool_prompt_fragments(selected))

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        tool = self.tools.get(call.name)
        if tool is None:
            return ToolResult.error("tool.unavailable")
        return await self.registry._execute_definition(call, context, tool)


class ToolRegistry:
    def __init__(
        self,
        definitions: list[ToolDefinition],
        operation_definitions: list[ToolDefinition] | None = None,
        *,
        contributions_resolver: Callable[..., Any] = effective_integration_contributions,
        policy_resolver: PolicyResolver | None = None,
        capability_resolver: CapabilityResolver | None = None,
        allow_handler: Callable[..., Awaitable[bool]] | None = None,
    ) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name in self._definitions:
                raise ValueError(f"duplicate tool: {definition.name}")
            self._definitions[definition.name] = definition
        self._operation_definitions = {
            definition.name: definition for definition in operation_definitions or []
        }
        if self._definitions.keys() & self._operation_definitions.keys():
            raise ValueError("duplicate operation tool")
        self._contributions_resolver = contributions_resolver
        self._policy_resolver = policy_resolver
        self._capability_resolver = capability_resolver
        self._allow_handler = allow_handler

    async def _allow(
        self, context: ToolContext, request: PermissionRequest
    ) -> bool:
        if self._allow_handler is not None:
            return await self._allow_handler(context, request)
        return await context.ask(request) == PermissionDecision.ALLOW

    def _actions_enabled(
        self, definition: ToolDefinition, tool_settings: ToolSettings
    ) -> bool:
        if self._policy_resolver is None:
            raise RuntimeError("tool registry policy resolver is not configured")
        return _actions_enabled(definition, tool_settings, self._policy_resolver)

    def _combined_definitions(
        self, integration_settings: IntegrationSettings
    ) -> tuple[
        dict[str, ToolDefinition],
        dict[
            str,
            tuple[str, str | None, str | None, tuple[tuple[str, str], ...]],
        ],
        tuple[str, ...],
    ]:
        contributions = self._contributions_resolver(integration_settings)
        definitions = dict(self._definitions)
        sources: dict[
            str,
            tuple[str, str | None, str | None, tuple[tuple[str, str], ...]],
        ] = {name: ("builtin", None, None, ()) for name in definitions}
        for contribution in contributions.tools:
            if contribution.name in definitions:
                raise ValueError(f"duplicate contributed tool: {contribution.name}")
            definitions[contribution.name] = self._contributed_definition(contribution)
            source_instance = next(
                (
                    instance
                    for instance in integration_settings.instances
                    if str(instance.id) == contribution.integration_id
                ),
                None,
            )
            sources[contribution.name] = (
                "integration",
                contribution.integration_id,
                _model_revision(source_instance) if source_instance is not None else None,
                tuple(
                    (str(instance.id), _model_revision(instance))
                    for instance in integration_settings.instances
                    if str(instance.id) in contribution.integration_ids
                ),
            )
        attached_fragments = {
            fragment
            for contribution in contributions.tools
            for fragment in contribution.prompt_fragments
        }
        return definitions, sources, tuple(
            fragment
            for fragment in contributions.prompt_fragments
            if fragment not in attached_fragments
        )

    @staticmethod
    def _contributed_definition(contribution: ToolContribution) -> ToolDefinition:
        return ToolDefinition(
            name=contribution.name,
            description=contribution.description,
            arguments_model=contribution.arguments_model,
            handler=contribution.handler,
            tool_type="computer" if contribution.execution_operation else "backend",
            requires_search=contribution.requires_search,
            requires_webfetch=contribution.requires_webfetch,
            requires_memory=contribution.requires_memory,
            requires_memory_write=contribution.requires_memory_write,
            required_capability=contribution.required_capability,
            group=contribution.group,
            label=contribution.label,
            icon=contribution.icon,
            actions=contribution.actions,
            alternative_action_groups=contribution.alternative_action_groups,
            instance_argument=contribution.instance_argument,
            resource_argument=contribution.resource_argument,
            releases_private_data=contribution.releases_private_data,
            authorization_factory=contribution.authorization_factory,
            prompt_fragments=contribution.prompt_fragments,
        )

    def inactive_capability_groups(
        self,
        integration_settings: IntegrationSettings,
        offered_names: set[str] | frozenset[str],
        *,
        webfetch_enabled: bool,
        web_search_enabled: bool,
        memory_enabled: bool,
        memory_write_enabled: bool,
        tool_capabilities: set[str] | frozenset[str],
        tool_settings: ToolSettings,
        execution_operations: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        active_definitions = self._combined_definitions(integration_settings)[0]
        installed_definitions = dict(self._definitions)
        installed_definitions.update(active_definitions)
        installed_definitions.update(
            {
                name: definition
                for name, definition in self._operation_definitions.items()
                if definition.required_operation in execution_operations
            }
        )
        for contribution in installed_integration_tools(integration_settings):
            installed_definitions.setdefault(
                contribution.name, self._contributed_definition(contribution)
            )

        grouped: dict[str, list[str]] = {
            "integrations": [],
            "media": [],
            "web": [],
            "memory": [],
            "settings": [],
            "temporary": [],
        }
        for name, definition in installed_definitions.items():
            if name in offered_names:
                continue
            if definition.group == "integrations" and name not in active_definitions:
                reason = "integrations"
            elif definition.requires_search and not web_search_enabled:
                reason = "web"
            elif definition.requires_webfetch and not webfetch_enabled:
                reason = "web"
            elif definition.requires_memory and not memory_enabled:
                reason = "memory"
            elif definition.requires_memory_write and not memory_write_enabled:
                reason = "memory"
            elif (
                definition.required_capability is not None
                and definition.required_capability not in tool_capabilities
            ):
                if definition.required_capability == "project.coding":
                    continue
                reason = (
                    "media"
                    if definition.required_capability.startswith("media.")
                    else "settings"
                )
            elif (
                definition.required_operation is not None
                and definition.required_operation not in execution_operations
            ):
                reason = "temporary"
            elif not self._actions_enabled(definition, tool_settings):
                reason = (
                    "integrations"
                    if definition.group == "integrations"
                    else "settings"
                )
            else:
                reason = "temporary"
            grouped[reason].append(name)

        templates = {
            "integrations": (
                "Integration tools [{names}]: configure under Integrationen or the agent profile."
            ),
            "media": (
                'Media tools [{names}]: enable them under "Einstellungen > Tools" and configure '
                'a capable model under "Einstellungen > Standardmodelle".'
            ),
            "web": (
                'Web tools [{names}]: enable web access for the session or under '
                '"Einstellungen > Tools".'
            ),
            "memory": (
                'Memory tools [{names}]: enable Memory for the session and its model.'
            ),
            "settings": (
                "Optional tools [{names}]: configure under Tools or the agent profile."
            ),
            "temporary": (
                "Temporarily unavailable tools [{names}]: finish the current required workflow first."
            ),
        }
        return tuple(
            templates[reason].format(names=", ".join(names))
            for reason, names in grouped.items()
            if names
        )

    def available(
        self,
        integration_settings: IntegrationSettings,
        webfetch_enabled: bool = False,
        web_search_enabled: bool = True,
        memory_enabled: bool = False,
        memory_write_enabled: bool = False,
        tool_capabilities: set[str] | frozenset[str] = frozenset(),
        execution_operations: set[str] | frozenset[str] = frozenset(),
    ) -> list[ToolDefinition]:
        return [
            definition
            for definition in (
                *self._definitions.values(),
                *self._operation_definitions.values(),
            )
            if self._is_available(
                definition,
                webfetch_enabled=webfetch_enabled,
                web_search_enabled=web_search_enabled,
                memory_enabled=memory_enabled,
                memory_write_enabled=memory_write_enabled,
                tool_capabilities=tool_capabilities,
                execution_operations=execution_operations,
            )
        ]

    @staticmethod
    def _is_available(
        definition: ToolDefinition,
        *,
        webfetch_enabled: bool,
        web_search_enabled: bool,
        memory_enabled: bool,
        memory_write_enabled: bool,
        tool_capabilities: set[str] | frozenset[str],
        execution_operations: set[str] | frozenset[str] = frozenset(),
    ) -> bool:
        return (
            (not definition.requires_search or web_search_enabled)
            and (not definition.requires_webfetch or webfetch_enabled)
            and (not definition.requires_memory or memory_enabled)
            and (not definition.requires_memory_write or memory_write_enabled)
            and (
                definition.required_capability is None
                or definition.required_capability in tool_capabilities
            )
            and (
                definition.required_operation is None
                or definition.required_operation in execution_operations
            )
        )

    def schemas(
        self,
        integration_settings: IntegrationSettings,
        webfetch_enabled: bool = False,
        web_search_enabled: bool = True,
        memory_enabled: bool = False,
        memory_write_enabled: bool = False,
        tool_capabilities: set[str] | frozenset[str] = frozenset(),
        execution_operations: set[str] | frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        return self.effective(
            integration_settings,
            webfetch_enabled,
            web_search_enabled,
            memory_enabled,
            memory_write_enabled,
            tool_capabilities,
            execution_operations=execution_operations,
        ).schemas()

    def effective(
        self,
        integration_settings: IntegrationSettings,
        webfetch_enabled: bool = False,
        web_search_enabled: bool = True,
        memory_enabled: bool = False,
        memory_write_enabled: bool = False,
        tool_capabilities: set[str] | frozenset[str] = frozenset(),
        tool_settings: ToolSettings | None = None,
        execution_operations: set[str] | frozenset[str] = frozenset(),
    ) -> EffectiveToolSet:
        definitions, sources, prompt_fragments = self._combined_definitions(
            integration_settings
        )
        for name, definition in self._operation_definitions.items():
            if definition.required_operation in execution_operations:
                definitions[name] = definition
                sources[name] = ("builtin", None, None, ())
        available_names = {
            item.name
            for item in definitions.values()
            if self._is_available(
                item,
                webfetch_enabled=webfetch_enabled,
                web_search_enabled=web_search_enabled,
                memory_enabled=memory_enabled,
                memory_write_enabled=memory_write_enabled,
                tool_capabilities=tool_capabilities,
                execution_operations=execution_operations,
            )
            and (tool_settings is None or self._actions_enabled(item, tool_settings))
        }
        search_providers = (
            effective_search_providers(tool_settings, integration_settings)
            if tool_settings is not None
            else None
        )

        def default_arguments(name: str) -> dict[str, str]:
            integration_revisions = sources[name][3]
            if name in {"calendar_query_events", "calendar_create_event"}:
                default_calendar = (
                    tool_settings.default_calendar if tool_settings else None
                )
                if (
                    default_calendar is not None
                    and str(default_calendar.integration_id) in dict(integration_revisions)
                ):
                    return {
                        "integration_id": str(default_calendar.integration_id),
                        "calendar_key": default_calendar.calendar_key,
                    }
            if name in {"tasks_query", "tasks_create", "tasks_create_many"}:
                default_task_list = (
                    tool_settings.default_task_list if tool_settings else None
                )
                if (
                    default_task_list is not None
                    and str(default_task_list.integration_id)
                    in dict(integration_revisions)
                ):
                    return {
                        "integration_id": str(default_task_list.integration_id),
                        "task_list_key": default_task_list.task_list_key,
                    }
            return {}

        tools = {
            name: BoundTool(
                definition,
                *sources[name],
                selected_options=(
                    {
                        "provider": search_providers[0],
                        "fallback_provider": search_providers[1],
                        "provider_revision": _provider_revision(
                            search_providers[0], integration_settings
                        ),
                        "fallback_provider_revision": _provider_revision(
                            search_providers[1], integration_settings
                        ),
                    }
                    if name == "web_search" and search_providers is not None
                    else (
                        {"settings_revision": _model_revision(tool_settings.web_fetch)}
                        if name in {"webfetch", "fetch_transcript"}
                        and tool_settings is not None
                        else {}
                    )
                ),
                default_arguments=default_arguments(name),
            )
            for name, definition in definitions.items()
            if name in available_names
        }
        return EffectiveToolSet(
            self,
            tools,
            (*prompt_fragments, *_tool_prompt_fragments(tools)),
        )

    def catalog(
        self, tool_settings: ToolSettings, integration_settings: IntegrationSettings
    ) -> list[dict[str, Any]]:
        definitions, sources, _prompt_fragments = self._combined_definitions(
            integration_settings
        )
        definitions.update(self._operation_definitions)
        sources.update(
            {name: ("builtin", None, None, ()) for name in self._operation_definitions}
        )
        tools_by_group: dict[str, list[dict[str, Any]]] = {}
        for definition in definitions.values():
            source_type, source_id, _source_revision, _integration_revisions = sources[
                definition.name
            ]
            actions = [
                {
                    "capability": action.capability,
                    "label": action.label,
                    "policy": self._policy_resolver(
                        tool_settings,
                        action.capability,
                        action.default_policy,
                    )
                    .value,
                    "configurable": action.configurable,
                    "allowedPolicies": [
                        policy.value for policy in ToolExecutionPolicy
                    ],
                    "available": True,
                }
                for action in definition.actions
            ]
            tools_by_group.setdefault(definition.group, []).append(
                {
                    "name": definition.name,
                    "label": definition.label or definition.name,
                    "icon": definition.icon,
                    "toolType": definition.tool_type,
                    "source": {
                        "type": source_type,
                        "id": source_id,
                        "ids": [
                            integration_id
                            for integration_id, _revision in _integration_revisions
                        ],
                    },
                    "actions": actions,
                    "available": tool_settings.tool_availability.get(
                        definition.name, True
                    ),
                }
            )
        group_metadata = {item["id"]: item for item in TOOL_GROUPS}
        group_ids = [item["id"] for item in TOOL_GROUPS]
        group_ids.extend(group for group in tools_by_group if group not in group_metadata)
        return [
            {
                **group_metadata.get(
                    group_id,
                    {"id": group_id, "label": group_id, "icon": "extension"},
                ),
                "tools": tools_by_group.get(group_id, []),
            }
            for group_id in group_ids
            if tools_by_group.get(group_id)
        ]

    def action_default(
        self, capability: str, integration_settings: IntegrationSettings | None = None
    ) -> ToolExecutionPolicy:
        definitions = (
            self._combined_definitions(integration_settings)[0]
            if integration_settings is not None
            else {**self._definitions, **self._operation_definitions}
        )
        defaults = {
            action.default_policy
            for definition in definitions.values()
            for action in definition.actions
            if action.capability == capability
        }
        if len(defaults) > 1:
            raise ValueError(f"conflicting defaults for capability: {capability}")
        return next(iter(defaults), ToolExecutionPolicy.ASK)

    def integration_resource_actions(
        self, instance: IntegrationInstance
    ) -> tuple[ToolAction, ...]:
        adapter = integration_adapter(instance.integration_type, instance.adapter_type)
        if adapter is None:
            return ()
        try:
            contributions = adapter.contributions(instance)
        except ValueError:
            return ()
        executable: dict[str, set[ToolAction]] = {}
        for contribution in (*contributions.tools, *adapter.shared_tools):
            if contribution.authorization_factory is None:
                continue
            for action in contribution.actions:
                executable.setdefault(action.capability, set()).add(action)
        if any(
            executable.get(action.capability) != {action}
            for action in adapter.resource_actions
        ):
            return ()
        return adapter.resource_actions

    async def execute(self, call: ToolCall, context: ToolContext) -> ToolResult:
        definition = self._definitions.get(call.name)
        if definition is None:
            return ToolResult.error("tool.unknown")
        return await self._execute_definition(call, context, BoundTool(definition))

    async def _execute_definition(
        self, call: ToolCall, context: ToolContext, tool: BoundTool
    ) -> ToolResult:
        definition = tool.definition
        context.bound_options = dict(tool.selected_options)

        if not context.tool_settings.tool_availability.get(definition.name, True):
            return ToolResult.error("tool.unavailable")

        async def integration_is_current(selected_id: str | None) -> bool:
            if selected_id is None:
                return True
            current_integrations = await load_effective_integrations(
                context.db, context.session.user_id
            )
            current_instance = next(
                (
                    instance
                    for instance in current_integrations.instances
                    if str(instance.id) == selected_id
                ),
                None,
            )
            current_revision = (
                _model_revision(current_instance)
                if current_instance is not None
                else None
            )
            expected_revision = (
                dict(tool.integration_revisions).get(selected_id)
                if tool.integration_revisions
                else tool.source_revision
            )
            return current_revision == expected_revision and any(
                contribution.name == definition.name
                and (
                    contribution.integration_id == selected_id
                    or selected_id in contribution.integration_ids
                )
                for contribution in self._contributions_resolver(
                    current_integrations
                ).tools
            )

        async def resolve_resource(
            selected_id: str, resource_key: str
        ) -> IntegrationResource | None:
            current_integrations = await load_effective_integrations(
                context.db, context.session.user_id
            )
            instance = next(
                (
                    item
                    for item in current_integrations.instances
                    if str(item.id) == selected_id and item.enabled
                ),
                None,
            )
            if instance is None:
                return None
            adapter = integration_adapter(
                instance.integration_type, instance.adapter_type
            )
            if adapter is None or adapter.resource_loader is None:
                return None
            credential = None
            if instance.credential_id is not None:
                record = await load_credential(
                    context.db,
                    instance.credential_id,
                    owner_id=instance.owner_id,
                )
                if record is None:
                    return None
                credential = read_credential(record)
            try:
                resources = await adapter.resources(instance, credential)
            except Exception:
                logger.warning(
                    "Resource discovery for integration %s failed",
                    selected_id,
                    exc_info=True,
                )
                return None
            return next((item for item in resources if item.key == resource_key), None)

        if tool.source_id is not None and not await integration_is_current(tool.source_id):
            return ToolResult.error("tool.unavailable")
        if definition.requires_memory and not context.memory_read_enabled:
            return ToolResult.error("tool.unavailable")
        if definition.requires_memory_write and not context.memory_write_enabled:
            return ToolResult.error("tool.unavailable")
        if definition.required_capability == "project.coding":
            if context.session.project_id is None:
                return ToolResult.error("tool.unavailable")
        elif definition.required_capability is not None:
            try:
                context.tool_settings, _profile = await load_agent_profile_tools(
                    context.db,
                    context.session.user_id,
                    context.agent_profile_id or context.session.agent_profile_id,
                    context.model_trust_level,
                )
            except AgentProfileUnavailable:
                return ToolResult.error("tool.unavailable")
            ai_settings = await load_effective_ai(context.db, context.session.user_id)
            if self._capability_resolver is None:
                raise RuntimeError("tool registry capability resolver is not configured")
            if definition.required_capability not in self._capability_resolver(
                context.tool_settings,
                available_media_capabilities(
                    ai_settings, active_model_id=context.model_id
                ),
                video_available=video_analysis_available(
                    ai_settings, active_model_id=context.model_id
                ),
            ):
                return ToolResult.error("tool.unavailable")
        raw_arguments = call.arguments
        if tool.default_arguments and tool.default_arguments.keys().isdisjoint(raw_arguments):
            raw_arguments = {**tool.default_arguments, **raw_arguments}
        try:
            arguments = definition.validate_arguments(raw_arguments)
        except ValidationError as exc:
            field_name = next(
                (
                    str(part)
                    for error in exc.errors(include_url=False)
                    for part in reversed(error.get("loc") or ())
                    if isinstance(part, str)
                ),
                None,
            )
            return ToolResult.error("tool.invalidArguments", field=field_name)
        selected_integration_id = tool.source_id
        aggregate_integration_ids: tuple[str, ...] = ()
        if definition.instance_argument is not None:
            try:
                selected_integration_id = str(
                    UUID(str(getattr(arguments, definition.instance_argument)))
                )
            except (AttributeError, TypeError, ValueError):
                return ToolResult.error(
                    "tool.invalidArguments", field=definition.instance_argument
                )
            if selected_integration_id not in dict(tool.integration_revisions):
                return ToolResult.error("tool.unavailable")
        elif tool.integration_revisions:
            aggregate_integration_ids = tuple(
                integration_id for integration_id, _revision in tool.integration_revisions
            )
            if not all(
                [
                    await integration_is_current(integration_id)
                    for integration_id in aggregate_integration_ids
                ]
            ):
                return ToolResult.error("tool.unavailable")
            context.bound_options["integration_ids"] = aggregate_integration_ids
        if definition.instance_argument is not None and not await integration_is_current(
            selected_integration_id
        ):
            return ToolResult.error("tool.unavailable")
        if selected_integration_id is not None:
            context.bound_options["integration_id"] = selected_integration_id
            context.bound_options["binding_revision"] = (
                dict(tool.integration_revisions).get(selected_integration_id)
                if tool.integration_revisions
                else tool.source_revision
            )
        try:
            if definition.actions and (
                selected_integration_id is not None or aggregate_integration_ids
            ):
                if definition.authorization_factory is None:
                    return ToolResult.error("tool.unavailable")
                resource_key = None
                if definition.resource_argument is not None:
                    resource_key = getattr(arguments, definition.resource_argument, None)
                    if not isinstance(resource_key, str) or not resource_key:
                        return ToolResult.error(
                            "tool.invalidArguments", field=definition.resource_argument
                        )
                requests = definition.authorization_factory(arguments, context)
                if selected_integration_id is not None:
                    requests = tuple(
                        replace(
                            request,
                            policy_integration_id=(
                                UUID(selected_integration_id)
                                if request.use_integration_policy
                                else None
                            ),
                            policy_resource_key=(
                                resource_key
                                if request.use_integration_policy
                                and definition.resource_argument is not None
                                else request.policy_resource_key
                            ),
                            policy_hard_unavailable_capabilities=frozenset(),
                        )
                        for request in requests
                    )
                declared = {action.capability for action in definition.actions}
                if not definition.actions_satisfied(
                    {request.capability for request in requests}
                ):
                    return ToolResult.error("tool.unavailable")
                for request in requests:
                    if not await self._allow(context, request):
                        return ToolResult.error("permission.denied")
                if selected_integration_id is not None:
                    if not await integration_is_current(selected_integration_id):
                        return ToolResult.error("tool.unavailable")
                elif not all(
                    [
                        await integration_is_current(integration_id)
                        for integration_id in aggregate_integration_ids
                    ]
                ):
                    return ToolResult.error("tool.unavailable")
                if resource_key is not None:
                    resource = await resolve_resource(
                        selected_integration_id, resource_key
                    )
                    if (
                        resource is None
                        or (
                            resource.applicable_capabilities is not None
                            and not declared <= resource.applicable_capabilities
                        )
                        or declared & resource.hard_unavailable_capabilities
                    ):
                        return ToolResult.error("tool.unavailable")
                    context.bound_resource = resource
            result = await definition.handler(arguments, context)
            if definition.releases_private_data and not result.is_error:
                result.metadata["privateIntegrationData"] = True
            return result
        except (
            AttachmentError,
            ExecutionClientError,
            RootFileError,
            SearchIntegrationError,
            WebFetchError,
        ) as exc:
            return ToolResult.error(
                exc.code,
                params=exc.params if isinstance(exc, (WebFetchError, ExecutionClientError)) else None,
            )
        except ProviderError as exc:
            return ToolResult.error(exc.public_code, params=exc.params)
        except SQLAlchemyError:
            raise
        except Exception:
            logger.exception("Tool %s failed", call.name)
            return ToolResult.error("tool.executionFailed")
