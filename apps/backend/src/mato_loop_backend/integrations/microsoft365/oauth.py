from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.integrations.microsoft365.client import (
    Microsoft365Config,
    Microsoft365Error,
    request_json,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.security import read_credential, scoped_credentials, store_credential
from mato_loop_backend.settings import IntegrationInstance

CREDENTIAL_KIND = "microsoft365.oauth"
CALLBACK_PATH = "/api/integrations/microsoft365/oauth/callback"
logger = logging.getLogger(__name__)


class Tokens(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    access_token: str = Field(min_length=1, max_length=100_000)
    refresh_token: str | None = Field(default=None, min_length=1, max_length=100_000)
    token_type: str
    expires_in: int = Field(gt=0, le=172800)
    scope: str = Field(max_length=10_000)


class OAuthCredential(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: Microsoft365Config
    client_secret: str | None = Field(default=None, min_length=1, max_length=10_000)
    tokens: Tokens | None = None
    expires_at: float = 0
    authorization_required: bool = False


def credential_payload(record: Credential | None, config: Microsoft365Config) -> OAuthCredential:
    if record is None or record.kind != CREDENTIAL_KIND:
        raise Microsoft365Error("microsoft365.authorizationRequired")
    try:
        payload = OAuthCredential.model_validate(read_credential(record))
        if payload.config != config:
            raise ValueError("Credential binding mismatch")
        return payload
    except (InvalidToken, ValueError, UnicodeError) as exc:
        logger.warning("Invalid Microsoft 365 credential: %s", type(exc).__name__)
        raise Microsoft365Error("microsoft365.configurationInvalid") from exc


def _oauth_error(error: object) -> Microsoft365Error:
    error = error if isinstance(error, str) else ""
    code = {
        "access_denied": "microsoft365.authorizationDenied",
        "authorization_declined": "microsoft365.authorizationDenied",
        "expired_token": "microsoft365.loginExpired",
        "bad_verification_code": "microsoft365.loginExpired",
        "invalid_grant": "microsoft365.authorizationRequired",
        "interaction_required": "microsoft365.authorizationRequired",
        "invalid_client": "microsoft365.configurationInvalid",
        "unauthorized_client": "microsoft365.configurationInvalid",
        "invalid_scope": "microsoft365.configurationInvalid",
    }.get(error, "microsoft365.authorizationFailed")
    logger.warning("Microsoft 365 OAuth failed: %s", code)
    return Microsoft365Error(code)


def apply_tokens(payload: OAuthCredential, response: dict[str, Any]) -> OAuthCredential:
    try:
        tokens = Tokens.model_validate(response)
        scopes = {scope.rsplit("/", 1)[-1].casefold() for scope in tokens.scope.split()}
        config = payload.config
        mail_scopes = {"mail.readwrite"} if config.allow_drafts or config.allow_send or config.allow_delete else {"mail.read", "mail.readwrite"}
        calendar_scopes = {"calendars.readwrite"} if config.write_calendar or config.delete_calendar else {"calendars.read", "calendars.readwrite"}
        task_scopes = {"tasks.readwrite"} if config.write_tasks or config.delete_tasks else {"tasks.read", "tasks.readwrite"}
        if (
            tokens.token_type.casefold() != "bearer"
            or not mail_scopes.intersection(scopes)
            or ((config.read_calendar or config.write_calendar or config.delete_calendar) and not calendar_scopes.intersection(scopes))
            or ((config.read_tasks or config.write_tasks or config.delete_tasks) and not task_scopes.intersection(scopes))
            or (payload.config.allow_send and "mail.send" not in scopes)
        ):
            raise ValueError("Missing delegated scope")
        if tokens.refresh_token is None and payload.tokens is not None:
            tokens.refresh_token = payload.tokens.refresh_token
        if tokens.refresh_token is None:
            raise ValueError("Missing refresh token")
    except (ValueError, ValidationError) as exc:
        raise Microsoft365Error("integration.invalidResponse") from exc
    return payload.model_copy(
        update={
            "tokens": tokens,
            "expires_at": time.time() + tokens.expires_in,
            "authorization_required": False,
        }
    )


async def token_request(
    payload: OAuthCredential, values: dict[str, str]
) -> tuple[int, dict[str, Any]]:
    data = {"client_id": str(payload.config.client_id), **values}
    if payload.config.auth_flow == "browser":
        if not payload.client_secret:
            raise Microsoft365Error("microsoft365.configurationInvalid")
        data["client_secret"] = payload.client_secret
    return await request_json("POST", payload.config.authority + "/token", data=data)


async def access_token(instance: IntegrationInstance) -> str:
    if instance.credential_id is None:
        raise Microsoft365Error("microsoft365.authorizationRequired")
    config = Microsoft365Config.model_validate(instance.config)
    # Commit refresh rotation independently of the caller's application transaction.
    async with AsyncSessionFactory() as db:
        record = await db.scalar(
            scoped_credentials(instance.owner_id)
            .where(Credential.id == instance.credential_id)
            .with_for_update()
        )
        payload = credential_payload(record, config)
        if payload.authorization_required or payload.tokens is None:
            raise Microsoft365Error("microsoft365.authorizationRequired")
        if payload.expires_at <= time.time() + 60:
            status, response = await token_request(
                payload,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": payload.tokens.refresh_token or "",
                    "scope": config.scopes,
                },
            )
            if status != 200:
                error = _oauth_error(response.get("error", ""))
                if error.code == "microsoft365.authorizationRequired":
                    payload.authorization_required = True
                    await store_credential(
                        db,
                        owner_id=instance.owner_id,
                        kind=CREDENTIAL_KIND,
                        label=instance.name,
                        credential=record,
                        payload=payload.model_dump(mode="json"),
                    )
                    await db.commit()
                raise error
            payload = apply_tokens(payload, response)
            await store_credential(
                db,
                owner_id=instance.owner_id,
                kind=CREDENTIAL_KIND,
                label=instance.name,
                credential=record,
                payload=payload.model_dump(mode="json"),
            )
            await db.commit()
        return payload.tokens.access_token


@dataclass(slots=True)
class LoginAttempt:
    user_id: UUID
    session_hash: str = field(repr=False)
    instance: IntegrationInstance
    credential: OAuthCredential = field(repr=False)
    id: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    state: str | None = field(default=None, repr=False)
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64), repr=False)
    redirect_uri: str | None = None
    code: str | None = field(default=None, repr=False)
    device_code: str | None = field(default=None, repr=False)
    error: str | None = None
    expires_at: float = field(default_factory=lambda: time.monotonic() + 600)
    interval: int = 5
    next_poll: float = 0
    ready: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class LoginManager:
    def __init__(self) -> None:
        self.attempts: dict[str, LoginAttempt] = {}

    def _prune(self) -> None:
        self.attempts = {
            key: value
            for key, value in self.attempts.items()
            if value.expires_at > time.monotonic()
        }

    async def start(self, attempt: LoginAttempt) -> dict[str, Any]:
        self._prune()
        for key, previous in list(self.attempts.items()):
            if (previous.user_id, previous.session_hash) == (attempt.user_id, attempt.session_hash):
                if previous.lock.locked():
                    raise Microsoft365Error("microsoft365.loginBusy")
                del self.attempts[key]
        if len(self.attempts) >= 32:
            raise Microsoft365Error("microsoft365.loginBusy")
        self.attempts[attempt.id] = attempt
        try:
            config = attempt.credential.config
            if config.auth_flow == "browser":
                attempt.state = secrets.token_urlsafe(32)
                challenge = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(attempt.verifier.encode("ascii")).digest()
                    )
                    .decode("ascii")
                    .rstrip("=")
                )
                return {
                    "id": attempt.id,
                    "interval": 3,
                    "expiresIn": 600,
                    "redirectUri": attempt.redirect_uri,
                    "authorizationUrl": config.authority
                    + "/authorize?"
                    + urlencode(
                        {
                            "client_id": str(config.client_id),
                            "response_type": "code",
                            "redirect_uri": attempt.redirect_uri,
                            "response_mode": "form_post",
                            "scope": config.scopes,
                            "state": attempt.state,
                            "code_challenge": challenge,
                            "code_challenge_method": "S256",
                            "prompt": "select_account",
                        }
                    ),
                }
            status, response = await request_json(
                "POST",
                config.authority + "/devicecode",
                data={"client_id": str(config.client_id), "scope": config.scopes},
            )
            if status != 200:
                raise _oauth_error(response.get("error", ""))
            device = DeviceResponse.model_validate(response)
            attempt.device_code = device.device_code
            attempt.interval = device.interval
            attempt.next_poll = time.monotonic() + attempt.interval
            attempt.expires_at = time.monotonic() + device.expires_in
            return {
                "id": attempt.id,
                "interval": attempt.interval,
                "expiresIn": device.expires_in,
                "userCode": device.user_code,
                "authorizationUrl": "https://microsoft.com/devicelogin",
            }
        except (Microsoft365Error, ValidationError) as exc:
            self.attempts.pop(attempt.id, None)
            if isinstance(exc, Microsoft365Error):
                raise
            raise Microsoft365Error("integration.invalidResponse") from exc

    def get(self, attempt_id: str, user_id: UUID, session_hash: str) -> LoginAttempt:
        self._prune()
        attempt = self.attempts.get(attempt_id)
        if (
            attempt is None
            or attempt.user_id != user_id
            or not secrets.compare_digest(attempt.session_hash, session_hash)
        ):
            raise Microsoft365Error("microsoft365.loginExpired")
        return attempt

    def callback(self, state: str, code: str | None, error: str | None) -> None:
        self._prune()
        attempt = next(
            (
                item
                for item in self.attempts.values()
                if item.state and secrets.compare_digest(item.state, state)
            ),
            None,
        )
        if attempt is None:
            raise Microsoft365Error("microsoft365.loginExpired")
        attempt.state = None
        if error or not code:
            attempt.error = _oauth_error(error or "").code
        else:
            attempt.code = code

    async def poll(self, attempt: LoginAttempt) -> bool:
        if attempt.error:
            raise Microsoft365Error(attempt.error)
        if attempt.ready:
            return True
        if time.monotonic() < attempt.next_poll:
            return False
        attempt.next_poll = time.monotonic() + attempt.interval
        if attempt.device_code:
            values = {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": attempt.device_code,
            }
        elif attempt.code:
            values = {
                "grant_type": "authorization_code",
                "code": attempt.code,
                "redirect_uri": attempt.redirect_uri or "",
                "code_verifier": attempt.verifier,
                "scope": attempt.credential.config.scopes,
            }
            # A code exchange is never replayed after an uncertain network outcome.
            attempt.code = None
            attempt.error = "microsoft365.authorizationFailed"
        else:
            return False
        status, response = await token_request(attempt.credential, values)
        if status != 200:
            error = response.get("error", "")
            if (
                attempt.device_code
                and isinstance(error, str)
                and error in {"authorization_pending", "slow_down"}
            ):
                if error == "slow_down":
                    attempt.interval += 5
                    attempt.next_poll = time.monotonic() + attempt.interval
                return False
            attempt.error = _oauth_error(error).code
            raise Microsoft365Error(attempt.error)
        attempt.credential = apply_tokens(attempt.credential, response)
        attempt.ready, attempt.error = True, None
        return True


class DeviceResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    device_code: str = Field(min_length=1, max_length=10_000)
    user_code: str = Field(min_length=1, max_length=64)
    expires_in: int = Field(ge=1, le=1800)
    interval: int = Field(default=5, ge=1, le=60)


oauth_logins = LoginManager()
