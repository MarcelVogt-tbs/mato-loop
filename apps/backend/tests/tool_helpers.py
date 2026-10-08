"""Owner-bound file and permission context shared by tool tests."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.files.service import RootFileService
from mato_loop_backend.files.storage import SessionStorage
from mato_loop_backend.models import ChatSession
from mato_loop_backend.permissions.policy import PermissionDecision
from mato_loop_backend.providers.base import ToolCall
from mato_loop_backend.settings import (
    GENERAL_PROFILE_ID,
    IntegrationSettings,
    ToolExecutionPolicy,
    ToolSettings,
    TrustLevel,
)
from mato_loop_backend.tools.context import ToolContext


def _session() -> ChatSession:
    return ChatSession(
        id=uuid4(),
        user_id=uuid4(),
        title="Test",
        memory_mode="disabled",
        web_search_policy="available",
    )


def _settings_record(settings: ToolSettings) -> dict:
    permissions = settings.private_permissions.model_dump(mode="json")
    return {
        **settings.model_dump(mode="json"),
        "builtin_access_overrides": {
            str(GENERAL_PROFILE_ID): {
                "private_permissions": permissions,
                "public_permissions": permissions,
            }
        },
    }


def _context(
    tmp_path,
    call: ToolCall,
    *,
    decision=PermissionDecision.ALLOW,
    web_fetch_policy=ToolExecutionPolicy.DISABLED,
    public_addresses_only=True,
) -> ToolContext:
    session = _session()
    storage = SessionStorage(tmp_path / "sessions")
    storage.provision(session.id)
    tool_settings = ToolSettings()
    tool_settings.private_permissions.policies["network.fetch"] = web_fetch_policy
    tool_settings.public_permissions.policies["network.fetch"] = web_fetch_policy
    tool_settings.web_fetch.public_addresses_only = public_addresses_only
    context = ToolContext(
        db=AsyncMock(spec=AsyncSession),
        run_id=uuid4(),
        session=session,
        tool_call=call,
        root_files=RootFileService(session.id, session_storage=storage),
        integration_settings=IntegrationSettings(),
        ask_handler=AsyncMock(return_value=decision),
        tool_settings=tool_settings,
    )
    context.db.scalar.return_value = Mock(data=_settings_record(tool_settings))
    context.model_trust_level = TrustLevel.PRIVATE
    return context
