from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ApiError(HTTPException):
    def __init__(
        self,
        status_code: int,
        code: str,
        *,
        field: str | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=code, headers=headers)
        self.code = code
        self.field = field
        self.params = params


def _payload(
    code: str,
    *,
    field: str | None = None,
    params: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    error: dict[str, Any] = {"code": code}
    if field:
        error["field"] = field
    if params:
        error["params"] = params
    return {"error": error}


def _validation_payload(exc: RequestValidationError) -> dict[str, dict[str, Any]]:
    item = exc.errors()[0] if exc.errors() else {}
    error_type = item.get("type", "")
    context = item.get("ctx") or {}
    location = item.get("loc") or ()
    field = next(
        (
            part
            for part in reversed(location)
            if isinstance(part, str) and part not in {"body", "cookie", "header", "path", "query"}
        ),
        None,
    )

    code = "validation.invalid"
    params: dict[str, Any] = {}
    if error_type == "missing":
        code = "validation.required"
    elif error_type in {"string_too_short", "too_short"}:
        code = "validation.tooShort"
        minimum = context.get("min_length")
        if isinstance(minimum, int):
            params["minLength"] = minimum
    elif error_type in {"string_too_long", "too_long"}:
        code = "validation.tooLong"
        maximum = context.get("max_length")
        if isinstance(maximum, int):
            params["maxLength"] = maximum
    elif error_type in {"url_parsing", "url_scheme"}:
        code = "validation.invalidUrl"
    elif error_type in {"uuid_parsing", "uuid_type"}:
        code = "validation.invalidId"
    elif error_type in {"enum", "literal_error"}:
        code = "validation.invalidChoice"
    elif error_type in {
        "bool_parsing",
        "bool_type",
        "dict_type",
        "int_parsing",
        "int_type",
        "list_type",
        "string_type",
    }:
        code = "validation.invalidType"
    elif error_type == "string_pattern_mismatch":
        code = "validation.invalidFormat"

    return _payload(code, field=field, params=params or None)


_HTTP_ERROR_CODES = {
    400: "http.badRequest",
    401: "http.unauthorized",
    403: "http.forbidden",
    404: "http.notFound",
    405: "http.methodNotAllowed",
    409: "http.conflict",
    422: "http.unprocessable",
    429: "http.rateLimited",
    502: "http.badGateway",
    503: "http.serviceUnavailable",
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, field=exc.field, params=exc.params),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content=_validation_payload(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _HTTP_ERROR_CODES.get(exc.status_code, "http.requestFailed")
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(code),
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled API error for %s %s", request.method, request.url.path, exc_info=exc)
        return JSONResponse(status_code=500, content=_payload("internal.unexpected"))
