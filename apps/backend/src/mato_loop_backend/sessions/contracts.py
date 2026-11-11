from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictInt, model_validator

from mato_loop_backend.execution.paths import is_absolute_path
from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.models import MemoryMode, WebSearchPolicy
from mato_loop_backend.settings.models import ReasoningEffort, TrustLevel
from mato_loop_backend.settings.session_profiles import SessionProfileSnapshot


class ControlCenterFileTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["session", "filesystem"]
    path: str = Field(min_length=1, max_length=4_096)
    name: str = Field(min_length=1, max_length=255)
    root_identity: str | None = Field(default=None, min_length=36, max_length=36)
    execution_agent_id: str | None = Field(default=None, min_length=36, max_length=36)
    current_directory: str | None = Field(default=None, min_length=1, max_length=4_096)
    read_only: bool = False
    file_id: str | None = Field(default=None, min_length=36, max_length=36)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_target(self) -> ControlCenterFileTarget:
        if self.revision is not None or self.file_id is not None:
            if (
                self.scope != "session" or not self.read_only
                or self.revision is None or self.file_id is None
                or str(UUID(self.file_id)) != self.file_id
            ):
                raise ValueError("historical targets require a read-only session file identity and revision")
        if self.scope == "session":
            service = RootFileService(UUID(int=0))
            try:
                valid_root = (
                    self.root_identity is not None
                    and str(UUID(self.root_identity)) == self.root_identity
                )
            except ValueError:
                valid_root = False
            if (
                service.normalize_relative_path(self.path) != self.path
                or self.path == "."
                or not valid_root
            ):
                raise ValueError("session file targets require a normalized file path and root")
            if self.execution_agent_id is not None or self.current_directory is not None:
                raise ValueError("session file targets cannot contain computer fields")
            return self
        if (
            self.root_identity is not None
            or self.execution_agent_id is None
            or self.current_directory is None
            or not is_absolute_path(self.path)
            or not is_absolute_path(self.current_directory)
        ):
            raise ValueError("computer file targets require canonical absolute context")
        try:
            if str(UUID(self.execution_agent_id)) != self.execution_agent_id:
                raise ValueError("computer ID must be canonical")
        except ValueError as exc:
            raise ValueError("computer ID must be canonical") from exc
        return self


class ControlCenterTabState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tab_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        pattern=r"^(?:[a-z][a-z0-9_-]*|file-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
    )
    kind: Literal["app", "file"] = "app"
    app_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    pinned: bool
    file_target: ControlCenterFileTarget | None = None

    @model_validator(mode="after")
    def validate_tab(self) -> ControlCenterTabState:
        if self.tab_id is None:
            self.tab_id = self.app_id
        if self.kind == "app":
            if self.tab_id != self.app_id or self.file_target is not None:
                raise ValueError("app tabs must use their app ID and cannot contain a file")
            return self
        if (
            self.app_id != "file-viewer"
            or not self.tab_id.startswith("file-")
            or self.file_target is None
        ):
            raise ValueError("file tabs require an opaque file tab ID and target")
        try:
            valid_id = f"file-{UUID(self.tab_id.removeprefix('file-'))}" == self.tab_id
        except ValueError:
            valid_id = False
        if not valid_id:
            raise ValueError("file tabs require a canonical UUID tab ID")
        return self


class ControlCenterFileView(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scope: Literal["session", "filesystem"]
    selected_path: str = Field(min_length=1, max_length=1_000)
    selected_kind: Literal["file", "directory"]
    viewed_file_path: str | None = Field(default=None, min_length=1, max_length=1_000)
    root_identity: str | None = Field(default=None, min_length=1, max_length=1_000)
    execution_agent_id: str | None = Field(default=None, min_length=36, max_length=36)
    current_directory: str | None = Field(default=None, min_length=1, max_length=4_096)

    @model_validator(mode="after")
    def validate_view(self) -> ControlCenterFileView:
        service = RootFileService(UUID(int=0))
        if self.scope == "session":
            if service.normalize_relative_path(self.selected_path) != self.selected_path:
                raise ValueError("selected path must be normalized")
            if self.selected_path == "." and self.selected_kind != "directory":
                raise ValueError("the root path must be a directory")
        elif not is_absolute_path(self.selected_path):
            raise ValueError("filesystem paths must be absolute")
        if self.viewed_file_path is not None:
            if (
                self.selected_kind != "file"
                or self.viewed_file_path == "."
                or self.viewed_file_path != self.selected_path
            ):
                raise ValueError("viewed file path must match the selected file")
        if self.scope == "session":
            if self.root_identity is None:
                return self
            if self.execution_agent_id is not None or self.current_directory is not None:
                raise ValueError("session views cannot contain workspace snapshot fields")
            return self
        if self.root_identity is not None:
            raise ValueError("filesystem views cannot contain root identity")
        if self.execution_agent_id is None or self.current_directory is None:
            raise ValueError("historical workspace views require snapshot fields")
        assert self.execution_agent_id is not None
        try:
            valid_id = str(UUID(self.execution_agent_id)) == self.execution_agent_id
        except ValueError:
            valid_id = False
        if not valid_id or not is_absolute_path(self.current_directory):
            raise ValueError("workspace snapshot must use canonical absolute identifiers")
        return self


class ControlCenterFileViews(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    session: ControlCenterFileView | None = None
    filesystem: ControlCenterFileView | None = None

    @model_validator(mode="after")
    def validate_scopes(self) -> ControlCenterFileViews:
        if self.session is not None and self.session.scope != "session":
            raise ValueError("session file view must use session scope")
        if self.filesystem is not None and self.filesystem.scope != "filesystem":
            raise ValueError("filesystem file view must use filesystem scope")
        return self


class ControlCenterState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tabs: list[ControlCenterTabState] = Field(max_length=21)
    active_app_id: str | None = Field(max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    open: bool = False
    file_view: ControlCenterFileView | None = None
    file_views: ControlCenterFileViews = Field(default_factory=ControlCenterFileViews)

    @model_validator(mode="after")
    def validate_tabs(self) -> ControlCenterState:
        session_tabs = [tab for tab in self.tabs if tab.app_id == "session"]
        if session_tabs:
            session_tab = session_tabs[0]
            if session_tab.kind != "app":
                raise ValueError("the Session tab must be an app")
            self.tabs = [session_tab, *[tab for tab in self.tabs if tab is not session_tab]]
        else:
            self.tabs.insert(0, ControlCenterTabState(app_id="session", pinned=False))
        if len(self.tabs) > 21:
            raise ValueError("control center supports at most 21 tabs")
        if self.active_app_id is None:
            self.active_app_id = "session"
        tab_ids = [tab.tab_id for tab in self.tabs]
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError("control center tab IDs must be unique")
        if self.active_app_id not in tab_ids:
            raise ValueError("active ID must identify a tab")
        return self


def default_control_center_state() -> ControlCenterState:
    return ControlCenterState(
        tabs=[
            ControlCenterTabState(app_id="session", pinned=False),
            ControlCenterTabState(app_id="files", pinned=False),
        ],
        active_app_id="session",
    )


class ActiveFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    root: Literal["session", "workspace"]
    root_identity: str | None = Field(
        default=None,
        min_length=36,
        max_length=36,
        validation_alias=AliasChoices("root_identity", "rootIdentity"),
    )
    execution_agent_id: UUID | None = Field(
        default=None,
        strict=False,
        validation_alias=AliasChoices("execution_agent_id", "executionAgentId"),
    )
    path: str = Field(min_length=1, max_length=4_096)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dirty: bool


class SessionOptionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID | None = None
    session_template_id: UUID | None = None
    session_profile: SessionProfileSnapshot | None = None
    model_id: UUID | None = None
    agent_profile_id: UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("agentProfileId", "agent_profile_id"),
    )
    reasoning_effort: ReasoningEffort | None = None
    memory_mode: MemoryMode | None = None


class SessionCreateRequest(SessionOptionsRequest):
    trust_level: TrustLevel | None = None
    content: str | None = Field(max_length=200_000)
    voice_attachment_indices: list[StrictInt] = Field(default_factory=list, alias="voiceAttachmentIndices")
    run_id: UUID | None = None
    memory_mode: MemoryMode = MemoryMode.FULL
    web_search_policy: WebSearchPolicy = WebSearchPolicy.AVAILABLE
    preferred_execution_agent_id: UUID | None = None
    current_directory: str | None = Field(default=None, max_length=4_096)
    workspace_mode: Literal["local", "worktree"] | None = None
    workspace_path: str | None = Field(default=None, max_length=4096)
    control_center: ControlCenterState = Field(default_factory=default_control_center_state)
    active_file: ActiveFileRequest | None = Field(
        default=None, validation_alias=AliasChoices("active_file", "activeFile")
    )
