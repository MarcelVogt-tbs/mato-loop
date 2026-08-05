from __future__ import annotations

import base64
import binascii
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, field_validator

from mato_loop_backend import __version__
from mato_loop_backend.providers.base import ProviderError
from mato_loop_backend.providers.openai_compatible import DiscoveredModel

CODEX_AUTH_BASE_URL = "https://auth.openai.com"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_DEVICE_REDIRECT_URI = f"{CODEX_AUTH_BASE_URL}/deviceauth/callback"
CODEX_DEVICE_URL = f"{CODEX_AUTH_BASE_URL}/codex/device"
CODEX_DEVICE_USER_CODE_URL = f"{CODEX_AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
CODEX_DEVICE_TOKEN_URL = f"{CODEX_AUTH_BASE_URL}/api/accounts/deviceauth/token"
CODEX_TOKEN_URL = f"{CODEX_AUTH_BASE_URL}/oauth/token"
CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_IMAGES_GENERATIONS_URL = (
    "https://chatgpt.com/backend-api/codex/images/generations"
)
CODEX_IMAGES_EDITS_URL = "https://chatgpt.com/backend-api/codex/images/edits"
CODEX_IMAGE_MODEL_ID = "openai-codex/gpt-image-2"
CODEX_COMPATIBILITY_VERSION = "0.144.6"
LOGIN_TTL_SECONDS = 15 * 60


class CodexOAuthError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DeviceCodeResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    device_auth_id: str = Field(min_length=1)
    user_code: str = Field(
        min_length=1,
        validation_alias=AliasChoices("user_code", "usercode"),
    )
    interval: int = Field(default=5, ge=1)

    @field_validator("interval", mode="before")
    @classmethod
    def parse_interval(cls, value: Any) -> Any:
        return int(value) if isinstance(value, str) else value


class _DeviceTokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    authorization_code: str = Field(min_length=1)
    code_verifier: str = Field(min_length=1)


class _TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    refresh_token: str | None = Field(default=None, min_length=1)
    expires_in: int = Field(gt=0)


class _CodexModel(BaseModel):
    model_config = ConfigDict(extra="ignore")

    slug: str = Field(min_length=1)
    display_name: str | None = None
    visibility: str = "none"
    context_window: int | None = Field(default=None, gt=1)
    supports_parallel_tool_calls: bool = False
    input_modalities: list[str] = Field(default_factory=list)


class _CodexModelsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    models: list[_CodexModel]


@dataclass
class LoginAttempt:
    id: str
    device_auth_id: str
    user_code: str
    verification_url: str
    interval: int
    created_at: float
    next_poll_at: float
    polling: bool = False


class CodexOAuthManager:
    def __init__(self) -> None:
        self.attempts: dict[str, LoginAttempt] = {}
        self._lock = threading.Lock()

    async def start_login(self) -> LoginAttempt:
        device_code = await request_device_code()
        now = time.time()
        attempt = LoginAttempt(
            id=secrets.token_urlsafe(24),
            device_auth_id=device_code.device_auth_id,
            user_code=device_code.user_code,
            verification_url=CODEX_DEVICE_URL,
            interval=max(1, min(device_code.interval, 30)),
            created_at=now,
            next_poll_at=now,
        )
        with self._lock:
            self._cleanup(now)
            self.attempts[attempt.id] = attempt
        return attempt

    def get(self, login_id: str) -> LoginAttempt | None:
        with self._lock:
            return self.attempts.get(login_id)

    def begin_poll(self, login_id: str) -> tuple[LoginAttempt | None, bool]:
        with self._lock:
            attempt = self.attempts.get(login_id)
            if attempt is None:
                return None, False
            now = time.time()
            if attempt.polling or now < attempt.next_poll_at:
                return attempt, False
            attempt.polling = True
            attempt.next_poll_at = now + attempt.interval
            return attempt, True

    def end_poll(self, login_id: str) -> None:
        with self._lock:
            attempt = self.attempts.get(login_id)
            if attempt:
                attempt.polling = False

    def finish(self, login_id: str) -> None:
        with self._lock:
            self.attempts.pop(login_id, None)

    @staticmethod
    def expired(attempt: LoginAttempt) -> bool:
        return time.time() - attempt.created_at >= LOGIN_TTL_SECONDS

    def _cleanup(self, now: float) -> None:
        expired = [
            key for key, value in self.attempts.items() if now - value.created_at >= LOGIN_TTL_SECONDS
        ]
        for key in expired:
            self.attempts.pop(key, None)


def decode_account_id(access_token: str) -> str | None:
    try:
        part = access_token.split(".")[1]
        part += "=" * (-len(part) % 4)
        payload = json.loads(base64.urlsafe_b64decode(part))
        auth = payload.get("https://api.openai.com/auth") or {}
        return auth.get("chatgpt_account_id")
    except (AttributeError, binascii.Error, IndexError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def request_device_code() -> _DeviceCodeResponse:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                CODEX_DEVICE_USER_CODE_URL,
                json={"client_id": CODEX_CLIENT_ID},
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CodexOAuthError("oauth.deviceCodeUnavailable") from exc
    try:
        return _DeviceCodeResponse.model_validate(response.json())
    except (ValidationError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise CodexOAuthError("oauth.deviceCodeUnavailable") from exc


async def poll_device_code(attempt: LoginAttempt) -> _DeviceTokenResponse | None:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                CODEX_DEVICE_TOKEN_URL,
                json={
                    "device_auth_id": attempt.device_auth_id,
                    "user_code": attempt.user_code,
                },
            )
    except httpx.RequestError as exc:
        raise CodexOAuthError("oauth.deviceCodeUnavailable") from exc
    if response.status_code in {403, 404}:
        return None
    if response.status_code >= 400:
        raise CodexOAuthError("oauth.authorizationFailed")
    try:
        return _DeviceTokenResponse.model_validate(response.json())
    except (ValidationError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise CodexOAuthError("oauth.exchangeFailed") from exc


async def exchange_code(code: str, verifier: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                CODEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": CODEX_CLIENT_ID,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": CODEX_DEVICE_REDIRECT_URI,
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise CodexOAuthError("oauth.exchangeFailed") from exc
    try:
        data = _TokenResponse.model_validate(response.json())
    except (ValidationError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise CodexOAuthError("oauth.exchangeFailed") from exc
    if not data.refresh_token:
        raise CodexOAuthError("oauth.exchangeFailed")
    return {
        "access": data.access_token,
        "refresh": data.refresh_token,
        "expires": int(time.time() * 1000) + data.expires_in * 1000,
        "account_id": decode_account_id(data.access_token),
    }


async def refresh_token(payload: dict[str, Any]) -> dict[str, Any]:
    if int(payload.get("expires", 0)) > int(time.time() * 1000) + 60_000:
        return payload
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            CODEX_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": payload["refresh"],
                "client_id": CODEX_CLIENT_ID,
            },
        )
        response.raise_for_status()
    data = _TokenResponse.model_validate(response.json())
    return {
        "access": data.access_token,
        "refresh": data.refresh_token or payload["refresh"],
        "expires": int(time.time() * 1000) + data.expires_in * 1000,
        "account_id": decode_account_id(data.access_token) or payload.get("account_id"),
    }


async def discover_models(
    *, access_token: str, account_id: str | None, timeout: float = 30
) -> list[DiscoveredModel]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "originator": "mato-loop",
        "version": CODEX_COMPATIBILITY_VERSION,
        "User-Agent": f"MatoLoop/{__version__}",
    }
    if account_id:
        headers["ChatGPT-Account-ID"] = account_id
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                CODEX_MODELS_URL,
                params={"client_version": CODEX_COMPATIBILITY_VERSION},
                headers=headers,
            )
            if response.status_code >= 400:
                raise ProviderError(
                    "Codex model discovery failed",
                    status_code=response.status_code,
                )
    except httpx.RequestError as exc:
        raise ProviderError(
            "Codex model discovery network request failed",
            code="provider.unavailable",
        ) from exc
    try:
        parsed = _CodexModelsResponse.model_validate(response.json())
    except (ValidationError, ValueError, TypeError, UnicodeDecodeError) as exc:
        raise ProviderError(
            "Codex returned an invalid model list",
            code="provider.invalidResponse",
        ) from exc
    models = [
        DiscoveredModel(
            id=f"openai-codex/{model.slug}",
            name=model.display_name or model.slug,
            max_model_len=model.context_window,
            supports_parallel_tool_calls=model.supports_parallel_tool_calls,
            supports_image_input="image" in model.input_modalities,
        )
        for model in parsed.models
        if model.visibility == "list"
    ]
    models.append(
        DiscoveredModel(
            id=CODEX_IMAGE_MODEL_ID,
            name="GPT Image 2",
            capabilities={"image_generation", "image_editing"},
            capabilities_authoritative=True,
            native_for_chat_provider=True,
            supports_tools=False,
        )
    )
    return models


oauth_manager = CodexOAuthManager()
