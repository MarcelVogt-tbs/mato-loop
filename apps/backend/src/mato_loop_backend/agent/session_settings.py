from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm.attributes import set_committed_value

from mato_loop_backend.models import AgentRun, ChatSession, MemoryMode, WebSearchPolicy
from mato_loop_backend.settings.session_profiles import SessionProfileSnapshot


class RunSessionSettings(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    model_config_id: UUID | None = None
    session_profile: SessionProfileSnapshot
    reasoning_effort: str | None = None
    memory_mode: MemoryMode
    web_search_policy: WebSearchPolicy
    coding_workspace: dict[str, Any] | None = None


def apply_run_session_settings(session: ChatSession, run: AgentRun) -> bool:
    from mato_loop_backend.agent.runtime import AgentRuntime

    settings = AgentRuntime._session_settings.get(run.id)
    if settings is None:
        return False
    values = settings.model_dump()
    values["session_profile"] = settings.session_profile.model_dump(mode="json")
    values.update(
        agent_profile_id=run.agent_profile_id or session.agent_profile_id,
        preferred_execution_agent_id=run.execution_agent_id,
        current_directory=run.current_directory_snapshot,
    )
    for name, value in values.items():
        # Keep the run's view stable without writing frozen values back to the session.
        set_committed_value(session, name, value)
    return True
