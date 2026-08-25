import base64
import json

import httpx
import pytest

from mato_loop_backend.integrations.opencode_client import (
    OpenCodeClient,
    OpenCodeError,
    OpenCodeFileContent,
)


def _client(
    handler,
    base_url: str = "http://opencode.example.test/base/",
    **kwargs,
) -> OpenCodeClient:
    return OpenCodeClient(
        base_url,
        "opencode",
        "secret",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


async def test_url_and_basic_auth_are_canonical_and_same_origin() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/global/health"):
            return httpx.Response(200, json={"healthy": True, "version": "1.0.0"})
        return httpx.Response(
            200,
            json={
                "state": "/state",
                "config": "/config",
                "worktree": "/srv/code",
                "directory": "/srv/code",
            },
        )

    async with _client(handler) as client:
        probe = await client.probe()

    expected_auth = "Basic " + base64.b64encode(b"opencode:secret").decode()
    assert [request.url.path for request in requests] == [
        "/base/global/health",
        "/base/path",
    ]
    assert all(request.headers["Authorization"] == expected_auth for request in requests)
    assert probe.version == "1.0.0"
    assert probe.root == "/srv/code"


def test_invalid_urls_and_credentials_are_rejected_safely() -> None:
    for url in (
        "ftp://example.test",
        "https://user:pass@example.test",
        "https://example.test?token=secret",
        "https://example.test#fragment",
        "https://example.test/ bad",
    ):
        with pytest.raises(OpenCodeError) as error:
            OpenCodeClient(url, "opencode")
        assert error.value.code == "opencode.invalidUrl"
        assert url not in str(error.value)

    with pytest.raises(OpenCodeError) as error:
        OpenCodeClient("https://example.test", "bad:user")
    assert error.value.code == "opencode.invalidRequest"


async def test_probe_rejects_unhealthy_or_malformed_responses() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"healthy": False, "version": "secret"})

    async with _client(handler) as client:
        with pytest.raises(OpenCodeError) as error:
            await client.probe()
    assert error.value.code == "opencode.malformedResponse"
    assert "secret" not in str(error.value)


async def test_projects_are_relative_to_probed_root() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        responses = {
            "/base/global/health": {"healthy": True, "version": "1.0"},
            "/base/path": {"directory": "/srv/code"},
            "/base/project": [
                {"id": "project-1", "worktree": "/srv/code/repository"}
            ],
        }
        return httpx.Response(200, json=responses[request.url.path])

    async with _client(handler) as client:
        await client.probe()
        projects = await client.list_projects()
    assert projects[0].relative_path == "repository"
    assert projects[0].label == "repository"


async def test_models_include_connected_providers_only() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/base/config/providers"
        return httpx.Response(
            200,
            json={
                "providers": [
                    {
                        "id": "openai",
                        "name": "OpenAI",
                        "models": {
                            "team/gpt-5": {
                                "id": "team/gpt-5",
                                "providerID": "openai",
                                "name": "GPT 5",
                            }
                        },
                    },
                ],
                "default": {},
            },
        )

    async with _client(handler) as client:
        models = await client.list_models()
    assert len(models) == 1
    assert models[0].key == "openai/team/gpt-5"
    assert OpenCodeClient.parse_model_key(models[0].key) == ("openai", "team/gpt-5")
    with pytest.raises(OpenCodeError):
        OpenCodeClient.parse_model_key("missing-separator")


async def test_file_requests_are_scoped_and_paths_are_contained() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "name": "main.py",
                    "path": "src/main.py",
                    "absolute": "/srv/code/src/main.py",
                    "type": "file",
                    "ignored": False,
                }
            ],
        )

    async with _client(handler) as client:
        entries = await client.list_files("/srv/code", "src")
    assert dict(requests[0].url.params) == {"directory": "/srv/code", "path": "src"}
    assert entries[0].path == "src/main.py"
    assert not hasattr(entries[0], "absolute")

    def escape_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "name": "secret",
                    "path": "../secret",
                    "type": "file",
                    "ignored": False,
                }
            ],
        )

    async with _client(escape_handler) as client:
        with pytest.raises(OpenCodeError) as error:
            await client.list_files("/srv/code", ".")
    assert error.value.code == "opencode.malformedResponse"


async def test_file_content_requires_bounded_strict_base64() -> None:
    def valid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "type": "binary",
                "content": "aGVsbG8=",
                "encoding": "base64",
                "mimeType": "application/octet-stream",
            },
        )

    async with _client(valid) as client:
        result = await client.read_file("/srv/code", "image.bin")
    assert result == OpenCodeFileContent(
        "binary", "aGVsbG8=", "base64", "application/octet-stream"
    )

    def invalid(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"type": "binary", "content": "not base64!", "encoding": "base64"},
        )

    async with _client(invalid) as client:
        with pytest.raises(OpenCodeError) as error:
            await client.read_file("/srv/code", "image.bin")
    assert error.value.code == "opencode.malformedResponse"


async def test_session_message_payload_and_result() -> None:
    request_seen: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_seen
        request_seen = request
        return httpx.Response(
            200,
            json={
                "info": {"role": "assistant"},
                "parts": [
                    {"type": "text", "text": "First "},
                    {"type": "tool"},
                    {"type": "text", "text": "second"},
                ],
            },
        )

    async with _client(handler) as client:
        result = await client.send_message(
            "/srv/code",
            "session-1",
            "plan",
            "openai/team/gpt-5",
            "Write a plan",
            {"bash": False, "edit": False},
        )
    assert request_seen is not None
    assert dict(request_seen.url.params) == {"directory": "/srv/code"}
    assert json.loads(request_seen.content) == {
        "agent": "plan",
        "model": {"providerID": "openai", "modelID": "team/gpt-5"},
        "parts": [{"type": "text", "text": "Write a plan"}],
        "tools": {"bash": False, "edit": False},
    }
    assert result.text == "First second"
    assert result.error is None


async def test_permission_reply_uses_expected_endpoint_and_payload() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=True)

    async with _client(handler) as client:
        assert await client.reply_permission(
            "/srv/code", "session-1", "permission-1", "once"
        )
    assert requests[0].url.path == "/base/session/session-1/permissions/permission-1"
    assert dict(requests[0].url.params) == {"directory": "/srv/code"}
    assert json.loads(requests[0].content) == {"response": "once"}


async def test_malformed_oversized_and_redirect_responses_are_rejected() -> None:
    cases = [
        (lambda _request: httpx.Response(200, content=b"not json", headers={"content-type": "application/json"}), "opencode.malformedResponse"),
        (lambda _request: httpx.Response(200, content=b"x" * 65, headers={"content-type": "application/json"}), "opencode.responseTooLarge"),
        (lambda _request: httpx.Response(302, headers={"location": "https://attacker.test"}), "opencode.redirectRejected"),
    ]
    for handler, code in cases:
        async with _client(handler, max_response_bytes=64) as client:
            with pytest.raises(OpenCodeError) as error:
                await client.list_models()
        assert error.value.code == code


async def test_external_errors_do_not_leak_bodies_or_exception_messages() -> None:
    secret = "upstream-secret"

    def status_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=secret)

    async with _client(status_handler) as client:
        with pytest.raises(OpenCodeError) as error:
            await client.list_models()
    assert error.value.code == "opencode.unexpectedStatus"
    assert secret not in str(error.value)

    def transport_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError(secret, request=request)

    async with _client(transport_handler) as client:
        with pytest.raises(OpenCodeError) as error:
            await client.abort_session("/srv/code", "session-1")
    assert error.value.code == "opencode.unavailable"
    assert error.value.outcome_unknown is True
    assert secret not in str(error.value)


async def test_sse_events_are_parsed_and_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"directory": "/srv/code"}
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"type":"server.connected","properties":{}}\n\n'
                b'data: {"type":"session.idle","properties":{}}\n\n'
            ),
        )

    async with _client(handler, max_event_bytes=64) as client:
        events = [event async for event in client.events("/srv/code")]
    assert events == [
        {"type": "server.connected", "properties": {}},
        {"type": "session.idle", "properties": {}},
    ]

    def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"data: " + b"x" * 65 + b"\n\n",
        )

    async with _client(oversized, max_event_bytes=64) as client:
        with pytest.raises(OpenCodeError) as error:
            _ = [event async for event in client.events("/srv/code")]
    assert error.value.code == "opencode.eventTooLarge"
