from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.mcp.adapter import (
    MCP_CREDENTIAL_KIND,
    McpConfig,
    mcp_resource_key,
)
from mato_loop_backend.integrations.mcp.client import McpClient, McpClientError
from mato_loop_backend.integrations.registry import validate_integration
from mato_loop_backend.integrations.service import (
    IntegrationSettingsDocument,
    IntegrationSettingsError,
    _clear_integration_policies,
    _load_integrations,
    _load_tools,
    _save_settings,
    clear_profile_integration_policies,
)
from mato_loop_backend.security import (
    delete_credential,
    load_credential,
    read_credential,
    store_credential,
)
from mato_loop_backend.settings import IntegrationInstance


class McpIntegrationService:
    integration_type = "mcp"
    adapter_type = "mcp_streamable_http"
    config_model = McpConfig
    capability = "mcp.call"
    defaults = None
    token_required = False

    @classmethod
    def _apply_defaults(cls, tools, instance: IntegrationInstance) -> None:
        if cls.defaults is None:
            return
        config = cls.config_model.model_validate(instance.config)
        integration_id = str(instance.id)
        labels = {}
        resource_policies = {}
        for item in config.tools:
            default = cls.defaults.tools.get(item.name)
            if default is None:
                continue
            resource_key = mcp_resource_key(item.name)
            labels[resource_key] = default.label
            resource_policies[resource_key] = {cls.capability: default.policy}
        for permissions in (tools.private_permissions, tools.public_permissions):
            permissions.integration_policies[integration_id] = {
                cls.capability: cls.defaults.server_policy
            }
            if resource_policies:
                permissions.resource_policies[integration_id] = {
                    key: dict(value) for key, value in resource_policies.items()
                }
        if labels:
            tools.resource_labels[integration_id] = labels

    @classmethod
    def _validate_connection(cls, endpoint: str, token: str | None) -> None:
        if cls.token_required and not token:
            raise IntegrationSettingsError("mcp.configurationInvalid")
        try:
            cls.config_model(endpoint=endpoint)
        except ValueError as exc:
            raise IntegrationSettingsError("mcp.configurationInvalid") from exc

    @classmethod
    def _find(cls, settings: IntegrationSettingsDocument, integration_id: UUID) -> IntegrationInstance | None:
        return next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == cls.integration_type
                and item.adapter_type == cls.adapter_type
            ),
            None,
        )

    @staticmethod
    async def _token(
        db: AsyncSession, instance: IntegrationInstance
    ) -> str | None:
        if instance.credential_id is None:
            return None
        try:
            credential = await load_credential(
                db,
                instance.credential_id,
                owner_id=instance.owner_id,
                populate_existing=True,
            )
            if credential is None or credential.kind != MCP_CREDENTIAL_KIND:
                raise ValueError("invalid MCP credential")
            token = read_credential(credential).get("token")
        except Exception as exc:
            raise IntegrationSettingsError("mcp.configurationInvalid") from exc
        if not isinstance(token, str) or not token:
            raise IntegrationSettingsError("mcp.configurationInvalid")
        return token

    @staticmethod
    def _prune_tool_state(tools, integration_id: UUID, resource_keys: set[str]) -> bool:
        key = str(integration_id)
        changed = False
        for permissions in (tools.private_permissions, tools.public_permissions):
            resources = permissions.resource_policies.get(key)
            if resources is None:
                continue
            filtered = {
                resource_key: policies
                for resource_key, policies in resources.items()
                if resource_key in resource_keys
            }
            if filtered != resources:
                changed = True
                if filtered:
                    permissions.resource_policies[key] = filtered
                else:
                    permissions.resource_policies.pop(key, None)
        labels = tools.resource_labels.get(key)
        if labels is not None:
            filtered_labels = {
                resource_key: label
                for resource_key, label in labels.items()
                if resource_key in resource_keys
            }
            if filtered_labels != labels:
                changed = True
                if filtered_labels:
                    tools.resource_labels[key] = filtered_labels
                else:
                    tools.resource_labels.pop(key, None)
        return changed

    @classmethod
    async def _discover(cls, endpoint: str, token: str | None) -> list[dict]:
        tools = await McpClient(endpoint, token).discover_tools()
        try:
            return cls.config_model(endpoint=endpoint, tools=tools).model_dump(mode="json")["tools"]
        except ValueError as exc:
            raise McpClientError("mcp.invalidResponse") from exc

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        endpoint: str,
        token: str | None,
        enabled: bool,
        owner_id: UUID | None,
    ) -> IntegrationInstance:
        cls._validate_connection(endpoint, token)
        tools = await cls._discover(endpoint, token)
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = IntegrationInstance(
            name=name,
            integration_type=cls.integration_type,
            adapter_type=cls.adapter_type,
            enabled=enabled,
            config={"endpoint": endpoint, "tools": tools},
        )
        instance.owner_id = owner_id
        validate_integration(instance)
        if token is not None:
            credential = await store_credential(
                db,
                owner_id=owner_id,
                kind=MCP_CREDENTIAL_KIND,
                label=name,
                payload={"token": token},
            )
            instance.credential_id = credential.id
        settings.instances.append(instance)
        if cls.defaults is not None:
            tool_settings = await _load_tools(db, owner_id, for_update=True)
            cls._apply_defaults(tool_settings, instance)
            await _save_settings(db, tool_settings, owner_id)
        await _save_settings(db, settings, owner_id)
        await db.commit()
        return instance

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        endpoint: str,
        token: str | None,
        remove_token: bool,
        enabled: bool,
        owner_id: UUID | None,
    ) -> IntegrationInstance:
        initial = await _load_integrations(db, owner_id)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("mcp.notFound")
        current.owner_id = owner_id
        discovery_token = token
        if discovery_token is None and not remove_token:
            discovery_token = await cls._token(db, current)
        cls._validate_connection(endpoint, discovery_token)
        tools = await cls._discover(endpoint, discovery_token)
        revision = current.model_dump(mode="json")

        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        if instance.model_dump(mode="json") != revision:
            raise IntegrationSettingsError("integration.changed")
        endpoint_changed = str(cls.config_model.model_validate(instance.config).endpoint) != endpoint
        old_credential_id = instance.credential_id
        instance.name = name
        instance.enabled = enabled
        instance.config = {
            "endpoint": endpoint,
            "tools": tools,
        }
        validate_integration(instance)
        if token is not None:
            credential = await store_credential(
                db,
                owner_id=owner_id,
                kind=MCP_CREDENTIAL_KIND,
                label=name,
                payload={"token": token},
            )
            instance.credential_id = credential.id
        elif remove_token:
            instance.credential_id = None
        if old_credential_id is not None and old_credential_id != instance.credential_id:
            await delete_credential(db, old_credential_id, owner_id=owner_id)
        tool_settings = await _load_tools(db, owner_id, for_update=True)
        if endpoint_changed:
            changed = _clear_integration_policies(tool_settings, integration_id)
            changed = (
                tool_settings.resource_labels.pop(str(integration_id), None) is not None
                or changed
            )
            if changed:
                await _save_settings(db, tool_settings, owner_id)
            await clear_profile_integration_policies(
                db, integration_id, owner_id=owner_id
            )
        else:
            resource_keys = {mcp_resource_key(tool["name"]) for tool in tools}
            if cls._prune_tool_state(tool_settings, integration_id, resource_keys):
                await _save_settings(db, tool_settings, owner_id)
        await _save_settings(db, settings, owner_id)
        await db.commit()
        return instance

    @classmethod
    async def refresh(
        cls, db: AsyncSession, integration_id: UUID, *, owner_id: UUID | None
    ) -> IntegrationInstance:
        initial = await _load_integrations(db, owner_id)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("mcp.notFound")
        current.owner_id = owner_id
        config = cls.config_model.model_validate(current.config)
        token = await cls._token(db, current)
        cls._validate_connection(str(config.endpoint), token)
        tools = await cls._discover(str(config.endpoint), token)
        revision = current.model_dump(mode="json")

        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        if instance.model_dump(mode="json") != revision:
            raise IntegrationSettingsError("integration.changed")
        instance.config["tools"] = tools
        validate_integration(instance)
        resource_keys = {mcp_resource_key(tool["name"]) for tool in tools}
        tool_settings = await _load_tools(db, owner_id, for_update=True)
        if cls._prune_tool_state(tool_settings, integration_id, resource_keys):
            await _save_settings(db, tool_settings, owner_id)
        await _save_settings(db, settings, owner_id)
        await db.commit()
        return instance

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID, *, owner_id: UUID | None
    ) -> None:
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        if instance.credential_id is not None:
            await delete_credential(db, instance.credential_id, owner_id=owner_id)
        tools = await _load_tools(db, owner_id, for_update=True)
        changed = _clear_integration_policies(tools, integration_id)
        changed = tools.resource_labels.pop(str(integration_id), None) is not None or changed
        if changed:
            await _save_settings(db, tools, owner_id)
        await clear_profile_integration_policies(db, integration_id, owner_id=owner_id)
        await _save_settings(db, settings, owner_id)
        await db.commit()

    @classmethod
    async def test(
        cls, db: AsyncSession, integration_id: UUID, *, owner_id: UUID | None
    ) -> None:
        settings = await _load_integrations(db, owner_id)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        instance.owner_id = owner_id
        config = cls.config_model.model_validate(instance.config)
        token = await cls._token(db, instance)
        cls._validate_connection(str(config.endpoint), token)
        await cls._discover(str(config.endpoint), token)
