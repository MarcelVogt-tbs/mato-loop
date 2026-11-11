"""Bounded session context data; never host instructions or authority."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTEXT_STATE_KEY = "context_state"
MAX_CONTEXT_CHARACTERS = 12_000
MAX_CONTEXT_BLOCKS = 16


class ContextBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    text: str = Field(min_length=1, max_length=MAX_CONTEXT_CHARACTERS)


class SessionContextState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0, strict=True)
    blocks: list[ContextBlock] = Field(default_factory=list, max_length=MAX_CONTEXT_BLOCKS)
    # Host-assigned classification, including legacy/unclassified context.
    trust_level: str = Field(default="private", pattern=r"^(public|private)$")

    @model_validator(mode="after")
    def bounded_unique_blocks(self):
        if len({block.key for block in self.blocks}) != len(self.blocks):
            raise ValueError("Context block keys must be unique.")
        if sum(len(block.key) + len(block.text) for block in self.blocks) > MAX_CONTEXT_CHARACTERS:
            raise ValueError("Combined context exceeds 12,000 characters.")
        return self


def session_context_state(session) -> SessionContextState:
    return SessionContextState.model_validate((session.session_profile or {}).get(CONTEXT_STATE_KEY, {}))


def replace_session_context(session, blocks, expected_revision: int, *, trust_level: str,
                            expected_trust: str | None = None) -> SessionContextState:
    """Replace validated context on an owner-checked, locked session; caller commits."""
    from mato_loop_backend.sessions.service import SessionServiceError

    current = session_context_state(session)
    if expected_trust is not None and current.trust_level != expected_trust:
        raise SessionServiceError(403, "permission.denied")
    if current.revision != expected_revision:
        raise SessionServiceError(409, "http.conflict", params={"revision": current.revision})
    state = SessionContextState(revision=current.revision + 1, blocks=blocks, trust_level=(
        "private" if current.blocks and current.trust_level == "private" else trust_level
    ))
    session.session_profile = {**(session.session_profile or {}), CONTEXT_STATE_KEY: state.model_dump(mode="json")}
    return state
