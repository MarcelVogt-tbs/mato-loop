from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

MAX_STATE_BYTES = 16 * 1024


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


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


class CredentialStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "agent-state"
        self._directory_fd: int | None = None
        self._lock_fd: int | None = None

    def __enter__(self) -> CredentialStore:
        created = False
        try:
            self.path.mkdir(parents=True, mode=0o700)
            created = True
        except FileExistsError:
            pass
        if created:
            self.path.chmod(0o700, follow_symlinks=False)
        metadata = self.path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise CredentialError("MatoLoop Agent credential directory is not secure")
        self._directory_fd = os.open(
            self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        )
        opened = os.fstat(self._directory_fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            self.close()
            raise CredentialError("MatoLoop Agent credential directory changed")
        try:
            try:
                self._lock_fd = os.open(
                    "credential.lock",
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=self._directory_fd,
                )
                os.fchmod(self._lock_fd, 0o600)
            except FileExistsError:
                self._lock_fd = os.open(
                    "credential.lock",
                    os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=self._directory_fd,
                )
            self._validate_file(self._lock_fd)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._lock_fd is not None:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
            os.close(self._lock_fd)
            self._lock_fd = None
        if self._directory_fd is not None:
            os.close(self._directory_fd)
            self._directory_fd = None

    @staticmethod
    def _validate_file(descriptor: int) -> os.stat_result:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise CredentialError("MatoLoop Agent credential file is not secure")
        return metadata

    @property
    def directory_fd(self) -> int:
        if self._directory_fd is None:
            raise RuntimeError("credential store is not open")
        return self._directory_fd

    def load(self) -> CredentialState | None:
        try:
            descriptor = os.open(
                "credential.json",
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=self.directory_fd,
            )
        except FileNotFoundError:
            return None
        try:
            metadata = self._validate_file(descriptor)
            if metadata.st_size > MAX_STATE_BYTES:
                raise CredentialError("MatoLoop Agent credential state is invalid")
            chunks: list[bytes] = []
            remaining = MAX_STATE_BYTES + 1
            while remaining and (chunk := os.read(descriptor, remaining)):
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
            if len(raw) > MAX_STATE_BYTES:
                raise CredentialError("MatoLoop Agent credential state is invalid")
        finally:
            os.close(descriptor)
        try:
            data = json.loads(
                raw,
                object_pairs_hook=_strict_object,
                parse_constant=_reject_constant,
            )
            return CredentialState.model_validate(data)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise CredentialError("MatoLoop Agent credential state is invalid") from exc

    def save(self, state: CredentialState) -> None:
        data: dict[str, object] = {
            "version": 1,
            "backendUrl": state.backend_url,
            "agentId": state.agent_id,
        }
        if state.access_key is not None:
            data["accessKey"] = state.access_key.get_secret_value()
            data["lastPairingTokenHash"] = state.last_pairing_token_hash
        if state.pending is not None:
            data["pending"] = {
                "accessKey": state.pending.access_key.get_secret_value(),
                "pairingToken": state.pending.pairing_token.get_secret_value(),
            }
        payload = json.dumps(data, separators=(",", ":")).encode()
        if len(payload) > MAX_STATE_BYTES:
            raise CredentialError("MatoLoop Agent credential state is too large")
        temporary = f".credential-{secrets.token_hex(8)}.tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=self.directory_fd,
        )
        try:
            os.fchmod(descriptor, 0o600)
            self._validate_file(descriptor)
            view = memoryview(payload)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
            os.replace(
                temporary,
                "credential.json",
                src_dir_fd=self.directory_fd,
                dst_dir_fd=self.directory_fd,
            )
            os.fsync(self.directory_fd)
        except BaseException:
            try:
                os.unlink(temporary, dir_fd=self.directory_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(descriptor)

    def delete(self) -> None:
        try:
            os.unlink("credential.json", dir_fd=self.directory_fd)
        except FileNotFoundError:
            return
        os.fsync(self.directory_fd)


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
) -> ResolvedCredential:
    with CredentialStore(data_dir) as store:
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
