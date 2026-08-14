from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Annotated, Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from mato_loop_backend.agent.tool_contracts import ToolAction
from mato_loop_backend.execution.client import _relative_path
from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    ToolContribution,
)
from mato_loop_backend.settings import ToolExecutionPolicy


def _camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(part.capitalize() for part in rest)


class _GitModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=_camel,
        populate_by_name=True,
        serialize_by_alias=True,
        extra="forbid",
    )


class GitConfig(_GitModel):
    allow_network: bool = Field(
        default=False,
        validation_alias=AliasChoices("allow_network", "allowNetwork"),
    )


class GitCloneArguments(_GitModel):
    source: str = Field(min_length=1, max_length=4_096, pattern=r"^[^\x00]+$")
    destination: str = Field(min_length=1, max_length=1_000)

    _validate_destination = field_validator("destination")(_relative_path)


class GitStatusArguments(_GitModel):
    pass


GitPath = Annotated[str, Field(min_length=1, max_length=1_000)]


class GitDiffArguments(_GitModel):
    staged: bool = False
    paths: list[GitPath] = Field(default_factory=list, max_length=100)

    _validate_paths = field_validator("paths")(
        lambda values: [_relative_path(value) for value in values]
    )


class GitLogArguments(_GitModel):
    limit: int = Field(default=20, ge=1, le=100)
    paths: list[GitPath] = Field(default_factory=list, max_length=100)

    _validate_paths = field_validator("paths")(
        lambda values: [_relative_path(value) for value in values]
    )


class GitAddArguments(_GitModel):
    paths: list[GitPath] = Field(min_length=1, max_length=100)

    _validate_paths = field_validator("paths")(
        lambda values: [_relative_path(value) for value in values]
    )


class GitCommitArguments(_GitModel):
    message: str = Field(min_length=1, max_length=10_000, pattern=r"^[^\x00]+$")
    expected_index_tree: str = Field(pattern=r"^[0-9a-f]{64}$")


class GitCloneResult(_GitModel):
    output: str = Field(max_length=512 * 1_024)
    truncated: bool


class GitStatusResult(_GitModel):
    output: str = Field(max_length=512 * 1_024)
    truncated: bool
    index_tree: str = Field(pattern=r"^[0-9a-f]{64}$")


class GitDiffResult(_GitModel):
    output: str = Field(max_length=512 * 1_024)
    truncated: bool


class GitLogResult(_GitModel):
    output: str = Field(max_length=512 * 1_024)
    truncated: bool


class GitAddResult(_GitModel):
    index_tree: str = Field(pattern=r"^[0-9a-f]{64}$")


class GitCommitResult(_GitModel):
    commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    tree: str = Field(pattern=r"^[0-9a-f]{7,64}$")


_READ = ToolAction("git.read", "Git lesen", ToolExecutionPolicy.AUTO)
_STAGE = ToolAction(
    "git.stage", "Git-Dateien vormerken", ToolExecutionPolicy.ASK, mutates=True
)
_CLONE = ToolAction(
    "git.clone", "Git-Repository klonen", ToolExecutionPolicy.ASK, mutates=True
)
_COMMIT = ToolAction(
    "git.commit", "Git-Commit erstellen", ToolExecutionPolicy.ASK, mutates=True
)


def _permission(
    capability: str,
    arguments: BaseModel,
    context: Any,
    *,
    title: str,
    summary: str,
    decisions: tuple[str, ...] = ("allow_once", "allow_session", "deny"),
) -> tuple[Any, ...]:
    from mato_loop_backend.agent.permissions import integration_operation_permission

    integration_id = UUID(context.bound_options["integration_id"])
    revision = context.bound_options["binding_revision"]
    material = json.dumps(
        arguments.model_dump(mode="json", by_alias=True),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    request = integration_operation_permission(
        capability=capability,
        integration_id=integration_id,
        binding_revision=revision,
        operation_key=hashlib.sha256(material.encode()).hexdigest(),
        title=title,
        summary=summary,
        resource_display=getattr(arguments, "destination", None),
        action_kind=capability.replace(".", "_"),
        available_decisions=decisions,
    )
    return (
        replace(
            request,
            approval_details={
                "git": arguments.model_dump(mode="json", by_alias=True, exclude_defaults=True)
            },
        ),
    )


def _read_permission(arguments: BaseModel, context: Any) -> tuple[Any, ...]:
    return _permission(
        "git.read",
        arguments,
        context,
        title="Git-Repository lesen?",
        summary="Der Agent möchte Informationen aus diesem Git-Repository lesen.",
    )


def _stage_permission(arguments: BaseModel, context: Any) -> tuple[Any, ...]:
    return _permission(
        "git.stage",
        arguments,
        context,
        title="Dateien für Git vormerken?",
        summary="Der Agent möchte Dateien für den nächsten Git-Commit vormerken.",
    )


def _clone_permission(arguments: BaseModel, context: Any) -> tuple[Any, ...]:
    return _permission(
        "git.clone",
        arguments,
        context,
        title="Git-Repository klonen?",
        summary="Der Agent möchte ein Git-Repository auf diesen Computer klonen.",
        decisions=("allow_once", "deny"),
    )


def _commit_permission(arguments: BaseModel, context: Any) -> tuple[Any, ...]:
    return _permission(
        "git.commit",
        arguments,
        context,
        title="Git-Commit erstellen?",
        summary="Der Agent möchte einen lokalen Git-Commit erstellen.",
        decisions=("allow_once", "deny"),
    )


GIT_TOOLS = (
    ToolContribution(
        name="git_clone",
        description="Clone one Git repository into a new local directory.",
        arguments_model=GitCloneArguments,
        handler=None,
        write=True,
        label="Repository klonen",
        icon="download",
        actions=(_CLONE,),
        authorization_factory=_clone_permission,
        execution_operation="integration.git.clone",
        execution_config_arguments=("allowNetwork",),
        result_model=GitCloneResult,
    ),
    ToolContribution(
        name="git_status",
        description="Show the branch and working-tree status of one Git repository.",
        arguments_model=GitStatusArguments,
        handler=None,
        label="Git-Status anzeigen",
        icon="account_tree",
        actions=(_READ,),
        authorization_factory=_read_permission,
        execution_operation="integration.git.status",
        result_model=GitStatusResult,
    ),
    ToolContribution(
        name="git_diff",
        description="Show unstaged or staged changes in one Git repository.",
        arguments_model=GitDiffArguments,
        handler=None,
        label="Git-Änderungen anzeigen",
        icon="difference",
        actions=(_READ,),
        authorization_factory=_read_permission,
        execution_operation="integration.git.diff",
        result_model=GitDiffResult,
    ),
    ToolContribution(
        name="git_log",
        description="List recent commits in one Git repository.",
        arguments_model=GitLogArguments,
        handler=None,
        label="Git-Verlauf anzeigen",
        icon="history",
        actions=(_READ,),
        authorization_factory=_read_permission,
        execution_operation="integration.git.log",
        result_model=GitLogResult,
    ),
    ToolContribution(
        name="git_add",
        description="Stage selected paths in one Git repository.",
        arguments_model=GitAddArguments,
        handler=None,
        write=True,
        label="Git-Dateien vormerken",
        icon="playlist_add",
        actions=(_STAGE,),
        authorization_factory=_stage_permission,
        execution_operation="integration.git.add",
        result_model=GitAddResult,
    ),
    ToolContribution(
        name="git_commit",
        description="Create one local commit from the staged changes in a Git repository.",
        arguments_model=GitCommitArguments,
        handler=None,
        write=True,
        label="Git-Commit erstellen",
        icon="commit",
        actions=(_COMMIT,),
        authorization_factory=_commit_permission,
        execution_operation="integration.git.commit",
        result_model=GitCommitResult,
    ),
)


GIT_ADAPTER = IntegrationAdapterDefinition(
    integration_type="git",
    adapter_type="git",
    config_model=GitConfig,
    execution_location="agent",
    execution_kind="git",
    execution_tools=GIT_TOOLS,
)


__all__ = [
    "GIT_ADAPTER",
    "GIT_TOOLS",
    "GitAddArguments",
    "GitAddResult",
    "GitCloneArguments",
    "GitCloneResult",
    "GitCommitArguments",
    "GitCommitResult",
    "GitConfig",
    "GitDiffArguments",
    "GitDiffResult",
    "GitLogArguments",
    "GitLogResult",
    "GitStatusArguments",
    "GitStatusResult",
]
