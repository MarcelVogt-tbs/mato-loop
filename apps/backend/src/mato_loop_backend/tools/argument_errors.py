"""Explicit, host-controlled validation hints; never pass rejected values here."""

from pydantic_core import PydanticCustomError


def invalid_argument(hint: str, *, field: str | None = None, reason: str = "invalid_value") -> PydanticCustomError:
    return PydanticCustomError("tool_argument_invalid", hint, {
        "reason": reason, "hint": hint, **({"field": field} if field else {}),
    })
