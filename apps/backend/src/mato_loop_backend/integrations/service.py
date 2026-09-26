from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.settings import (
    AgentProfilesSettings,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
    UserAgentProfilesSettings,
    UserIntegrationSettings,
    UserToolSettings,
    WebSearchToolSettings,
)
from mato_loop_backend.settings.effective import personal_integration_tool_settings


class IntegrationSettingsError(RuntimeError):
    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


IntegrationSettingsDocument = IntegrationSettings | UserIntegrationSettings
ToolSettingsDocument = ToolSettings | UserToolSettings


def _clear_integration_policies(
    tools: ToolSettingsDocument, integration_id: UUID | str, *, resources: bool = True
) -> bool:
    key = str(integration_id)
    changed = False
    for permissions in (tools.private_permissions, tools.public_permissions):
        changed = permissions.integration_policies.pop(key, None) is not None or changed
        if resources:
            changed = permissions.resource_policies.pop(key, None) is not None or changed
    return changed


async def clear_profile_integration_policies(
    db: AsyncSession, integration_id: UUID | str, *, owner_id: UUID | None
) -> None:
    key = str(integration_id)
    documents: list[
        tuple[AgentProfilesSettings | UserAgentProfilesSettings | None, UUID | None]
    ]
    if owner_id is None:
        instance = await SettingsStore.load_optional(
            db, AgentProfilesSettings, for_update=True
        )
        user_ids = await SettingsStore.list_user_scopes(db, UserAgentProfilesSettings)
        documents = [(instance, None)]
        for user_id in user_ids:
            documents.append(
                (
                    await SettingsStore.load_optional(
                        db,
                        UserAgentProfilesSettings,
                        user_id=user_id,
                        for_update=True,
                    ),
                    user_id,
                )
            )
    else:
        documents = [
            (
                await SettingsStore.load_optional(
                    db,
                    UserAgentProfilesSettings,
                    user_id=owner_id,
                    for_update=True,
                ),
                owner_id,
            )
        ]

    for settings, user_id in documents:
        if settings is None:
            continue
        overrides = [profile.overrides for profile in settings.profiles]
        if isinstance(settings, AgentProfilesSettings):
            overrides.extend(settings.builtin_access_overrides.values())
        changed = False
        for override in overrides:
            for permissions in (
                override.private_permissions,
                override.public_permissions,
            ):
                if permissions is None:
                    continue
                changed = (
                    permissions.integration_policies.pop(key, None) is not None
                    or changed
                )
                changed = (
                    permissions.resource_policies.pop(key, None) is not None
                    or changed
                )
        if changed:
            await SettingsStore.save(db, settings, user_id=user_id)


async def _load_integrations(
    db: AsyncSession, owner_id: UUID | None, *, for_update: bool = False
) -> IntegrationSettingsDocument:
    settings_type = UserIntegrationSettings if owner_id is not None else IntegrationSettings
    settings = await SettingsStore.load(
        db, settings_type, user_id=owner_id, for_update=for_update
    )
    for instance in settings.instances:
        instance.owner_id = owner_id
    return settings


async def _load_tools(
    db: AsyncSession, owner_id: UUID | None, *, for_update: bool = False
) -> ToolSettingsDocument:
    if owner_id is None:
        return await SettingsStore.load(db, ToolSettings, for_update=for_update)
    personal = await SettingsStore.load_optional(
        db, UserToolSettings, user_id=owner_id, for_update=for_update
    )
    if personal is not None:
        return personal
    instance = await SettingsStore.load(db, ToolSettings)
    return personal_integration_tool_settings(instance)


async def _save_settings(
    db: AsyncSession,
    settings: IntegrationSettingsDocument | ToolSettingsDocument,
    owner_id: UUID | None,
) -> None:
    await SettingsStore.save(db, settings, user_id=owner_id)


async def save_search_selection(
    db: AsyncSession,
    owner_id: UUID | None,
    selection: WebSearchToolSettings,
    available_settings: IntegrationSettings,
    tools: ToolSettingsDocument,
) -> None:
    from mato_loop_backend.integrations.search import search_provider_options

    available = {str(option["value"]) for option in search_provider_options(available_settings)}
    for field in ("primary_provider", "fallback_provider"):
        provider = getattr(selection, field)
        if provider is not None and provider not in available:
            raise IntegrationSettingsError("integration.providerUnavailable", field=field)
    tools.web_search = selection
    await SettingsStore.save(db, tools, user_id=owner_id)
    await db.commit()


async def save_with_policy_cleanup(
    db: AsyncSession,
    settings: IntegrationSettingsDocument,
    *,
    owner_id: UUID | None,
    clear_policies: UUID | None = None,
) -> IntegrationSettingsDocument:
    if clear_policies is not None:
        tools = await _load_tools(db, owner_id, for_update=True)
        integration_id = str(clear_policies)
        changed = _clear_integration_policies(tools, integration_id)
        changed = tools.resource_labels.pop(integration_id, None) is not None or changed
        if changed:
            await _save_settings(db, tools, owner_id)
        await clear_profile_integration_policies(db, clear_policies, owner_id=owner_id)
    await _save_settings(db, settings, owner_id)
    await db.commit()
    return settings
