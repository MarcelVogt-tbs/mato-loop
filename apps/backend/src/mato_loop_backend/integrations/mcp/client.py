from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthRegistrationError
from mcp.client.streamable_http import streamable_http_client

# Bound discovery memory, not the number of tools or pages a server exposes.
MAX_MCP_CATALOG_BYTES = 32 * 1024 * 1024
MAX_MCP_RESULT_BYTES = 1024 * 1024


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, max_bytes: int) -> None:
        self.stream = stream
        self.max_bytes = max_bytes

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > self.max_bytes:
                raise McpClientError("mcp.responseTooLarge")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class _BoundedTransport(httpx.AsyncBaseTransport):
    interactive_auth = False

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport | None = None,
        authentication_failed: Callable[[], Awaitable[None]] | None = None,
        *,
        max_response_bytes: int = MAX_MCP_RESULT_BYTES,
    ) -> None:
        self.transport = transport or httpx.AsyncHTTPTransport(retries=0)
        self.authentication_failed = authentication_failed
        self.max_response_bytes = max_response_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.transport.handle_async_request(request)
        response.stream = _BoundedStream(response.stream, self.max_response_bytes)
        if self.authentication_failed and (
            response.status_code == 401
            or (
                response.status_code == 403
                and "insufficient_scope" in response.headers.get("www-authenticate", "")
            )
        ):
            await response.aclose()
            await self.authentication_failed()
            raise McpClientError("mcp.authorizationRequired")
        if request.method == "POST" and response.status_code >= 400:
            try:
                mcp_request = json.loads(request.content).get("jsonrpc") == "2.0"
            except (ValueError, AttributeError, httpx.RequestNotRead):
                mcp_request = False
            code = (
                "mcp.endpointNotFound" if response.status_code == 404 else
                "mcp.authenticationFailed" if response.status_code in {401, 403} else
                "mcp.protocolError" if response.status_code < 500 else "mcp.unavailable"
            )
            if mcp_request and not (self.interactive_auth and response.status_code in {401, 403}):
                await response.aclose()
                raise McpClientError(code)
        return response

    async def aclose(self) -> None:
        await self.transport.aclose()


class McpClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _nested_client_error(exc: BaseException) -> McpClientError | None:
    if isinstance(exc, McpClientError):
        return exc
    if isinstance(exc, httpx.TimeoutException):
        return McpClientError("mcp.timeout")
    if isinstance(exc, TimeoutError):
        return McpClientError("mcp.timeout")
    if isinstance(exc, httpx.TransportError):
        return McpClientError("mcp.connectionFailed")
    if isinstance(exc, OAuthRegistrationError):
        return McpClientError("mcp.oauthRegistrationFailed")
    if isinstance(exc, BaseExceptionGroup):
        for child in exc.exceptions:
            if error := _nested_client_error(child):
                return error
    return None


class McpClient:
    def __init__(
        self,
        endpoint: str,
        token: str | None = None,
        *,
        authentication_failed: Callable[[], Awaitable[None]] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.authentication_failed = authentication_failed
        self.http_client = http_client

    @asynccontextmanager
    async def _session(self, *, max_response_bytes: int = MAX_MCP_RESULT_BYTES):
        async with AsyncExitStack() as stack:
            client = self.http_client or await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=self.headers,
                    timeout=httpx.Timeout(60, connect=10),
                    trust_env=False,
                    transport=_BoundedTransport(
                        authentication_failed=self.authentication_failed,
                        max_response_bytes=max_response_bytes,
                    ),
                )
            )
            async with streamable_http_client(self.endpoint, http_client=client) as streams:
                yield streams

    async def discover_tools(self, *, read_timeout_seconds: int = 30) -> list[dict[str, Any]]:
        initialized = False
        try:
            async with (
                asyncio.timeout(read_timeout_seconds),
                self._session(max_response_bytes=MAX_MCP_CATALOG_BYTES) as (read, write, _session_id),
            ):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=read_timeout_seconds)
                ) as session:
                    await session.initialize()
                    initialized = True
                    tools = []
                    catalog_size = 2
                    cursors = set()
                    cursor = None
                    while True:
                        result = await session.list_tools(cursor=cursor)
                        for tool in result.tools:
                            item = {
                                "name": tool.name,
                                "title": tool.title,
                                "description": tool.description or "",
                                "input_schema": tool.inputSchema,
                                "output_schema": tool.outputSchema,
                                "annotations": {
                                    key: getattr(tool.annotations, key, None)
                                    for key in (
                                        "readOnlyHint",
                                        "destructiveHint",
                                        "idempotentHint",
                                        "openWorldHint",
                                    )
                                },
                            }
                            try:
                                catalog_size += len(json.dumps(
                                    item, ensure_ascii=True, separators=(",", ":"), allow_nan=False
                                ).encode()) + bool(tools)
                            except (TypeError, ValueError) as exc:
                                raise McpClientError("mcp.invalidResponse") from exc
                            if catalog_size > MAX_MCP_CATALOG_BYTES:
                                raise McpClientError("mcp.responseTooLarge")
                            tools.append(item)
                        cursor = result.nextCursor
                        if cursor is None:
                            break
                        if cursor in cursors:
                            raise McpClientError("mcp.invalidResponse")
                        catalog_size += len(cursor.encode())
                        if catalog_size > MAX_MCP_CATALOG_BYTES:
                            raise McpClientError("mcp.responseTooLarge")
                        cursors.add(cursor)
        except Exception as exc:
            error = _nested_client_error(exc)
            url = httpx.URL(self.endpoint)
            if (error is not None and error.code == "mcp.endpointNotFound" and not initialized
                and not url.raw_path.endswith(b"/") and not url.query and not url.fragment):
                self.endpoint = str(url.copy_with(raw_path=url.raw_path + b"/"))
                return await self.discover_tools(read_timeout_seconds=read_timeout_seconds)
            if error is exc:
                raise
            raise error or McpClientError("mcp.refreshFailed") from exc
        return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        output_schema: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        dispatched = False
        try:
            async with self._session() as (read, write, _session_id):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=60)
                ) as session:
                    await session.initialize()
                    session._tool_output_schemas[name] = output_schema
                    dispatched = True
                    result = await session.call_tool(name, arguments=arguments)
        except asyncio.CancelledError as exc:
            raise McpClientError("mcp.outcomeUnknown" if dispatched else "mcp.unavailable") from exc
        except Exception as exc:
            if not dispatched and (error := _nested_client_error(exc)):
                raise error from exc
            raise McpClientError("mcp.outcomeUnknown" if dispatched else "mcp.unavailable") from exc
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        try:
            result_size = len(
                json.dumps(
                    payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False
                ).encode()
            )
        except (TypeError, ValueError) as exc:
            raise McpClientError("mcp.invalidResponse") from exc
        if result_size > MAX_MCP_RESULT_BYTES:
            raise McpClientError("mcp.responseTooLarge")
        return payload, bool(result.isError)
