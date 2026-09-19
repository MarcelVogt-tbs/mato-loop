# Model imports are ordered for dependency-safe SQLAlchemy registration.
# ruff: noqa: I001

from mato_loop_backend.models.authentication import AuthenticationProvider, UserAuthIdentity
from mato_loop_backend.models.automation import Automation, AutomationRun, AutomationSchedule
from mato_loop_backend.models.diagnostic import DiagnosticEvent
from mato_loop_backend.models.execution_agent import ExecutionAgent
from mato_loop_backend.models.project import Project
from mato_loop_backend.models.coding import ProjectPlan, ProjectPlanExecution
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
from mato_loop_backend.models.mail import MailSendOperation
from mato_loop_backend.models.messaging import MessagingEndpoint, MessagingInbox, MessagingOutbox
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
    "DiagnosticEvent",
    "ExecutionAgent",
    "LoginSession",
    "MailSendOperation",
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
    "MemorySourceMessage",
    "MemorySourceType",
    "Message",
    "MessageAttachment",
    "MessageSearchChunk",
    "MessagingEndpoint",
    "MessagingInbox",
    "MessagingOutbox",
    "Notification",
    "PermissionGrant",
    "Project",
    "ProjectPlan",
    "ProjectPlanExecution",
    "QuestionRequest",
    "SettingRecord",
    "TitleGenerationStatus",
    "UserAccount",
    "UserAuthIdentity",
    "WebPushSubscription",
    "WebSearchPolicy",
]
