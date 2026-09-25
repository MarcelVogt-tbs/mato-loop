from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from mato_loop_backend.integrations.contracts import (
    IntegrationAdapterDefinition,
    IntegrationContributions,
    IntegrationResource,
    ToolContribution,
)
from mato_loop_backend.integrations.mcp.client import McpClient, McpClientError
from mato_loop_backend.security import load_credential, read_credential
from mato_loop_backend.settings import IntegrationInstance, ToolExecutionPolicy
from mato_loop_backend.tools.actions import ToolAction

MCP_CREDENTIAL_KIND = "mcp.bearer"
MCP_ACTION = ToolAction("mcp.call", "MCP-Tool aufrufen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK)
MAX_MCP_SCHEMA_BYTES = 64 * 1024
MAX_MCP_SCHEMA_DEPTH = 64


def _validate_schema_value(value: Any, depth: int = 0) -> None:
    if depth > MAX_MCP_SCHEMA_DEPTH:
        raise ValueError("MCP schema exceeds maximum depth")
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None and (
            not isinstance(reference, str) or not reference.startswith("#")
        ):
            raise ValueError("MCP schemas may only contain fragment references")
        for item in value.values():
            _validate_schema_value(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_schema_value(item, depth + 1)


def validate_mcp_schema(value: dict[str, Any], *, require_object: bool) -> dict[str, Any]:
    _validate_schema_value(value)
    try:
        size = len(
            json.dumps(
                value, ensure_ascii=True, separators=(",", ":"), allow_nan=False
            ).encode()
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("MCP schema must be finite JSON") from exc
    if size > MAX_MCP_SCHEMA_BYTES:
        raise ValueError("MCP schema exceeds maximum size")
    try:
        validator_for(value).check_schema(value)
    except SchemaError as exc:
        raise ValueError("MCP schema is invalid") from exc
    if require_object and value.get("type") not in (None, "object"):
        raise ValueError("MCP tool input schema must describe an object")
    return value


class McpToolAnnotations(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readOnlyHint: bool | None = None
    destructiveHint: bool | None = None
    idempotentHint: bool | None = None
    openWorldHint: bool | None = None


class McpCatalogTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=256)
    title: str | None = Field(default=None, max_length=160)
    description: str = Field(default="", max_length=4_000)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: McpToolAnnotations = Field(default_factory=McpToolAnnotations)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("MCP tool names must not have surrounding whitespace")
        return value

    @field_validator("input_schema")
    @classmethod
    def validate_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_mcp_schema(value, require_object=True)

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        return (
            validate_mcp_schema(value, require_object=False) if value is not None else None
        )


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: HttpUrl
    tools: list[McpCatalogTool] = Field(default_factory=list, max_length=100)

    @field_validator("tools")
    @classmethod
    def unique_tools(cls, value: list[McpCatalogTool]) -> list[McpCatalogTool]:
        if len({tool.name for tool in value}) != len(value):
            raise ValueError("MCP tool names must be unique")
        return value

    @model_validator(mode="after")
    def endpoint_without_query(self) -> McpConfig:
        if self.endpoint.query is not None:
            raise ValueError("MCP endpoint must not contain a query")
        return self


def mcp_resource_key(tool_name: str) -> str:
    return f"tool:{hashlib.sha256(tool_name.encode()).hexdigest()}"


def mcp_model_tool_name(instance_id: Any, tool_name: str) -> str:
    digest = hashlib.sha256(tool_name.encode()).hexdigest()[:12]
    return f"mcp_{instance_id.hex}_{digest}"


async def _credential(context: Any, instance: IntegrationInstance) -> str | None:
    if instance.credential_id is None:
        return None
    try:
        record = await load_credential(
            context.db, instance.credential_id, owner_id=instance.owner_id
        )
        if record is None or record.kind != MCP_CREDENTIAL_KIND:
            raise ValueError("invalid MCP credential")
        token = read_credential(record).get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("invalid MCP token")
    except Exception as exc:
        if isinstance(exc, McpClientError):
            raise
        raise McpClientError("mcp.configurationInvalid") from exc
    if not token:
        raise McpClientError("mcp.configurationInvalid")
    return token


def mcp_tool_contribution(
    instance: IntegrationInstance,
    item: McpCatalogTool,
    *,
    action: ToolAction = MCP_ACTION,
    label: str | None = None,
    icon: str = "hub",
    approval_title: str = "MCP-Tool aufrufen?",
    approval_summary: str = "Der Agent möchte ein Tool dieses MCP-Servers aufrufen.",
) -> ToolContribution:
    resource_key = mcp_resource_key(item.name)

    async def call(arguments: dict[str, Any], context: Any) -> Any:
        from mato_loop_backend.tools.contracts import ToolResult

        current = next(
            (
                candidate
                for candidate in context.integration_settings.instances
                if candidate.id == instance.id
            ),
            None,
        )
        if current is None:
            return ToolResult.error("tool.unavailable")
        try:
            config = McpConfig.model_validate(current.config)
            payload, is_error = await McpClient(
                str(config.endpoint), await _credential(context, current)
            ).call_tool(item.name, arguments, item.output_schema)
        except McpClientError as exc:
            return ToolResult.error(exc.code)
        result = ToolResult.raw_json(payload)
        result.is_error = is_error
        return result

    def authorize(_arguments: Any, context: Any) -> tuple[Any, ...]:
        from mato_loop_backend.permissions.policy import integration_operation_permission

        binding_revision = json.dumps(
            {
                "sha256": hashlib.sha256(
                    context.bound_options["binding_revision"].encode()
                ).hexdigest()
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return (
            integration_operation_permission(
                capability=action.capability,
                integration_id=instance.id,
                binding_revision=binding_revision,
                operation_key=f"call:{hashlib.sha256(item.name.encode()).hexdigest()[:16]}",
                resource_key=resource_key,
                resource_display=f'{label or item.name} · "{instance.name}"',
                title=approval_title,
                summary=approval_summary,
            ),
        )

    return ToolContribution(
        name=mcp_model_tool_name(instance.id, item.name),
        description=item.description or f"Call the {item.name} tool on the configured MCP server.",
        arguments_model=None,
        arguments_schema=item.input_schema,
        handler=call,
        group="integrations",
        label=label or item.title or item.name,
        icon=icon,
        actions=(action,),
        integration_id=str(instance.id),
        policy_resource_key=resource_key,
        authorization_factory=authorize,
        releases_private_data=True,
    )


def mcp_contributions(
    instance: IntegrationInstance,
    *,
    action: ToolAction = MCP_ACTION,
    labels: Mapping[str, str] | None = None,
    icon: str = "hub",
    approval_title: str = "MCP-Tool aufrufen?",
    approval_summary: str = "Der Agent möchte ein Tool dieses MCP-Servers aufrufen.",
) -> IntegrationContributions:
    config = McpConfig.model_validate(instance.config)
    labels = labels or {}
    return IntegrationContributions(
        tools=tuple(
            mcp_tool_contribution(
                instance,
                item,
                action=action,
                label=labels.get(item.name),
                icon=icon,
                approval_title=approval_title,
                approval_summary=approval_summary,
            )
            for item in config.tools
        )
    )


async def mcp_resources(
    instance: IntegrationInstance,
    _credential: dict[str, Any] | None,
    *,
    labels: Mapping[str, str] | None = None,
) -> tuple[IntegrationResource, ...]:
    config = McpConfig.model_validate(instance.config)
    labels = labels or {}
    return tuple(
        IntegrationResource(
            key=mcp_resource_key(tool.name),
            label=labels.get(tool.name, tool.name[:160]),
        )
        for tool in config.tools
    )


def _contributions(instance: IntegrationInstance) -> IntegrationContributions:
    return mcp_contributions(instance)


async def _resources(
    instance: IntegrationInstance, credential: dict[str, Any] | None
) -> tuple[IntegrationResource, ...]:
    return await mcp_resources(instance, credential)


MCP_ADAPTER = IntegrationAdapterDefinition(
    integration_type="mcp",
    adapter_type="mcp_streamable_http",
    config_model=McpConfig,
    contribution_factory=_contributions,
    resource_actions=(MCP_ACTION,),
    resource_loader=_resources,
    catalog_visible=False,
)
