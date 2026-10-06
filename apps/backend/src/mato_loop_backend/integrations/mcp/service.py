from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.mcp.adapter import (
    MCP_CREDENTIAL_KIND,
    McpConfig,
    mcp_resource_key,
)
from mato_loop_backend.integrations.mcp.client import McpClient, McpClientError
from mato_loop_backend.integrations.mcp.oauth import (
    OAUTH_CREDENTIAL_KIND,
    connection_token,
    mark_authorization_required,
    oauth_status,
    rebind_oauth_endpoint,
)
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
    store_credential,
)
from mato_loop_backend.settings import IntegrationInstance, ToolSettings


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
        for permissions in ((tools.private_permissions, tools.public_permissions) if isinstance(tools, ToolSettings) else ()):
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
    def _validate_connection(cls, endpoint: str, token: str | None) -> str:
        if cls.token_required and not token:
            raise IntegrationSettingsError("mcp.configurationInvalid")
        try:
            return str(cls.config_model(endpoint=endpoint).endpoint)
        except ValueError as exc:
            raise IntegrationSettingsError("mcp.configurationInvalid") from exc

    @classmethod
    def _find(
        cls, settings: IntegrationSettingsDocument, integration_id: UUID
    ) -> IntegrationInstance | None:
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
    def _prune_tool_state(tools, integration_id: UUID, resource_keys: set[str]) -> bool:
        key = str(integration_id)
        changed = False
        for permissions in ((tools.private_permissions, tools.public_permissions) if isinstance(tools, ToolSettings) else ()):
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
    async def _discover(
        cls, endpoint: str, token: str | None, *, authentication_failed=None
    ) -> tuple[str, list[dict]]:
        client = McpClient(
            endpoint, token, authentication_failed=authentication_failed
        )
        tools = await client.discover_tools()
        try:
            config = cls.config_model(endpoint=client.endpoint, tools=tools)
            return str(config.endpoint), config.model_dump(mode="json")["tools"]
        except ValueError as exc:
            raise McpClientError("mcp.invalidResponse") from exc

    @classmethod
    async def _discover_instance(cls, db, instance):
        config = cls.config_model.model_validate(instance.config)
        token = await connection_token(db, instance)
        cls._validate_connection(str(config.endpoint), token)
        return await cls._discover(
            str(config.endpoint),
            token,
            authentication_failed=(lambda: mark_authorization_required(instance, token))
            if config.oauth
            else None,
        )

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
        endpoint = cls._validate_connection(endpoint, token)
        endpoint, tools = await cls._discover(endpoint, token)
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
        remove_oauth: bool = False,
    ) -> IntegrationInstance:
        initial = await _load_integrations(db, owner_id)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("mcp.notFound")
        current.owner_id = owner_id
        revision = current.model_dump(mode="json")
        current_config = cls.config_model.model_validate(current.config)
        remove_oauth = remove_oauth and current_config.oauth is not None
        if current_config.oauth is not None and not remove_oauth:
            if str(current_config.endpoint) != endpoint or token is not None or remove_token:
                raise McpClientError("mcp.authorizationRequired")
            if enabled:
                endpoint, tools = await cls._discover_instance(db, current)
            else:
                tools = [item.model_dump(mode="json") for item in current_config.tools]
        else:
            discovery_token = token
            if discovery_token is None and not remove_token and current_config.oauth is None:
                discovery_token = await connection_token(db, current)
            endpoint = cls._validate_connection(endpoint, discovery_token)
            endpoint, tools = await cls._discover(endpoint, discovery_token)

        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        if instance.model_dump(mode="json") != revision:
            raise IntegrationSettingsError("integration.changed")
        endpoint_changed = (
            str(cls.config_model.model_validate(instance.config).endpoint) != endpoint
        )
        if endpoint_changed and current_config.oauth is not None and not remove_oauth:
            await rebind_oauth_endpoint(db, instance, endpoint)
        old_credential_id = instance.credential_id
        instance.name = name
        instance.enabled = enabled
        instance.config = {
            **instance.config,
            "endpoint": endpoint,
            "tools": tools,
        }
        if remove_oauth:
            instance.config.pop("oauth", None)
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
        elif remove_token or remove_oauth:
            instance.credential_id = None
        if old_credential_id is not None and old_credential_id != instance.credential_id:
            await delete_credential(db, old_credential_id, owner_id=owner_id)
        tool_settings = await _load_tools(db, owner_id, for_update=True)
        if endpoint_changed or (remove_oauth and current_config.oauth is not None):
            changed = _clear_integration_policies(tool_settings, integration_id)
            changed = (
                tool_settings.resource_labels.pop(str(integration_id), None) is not None or changed
            )
            if changed:
                await _save_settings(db, tool_settings, owner_id)
            await clear_profile_integration_policies(db, integration_id, owner_id=owner_id)
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
        revision = current.model_dump(mode="json")
        endpoint, tools = await cls._discover_instance(db, current)

        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        if instance.model_dump(mode="json") != revision:
            raise IntegrationSettingsError("integration.changed")
        if instance.config.get("oauth") is not None and instance.config["endpoint"] != endpoint:
            await rebind_oauth_endpoint(db, instance, endpoint)
        instance.config["endpoint"] = endpoint
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
    async def delete(cls, db: AsyncSession, integration_id: UUID, *, owner_id: UUID | None) -> None:
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
    async def test(cls, db: AsyncSession, integration_id: UUID, *, owner_id: UUID | None) -> None:
        settings = await _load_integrations(db, owner_id)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        instance.owner_id = owner_id
        await cls._discover_instance(db, instance)

    @classmethod
    async def complete_oauth(cls, db, attempt):
        if oauth_status(attempt.payload) != "authorized":
            raise McpClientError("mcp.authorizationRequired")
        settings = await _load_integrations(db, attempt.owner_id, for_update=True)
        instance = cls._find(settings, attempt.integration_id) if attempt.integration_id else None
        if attempt.integration_id and (
            instance is None or instance.model_dump(mode="json") != attempt.revision
        ):
            raise IntegrationSettingsError("integration.changed")
        if instance is None:
            instance = IntegrationInstance(
                name=attempt.draft["name"],
                integration_type="mcp",
                adapter_type="mcp_streamable_http",
            )
            settings.instances.append(instance)
        instance.owner_id = attempt.owner_id
        old_credential_id = instance.credential_id
        credential = await store_credential(
            db,
            owner_id=attempt.owner_id,
            kind=OAUTH_CREDENTIAL_KIND,
            label=attempt.draft["name"],
            payload=attempt.payload,
        )
        instance.name = attempt.draft["name"]
        instance.enabled = attempt.draft["enabled"]
        instance.config = {
            **instance.config,
            "endpoint": attempt.draft["endpoint"],
            "oauth": attempt.draft["oauth"],
            "tools": attempt.tools,
        }
        instance.credential_id = credential.id
        validate_integration(instance)
        if old_credential_id:
            await delete_credential(db, old_credential_id, owner_id=attempt.owner_id)
        if attempt.integration_id:
            await cls._clear_binding_policies(db, instance)
        await _save_settings(db, settings, attempt.owner_id)
        await db.commit()
        return instance

    @classmethod
    async def _clear_binding_policies(cls, db, instance):
        tools = await _load_tools(db, instance.owner_id, for_update=True)
        _clear_integration_policies(tools, instance.id)
        tools.resource_labels.pop(str(instance.id), None)
        await _save_settings(db, tools, instance.owner_id)
        await clear_profile_integration_policies(db, instance.id, owner_id=instance.owner_id)

    @classmethod
    async def disconnect_oauth(cls, db, integration_id, *, owner_id):
        settings = await _load_integrations(db, owner_id, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("mcp.notFound")
        instance.owner_id = owner_id
        if instance.config.get("oauth") is None:
            raise IntegrationSettingsError("mcp.oauthConfigurationInvalid")
        if instance.credential_id:
            await delete_credential(db, instance.credential_id, owner_id=owner_id)
        instance.credential_id = None
        await cls._clear_binding_policies(db, instance)
        await _save_settings(db, settings, owner_id)
        await db.commit()
        return instance
