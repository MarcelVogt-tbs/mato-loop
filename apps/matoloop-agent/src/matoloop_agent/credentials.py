from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, Self
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class CredentialError(Exception):
    pass


class NotPairedError(CredentialError):
    pass


class PairingTemporaryError(CredentialError):
    pass


class PairingPermanentError(CredentialError):
    pass


@dataclass(frozen=True)
class ResolvedCredential:
    backend_url: str
    agent_id: str
    access_key: str


class PendingCredential(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    access_key: SecretStr = Field(alias="accessKey", min_length=1, max_length=256)
    pairing_token: SecretStr = Field(alias="pairingToken", min_length=1, max_length=4096)


class CredentialState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    backend_url: str = Field(alias="backendUrl", min_length=1, max_length=4096)
    agent_id: str = Field(alias="agentId", min_length=1, max_length=128)
    access_key: SecretStr | None = Field(
        default=None, alias="accessKey", min_length=1, max_length=256
    )
    last_pairing_token_hash: str | None = Field(
        default=None, alias="lastPairingTokenHash", pattern=r"^[0-9a-f]{64}$"
    )
    pending: PendingCredential | None = None

    @model_validator(mode="after")
    def valid_lifecycle(self) -> Self:
        if (self.access_key is None) != (self.last_pairing_token_hash is None):
            raise ValueError("finalized credential fields must be paired")
        if self.access_key is None and self.pending is None:
            raise ValueError("credential state is empty")
        return self


def select_pairing_token(
    command_line_token: str | None,
    environ: Mapping[str, str] = os.environ,
) -> str | None:
    if command_line_token is not None:
        return command_line_token or None
    return environ.get("MATOLOOP_AGENT_PAIRING_TOKEN") or None


def derive_pairing_url(backend_url: str) -> str:
    parsed = urlsplit(backend_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    path = parsed.path.removesuffix("/execution-agents/connect") + "/execution-agents/pair"
    return urlunsplit((scheme, parsed.netloc, path, "", ""))


class CredentialStore(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *_: object) -> None: ...

    def load(self) -> CredentialState | None: ...

    def save(self, state: CredentialState) -> None: ...

    def delete(self) -> None: ...


CredentialStoreFactory = Callable[[Path], CredentialStore]


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _pair(
    backend_url: str,
    agent_id: str,
    pending: PendingCredential,
    client: httpx.AsyncClient,
) -> int:
    response = await client.post(
        derive_pairing_url(backend_url),
        headers={"Authorization": f"Bearer {pending.pairing_token.get_secret_value()}"},
        json={
            "agentId": agent_id,
            "accessKeyHash": hashlib.sha256(
                pending.access_key.get_secret_value().encode()
            ).hexdigest(),
        },
    )
    return response.status_code


async def resolve_credential(
    *,
    data_dir: Path,
    backend_url: str | None,
    agent_id: str | None,
    pairing_token: str | None,
    client: httpx.AsyncClient | None = None,
    store_factory: CredentialStoreFactory | None = None,
) -> ResolvedCredential:
    if store_factory is None:
        from .platform import current_platform

        store_factory = current_platform().credential_store
    with store_factory(data_dir) as store:
        state = store.load()
        if state is not None:
            if (backend_url is not None and state.backend_url != backend_url) or (
                agent_id is not None and state.agent_id != agent_id
            ):
                raise CredentialError(
                    "MatoLoop Agent credential belongs to different configuration"
                )
            backend_url = state.backend_url
            agent_id = state.agent_id

        if state is None:
            if pairing_token is None:
                raise NotPairedError("MatoLoop Agent has not been paired")
            if backend_url is None or agent_id is None:
                raise CredentialError("MatoLoop Agent connection settings are incomplete")
            state = CredentialState(
                version=1,
                backendUrl=backend_url,
                agentId=agent_id,
                pending=PendingCredential(
                    accessKey=secrets.token_urlsafe(48), pairingToken=pairing_token
                ),
            )
            store.save(state)
        elif state.pending is None:
            if pairing_token is None or _token_hash(pairing_token) == state.last_pairing_token_hash:
                if state.access_key is None:
                    raise CredentialError("MatoLoop Agent credential state is invalid")
                return ResolvedCredential(
                    backend_url=state.backend_url,
                    agent_id=state.agent_id,
                    access_key=state.access_key.get_secret_value(),
                )
            state.pending = PendingCredential(
                accessKey=secrets.token_urlsafe(48), pairingToken=pairing_token
            )
            store.save(state)

        if state.pending is None:
            raise CredentialError("MatoLoop Agent credential state is invalid")
        owns_client = client is None
        http = client or httpx.AsyncClient(trust_env=False, follow_redirects=False)
        try:
            try:
                status = await _pair(state.backend_url, state.agent_id, state.pending, http)
            except httpx.HTTPError:
                raise PairingTemporaryError(
                    "MatoLoop Agent pairing is temporarily unavailable"
                ) from None
        finally:
            if owns_client:
                await http.aclose()

        if status == 204:
            access_key = state.pending.access_key.get_secret_value()
            token_hash = _token_hash(state.pending.pairing_token.get_secret_value())
            state.access_key = SecretStr(access_key)
            state.last_pairing_token_hash = token_hash
            state.pending = None
            store.save(state)
            return ResolvedCredential(
                backend_url=state.backend_url,
                agent_id=state.agent_id,
                access_key=access_key,
            )
        if 500 <= status < 600:
            raise PairingTemporaryError("MatoLoop Agent pairing is temporarily unavailable")

        state.pending = None
        if state.access_key is None:
            store.delete()
        else:
            store.save(state)
        raise PairingPermanentError("MatoLoop Agent pairing was rejected")
