from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.api.errors import ApiError
from mato_loop_backend.models import UserAccount
from mato_loop_backend.settings import (
    AgentProfilePermissions,
    AgentProfilesSettings,
    SettingsStore,
    ToolExecutionPolicy,
    ToolSettings,
    UserAgentProfilesSettings,
    load_effective_agent_profiles,
    shipped_profiles,
)
from mato_loop_backend.settings.models import AgentProfileAccessOverrides

SettingsApiScope = Literal["instance", "personal"]


async def _load_scoped(
    db: AsyncSession, scope: SettingsApiScope, user: UserAccount, *, for_update: bool = False,
) -> tuple[AgentProfilesSettings | UserAgentProfilesSettings, UUID | None]:
    if scope == "instance":
        if not user.is_admin:
            raise ApiError(403, "auth.adminRequired")
        return await SettingsStore.load(db, AgentProfilesSettings, for_update=for_update), None
    instance = await SettingsStore.load(db, AgentProfilesSettings, for_update=for_update)
    if not instance.personal_agents_enabled and not user.is_admin:
        raise ApiError(409, "agentProfile.personalDisabled")
    return await SettingsStore.load(db, UserAgentProfilesSettings, user_id=user.id, for_update=for_update), user.id


async def _load_profile_access(db, user, profile_id, scope, *, for_update=False):
    settings, owner_id = await _load_scoped(db, scope, user, for_update=for_update)
    tools = await SettingsStore.load(db, ToolSettings, for_update=for_update)
    if owner_id is not None and not tools.personal_settings_enabled and not user.is_admin:
        raise ApiError(409, "tools.personalDisabled")
    builtin = next((item for item in shipped_profiles() if item.id == profile_id), None)
    if builtin is not None and owner_id is None:
        overrides = settings.builtin_access_overrides.get(profile_id, AgentProfileAccessOverrides())
        baseline = builtin
    else:
        stored = next((item for item in settings.profiles if item.id == profile_id), None)
        if stored is None:
            raise ApiError(404, "agentProfile.notFound")
        overrides = stored.overrides
        profiles = await load_effective_agent_profiles(db, user.id, include_personal=False, include_integrations=False)
        baseline = next(item for item in profiles.profiles if item.id == stored.base_profile_id)
    return settings, owner_id, overrides, baseline, tools


def _catalog_policies(groups: list[dict]) -> dict[str, set[ToolExecutionPolicy]]:
    allowed: dict[str, set[ToolExecutionPolicy]] = {}
    for group in groups:
        actions = [*group.get("actions", []), *(action for tool in group["tools"] for action in tool["actions"])]
        for action in actions:
            if action["configurable"]:
                modes = {ToolExecutionPolicy(policy) for policy in action["allowedPolicies"]}
                allowed.setdefault(action["capability"], modes).intersection_update(modes)
    return allowed


def _validate_permissions(
    permissions: AgentProfilePermissions,
    allowed: dict[str, set[ToolExecutionPolicy]],
    field: str,
) -> None:
    if permissions.integration_policies or permissions.resource_policies or permissions.resource_availability:
        raise ApiError(422, "validation.invalidChoice", field=field)
    for capability, policy in permissions.policies.items():
        if policy not in allowed.get(capability, set()):
            raise ApiError(422, "validation.invalidChoice", field=field)
