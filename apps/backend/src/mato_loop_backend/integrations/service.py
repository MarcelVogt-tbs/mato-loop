from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.integrations.bookstack import BOOKSTACK_CREDENTIAL_KIND
from mato_loop_backend.integrations.caldav import CALDAV_CREDENTIAL_KIND
from mato_loop_backend.integrations.registry import validate_integration
from mato_loop_backend.integrations.search import normalize_search_tool_settings
from mato_loop_backend.models import Credential, ExecutionAgent
from mato_loop_backend.security import store_credential
from mato_loop_backend.settings import (
    IntegrationInstance,
    IntegrationSettings,
    SettingsStore,
    ToolSettings,
)


class IntegrationSettingsError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GitIntegrationService:
    @staticmethod
    async def _owned_agent(
        db: AsyncSession,
        user_id: UUID,
        execution_agent_id: UUID,
        *,
        for_update: bool = True,
    ) -> None:
        statement = select(ExecutionAgent.id).where(
            ExecutionAgent.id == execution_agent_id,
            ExecutionAgent.user_id == user_id,
            ExecutionAgent.revoked_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        agent = await db.scalar(statement)
        if agent is None:
            raise IntegrationSettingsError("executionAgent.notFound")

    @classmethod
    async def check_ownership(
        cls, db: AsyncSession, settings: IntegrationSettings, *, user_id: UUID
    ) -> None:
        for instance in settings.instances:
            if instance.integration_type != "git" or instance.adapter_type != "git":
                continue
            if instance.execution_agent_id is None:
                raise IntegrationSettingsError("executionAgent.notFound")
            await cls._owned_agent(
                db, user_id, instance.execution_agent_id, for_update=False
            )

    @staticmethod
    def _find(
        settings: IntegrationSettings, integration_id: UUID
    ) -> IntegrationInstance | None:
        return next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "git"
                and item.adapter_type == "git"
            ),
            None,
        )

    @staticmethod
    def _ensure_unique(
        settings: IntegrationSettings,
        execution_agent_id: UUID,
        *,
        excluding: UUID | None = None,
    ) -> None:
        if any(
            item.id != excluding
            and item.integration_type == "git"
            and item.adapter_type == "git"
            and item.execution_agent_id == execution_agent_id
            for item in settings.instances
        ):
            raise IntegrationSettingsError("git.alreadyConfigured")

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        user_id: UUID,
        execution_agent_id: UUID,
        name: str = "Git",
        allow_network: bool = False,
        enabled: bool = True,
    ) -> IntegrationSettings:
        await cls._owned_agent(db, user_id, execution_agent_id)
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        cls._ensure_unique(settings, execution_agent_id)
        instance = IntegrationInstance(
            name=name,
            integration_type="git",
            adapter_type="git",
            enabled=enabled,
            execution_agent_id=execution_agent_id,
            config={"allow_network": allow_network},
        )
        validate_integration(instance)
        settings.instances.append(instance)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        user_id: UUID,
        execution_agent_id: UUID,
        name: str = "Git",
        allow_network: bool = False,
        enabled: bool = True,
    ) -> IntegrationSettings:
        initial = await SettingsStore.load(db, IntegrationSettings)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("integration.notFound")
        if current.execution_agent_id is None:
            raise IntegrationSettingsError("executionAgent.notFound")
        for agent_id in sorted(
            {current.execution_agent_id, execution_agent_id}, key=str
        ):
            await cls._owned_agent(db, user_id, agent_id)
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if instance.execution_agent_id != current.execution_agent_id:
            raise IntegrationSettingsError("integration.changed")
        cls._ensure_unique(settings, execution_agent_id, excluding=integration_id)
        instance.enabled = enabled
        instance.name = name
        instance.execution_agent_id = execution_agent_id
        instance.config = {"allow_network": allow_network}
        validate_integration(instance)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID, *, user_id: UUID
    ) -> IntegrationSettings:
        initial = await SettingsStore.load(db, IntegrationSettings)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("integration.notFound")
        if current.execution_agent_id is None:
            raise IntegrationSettingsError("executionAgent.notFound")
        await cls._owned_agent(db, user_id, current.execution_agent_id)
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if instance.execution_agent_id != current.execution_agent_id:
            raise IntegrationSettingsError("integration.changed")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        tools = await SettingsStore.load(db, ToolSettings, for_update=True)
        tools.integration_policies.pop(str(integration_id), None)
        await SettingsStore.save(db, tools)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings


class OpenScadIntegrationService:
    @staticmethod
    async def _owned_agent(
        db: AsyncSession,
        user_id: UUID,
        execution_agent_id: UUID,
        *,
        for_update: bool = True,
    ) -> None:
        statement = select(ExecutionAgent.id).where(
            ExecutionAgent.id == execution_agent_id,
            ExecutionAgent.user_id == user_id,
            ExecutionAgent.revoked_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        if await db.scalar(statement) is None:
            raise IntegrationSettingsError("executionAgent.notFound")

    @staticmethod
    def _find(
        settings: IntegrationSettings, integration_id: UUID
    ) -> IntegrationInstance | None:
        return next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "openscad"
                and item.adapter_type == "openscad"
            ),
            None,
        )

    @staticmethod
    def _ensure_unique(
        settings: IntegrationSettings,
        execution_agent_id: UUID,
        *,
        excluding: UUID | None = None,
    ) -> None:
        if any(
            item.id != excluding
            and item.integration_type == "openscad"
            and item.adapter_type == "openscad"
            and item.execution_agent_id == execution_agent_id
            for item in settings.instances
        ):
            raise IntegrationSettingsError("openscad.alreadyConfigured")

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        user_id: UUID,
        execution_agent_id: UUID,
        name: str,
        binary_path: str,
        enabled: bool,
    ) -> IntegrationSettings:
        await cls._owned_agent(db, user_id, execution_agent_id)
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        cls._ensure_unique(settings, execution_agent_id)
        instance = IntegrationInstance(
            name=name,
            integration_type="openscad",
            adapter_type="openscad",
            enabled=enabled,
            execution_agent_id=execution_agent_id,
            config={"binary_path": binary_path},
        )
        validate_integration(instance)
        settings.instances.append(instance)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        user_id: UUID,
        execution_agent_id: UUID,
        name: str,
        binary_path: str,
        enabled: bool,
    ) -> IntegrationSettings:
        initial = await SettingsStore.load(db, IntegrationSettings)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("integration.notFound")
        if current.execution_agent_id is None:
            raise IntegrationSettingsError("executionAgent.notFound")
        for agent_id in sorted({current.execution_agent_id, execution_agent_id}, key=str):
            await cls._owned_agent(db, user_id, agent_id)
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if instance.execution_agent_id != current.execution_agent_id:
            raise IntegrationSettingsError("integration.changed")
        cls._ensure_unique(settings, execution_agent_id, excluding=integration_id)
        instance.name = name
        instance.enabled = enabled
        instance.execution_agent_id = execution_agent_id
        instance.config = {"binary_path": binary_path}
        validate_integration(instance)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID, *, user_id: UUID
    ) -> IntegrationSettings:
        initial = await SettingsStore.load(db, IntegrationSettings)
        current = cls._find(initial, integration_id)
        if current is None:
            raise IntegrationSettingsError("integration.notFound")
        if current.execution_agent_id is None:
            raise IntegrationSettingsError("executionAgent.notFound")
        await cls._owned_agent(db, user_id, current.execution_agent_id)
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if instance.execution_agent_id != current.execution_agent_id:
            raise IntegrationSettingsError("integration.changed")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        tools = await SettingsStore.load(db, ToolSettings, for_update=True)
        tools.integration_policies.pop(str(integration_id), None)
        await SettingsStore.save(db, tools)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings


class SearchIntegrationService:
    @staticmethod
    async def _save(
        db: AsyncSession,
        settings: IntegrationSettings,
        *,
        normalize_tools: bool = False,
        remove_resource_policies: UUID | None = None,
    ) -> IntegrationSettings:
        if normalize_tools or remove_resource_policies is not None:
            tool_settings = await SettingsStore.load(db, ToolSettings, for_update=True)
            changed = normalize_tools and normalize_search_tool_settings(
                tool_settings, settings
            )
            if remove_resource_policies is not None:
                changed = (
                    tool_settings.resource_policies.pop(
                        str(remove_resource_policies), None
                    )
                    is not None
                    or changed
                )
                changed = (
                    tool_settings.resource_labels.pop(
                        str(remove_resource_policies), None
                    )
                    is not None
                    or changed
                )
            if changed:
                await SettingsStore.save(db, tool_settings)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        provider_type: str,
        base_url: str,
        enabled: bool,
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = IntegrationInstance(
            name=name,
            integration_type="search",
            adapter_type=provider_type,
            enabled=enabled,
            config={"base_url": base_url.rstrip("/")},
        )
        validate_integration(instance)
        settings.instances.append(instance)
        return await cls._save(db, settings)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        provider_type: str,
        base_url: str,
        enabled: bool,
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "search"
                and item.adapter_type == provider_type
            ),
            None,
        )
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        config = {"base_url": base_url.rstrip("/")}
        connection_changed = instance.config != config
        instance.name = name
        instance.enabled = enabled
        instance.config = config
        validate_integration(instance)
        return await cls._save(
            db,
            settings,
            normalize_tools=True,
            remove_resource_policies=integration_id if connection_changed else None,
        )

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = next(
            (
                item
                for item in settings.instances
                if item.id == integration_id and item.integration_type == "search"
            ),
            None,
        )
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        if instance.credential_id:
            await db.execute(delete(Credential).where(Credential.id == instance.credential_id))
        return await cls._save(
            db,
            settings,
            normalize_tools=True,
            remove_resource_policies=integration_id,
        )


class CalDavIntegrationService:
    @staticmethod
    async def _save(
        db: AsyncSession,
        settings: IntegrationSettings,
        *,
        clear_policies: UUID | None = None,
    ) -> IntegrationSettings:
        if clear_policies is not None:
            tools = await SettingsStore.load(db, ToolSettings, for_update=True)
            integration_id = str(clear_policies)
            changed = tools.integration_policies.pop(integration_id, None) is not None
            changed = tools.resource_policies.pop(integration_id, None) is not None or changed
            changed = tools.resource_labels.pop(integration_id, None) is not None or changed
            if (
                tools.default_calendar is not None
                and str(tools.default_calendar.integration_id) == integration_id
            ):
                tools.default_calendar = None
                changed = True
            if (
                tools.default_task_list is not None
                and str(tools.default_task_list.integration_id) == integration_id
            ):
                tools.default_task_list = None
                changed = True
            if changed:
                await SettingsStore.save(db, tools)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        base_url: str,
        username: str,
        password: str,
        enabled: bool,
    ) -> IntegrationSettings:
        if not password:
            raise IntegrationSettingsError("validation.required")
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = IntegrationInstance(
            name=name,
            integration_type="calendar",
            adapter_type="caldav",
            enabled=enabled,
            config={"base_url": base_url, "username": username},
        )
        validate_integration(instance)
        credential = await store_credential(
            db,
            kind=CALDAV_CREDENTIAL_KIND,
            label=name,
            payload={"password": password},
        )
        instance.credential_id = credential.id
        settings.instances.append(instance)
        return await cls._save(db, settings)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        base_url: str,
        username: str,
        password: str | None,
        enabled: bool,
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "calendar"
                and item.adapter_type == "caldav"
            ),
            None,
        )
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if password == "":
            raise IntegrationSettingsError("validation.required")

        config = {"base_url": base_url, "username": username}
        connection_changed = instance.config != config
        old_credential_id = instance.credential_id
        instance.name = name
        instance.enabled = enabled
        instance.config = config
        validate_integration(instance)
        if password is not None:
            credential = await store_credential(
                db,
                kind=CALDAV_CREDENTIAL_KIND,
                label=name,
                payload={"password": password},
            )
            instance.credential_id = credential.id
            if old_credential_id is not None:
                await db.execute(
                    delete(Credential).where(Credential.id == old_credential_id)
                )
        return await cls._save(
            db,
            settings,
            clear_policies=integration_id if connection_changed else None,
        )

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "calendar"
                and item.adapter_type == "caldav"
            ),
            None,
        )
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        if instance.credential_id is not None:
            await db.execute(
                delete(Credential).where(Credential.id == instance.credential_id)
            )
        return await cls._save(db, settings, clear_policies=integration_id)


class BookStackIntegrationService:
    @staticmethod
    async def _save(
        db: AsyncSession,
        settings: IntegrationSettings,
        *,
        clear_policies: UUID | None = None,
    ) -> IntegrationSettings:
        if clear_policies is not None:
            tools = await SettingsStore.load(db, ToolSettings, for_update=True)
            integration_id = str(clear_policies)
            changed = tools.integration_policies.pop(integration_id, None) is not None
            changed = tools.resource_policies.pop(integration_id, None) is not None or changed
            changed = tools.resource_labels.pop(integration_id, None) is not None or changed
            if changed:
                await SettingsStore.save(db, tools)
        await SettingsStore.save(db, settings)
        await db.commit()
        return settings

    @staticmethod
    def _find(
        settings: IntegrationSettings, integration_id: UUID
    ) -> IntegrationInstance | None:
        return next(
            (
                item
                for item in settings.instances
                if item.id == integration_id
                and item.integration_type == "knowledge"
                and item.adapter_type == "bookstack"
            ),
            None,
        )

    @classmethod
    async def create(
        cls,
        db: AsyncSession,
        *,
        name: str,
        base_url: str,
        token_id: str,
        token_secret: str,
        enabled: bool,
    ) -> IntegrationSettings:
        if not token_id or not token_secret:
            raise IntegrationSettingsError("validation.required")
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = IntegrationInstance(
            name=name,
            integration_type="knowledge",
            adapter_type="bookstack",
            enabled=enabled,
            config={"base_url": base_url},
        )
        validate_integration(instance)
        credential = await store_credential(
            db,
            kind=BOOKSTACK_CREDENTIAL_KIND,
            label=name,
            payload={"token_id": token_id, "token_secret": token_secret},
        )
        instance.credential_id = credential.id
        settings.instances.append(instance)
        return await cls._save(db, settings)

    @classmethod
    async def update(
        cls,
        db: AsyncSession,
        integration_id: UUID,
        *,
        name: str,
        base_url: str,
        token_id: str | None,
        token_secret: str | None,
        enabled: bool,
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        if token_id == "" or token_secret == "":
            raise IntegrationSettingsError("validation.required")
        if (token_id is None) != (token_secret is None):
            raise IntegrationSettingsError("validation.required")

        config = {"base_url": base_url}
        connection_changed = instance.config != config
        old_credential_id = instance.credential_id
        instance.name = name
        instance.enabled = enabled
        instance.config = config
        validate_integration(instance)
        if token_id is not None and token_secret is not None:
            credential = await store_credential(
                db,
                kind=BOOKSTACK_CREDENTIAL_KIND,
                label=name,
                payload={"token_id": token_id, "token_secret": token_secret},
            )
            instance.credential_id = credential.id
            if old_credential_id is not None:
                await db.execute(
                    delete(Credential).where(Credential.id == old_credential_id)
                )
        return await cls._save(
            db,
            settings,
            clear_policies=integration_id if connection_changed else None,
        )

    @classmethod
    async def delete(
        cls, db: AsyncSession, integration_id: UUID
    ) -> IntegrationSettings:
        settings = await SettingsStore.load(db, IntegrationSettings, for_update=True)
        instance = cls._find(settings, integration_id)
        if instance is None:
            raise IntegrationSettingsError("integration.notFound")
        settings.instances = [item for item in settings.instances if item.id != integration_id]
        if instance.credential_id is not None:
            await db.execute(
                delete(Credential).where(Credential.id == instance.credential_id)
            )
        return await cls._save(db, settings, clear_policies=integration_id)
