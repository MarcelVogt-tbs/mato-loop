from fastapi import APIRouter

from mato_loop_backend.api.routes.agent_identity import router as agent_identity_router
from mato_loop_backend.api.routes.approvals import router as approvals_router
from mato_loop_backend.api.routes.auth import router as auth_router
from mato_loop_backend.api.routes.health import router as health_router
from mato_loop_backend.api.routes.integrations import router as integrations_router
from mato_loop_backend.api.routes.media import router as media_router
from mato_loop_backend.api.routes.memories import router as memories_router
from mato_loop_backend.api.routes.providers import router as providers_router
from mato_loop_backend.api.routes.questions import router as questions_router
from mato_loop_backend.api.routes.sessions import router as sessions_router
from mato_loop_backend.api.routes.skills import router as skills_router
from mato_loop_backend.api.routes.tool_settings import router as tool_settings_router
from mato_loop_backend.api.routes.workspace import router as workspace_router

api_router = APIRouter()
api_router.include_router(health_router, tags=["health"])
api_router.include_router(auth_router, tags=["auth"])
api_router.include_router(agent_identity_router, tags=["agent-identity"])
api_router.include_router(providers_router, tags=["providers"])
api_router.include_router(integrations_router, tags=["integrations"])
api_router.include_router(memories_router, tags=["memories"])
api_router.include_router(media_router, tags=["media"])
api_router.include_router(sessions_router, tags=["sessions"])
api_router.include_router(skills_router, tags=["skills"])
api_router.include_router(approvals_router, tags=["approvals"])
api_router.include_router(questions_router, tags=["questions"])
api_router.include_router(workspace_router, tags=["workspace"])
api_router.include_router(tool_settings_router, tags=["tools"])
