from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import logging
import os
import re
import stat
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

import aiohttp
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pywebpush import WebPushException, webpush_async
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.config import RuntimeConfig, get_runtime_config
from mato_loop_backend.db.session import AsyncSessionFactory
from mato_loop_backend.models import UserAccount, WebPushSubscription
from mato_loop_backend.security import CredentialCipher

logger = logging.getLogger(__name__)
_DELIVERY_TIMEOUT_SECONDS = 10.0


def _origin(value: str, *, origin_only: bool = False) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("invalid HTTPS origin") from exc
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or (origin_only and (parsed.path not in {"", "/"} or parsed.query))
    ):
        raise ValueError("invalid HTTPS origin")
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    return f"https://{host}{f':{port}' if port is not None else ''}"


def _allowed_origins(config: RuntimeConfig) -> frozenset[str]:
    return frozenset(
        _origin(value.strip(), origin_only=True)
        for value in config.web_push_allowed_origins.split(",")
        if value.strip()
    )


def _validate_subject(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"mailto:[^@\s]+@[^@\s]+", value):
        return value
    if value == _origin(value, origin_only=True):
        return value
    raise ValueError("invalid VAPID subject")


def _decode_key(value: str, *, size: int, uncompressed: bool = False) -> str:
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("invalid Web Push key") from exc
    if len(raw) != size or (uncompressed and raw[0] != 4):
        raise ValueError("invalid Web Push key")
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value.rstrip("="):
        raise ValueError("invalid Web Push key")
    return value.rstrip("=")


class WebPushSubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    p256dh: Annotated[str, Field(min_length=80, max_length=100)]
    auth: Annotated[str, Field(min_length=20, max_length=32)]

    @field_validator("p256dh")
    @classmethod
    def validate_p256dh(cls, value: str) -> str:
        return _decode_key(value, size=65, uncompressed=True)

    @field_validator("auth")
    @classmethod
    def validate_auth(cls, value: str) -> str:
        return _decode_key(value, size=16)


class WebPushSubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    endpoint: Annotated[str, Field(min_length=1, max_length=2_048)]
    expiration_time: Annotated[int | None, Field(default=None, alias="expirationTime", ge=0)]
    keys: WebPushSubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        _origin(value)
        return value

    def secret_payload(self) -> dict[str, object]:
        return {"endpoint": self.endpoint, "keys": self.keys.model_dump()}


class WebPushSessionTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    type: Literal["session"]
    session_id: UUID = Field(alias="sessionId")


class WebPushAutomationRunTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    type: Literal["automationRun"]
    automation_id: UUID = Field(alias="automationId")
    run_id: UUID = Field(alias="runId")


class WebPushActionsTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    type: Literal["actions"]


class WebPushNotificationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    title: Annotated[str, Field(min_length=1, max_length=240)]
    body: Annotated[str | None, Field(default=None, max_length=500)]
    tag: Annotated[str | None, Field(default=None, min_length=1, max_length=120)]
    target: WebPushSessionTarget | WebPushAutomationRunTarget | WebPushActionsTarget | None = None


class WebPushPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    version: Literal[1] = 1
    notification: WebPushNotificationPayload


def web_push_payload(
    *,
    title: str,
    body: str | None,
    tag: str,
    target: WebPushSessionTarget | WebPushAutomationRunTarget | WebPushActionsTarget | None,
) -> WebPushPayload:
    preview = " ".join(body.split())[:500] if body else None
    return WebPushPayload(
        notification=WebPushNotificationPayload(
            title=title[:240],
            body=preview or None,
            tag=tag,
            target=target,
        )
    )


class WebPushConfigurationError(RuntimeError):
    pass


class WebPushRegistrationError(ValueError):
    pass


class VapidKeyStore:
    def __init__(self, config: RuntimeConfig | None = None) -> None:
        self.config = config or get_runtime_config()
        self.path = self.config.secrets_dir / "web-push-vapid.pem"

    def public_key(self) -> str | None:
        if not self.config.web_push_configured:
            return None
        try:
            if not _allowed_origins(self.config):
                raise ValueError("Web Push origin allowlist is empty")
            _validate_subject(self.config.web_push_vapid_subject)
            key = self._load_or_create()
        except (OSError, ValueError, TypeError) as exc:
            raise WebPushConfigurationError from exc
        raw = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def _load_or_create(self) -> ec.EllipticCurvePrivateKey:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            payload = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            try:
                descriptor = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
            except FileExistsError:
                pass
            else:
                with os.fdopen(descriptor, "wb") as target:
                    target.write(payload)
        details = self.path.lstat()
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("VAPID key path is not a regular file")
        self.path.chmod(0o600)
        key = serialization.load_pem_private_key(self.path.read_bytes(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(
            key.curve, ec.SECP256R1
        ):
            raise ValueError("invalid VAPID private key")
        return key


class _NoRedirectSession:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def post(self, url: str, **kwargs):
        kwargs["allow_redirects"] = False
        return await self.session.post(url, **kwargs)


class WebPushService:
    @staticmethod
    async def register(
        db: AsyncSession,
        *,
        user_id: UUID,
        device_id: UUID,
        subscription: WebPushSubscriptionInput,
    ) -> None:
        config = get_runtime_config()
        if not config.web_push_configured:
            raise WebPushConfigurationError
        VapidKeyStore(config).public_key()
        if _origin(subscription.endpoint) not in _allowed_origins(config):
            raise WebPushRegistrationError
        endpoint_hash = hashlib.sha256(subscription.endpoint.encode()).hexdigest()
        encrypted = CredentialCipher().encrypt(subscription.secret_payload())
        endpoint_item = await db.scalar(
            select(WebPushSubscription)
            .where(WebPushSubscription.endpoint_hash == endpoint_hash)
            .with_for_update()
        )
        item = await db.scalar(
            select(WebPushSubscription)
            .where(
                WebPushSubscription.user_id == user_id,
                WebPushSubscription.device_id == device_id,
            )
            .with_for_update()
        )
        if endpoint_item is not None and endpoint_item is not item:
            await db.delete(endpoint_item)
            await db.flush()
        if item is None:
            db.add(
                WebPushSubscription(
                    user_id=user_id,
                    device_id=device_id,
                    endpoint_hash=endpoint_hash,
                    encrypted_payload=encrypted,
                )
            )
        else:
            item.endpoint_hash = endpoint_hash
            item.encrypted_payload = encrypted
        await db.flush()

    @staticmethod
    async def delete(db: AsyncSession, *, user_id: UUID, device_id: UUID) -> None:
        await db.execute(
            delete(WebPushSubscription).where(
                WebPushSubscription.user_id == user_id,
                WebPushSubscription.device_id == device_id,
            )
        )

    @staticmethod
    async def deliver_after_commit(user_id: UUID, payload: WebPushPayload) -> None:
        """Best-effort delivery. Call only after the producer transaction commits."""
        config = get_runtime_config()
        if not config.web_push_configured:
            return
        try:
            VapidKeyStore(config).public_key()
            async with AsyncSessionFactory() as db:
                subscriptions = list(
                    (
                        await db.scalars(
                            select(WebPushSubscription)
                            .join(UserAccount, UserAccount.id == WebPushSubscription.user_id)
                            .where(
                                WebPushSubscription.user_id == user_id,
                                UserAccount.is_active.is_(True),
                            )
                        )
                    ).all()
                )
            cipher = CredentialCipher()
        except Exception:
            logger.error("Web Push delivery could not be prepared")
            return
        try:
            timeout = aiohttp.ClientTimeout(total=_DELIVERY_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                sender = _NoRedirectSession(session)
                await asyncio.gather(
                    *(
                        WebPushService._deliver_one(item, payload, config, cipher, sender)
                        for item in subscriptions
                    )
                )
        except Exception:
            logger.error("Web Push delivery failed")

    @staticmethod
    async def _deliver_one(
        item: WebPushSubscription,
        payload: WebPushPayload,
        config: RuntimeConfig,
        cipher: CredentialCipher,
        sender: _NoRedirectSession | None = None,
    ) -> None:
        try:
            subscription = cipher.decrypt(item.encrypted_payload)
            endpoint = subscription.get("endpoint")
            if not isinstance(endpoint, str) or _origin(endpoint) not in _allowed_origins(config):
                return
            async with asyncio.timeout(_DELIVERY_TIMEOUT_SECONDS):
                if sender is None:
                    timeout = aiohttp.ClientTimeout(total=_DELIVERY_TIMEOUT_SECONDS)
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        response = await WebPushService._send(
                            subscription, payload, config, _NoRedirectSession(session)
                        )
                else:
                    response = await WebPushService._send(subscription, payload, config, sender)
                if hasattr(response, "release"):
                    response.release()
        except WebPushException as exc:
            status_code = getattr(exc.response, "status", None)
            if status_code is None:
                status_code = getattr(exc.response, "status_code", None)
            if status_code in {404, 410}:
                await WebPushService._delete_stale(item.id, item.endpoint_hash)
        except Exception:
            return

    @staticmethod
    async def _send(
        subscription: dict[str, object],
        payload: WebPushPayload,
        config: RuntimeConfig,
        sender: _NoRedirectSession,
    ):
        return await webpush_async(
            subscription_info=subscription,
            data=payload.model_dump_json(exclude_none=True, by_alias=True),
            vapid_private_key=str(VapidKeyStore(config).path),
            vapid_claims={"sub": _validate_subject(config.web_push_vapid_subject)},
            timeout=_DELIVERY_TIMEOUT_SECONDS,
            ttl=60,
            aiohttp_session=sender,
        )

    @staticmethod
    async def _delete_stale(subscription_id: UUID, endpoint_hash: str) -> None:
        try:
            async with AsyncSessionFactory() as db:
                await db.execute(
                    delete(WebPushSubscription).where(
                        WebPushSubscription.id == subscription_id,
                        WebPushSubscription.endpoint_hash == endpoint_hash,
                    )
                )
                await db.commit()
        except Exception:
            logger.error("A stale Web Push subscription could not be removed")
