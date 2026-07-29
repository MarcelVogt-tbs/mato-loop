from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from mato_loop_backend.api.errors import register_error_handlers
from mato_loop_backend.error_contract import describe_error
from mato_loop_backend.providers.base import ProviderError


def test_http_error_uses_safe_english_message_instead_of_raw_detail() -> None:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/failure")
    async def failure() -> None:
        raise HTTPException(418, detail="secret upstream response")

    response = TestClient(app).get("/failure")

    assert response.status_code == 418
    assert response.json() == {
        "error": {
            "code": "http.requestFailed",
            "message": "The request failed.",
        }
    }
    assert "secret upstream response" not in response.text


def test_unknown_error_code_and_provider_code_fall_back_without_raw_text() -> None:
    unknown = describe_error("secret.upstreamResponse")
    provider = ProviderError(
        "raw provider exception",
        code="provider.rawSecretResponse",
    )

    assert unknown.payload() == {
        "code": "secret.upstreamResponse",
        "message": "The action failed.",
    }
    assert provider.public_code == "provider.requestFailed"
    assert "raw" not in str(unknown.payload())


def test_chat_history_errors_have_controlled_fallbacks() -> None:
    assert describe_error("session.messageNotFound").message == "The chat message was not found."
    assert (
        describe_error("session.retryUnavailable").message
        == "The failed response can no longer be retried."
    )
    assert (
        describe_error("tool.executionInterrupted").message
        == "The tool execution was interrupted and its outcome is unknown. Do not infer success or "
        "failure without verification."
    )
