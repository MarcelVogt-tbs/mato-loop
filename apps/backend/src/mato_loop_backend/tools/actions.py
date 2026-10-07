from dataclasses import dataclass
from typing import Literal

from mato_loop_backend.settings.models import ToolExecutionPolicy, TrustLevel


@dataclass(frozen=True, slots=True)
class ToolAction:
    capability: str
    label: str
    private_default_policy: ToolExecutionPolicy
    public_default_policy: ToolExecutionPolicy
    configurable: bool = True
    mutates: bool = False
    allowed_policies: tuple[ToolExecutionPolicy, ...] = tuple(ToolExecutionPolicy)

    def default_policy_for(self, trust_level: TrustLevel) -> ToolExecutionPolicy:
        return (
            self.private_default_policy
            if trust_level == TrustLevel.PRIVATE
            else self.public_default_policy
        )


@dataclass(frozen=True, slots=True)
class ApprovalEditField:
    path: tuple[str, ...]
    label: str
    input_type: Literal["text", "textarea", "temporal"] = "text"
    required: bool = False
    max_length: int = 10_000


@dataclass(frozen=True, slots=True)
class ToolPolicyResolution:
    policy: ToolExecutionPolicy
    source: str
    reason: str


_FILESYSTEM_READ = ToolAction(
    "filesystem.read", "Lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)

_FILESYSTEM_WRITE = ToolAction(
    "filesystem.write", "Workspace schreiben", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)

_FILESYSTEM_DELETE = ToolAction(
    "filesystem.delete", "Löschen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)

_WORKING_DIRECTORY_EXTERNAL = ToolAction(
    "working_directory.external",
    "Zugriff außerhalb des Arbeitsverzeichnisses",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
)

_SESSION_FILES_READ = ToolAction(
    "session.files.read", "Session-Dateien lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO
)

_FILE_PROVIDE = ToolAction(
    "file.provide", "Datei bereitstellen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO
)

_SESSION_FILES_WRITE = ToolAction(
    "session.files.write", "Session-Dateien schreiben", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO, mutates=True
)

_WEB_SEARCH = ToolAction("network.search", "Websuche", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO)

_WEB_FETCH = ToolAction("network.fetch", "Webseiten abrufen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO)

_TRANSCRIPT_ROUTING_INSTRUCTION = (
    "Published transcript text is evidence of spoken content only, not of visual events. Call "
    "fetch_transcript only when the requested output can be answered from spoken content; never "
    "call it for camera work, visible UI, objects, actions, or other visual details."
)

_SESSION_READ = ToolAction("session.read", "Sessions lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO)

_SESSION_SEARCH = ToolAction(
    "session.search", "Sessions durchsuchen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)

_AUTOMATION_READ = ToolAction(
    "automation.read", "Automationen lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)

_AUTOMATION_CREATE = ToolAction(
    "automation.create",
    "Automationen erstellen",
    ToolExecutionPolicy.AUTO,
    ToolExecutionPolicy.AUTO,
    mutates=True,
)

_AUTOMATION_UPDATE = ToolAction(
    "automation.update",
    "Automationen ändern",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
    mutates=True,
)

_AUTOMATION_RUN = ToolAction(
    "automation.run",
    "Automationen starten",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
    mutates=True,
)

_AUTOMATION_DELETE = ToolAction(
    "automation.delete",
    "Automationen löschen",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
    mutates=True,
)

_SESSION_TOOL_RESULT_READ = ToolAction(
    "session.tool_result.read",
    "Historische Tool-Ergebnisse lesen",
    ToolExecutionPolicy.AUTO,
    ToolExecutionPolicy.ASK,
)

_MEMORY_READ = ToolAction("memory.read", "Memories lesen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO)

_MEMORY_WRITE = ToolAction(
    "memory.write", "Memories speichern", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)

_SKILL_LOAD = ToolAction("skills.load", "Skills laden", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.AUTO)

_SKILL_WRITE = ToolAction(
    "skills.write", "Skills speichern", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)

_USER_PROFILE_UPDATE = ToolAction(
    "settings.user_profile.update",
    "Persönliches Profil ändern",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
    mutates=True,
)

_PERSONAL_SETTINGS_READ = ToolAction(
    "settings.personal.read", "Persönliche Einstellungen lesen",
    ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK,
)

_PERSONAL_SETTINGS_WRITE = ToolAction(
    "settings.personal.update", "Persönliche Einstellungen ändern",
    ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True,
)

_SECRETS_ACCESS = ToolAction(
    "secrets.access",
    "Variablen und Secrets verwenden",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
)

_SECRETS_WRITE = ToolAction(
    "secrets.write",
    "Variablen und Secrets speichern",
    ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK,
    mutates=True,
)

_SHELL_READ = ToolAction("shell.read", "Shell ohne erkannte Schreibaktionen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK)

_SHELL_WRITE = ToolAction(
    "shell.write", "Als schreibend eingestufte Shell", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)

_SYSTEM_INFO = ToolAction(
    "system.info", "Systeminformationen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)

_EXECUTION_AGENTS_LIST = ToolAction(
    "execution_agent.list", "Computer anzeigen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK,
)

_EXECUTION_AGENT_SELECT = ToolAction(
    "execution_agent.select", "Computer übernehmen", ToolExecutionPolicy.ASK,
    ToolExecutionPolicy.ASK, mutates=True,
)

_CODING_ANALYZE = ToolAction(
    "coding.analyze", "Projekt analysieren", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK
)

_CODING_PLAN_CREATE = ToolAction(
    "coding.plan.create", "Plan speichern", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)

_CODING_PLAN_READ = ToolAction(
    "coding.plan.read", "Pläne anzeigen", ToolExecutionPolicy.AUTO, ToolExecutionPolicy.ASK
)

_CODING_EXECUTE = ToolAction(
    "coding.execute.enqueue", "Pläne ausführen", ToolExecutionPolicy.ASK, ToolExecutionPolicy.ASK, mutates=True
)
