from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import stat
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx

from .protocol import TransferSpec
from .workspace import AgentError


class TransferClient:
    def __init__(
        self,
        *,
        max_bytes: int,
        backend_url: str,
        access_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        backend = urlsplit(backend_url)
        scheme = "https" if backend.scheme == "wss" else "http"
        self._origin = (scheme, backend.hostname, backend.port)
        prefix = backend.path[: -len("/execution-agents/connect")]
        self._path_prefix = f"{prefix}/workspace/transfers/"
        self._access_key = access_key
        self._client = client
        self._owns_client = client is None

    def _validate_url(self, url: str) -> None:
        parsed = urlsplit(url)
        origin = (parsed.scheme, parsed.hostname, parsed.port)
        ticket = parsed.path.removeprefix(self._path_prefix)
        try:
            valid_ticket = str(UUID(ticket)) == ticket.lower()
        except ValueError:
            valid_ticket = False
        if (
            origin != self._origin
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.startswith(self._path_prefix)
            or "/" in ticket
            or not valid_ticket
        ):
            raise AgentError("transfer.invalidUrl", "Transfer URL is outside the backend route")

    def _headers(self, ticket: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_key}",
            "X-Mato-Transfer-Token": ticket,
        }

    async def __aenter__(self) -> TransferClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False)
        return self._client

    async def download(
        self,
        transfer: TransferSpec,
        directory: Path,
        *,
        expected_size: int,
        expected_sha256: str,
    ) -> Path:
        self._validate_url(transfer.url)
        if expected_size > self.max_bytes:
            raise AgentError("transfer.tooLarge", "Transfer exceeds the configured limit")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = directory.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AgentError("transfer.invalidPath", "Transfer directory is unavailable")
        temporary = directory / f".download-{uuid4().hex}.tmp"
        digest = hashlib.sha256()
        size = 0
        try:
            async with self.client.stream(
                "GET",
                transfer.url,
                headers=self._headers(transfer.token),
            ) as response:
                if response.status_code != 200:
                    raise AgentError("transfer.failed", "Backend download failed")
                with temporary.open("xb", buffering=0) as handle:
                    os.chmod(temporary, 0o600, follow_symlinks=False)
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > expected_size or size > self.max_bytes:
                            raise AgentError(
                                "transfer.invalidContent", "Downloaded content is too large"
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                    os.fsync(handle.fileno())
            if size != expected_size or digest.hexdigest() != expected_sha256:
                raise AgentError("transfer.invalidContent", "Downloaded content failed validation")
            return temporary
        except httpx.HTTPError as exc:
            raise AgentError("transfer.failed", "Backend download failed") from exc
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    async def upload(
        self,
        transfer: TransferSpec,
        source,
        *,
        size: int,
        sha256: str,
    ) -> None:
        self._validate_url(transfer.url)
        if size > self.max_bytes:
            raise AgentError("transfer.tooLarge", "Transfer exceeds the configured limit")

        sent = 0
        digest = hashlib.sha256()

        async def chunks():
            nonlocal sent
            while chunk := await asyncio.to_thread(source.read, 1024 * 1024):
                sent += len(chunk)
                if sent > size or sent > self.max_bytes:
                    raise AgentError("transfer.invalidContent", "Upload content is too large")
                digest.update(chunk)
                yield chunk
            if sent != size or digest.hexdigest() != sha256:
                raise AgentError("transfer.invalidContent", "Upload content failed validation")

        try:
            response = await self.client.put(
                transfer.url,
                headers={
                    **self._headers(transfer.token),
                    "Content-Length": str(size),
                    "Digest": f"sha-256={base64.b64encode(bytes.fromhex(sha256)).decode()}",
                },
                content=chunks(),
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise AgentError("transfer.failed", "Backend upload failed")
        except httpx.HTTPError as exc:
            raise AgentError("transfer.failed", "Backend upload failed") from exc
