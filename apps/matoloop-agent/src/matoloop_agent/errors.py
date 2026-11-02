from __future__ import annotations

from pydantic import ValidationError


def argument_details(exc: ValidationError) -> dict[str, str | int | float]:
    """Serialize schema constraints, not Pydantic messages, inputs or exception context."""
    error = exc.errors(include_url=False, include_input=False)[0]
    kind, context = error["type"], error.get("ctx", {})
    params: dict[str, str | int | float] = {
        "reason": {
            "missing": "missing",
            "extra_forbidden": "extra_field",
            "literal_error": "invalid_choice",
            "string_pattern_mismatch": "invalid_format",
            "string_too_short": "too_short",
            "string_too_long": "too_long",
            "too_short": "too_short",
            "too_long": "too_long",
            "greater_than": "below_minimum",
            "greater_than_equal": "below_minimum",
            "less_than": "above_maximum",
            "less_than_equal": "above_maximum",
        }.get(kind, "invalid_type" if kind.endswith(("_type", "_parsing")) else "invalid_value"),
    }
    if error["loc"]:
        params["field"] = ".".join(str(part) for part in error["loc"])
    for source, target in {
        "min_length": "minLength",
        "max_length": "maxLength",
        "gt": "exclusiveMinimum",
        "ge": "minimum",
        "lt": "exclusiveMaximum",
        "le": "maximum",
    }.items():
        if type(context.get(source)) in (int, float):
            if kind in {"too_short", "too_long"} and source in {"min_length", "max_length"}:
                target = ("min" if source == "min_length" else "max") + (
                    "Properties" if context.get("field_type") == "Dictionary" else "Items"
                )
            params[target] = context[source]
    if kind == "operation_argument_invalid":
        params["hint"] = context["hint"]
    return params


class AgentError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        params: dict[str, str | bool | int | float] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.params = params

    @classmethod
    def invalid_arguments(
        cls, hint: str, *, field: str, reason: str = "invalid_value", line: int | None = None
    ) -> AgentError:
        """Only host-controlled hints and locations, never rejected content."""
        return cls(
            "request.invalid",
            hint,
            {
                "field": field,
                "reason": reason,
                "hint": hint,
                **({"line": line} if line is not None else {}),
            },
        )
