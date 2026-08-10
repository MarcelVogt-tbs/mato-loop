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
    "agent.researchBudgetReached": "The current-turn research budget has been reached.",
    "agent.repeatedToolCall": "The model repeated a failed tool call without changing its arguments.",
    "agent.toolCallLimitReached": "The agent reached the maximum number of tool calls.",
    "attachment.tooLarge": "The attachment is too large.",
    "file.changed": "The file changed since it was opened.",
    "file.fileNotFound": "The file was not found.",
    "file.invalidEncoding": "The file is not editable UTF-8 text.",
    "file.invalidName": "The file name is invalid.",
    "file.notAFile": "The target is not a regular file.",
    "file.previewUnavailable": "A preview is not available for this file.",
    "file.renameUnavailable": "Files cannot be renamed on this system.",
    "file.sessionRequired": "A persisted session is required for this file root.",
    "file.sessionQuotaExceeded": "The session storage limit has been reached.",
    "file.storageFull": "There is not enough storage available for this file.",
    "file.tooLarge": "The file is too large.",
    "workspace.homeMutationForbidden": "The agent home itself cannot be changed.",
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
    "integration.authenticationFailed": "The integration credentials were rejected.",
    "integration.attachmentTooLarge": "The file is too large to be attached.",
    "integration.searchUnavailable": "The required web search integration is unavailable.",
    "integration.changed": "The integration changed while it was being configured.",
    "integration.invalidConfiguration": "The integration configuration is invalid.",
    "integration.invalidResponse": "The integration returned an invalid response.",
    "integration.notFound": "The integration was not found.",
    "integration.operationUnsupported": "The integration does not support this operation.",
    "integration.outcomeUnknown": "The remote outcome is unknown. Check the remote service before retrying.",
    "integration.permissionDenied": "The integration does not permit this operation.",
    "integration.remoteChanged": "The remote item changed. Load it again before retrying.",
    "integration.remoteNotFound": "The remote item was not found.",
    "integration.resourceDiscoveryFailed": "The integration resources could not be loaded.",
    "integration.resourcePoliciesUnsupported": "The integration has no configurable resources.",
    "integration.responseTooLarge": "The integration response is too large.",
    "integration.staleReference": (
        "The item reference is outdated or does not belong to the named container. List or "
        "search the items again to obtain a current reference, then retry. Do not reuse a "
        "reference from an earlier tool result."
    ),
    "integration.unavailable": "The integration is unavailable.",
    "attachment.invalidVideo": "The video file is invalid or unsupported.",
    "provider.disabled": "The model provider is disabled.",
    "provider.imageLimitExceeded": "The request contains too many images.",
    "provider.notFound": "The model provider is unavailable.",
    "provider.requestFailed": "The model provider request failed.",
    "provider.unavailable": "The model provider is unavailable.",
    "privacy.privateContentBlocked": "Private image content cannot be sent to the selected public model.",
    "question.expired": "The question expired before an answer was received.",
    "question.answerTooLarge": "The submitted answer is too large.",
    "question.exclusiveCallRequired": "The question tool must be called without other tools in the same response.",
    "question.invalidAnswer": "The submitted answer does not match the question.",
    "question.rejected": "The user dismissed the question without answering.",
    "session.modelToolsUnavailable": "The selected model does not support the required tools.",
    "session.modelUnavailable": "The selected model is unavailable.",
    "session.messageNotFound": "The chat message was not found.",
    "session.retryUnavailable": "The failed response can no longer be retried.",
    "skill.duplicate": "A skill with this name already exists.",
    "skill.alreadyActive": "A different skill is already active for this run.",
    "skill.creationModelFailed": "The skill creation model could not produce a valid draft.",
    "skill.draftInvalid": "The skill draft is invalid or unavailable.",
    "skill.limitReached": "The maximum number of skills has been reached.",
    "skill.loadMustBeExclusive": "The skill loader must be called without other tools in the same response.",
    "skill.notFound": "The skill was not found.",
    "tool.executionFailed": "The tool execution failed.",
    "tool.executionInterrupted": (
        "The tool execution was interrupted and its outcome is unknown. Do not infer success or "
        "failure without verification."
    ),
    "validation.invalid": "The request contains invalid data.",
    "validation.required": "A required value is missing.",
    "video.analysisFailed": "The video could not be analyzed.",
    "video.analysisTimeout": "The video analysis timed out.",
    "video.analysisUnavailable": "No eligible video analysis model is available.",
    "video.audioExtractionFailed": "The video audio track could not be extracted.",
    "video.audioAnalysisUnavailable": "No eligible transcription model is available for the video audio.",
    "video.emptyAnalysis": "The video model returned no usable analysis.",
    "video.frameExtractionFailed": "The video frames could not be extracted.",
    "webfetch.addressBlocked": "The web server's address is not allowed.",
    "webfetch.connectionFailed": "The web server could not be reached.",
    "webfetch.contentTypeMismatch": "The web server sent content that does not match its declared type.",
    "webfetch.contentTypeUnsupported": "The web server sent an unsupported content type.",
    "webfetch.dnsFailed": "The web server's host name could not be resolved.",
    "webfetch.pdfInvalid": "The PDF document is invalid or unsupported.",
    "webfetch.pdfRenderFailed": "The PDF page could not be rendered.",
    "webfetch.pdfVisionFailed": "The PDF page could not be read by the vision model.",
    "webfetch.pdfVisionUnavailable": "No eligible vision model is available for this PDF.",
    "webfetch.requestFailed": "The web server rejected the request.",
    "webfetch.responseLimitInvalid": "The web response size limit is invalid.",
    "webfetch.responseTooLarge": "The web response exceeds the configured size limit.",
    "webfetch.timeout": "The web server did not answer in time.",
    "webfetch.tlsFailed": "The web server's TLS certificate could not be verified.",
    "webfetch.tooManyRedirects": "The web server sent too many redirects.",
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
    "question": "The question request could not be completed.",
    "session": "The session request could not be completed.",
    "skill": "The skill operation could not be completed.",
    "stream": "The event stream failed.",
    "tool": "The tool operation could not be completed.",
    "validation": "The request contains invalid data.",
    "video": "The video could not be analyzed.",
    "webfetch": "The web request could not be completed.",
    "workspace": "The workspace operation could not be completed.",
}

_PROVIDER_CODES = {
    "provider.authenticationFailed",
    "provider.configurationInvalid",
    "provider.discoveryFailed",
    "provider.discoveryUnsupported",
    "provider.endpointNotFound",
    "provider.imageLimitExceeded",
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
