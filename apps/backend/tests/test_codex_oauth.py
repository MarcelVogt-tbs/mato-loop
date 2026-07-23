import time

import httpx
import pytest

from mato_loop_backend.providers import codex_oauth
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.codex_oauth import LoginAttempt


def _client_with(handler):
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    return lambda **kwargs: real_client(transport=transport, **kwargs)


async def test_device_code_login_uses_official_remote_flow(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == codex_oauth.CODEX_DEVICE_USER_CODE_URL
        assert request.method == "POST"
        assert request.read() == (
            f'{{"client_id":"{codex_oauth.CODEX_CLIENT_ID}"}}'.encode()
        )
        return httpx.Response(
            200,
            json={"device_auth_id": "device-id", "user_code": "ABCD-EFGH", "interval": "5"},
        )

    monkeypatch.setattr(codex_oauth.httpx, "AsyncClient", _client_with(handler))

    attempt = await codex_oauth.CodexOAuthManager().start_login()

    assert attempt.device_auth_id == "device-id"
    assert attempt.user_code == "ABCD-EFGH"
    assert attempt.verification_url == "https://auth.openai.com/codex/device"
    assert attempt.interval == 5


async def test_device_code_poll_treats_forbidden_as_pending(monkeypatch) -> None:
    monkeypatch.setattr(
        codex_oauth.httpx,
        "AsyncClient",
        _client_with(lambda _request: httpx.Response(403)),
    )
    attempt = LoginAttempt(
        id="login",
        device_auth_id="device-id",
        user_code="ABCD-EFGH",
        verification_url=codex_oauth.CODEX_DEVICE_URL,
        interval=5,
        created_at=time.time(),
        next_poll_at=0,
    )

    assert await codex_oauth.poll_device_code(attempt) is None


async def test_device_code_exchange_uses_registered_hosted_redirect(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == codex_oauth.CODEX_TOKEN_URL
        body = request.read().decode()
        assert "redirect_uri=https%3A%2F%2Fauth.openai.com%2Fdeviceauth%2Fcallback" in body
        assert "code_verifier=verifier" in body
        return httpx.Response(
            200,
            json={"access_token": "access", "refresh_token": "refresh", "expires_in": 3600},
        )

    monkeypatch.setattr(codex_oauth.httpx, "AsyncClient", _client_with(handler))

    token = await codex_oauth.exchange_code("authorization-code", "verifier")

    assert token["access"] == "access"
    assert token["refresh"] == "refresh"


async def test_codex_model_discovery_filters_and_normalizes_models(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/backend-api/codex/models"
        assert (
            request.url.params["client_version"]
            == codex_oauth.CODEX_COMPATIBILITY_VERSION
        )
        assert request.headers["Authorization"] == "Bearer access"
        assert request.headers["ChatGPT-Account-ID"] == "account"
        assert request.headers["originator"] == "mato-loop"
        assert request.headers["version"] == codex_oauth.CODEX_COMPATIBILITY_VERSION
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "slug": "gpt-test",
                        "display_name": "GPT Test",
                        "visibility": "list",
                        "context_window": 272000,
                        "supports_parallel_tool_calls": True,
                        "input_modalities": ["text", "image"],
                    },
                    {"slug": "hidden", "visibility": "hide"},
                ]
            },
        )

    monkeypatch.setattr(codex_oauth.httpx, "AsyncClient", _client_with(handler))

    models = await codex_oauth.discover_models(
        access_token="access",
        account_id="account",
    )

    assert [model.id for model in models] == ["openai-codex/gpt-test"]
    assert models[0].metadata == {
        "name": "GPT Test",
        "max_model_len": 272000,
        "supports_parallel_tool_calls": True,
        "supports_image_input": True,
    }


async def test_codex_model_discovery_rejects_invalid_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        codex_oauth.httpx,
        "AsyncClient",
        _client_with(lambda _request: httpx.Response(200, json={"models": [{"name": "missing"}]})),
    )

    with pytest.raises(ProviderError) as exc_info:
        await codex_oauth.discover_models(access_token="access", account_id=None)

    assert exc_info.value.public_code == "provider.invalidResponse"
