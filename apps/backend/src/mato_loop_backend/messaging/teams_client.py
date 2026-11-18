from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from matoloop_teams_protocol import (
    MAX_BODY_BYTES,
    MAX_FILE_BYTES,
    Delivery,
    DeliveryResult,
    RelayStatus,
)
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.security import load_credential, read_credential
from mato_loop_backend.settings.models import TeamsChannelSettings

TEAMS_CREDENTIAL_KIND = "teams.relay_token"


class TeamsServiceError(RuntimeError):
    def __init__(self, code: str = "channel.unavailable") -> None:
        super().__init__(code)
        self.code = code


def relay_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise TeamsServiceError("validation.invalid") from None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise TeamsServiceError("validation.invalid")
    return value.rstrip("/")


@asynccontextmanager
async def relay_client(db: AsyncSession, settings: TeamsChannelSettings):
    if (
        not settings.relay_url
        or not settings.credential_id
        or not settings.app_id
        or not settings.tenant_id
    ):
        raise TeamsServiceError("channel.relayUnconfigured")
    credential = await load_credential(db, settings.credential_id, owner_id=None)
    if credential is None or credential.kind != TEAMS_CREDENTIAL_KIND:
        raise TeamsServiceError("channel.authenticationFailed")
    token = read_credential(credential).get("token")
    if not isinstance(token, str) or not token:
        raise TeamsServiceError("channel.authenticationFailed")
    async with httpx.AsyncClient(
        base_url=relay_url(settings.relay_url) + "/",
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx.Timeout(35, connect=10),
        follow_redirects=False,
    ) as client:
        yield TeamsRelayClient(client)


class TeamsRelayClient:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def request(
        self, method: str, path: str, *, missing_ok: bool = False, binary: bool = False, **kwargs
    ):
        maximum = MAX_FILE_BYTES if binary else MAX_BODY_BYTES
        try:
            async with self.client.stream(method, "relay/v1/" + path, **kwargs) as response:
                if missing_ok and response.status_code == 404:
                    return None
                if response.status_code == 401:
                    raise TeamsServiceError("channel.authenticationFailed")
                if method == "POST" and path == "deliveries" and response.status_code >= 500:
                    raise TeamsServiceError("channel.deliveryUnknown")
                if response.status_code >= 400:
                    raise TeamsServiceError()
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > maximum:
                        raise TeamsServiceError("channel.invalidResponse")
            return bytes(data)
        except httpx.HTTPError:
            code = (
                "channel.deliveryUnknown" if method == "POST" and path == "deliveries"
                else "channel.unavailable"
            )
            raise TeamsServiceError(code) from None

    async def status(self, settings: TeamsChannelSettings) -> RelayStatus:
        try:
            result = RelayStatus.model_validate_json(
                await self.request("GET", "status", params={"include_pairing_origin": "true"})
            )
        except ValidationError:
            raise TeamsServiceError("channel.invalidResponse") from None
        if result.app_id != settings.app_id or result.tenant_id != settings.tenant_id:
            raise TeamsServiceError("channel.authenticationFailed")
        return result

    async def deliver(self, value: Delivery, *, recovering: bool = False) -> DeliveryResult:
        try:
            if recovering:
                raw = await self.request("GET", f"deliveries/{value.id}", missing_ok=True)
                if raw is None:
                    raise TeamsServiceError("channel.deliveryUnknown")
            else:
                raw = await self.request("POST", "deliveries", json=value.model_dump(mode="json"))
            result = DeliveryResult.model_validate_json(raw)
            if result.id != value.id:
                raise TeamsServiceError("channel.invalidResponse")
            if value.kind == "file" and result.status == "pending":
                result = DeliveryResult.model_validate_json(
                    await self.request("POST", f"deliveries/{value.id}/accept"),
                )
        except ValidationError:
            raise TeamsServiceError("channel.invalidResponse") from None
        if result.status == "unknown" or (result.status == "sending" and value.kind != "file"):
            raise TeamsServiceError("channel.deliveryUnknown")
        if result.status == "failed":
            raise TeamsServiceError()
        if result.status == "declined":
            raise TeamsServiceError("channel.fileDeclined")
        return result
