# Model imports are ordered for dependency-safe SQLAlchemy registration.
# ruff: noqa: I001

from mato_loop_backend.models.authentication import AuthenticationProvider, UserAuthIdentity
from mato_loop_backend.models.automation import Automation, AutomationRun, AutomationSchedule
from mato_loop_backend.models.execution_agent import ExecutionAgent, ExecutionAgentRoot
from mato_loop_backend.models.project import Project
from mato_loop_backend.models.account import Credential, LoginSession, UserAccount
from mato_loop_backend.models.session import (
    ChatSession,
    ContextSummary,
    Message,
    MessageAttachment,
    MessageSearchChunk,
    TitleGenerationStatus,
    WebSearchPolicy,
)
from mato_loop_backend.models.agent import (
    AgentRun,
    ApprovalRequest,
    PermissionGrant,
    QuestionRequest,
)
from mato_loop_backend.models.notification import Notification, WebPushSubscription
from mato_loop_backend.models.memory import (
    MemoryAudience,
    MemoryCategory,
    MemoryDreamRun,
    MemoryDreamState,
    MemoryDreamStatus,
    MemoryDreamTrigger,
    MemoryEmbedding,
    MemoryEmbeddingStatus,
    MemoryMode,
    MemoryPinKind,
    MemoryRecord,
    MemorySourceMessage,
    MemorySourceType,
)
from mato_loop_backend.models.setting import SettingRecord

__all__ = [
    "AgentRun",
    "ApprovalRequest",
    "Automation",
    "AutomationRun",
    "AutomationSchedule",
    "AuthenticationProvider",
    "ChatSession",
    "ContextSummary",
    "Credential",
    "ExecutionAgent",
    "ExecutionAgentRoot",
    "LoginSession",
    "MemoryAudience",
    "MemoryCategory",
    "MemoryRecord",
    "MemoryDreamRun",
    "MemoryDreamState",
    "MemoryDreamStatus",
    "MemoryDreamTrigger",
    "MemoryEmbedding",
    "MemoryEmbeddingStatus",
    "MemoryMode",
    "MemoryPinKind",
    "MemorySourceMessage",
    "MemorySourceType",
    "Message",
    "MessageAttachment",
    "MessageSearchChunk",
    "Notification",
    "PermissionGrant",
    "Project",
    "QuestionRequest",
    "SettingRecord",
    "TitleGenerationStatus",
    "UserAccount",
    "UserAuthIdentity",
    "WebPushSubscription",
    "WebSearchPolicy",
]
