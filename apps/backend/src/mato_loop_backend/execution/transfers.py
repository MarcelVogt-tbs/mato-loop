from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from collections.abc import AsyncIterable, Iterator
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import BinaryIO
from uuid import UUID, uuid4

MAX_TRANSFER_BYTES = 256 * 1024 * 1024
TRANSFER_TTL_SECONDS = 60
_SPOOL_BYTES = 1024 * 1024


class TransferError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TransferGrant:
    ticket_id: UUID
    token: str


@dataclass(slots=True)
class TransferTicket:
    id: UUID
    owner_id: UUID
    direction: str
    token_hash: bytes
    expires_at: float
    expected_size: int
    expected_sha256: str | None
    content: BinaryIO | None = None
    actual_size: int | None = None
    actual_sha256: str | None = None


def stage_file(source: BinaryIO, *, max_bytes: int = MAX_TRANSFER_BYTES) -> tuple[BinaryIO, int, str]:
    max_bytes = min(max_bytes, MAX_TRANSFER_BYTES)
    staged = SpooledTemporaryFile(max_size=_SPOOL_BYTES, mode="w+b")
    digest = hashlib.sha256()
    size = 0
    try:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise TransferError("transfer.tooLarge")
            digest.update(chunk)
            staged.write(chunk)
        staged.seek(0)
        return staged, size, digest.hexdigest()
    except BaseException:
        staged.close()
        raise


def stream_ticket(ticket: TransferTicket) -> Iterator[bytes]:
    if ticket.content is None:
        raise TransferError("transfer.invalidTicket")
    with ticket.content:
        while chunk := ticket.content.read(1024 * 1024):
            yield chunk


class TransferTicketStore:
    def __init__(self, *, ttl_seconds: float = TRANSFER_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._tickets: dict[UUID, TransferTicket] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _token_hash(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @staticmethod
    def _validate_expectations(size: int, sha256: str | None) -> None:
        if size < 0 or size > MAX_TRANSFER_BYTES:
            raise TransferError("transfer.tooLarge")
        if sha256 is not None and (
            len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256)
        ):
            raise TransferError("transfer.invalidContent")

    def _create(
        self,
        owner_id: UUID,
        direction: str,
        *,
        size: int,
        sha256: str | None,
        content: BinaryIO | None = None,
    ) -> tuple[TransferGrant, TransferTicket]:
        self._validate_expectations(size, sha256)
        token = secrets.token_urlsafe(48)
        ticket = TransferTicket(
            id=uuid4(),
            owner_id=owner_id,
            direction=direction,
            token_hash=self._token_hash(token),
            expires_at=time.monotonic() + self.ttl_seconds,
            expected_size=size,
            expected_sha256=sha256,
            content=content,
        )
        with self._lock:
            self._discard_expired_locked()
            self._tickets[ticket.id] = ticket
        return TransferGrant(ticket.id, token), ticket

    def create_download(
        self, owner_id: UUID, content: BinaryIO, *, size: int, sha256: str
    ) -> tuple[TransferGrant, TransferTicket]:
        return self._create(
            owner_id, "download", size=size, sha256=sha256, content=content
        )

    def create_upload(
        self, owner_id: UUID, *, size: int, sha256: str | None = None
    ) -> tuple[TransferGrant, TransferTicket]:
        return self._create(owner_id, "upload", size=size, sha256=sha256)

    def claim(
        self,
        ticket_id: UUID,
        token: str,
        direction: str,
        *,
        owner_id: UUID | None = None,
    ) -> TransferTicket:
        with self._lock:
            ticket = self._tickets.get(ticket_id)
            if ticket is None:
                raise TransferError("transfer.invalidTicket")
            if ticket.expires_at <= time.monotonic():
                self._tickets.pop(ticket_id)
                self._close(ticket)
                raise TransferError("transfer.expired")
            if (
                ticket.direction != direction
                or owner_id is not None and ticket.owner_id != owner_id
                or not secrets.compare_digest(ticket.token_hash, self._token_hash(token))
            ):
                raise TransferError("transfer.invalidTicket")
            self._tickets.pop(ticket_id)
            return ticket

    def revoke(self, ticket_id: UUID) -> None:
        with self._lock:
            ticket = self._tickets.pop(ticket_id, None)
        if ticket is not None:
            self._close(ticket)

    async def receive(
        self,
        ticket: TransferTicket,
        chunks: AsyncIterable[bytes],
        *,
        content_length: int | None,
        digest_header: str | None,
    ) -> None:
        if content_length != ticket.expected_size:
            raise TransferError("transfer.invalidContent")
        header_sha256 = self._digest_sha256(digest_header)
        if ticket.expected_sha256 is not None and header_sha256 != ticket.expected_sha256:
            raise TransferError("transfer.invalidContent")
        staged = SpooledTemporaryFile(max_size=_SPOOL_BYTES, mode="w+b")
        digest = hashlib.sha256()
        size = 0
        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > ticket.expected_size or size > MAX_TRANSFER_BYTES:
                    raise TransferError("transfer.invalidContent")
                digest.update(chunk)
                staged.write(chunk)
            actual_sha256 = digest.hexdigest()
            if size != ticket.expected_size or actual_sha256 != header_sha256:
                raise TransferError("transfer.invalidContent")
            staged.seek(0)
            ticket.content = staged
            ticket.actual_size = size
            ticket.actual_sha256 = actual_sha256
        except BaseException:
            staged.close()
            raise

    @staticmethod
    def _digest_sha256(value: str | None) -> str:
        prefix = "sha-256="
        if value is None or not value.startswith(prefix):
            raise TransferError("transfer.invalidContent")
        try:
            digest = base64.b64decode(value[len(prefix) :], validate=True)
        except ValueError as exc:
            raise TransferError("transfer.invalidContent") from exc
        if len(digest) != 32:
            raise TransferError("transfer.invalidContent")
        return digest.hex()

    def _discard_expired_locked(self) -> None:
        now = time.monotonic()
        expired = [ticket_id for ticket_id, ticket in self._tickets.items() if ticket.expires_at <= now]
        for ticket_id in expired:
            self._close(self._tickets.pop(ticket_id))

    @staticmethod
    def _close(ticket: TransferTicket) -> None:
        if ticket.content is not None:
            ticket.content.close()


transfer_tickets = TransferTicketStore()
