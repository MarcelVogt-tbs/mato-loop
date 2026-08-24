from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from mato_loop_backend.automations import next_run_at
from mato_loop_backend.models import MemoryCategory
from mato_loop_backend.settings.models import normalize_agent_display_name

QR_CODE_MAX_CONTENT_BYTES = 2_000


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuestionOption(ToolArguments):
    label: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=500)

    @field_validator("label")
    @classmethod
    def label_must_not_be_blank(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("label must not be blank")
        return cleaned


class QuestionBase(ToolArguments):
    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
    header: str = Field(min_length=1, max_length=30)
    question: str = Field(min_length=1, max_length=2_000)
    default_answers: list[str] = Field(
        default_factory=list,
        max_length=13,
        description="Answers to use when the user submits no answer.",
    )

    @field_validator("header", "question")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("question text must not be blank")
        return cleaned


class ChoiceQuestion(QuestionBase):
    options: list[QuestionOption] = Field(min_length=2, max_length=12)
    allow_custom: bool = Field(
        default=True,
        description="Allow one custom text answer; do not add an Other option.",
    )

    @model_validator(mode="after")
    def options_are_unique(self) -> ChoiceQuestion:
        labels = [option.label for option in self.options]
        if len(labels) != len(set(labels)):
            raise ValueError("option labels must be unique")
        return self


class SingleChoiceQuestion(ChoiceQuestion):
    type: Literal["single_choice"]


class MultipleChoiceQuestion(ChoiceQuestion):
    type: Literal["multiple_choice"]


class FreeTextQuestion(QuestionBase):
    type: Literal["free_text"]
    placeholder: str | None = Field(default=None, max_length=200)


QuestionItem = Annotated[
    SingleChoiceQuestion | MultipleChoiceQuestion | FreeTextQuestion,
    Field(discriminator="type"),
]


class QuestionArguments(ToolArguments):
    questions: list[QuestionItem] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def ids_and_defaults_are_valid(self) -> QuestionArguments:
        ids = [question.id for question in self.questions]
        if len(ids) != len(set(ids)):
            raise ValueError("question IDs must be unique")
        for question in self.questions:
            _validate_question_answer(question, question.default_answers)
        return self


def _validate_question_answer(question: QuestionItem, answer: list[str]) -> list[str]:
    if not isinstance(answer, list) or len(answer) != len(set(answer)):
        raise ValueError("answer must be a list of unique strings")
    cleaned = []
    for item in answer:
        if not isinstance(item, str) or len(item) > 20_000:
            raise ValueError("answer item is invalid")
        value = item.strip()
        if value:
            cleaned.append(value)
    if len(cleaned) != len(set(cleaned)):
        raise ValueError("answer items must be unique")
    if question.type == "free_text":
        if len(cleaned) > 1:
            raise ValueError("free-text answer is invalid")
    elif question.type == "single_choice":
        options = {option.label for option in question.options}
        if len(cleaned) > 1 or (
            cleaned and cleaned[0] not in options and not question.allow_custom
        ):
            raise ValueError("single-choice answer is invalid")
    else:
        options = {option.label for option in question.options}
        custom = [item for item in cleaned if item not in options]
        if custom and (not question.allow_custom or len(custom) > 1):
            raise ValueError("multiple-choice answer is invalid")
    return cleaned


def validate_question_answers(
    questions: QuestionArguments, answers: dict[str, list[str]]
) -> dict[str, list[str]]:
    if set(answers) != {question.id for question in questions.questions}:
        raise ValueError("every question requires exactly one answer")
    validated: dict[str, list[str]] = {}
    for question in questions.questions:
        answer = _validate_question_answer(question, answers[question.id])
        validated[question.id] = answer or _validate_question_answer(
            question, question.default_answers
        )
    return validated


class SessionFileReadArguments(ToolArguments):
    root: Literal["session", "workspace"]
    session_id: UUID | None = None

    @model_validator(mode="after")
    def session_target_requires_session_root(self) -> SessionFileReadArguments:
        if self.session_id is not None and self.root != "session":
            raise ValueError("session_id requires root=session")
        return self


class ReadArguments(SessionFileReadArguments):
    path: str = Field(min_length=1, max_length=1_000)
    offset: int = Field(default=1, ge=1)
    limit: int = Field(default=1_000, ge=1, le=1_000)
    revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def revision_requires_managed_files(self) -> ReadArguments:
        if self.revision is not None and self.root != "session":
            raise ValueError("revision requires root=files")
        return self


class GlobArguments(SessionFileReadArguments):
    pattern: str = Field(min_length=1, max_length=1_000, pattern=r"^[^\x00]+$")
    path: str = Field(default=".", max_length=1_000)


class GrepArguments(GlobArguments):
    pattern: str = Field(min_length=1, max_length=4_096, pattern=r"^[^\x00]+$")
    include: str | None = Field(
        default=None, min_length=1, max_length=1_000, pattern=r"^[^\x00]+$"
    )


class ProvideFileArguments(SessionFileReadArguments):
    path: str = Field(min_length=1, max_length=1_000)


class WriteFileArguments(ToolArguments):
    root: Literal["session", "workspace"]
    path: str = Field(min_length=1, max_length=1_000)
    content: str = Field(max_length=1_000_000)


class EditFileArguments(ToolArguments):
    root: Literal["session", "workspace"]
    path: str = Field(min_length=1, max_length=1_000)
    old_text: str = Field(min_length=1, max_length=1_000_000, alias="oldText")
    new_text: str = Field(max_length=1_000_000, alias="newText")
    replace_all: bool = Field(default=False, alias="replaceAll")

    @model_validator(mode="after")
    def require_change(self) -> EditFileArguments:
        if self.old_text == self.new_text:
            raise ValueError("old_text and new_text must differ")
        return self


class CreateDirectoryArguments(ToolArguments):
    root: Literal["session", "workspace"]
    path: str = Field(min_length=1, max_length=1_000)


class DeletePathArguments(ToolArguments):
    root: Literal["session", "workspace"]
    path: str = Field(min_length=1, max_length=1_000)
    recursive: bool = False


class CopyPathArguments(ToolArguments):
    source_root: Literal["session", "workspace"]
    source_path: str = Field(min_length=1, max_length=1_000)
    destination_root: Literal["session", "workspace"]
    destination_path: str = Field(min_length=1, max_length=1_000)


class MovePathArguments(CopyPathArguments):
    pass


class ProcessExecArguments(ToolArguments):
    program: str = Field(
        min_length=1,
        max_length=128,
        description="Configured program alias, never an executable path.",
    )
    args: list[Annotated[str, Field(max_length=10_000)]] = Field(
        default_factory=list, max_length=256
    )


class ShellRunArguments(ToolArguments):
    command: str = Field(min_length=1, max_length=256 * 1024)


class SystemInfoArguments(ToolArguments):
    pass


class ChangeDirectoryArguments(ToolArguments):
    path: str = Field(min_length=1, max_length=4096)


class WebSearchArguments(ToolArguments):
    query: str = Field(
        min_length=1,
        max_length=2_000,
        description=(
            "One compact search-engine query for one information need. Preserve user-supplied dates, "
            "versions, model numbers, and exact names; never add a date."
        ),
    )
    search_type: Literal["web", "images"] = "web"


class WebFetchArguments(ToolArguments):
    url: str = Field(min_length=1, max_length=1_200)
    format: Literal["markdown", "text", "html"] = "markdown"
    timeout: int = Field(default=30, ge=1, le=120)
    pdf_pages: list[Annotated[int, Field(ge=1)]] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "PDF pages to attach as images for layout; omit for text."
        ),
    )

    @field_validator("pdf_pages")
    @classmethod
    def normalize_pdf_pages(cls, value: list[int]) -> list[int]:
        return sorted(set(value))


class FetchTranscriptArguments(ToolArguments):
    url: str = Field(
        min_length=1,
        max_length=1_200,
        description=(
            "Complete remote media page URL. Pass a URL, not a video ID, caption endpoint, "
            "attachment ID, or file path."
        ),
    )
    language: str | None = Field(
        default=None,
        min_length=2,
        max_length=35,
        pattern=r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$",
        description=(
            "Preferred existing caption language as a BCP 47 tag, for example de or en-US. "
            "Omit unless the user requests a language. If unavailable, the host selects the "
            "original language; this never translates or transcribes media."
        ),
    )


class GenerateImageArguments(ToolArguments):
    prompt: str = Field(min_length=1, max_length=20_000)
    width: int | None = Field(default=None, ge=64, le=4_096)
    height: int | None = Field(default=None, ge=64, le=4_096)
    seed: int | None = Field(default=None, ge=0)


class GenerateQrCodeArguments(ToolArguments):
    content: str = Field(min_length=1, max_length=2_000)

    @field_validator("content")
    @classmethod
    def content_fits_qr_code(cls, value: str) -> str:
        if len(value.encode("utf-8")) > QR_CODE_MAX_CONTENT_BYTES:
            raise ValueError("content exceeds the QR code byte limit")
        return value


class ReadQrCodeArguments(ToolArguments):
    attachment_id: UUID


class EditImageArguments(ToolArguments):
    prompt: str = Field(
        min_length=1,
        max_length=20_000,
        description=(
            "Standalone edit contract. Put the base edit target first as Image 1, identify every "
            "other input only by its reference role, and state the requested delta before the "
            "invariants. For localized edits, keep Image 1 as the same scene and preserve its canvas, "
            "crop, framing, camera, perspective, geometry, identity, and unrelated content. Quote "
            "visible text that must remain verbatim."
        ),
    )
    attachment_ids: list[UUID] = Field(
        min_length=1,
        max_length=5,
        description=(
            "One to five image attachment IDs from the current session, ordered as Image 1, "
            "Image 2, and so on."
        ),
    )

    @field_validator("attachment_ids")
    @classmethod
    def unique_attachment_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("attachment_ids must be unique")
        return value


class GenerateSpeechArguments(ToolArguments):
    text: str = Field(min_length=1, max_length=100_000)
    voice: str | None = Field(default=None, min_length=1, max_length=500)
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


class GenerateAudioArguments(ToolArguments):
    prompt: str = Field(min_length=1, max_length=20_000)
    duration_seconds: float | None = Field(default=None, gt=0, le=3_600)
    lyrics: str | None = Field(default=None, max_length=100_000)
    vocal_language: str | None = Field(default=None, min_length=2, max_length=35)
    seed: int | None = Field(default=None, ge=0)


class TranscribeAudioArguments(ToolArguments):
    attachment_id: UUID
    language: str | None = Field(default=None, min_length=2, max_length=35)


class AnalyzeVideoArguments(ToolArguments):
    root: Literal["session", "workspace"]
    path: str = Field(min_length=1, max_length=2_000)
    language: str | None = Field(default=None, min_length=2, max_length=35)
    analysis_mode: Literal["speech", "overview", "fine_grained"] = Field(
        default="overview",
        validation_alias=AliasChoices("analysis_mode", "detail"),
        description=(
            "speech for spoken content; overview for visual summaries or chronology; fine_grained for "
            "exact visual steps or troubleshooting."
        ),
    )
    focus: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
        description="Requested deliverable or question.",
    )

    @field_validator("analysis_mode", mode="before")
    @classmethod
    def migrate_detail_names(cls, value: Any) -> Any:
        return {"standard": "overview", "detailed": "fine_grained"}.get(value, value)


class SessionReadArguments(ToolArguments):
    session_id: UUID | None = Field(
        default=None,
        description="Target session UUID. Omit only to read the current session.",
    )
    after_message_id: UUID | None = Field(
        default=None,
        description="Continue after session_read nextAfterMessageId; never invent it.",
    )
    around_message_id: UUID | None = Field(
        default=None,
        description="Use an ID from session discovery or compacted history; never invent it.",
    )
    limit: int = Field(default=30, ge=1, le=50)

    @model_validator(mode="after")
    def one_position(self) -> SessionReadArguments:
        if self.after_message_id is not None and self.around_message_id is not None:
            raise ValueError("after_message_id and around_message_id are mutually exclusive")
        return self


class SessionToolResultReadArguments(ToolArguments):
    message_id: UUID
    offset: int = Field(default=0, ge=0)


class SessionSearchArguments(ToolArguments):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=10, ge=1, le=20)


class SessionBrowseArguments(ToolArguments):
    limit: int = Field(default=10, ge=1, le=20)


class MemoryWriteArguments(ToolArguments):
    action: Literal["create", "update", "forget"]
    memory_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    content: str | None = Field(default=None, min_length=1, max_length=20_000)
    category: MemoryCategory | None = Field(
        default=None,
        description=(
            "Use exactly one of context, fact, goal, identity, or preference. "
            "Names and personal identity use identity; occupations use fact."
        )
    )
    pin: bool = Field(
        default=False,
        description=(
            "Set true when the latest user explicitly asks to always consider this memory "
            "in future answers, including answers about a stated topic. Plain remember or "
            "store wording stays false. Example: 'Always consider this for meal suggestions' "
            "means true. False does not unpin."
        ),
    )

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("content must not be blank")
        return cleaned

    @model_validator(mode="after")
    def memory_id_matches_action(self) -> MemoryWriteArguments:
        if self.action in {"update", "forget"} and self.memory_id is None:
            raise ValueError("memory_id is required for updates and forgetting")
        if self.action in {"update", "forget"} and self.expected_revision is None:
            raise ValueError("expected_revision is required for updates and forgetting")
        if self.action == "create":
            self.memory_id = None
            self.expected_revision = None
        if self.action in {"create", "update"} and (
            self.content is None or self.category is None
        ):
            raise ValueError("content and category are required for creates and updates")
        if self.action == "forget":
            if self.pin:
                raise ValueError("pin is not allowed for forgetting")
            self.content = None
            self.category = None
        return self


class MemoryRetrieveArguments(ToolArguments):
    purpose: Literal["context", "write"] = Field(
        default="context",
        description="Use write when this lookup prepares a memory_write; otherwise context.",
    )
    mode: Literal["search", "browse"] = Field(
        description="search for a topic; browse for an overview."
    )
    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=2_000,
        description="Required for search and forbidden for browse.",
    )
    category: MemoryCategory | None = Field(
        default=None, description="Optional memory-category filter."
    )
    cursor_updated_at: datetime | None = Field(
        default=None,
        description="Timestamp from nextCursor; pair with cursor_id.",
    )
    cursor_id: UUID | None = Field(
        default=None,
        description="Memory ID from nextCursor; pair with cursor_updated_at.",
    )
    limit: int = Field(default=10, ge=1, le=50, description="Maximum returned items.")

    @model_validator(mode="after")
    def query_matches_mode(self) -> MemoryRetrieveArguments:
        if self.mode == "search" and self.query is None:
            raise ValueError("query is required for search mode")
        if self.mode == "browse" and self.query is not None:
            raise ValueError("query is not allowed for browse mode")
        if (self.cursor_updated_at is None) != (self.cursor_id is None):
            raise ValueError("both cursor fields are required together")
        if self.mode == "search" and self.cursor_updated_at is not None:
            raise ValueError("cursor is only allowed for browse mode")
        return self


class UpdateAgentIdentityArguments(ToolArguments):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    instructions: str | None = Field(
        default=None,
        max_length=16_000,
        description=(
            "Copy explicit user-supplied persona instructions exactly. Do not paraphrase, "
            "translate, strengthen, or add qualifiers."
        ),
    )

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("display_name must not be null")
        return normalize_agent_display_name(value)

    @field_validator("instructions")
    @classmethod
    def normalize_instructions(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("instructions must not be null")
        return value.strip()

    @model_validator(mode="after")
    def require_change(self) -> UpdateAgentIdentityArguments:
        if self.display_name is None and self.instructions is None:
            raise ValueError("at least one identity field is required")
        return self


class BuildSkillArguments(ToolArguments):
    brief: str = Field(
        min_length=1,
        max_length=2_000,
        description="Compact description of the skill the user wants.",
    )
    name_hint: str | None = Field(
        default=None,
        min_length=1,
        max_length=64,
        description="Optional short name suggested by the user.",
    )

    @field_validator("brief", "name_hint")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class EditSkillArguments(ToolArguments):
    skill_id: UUID = Field(description="Exact ID copied from the available skill catalog.")
    brief: str = Field(
        min_length=1,
        max_length=2_000,
        description="Compact description of the requested changes.",
    )

    @field_validator("brief")
    @classmethod
    def normalize_brief(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("brief must not be blank")
        return cleaned


class LoadSkillArguments(ToolArguments):
    skill_id: UUID = Field(description="Exact ID copied from the available skill catalog.")


class AutomationScheduleArguments(ToolArguments):
    cron: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
    )
    timezone: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    run_at: datetime | None = None
    valid_until: datetime | None = None
    enabled: bool = True

    @field_validator("cron", "timezone")
    @classmethod
    def normalize_schedule_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not (cleaned := value.strip()):
            raise ValueError("schedule value must not be blank")
        return cleaned

    @model_validator(mode="after")
    def schedule_is_valid(self) -> AutomationScheduleArguments:
        if self.valid_until is not None and (
            self.valid_until.tzinfo is None or self.valid_until.utcoffset() is None
        ):
            raise ValueError("valid_until must be timezone-aware")
        if self.run_at is None:
            if self.cron is None or self.timezone is None:
                raise ValueError("cron and timezone are required together")
            next_run_at(self.cron, self.timezone, datetime.now(UTC))
        elif (
            self.cron is not None
            or self.valid_until is not None
            or self.run_at.tzinfo is None
            or self.run_at.utcoffset() is None
        ):
            raise ValueError(
                "run_at must be timezone-aware and cannot use recurrence fields"
            )
        else:
            self.timezone = None
        return self


class AutomationDefinitionArguments(ToolArguments):
    name: str = Field(min_length=1, max_length=240)
    task: str = Field(min_length=1, max_length=40_000)
    skill_id: UUID | None = None
    enabled: bool = True
    schedules: list[AutomationScheduleArguments] = Field(min_length=1, max_length=50)
    output_session_required: bool = False
    archive_after_success: bool = False

    @field_validator("name", "task")
    @classmethod
    def normalize_automation_text(cls, value: str) -> str:
        if not (cleaned := value.strip()):
            raise ValueError("automation text must not be blank")
        return cleaned

    @model_validator(mode="after")
    def schedules_are_unique(self) -> AutomationDefinitionArguments:
        keys = [
            ("once", item.run_at)
            if item.run_at is not None
            else ("cron", item.cron, item.timezone)
            for item in self.schedules
        ]
        if len(keys) != len(set(keys)):
            raise ValueError("automation schedules must be unique")
        if self.archive_after_success and (
            len(self.schedules) != 1 or self.schedules[0].run_at is None
        ):
            raise ValueError("archive_after_success requires one one-time schedule")
        return self


class ListAutomationsArguments(ToolArguments):
    limit: int = Field(default=20, ge=1, le=50)


class GetAutomationArguments(ToolArguments):
    automation_id: UUID


class CreateAutomationArguments(AutomationDefinitionArguments):
    deliver_to_current_session: bool = False


class EditAutomationArguments(AutomationDefinitionArguments):
    automation_id: UUID
    expected_revision: int = Field(ge=1)
    output_mode: Literal["keep", "current", "none"] = "keep"
    copy_runtime_from_current_session: bool = False

    @model_validator(mode="after")
    def required_output_is_possible(self) -> EditAutomationArguments:
        if self.output_session_required and self.output_mode == "none":
            raise ValueError("required output cannot use none")
        return self


class RunAutomationArguments(ToolArguments):
    automation_id: UUID
    expected_revision: int = Field(ge=1)


class DeleteAutomationArguments(RunAutomationArguments):
    pass
