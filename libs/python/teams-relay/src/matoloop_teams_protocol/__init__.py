from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

PROTOCOL_VERSION = 1
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_BODY_BYTES = 2 * 1024 * 1024


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Identity(WireModel):
    tenant_id: UUID
    app_id: UUID
    user_id: UUID
    conversation_id: str = Field(min_length=1, max_length=1024)
    display_name: str = Field(default="", max_length=160)


class Blob(WireModel):
    id: UUID
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=160)
    size: int = Field(ge=1, le=MAX_FILE_BYTES)


class Event(WireModel):
    version: Literal[1] = PROTOCOL_VERSION
    id: UUID
    identity: Identity
    generation: UUID
    received_at: AwareDatetime
    expires_at: AwareDatetime
    kind: Literal["message", "action"]
    text: str = Field(default="", max_length=200_000)
    message_id: str = Field(min_length=1, max_length=1024)
    source_message_id: str | None = Field(default=None, max_length=1024)
    action: dict[str, JsonValue] = Field(default_factory=dict)
    blobs: list[Blob] = Field(default_factory=list, max_length=10)


class PollResult(WireModel):
    version: Literal[1] = PROTOCOL_VERSION
    generation: UUID
    events: list[Event] = Field(default_factory=list, max_length=20)


class Receipt(WireModel):
    event_id: UUID
    generation: UUID
    status: Literal["admitted", "rejected"]


class Delivery(WireModel):
    version: Literal[1] = PROTOCOL_VERSION
    id: UUID
    identity: Identity
    kind: Literal["message", "typing", "file"]
    text: str = Field(default="", max_length=30_000)
    text_format: Literal["plain", "markdown"] = "plain"
    card: dict[str, JsonValue] | None = None
    blob: Blob | None = None
    source_message_id: str | None = Field(default=None, max_length=1024)


class DeliveryResult(WireModel):
    id: UUID
    status: Literal["pending", "sending", "sent", "failed", "unknown", "offered", "declined"]
    message_ids: list[Annotated[str, Field(max_length=1024)]] = Field(default_factory=list)


class StreamPreview(WireModel):
    identity: Identity
    source_message_id: str = Field(min_length=1, max_length=1024)
    text: str = Field(min_length=1, max_length=8_000)
    informative: bool = False


class RelayStatus(WireModel):
    version: Literal[1] = PROTOCOL_VERSION
    app_id: UUID
    tenant_id: UUID
    online: bool
