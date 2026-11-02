"""Model-facing validation details without rejected values or exception messages."""

from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema.exceptions import best_match
from pydantic import ValidationError

from mato_loop_backend.tools.contracts import ToolResult


def _argument_path(error: dict[str, Any], arguments: Any) -> tuple[list[str | int], Any]:
    path = []
    value = arguments
    location = error.get("loc", ())
    for index, part in enumerate(location):
        if isinstance(value, dict) and part in value:
            path.append(part)
            value = value[part]
        elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
            path.append(part)
            value = value[part]
        elif error["type"] == "missing" and index == len(location) - 1:
            path.append(part)
        # Other segments name Pydantic union branches, not input fields.
    return path, value


def argument_error(
    exc: ValidationError | JsonSchemaValidationError,
    arguments: Any,
    *,
    prefix: str | None = None,
) -> ToolResult:
    params: dict[str, Any] = {"reason": "invalid_value"}
    if isinstance(exc, JsonSchemaValidationError):
        exc = best_match([exc])
        path = list(exc.absolute_path)
        keyword = exc.validator
        if keyword == "required":
            path.append(next(name for name in exc.validator_value if name not in exc.instance))
        elif keyword == "additionalProperties" and isinstance(exc.instance, dict):
            # Pattern properties need their own matching; do not guess the rejected key.
            if not exc.schema.get("patternProperties"):
                path.extend(sorted(set(exc.instance) - exc.schema.get("properties", {}).keys())[:1])
        params["reason"] = {
            "required": "missing", "type": "invalid_type", "enum": "invalid_choice",
            "const": "invalid_choice", "additionalProperties": "extra_field",
            "minLength": "too_short", "maxLength": "too_long",
            "minItems": "too_short", "maxItems": "too_long",
            "minProperties": "too_short", "maxProperties": "too_long",
            "minimum": "below_minimum", "exclusiveMinimum": "below_minimum",
            "maximum": "above_maximum", "exclusiveMaximum": "above_maximum",
            "pattern": "invalid_format", "format": "invalid_format",
        }.get(keyword, "invalid_value")
        if keyword == "type":
            params["expectedType"] = exc.validator_value
        elif keyword in {
            "minLength", "maxLength", "minItems", "maxItems", "minProperties", "maxProperties",
            "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
        }:
            params[keyword] = exc.validator_value
    else:
        # Prefer the matching structured alternative over a plain-type union error.
        error = max(exc.errors(include_url=False, include_input=False), key=lambda item: (
            len(_argument_path(item, arguments)[0]), item["type"] == "tool_argument_invalid",
        ))
        kind, context = error["type"], error.get("ctx", {})
        path, value = _argument_path(error, arguments)
        expected = {
            "string_type": "string", "int_type": "integer", "int_parsing": "integer",
            "int_from_float": "integer", "float_type": "number", "float_parsing": "number",
            "bool_type": "boolean", "bool_parsing": "boolean", "list_type": "array",
            "dict_type": "object", "model_type": "object",
        }.get(kind)
        if expected:
            params.update(reason="invalid_type", expectedType=expected)
        else:
            params["reason"] = {
                "missing": "missing", "uuid_parsing": "invalid_uuid", "uuid_type": "invalid_uuid",
                "literal_error": "invalid_choice", "enum": "invalid_choice",
                "extra_forbidden": "extra_field", "string_too_short": "too_short",
                "string_too_long": "too_long", "too_short": "too_short", "too_long": "too_long",
                "greater_than": "below_minimum", "greater_than_equal": "below_minimum",
                "less_than": "above_maximum", "less_than_equal": "above_maximum",
                "string_pattern_mismatch": "invalid_format", "url_parsing": "invalid_format",
                "date_parsing": "invalid_format", "date_from_datetime_parsing": "invalid_format",
                "datetime_parsing": "invalid_format", "datetime_from_date_parsing": "invalid_format",
            }.get(kind, "invalid_value")
        for source, target in {
            "min_length": "minLength", "max_length": "maxLength",
            "gt": "exclusiveMinimum", "ge": "minimum", "lt": "exclusiveMaximum", "le": "maximum",
        }.items():
            if type(context.get(source)) in (int, float):
                if kind in {"too_short", "too_long"} and source in {"min_length", "max_length"}:
                    target = ("min" if source == "min_length" else "max") + ("Properties" if isinstance(value, dict) else "Items")
                params[target] = context[source]
        if kind in {"tool_argument_invalid", "personal_settings_invalid"}:
            if isinstance(context.get("field"), str):
                path.append(context["field"])
            if kind == "tool_argument_invalid":
                params.update({key: context[key] for key in ("reason", "hint") if key in context})
    field = ".".join(str(part) for part in path)
    field = ".".join(part for part in (prefix, field) if part) or None
    return ToolResult.error("tool.invalidArguments", field=field, params=params)
