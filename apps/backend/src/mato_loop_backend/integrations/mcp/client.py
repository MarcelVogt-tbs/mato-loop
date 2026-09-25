from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

MAX_MCP_TOOLS = 100
MAX_MCP_CATALOG_BYTES = 256 * 1024
MAX_MCP_RESULT_BYTES = 1024 * 1024
MAX_MCP_PAGES = 20


class _BoundedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self):
        size = 0
        async for chunk in self.stream:
            size += len(chunk)
            if size > MAX_MCP_RESULT_BYTES:
                raise McpClientError("mcp.responseTooLarge")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class _BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport or httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self.transport.handle_async_request(request)
        response.stream = _BoundedStream(response.stream)
        return response

    async def aclose(self) -> None:
        await self.transport.aclose()


class McpClientError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class McpClient:
    def __init__(self, endpoint: str, token: str | None = None) -> None:
        self.endpoint = endpoint
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    @asynccontextmanager
    async def _session(self):
        async with httpx.AsyncClient(
            headers=self.headers,
            timeout=httpx.Timeout(60, connect=10),
            trust_env=False,
            transport=_BoundedTransport(),
        ) as client:
            async with streamable_http_client(
                self.endpoint, http_client=client
            ) as streams:
                yield streams

    async def discover_tools(self) -> list[dict[str, Any]]:
        try:
            async with self._session() as (read, write, _session_id):
                async with ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=30)
                ) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    discovered = list(result.tools)
                    cursors = set()
                    pages = 1
                    while result.nextCursor is not None:
                        if (
                            len(discovered) >= MAX_MCP_TOOLS
                            or pages >= MAX_MCP_PAGES
                            or result.nextCursor in cursors
                        ):
                            raise McpClientError("mcp.invalidResponse")
                        cursors.add(result.nextCursor)
                        result = await session.list_tools(cursor=result.nextCursor)
                        discovered.extend(result.tools)
                        pages += 1
        except McpClientError:
            raise
        except Exception as exc:
            raise McpClientError("mcp.refreshFailed") from exc
        tools = [
            {
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
            for tool in discovered
        ]
        try:
            catalog_size = len(
                json.dumps(
                    tools, ensure_ascii=True, separators=(",", ":"), allow_nan=False
                ).encode()
            )
        except (TypeError, ValueError) as exc:
            raise McpClientError("mcp.invalidResponse") from exc
        if len(tools) > MAX_MCP_TOOLS or catalog_size > MAX_MCP_CATALOG_BYTES:
            raise McpClientError("mcp.responseTooLarge")
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
            raise McpClientError(
                "mcp.outcomeUnknown" if dispatched else "mcp.unavailable"
            ) from exc
        except Exception as exc:
            raise McpClientError(
                "mcp.outcomeUnknown" if dispatched else "mcp.unavailable"
            ) from exc
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
