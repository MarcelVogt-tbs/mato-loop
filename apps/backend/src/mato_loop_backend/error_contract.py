from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

_CODE_PATTERN = re.compile(r"^[a-z][A-Za-z]*(?:\.[a-z][A-Za-z]*)+$")
_GENERIC_MESSAGE = "The action failed."

_MESSAGES = {
    "agent.backendRestarted": "The agent run was interrupted by a backend restart.",
    "agent.failed": "The agent run failed.",
    "agent.requiredWebSearchFailed": "The required web search failed.",
    "agent.requiredWebSearchMissing": "The model did not perform the required web search.",
    "agent.requiredWebSearchUnsupported": "The selected model cannot perform the required web search.",
    "agent.requiredMemoryWriteMissing": "The model did not complete the required memory write.",
    "agent.researchSourcesInvalid": "The researched answer could not be supported by valid current sources.",
    "agent.repeatedToolCall": "The model repeated a failed tool call without changing its arguments.",
    "agent.toolCallLimitReached": "The agent reached the maximum number of tool calls.",
    "attachment.tooLarge": "The attachment is too large.",
    "http.badGateway": "A connected service is unavailable.",
    "http.badRequest": "The request could not be processed.",
    "http.conflict": "The request conflicts with the current state.",
    "http.forbidden": "The request is not permitted.",
    "http.methodNotAllowed": "The request method is not supported.",
    "http.notFound": "The requested resource was not found.",
    "http.rateLimited": "Too many requests were received.",
    "http.requestFailed": "The request failed.",
    "http.serviceUnavailable": "The service is temporarily unavailable.",
    "http.unauthorized": "Authentication is required.",
    "http.unprocessable": "The request data could not be processed.",
    "internal.unexpected": "An unexpected error occurred.",
    "integration.searchUnavailable": "The required web search integration is unavailable.",
    "provider.disabled": "The model provider is disabled.",
    "provider.notFound": "The model provider is unavailable.",
    "provider.requestFailed": "The model provider request failed.",
    "provider.unavailable": "The model provider is unavailable.",
    "session.modelToolsUnavailable": "The selected model does not support the required tools.",
    "session.modelUnavailable": "The selected model is unavailable.",
    "tool.executionFailed": "The tool execution failed.",
    "tool.executionInterrupted": "The tool execution was interrupted.",
    "validation.invalid": "The request contains invalid data.",
    "validation.required": "A required value is missing.",
}

_NAMESPACE_MESSAGES = {
    "agent": "The agent run could not be completed.",
    "approval": "The approval request could not be completed.",
    "attachment": "The attachment request could not be completed.",
    "auth": "Authentication failed.",
    "file": "The file operation could not be completed.",
    "http": "The request could not be completed.",
    "integration": "The integration request could not be completed.",
    "internal": "An unexpected error occurred.",
    "memory": "The memory operation could not be completed.",
    "oauth": "The authorization request could not be completed.",
    "permission": "The requested action is not permitted.",
    "privacy": "The privacy operation could not be completed.",
    "provider": "The model provider request could not be completed.",
    "session": "The session request could not be completed.",
    "stream": "The event stream failed.",
    "tool": "The tool operation could not be completed.",
    "validation": "The request contains invalid data.",
    "webfetch": "The web request could not be completed.",
    "workspace": "The workspace operation could not be completed.",
}

_PROVIDER_CODES = {
    "provider.authenticationFailed",
    "provider.configurationInvalid",
    "provider.discoveryFailed",
    "provider.discoveryUnsupported",
    "provider.endpointNotFound",
    "provider.invalidResponse",
    "provider.modelCapabilityUnavailable",
    "provider.notConnected",
    "provider.rateLimited",
    "provider.requestFailed",
    "provider.responseTooLarge",
    "provider.timeout",
    "provider.unavailable",
    "privacy.pseudonymizationFailed",
    "privacy.pseudonymizationUnavailable",
}


class ErrorDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    field: str | None = None
    params: dict[str, JsonValue] | None = None

    def payload(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def describe_error(
    code: str | None,
    *,
    field: str | None = None,
    params: dict[str, Any] | None = None,
    fallback_code: str = "internal.unexpected",
) -> ErrorDescriptor:
    candidate = code if isinstance(code, str) and _CODE_PATTERN.fullmatch(code) else fallback_code
    namespace = candidate.partition(".")[0]
    safe_params = None
    if params:
        try:
            safe_params = ErrorDescriptor(
                code=candidate,
                message="placeholder",
                params=params,
            ).params
        except ValidationError:
            safe_params = None
    safe_field = field if isinstance(field, str) and len(field) <= 200 else None
    return ErrorDescriptor(
        code=candidate,
        message=_MESSAGES.get(candidate, _NAMESPACE_MESSAGES.get(namespace, _GENERIC_MESSAGE)),
        field=safe_field,
        params=safe_params,
    )


def error_payload(
    code: str | None,
    *,
    field: str | None = None,
    params: dict[str, Any] | None = None,
    fallback_code: str = "internal.unexpected",
) -> dict[str, dict[str, Any]]:
    descriptor = describe_error(
        code,
        field=field,
        params=params,
        fallback_code=fallback_code,
    )
    return {"error": descriptor.payload()}


def safe_provider_error_code(code: str | None, status_code: int | None = None) -> str:
    if code in _PROVIDER_CODES:
        return code
    if status_code in {401, 403}:
        return "provider.authenticationFailed"
    if status_code == 404:
        return "provider.endpointNotFound"
    if status_code == 429:
        return "provider.rateLimited"
    if status_code is not None and status_code >= 500:
        return "provider.unavailable"
    return "provider.requestFailed"
