from __future__ import annotations

import asyncio
import fcntl
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from matoloop_teams_protocol import MAX_BODY_BYTES, DeliveryResult, Event
from pydantic import BaseModel, ConfigDict, Field, JsonValue


class SpoolError(RuntimeError):
    def __init__(self, code: str, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    kind: str
    status: str
    expires_at: datetime
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    message_ids: list[str] = Field(default_factory=list)


class Spool:
    def __init__(self, root: Path, *, max_bytes: int, max_records: int) -> None:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root
        self.max_bytes = max_bytes
        self.max_records = max_records
        self._lock_file = (root / "instance.lock").open("a")
        fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.records: dict[UUID, Record] = {}
        self.changed = asyncio.Event()
        self.receiver: UUID | None = None
        self.generation = uuid4()
        self.lease_until = 0.0
        for path in root.glob("*.json"):
            record = Record.model_validate_json(path.read_bytes())
            self.records[record.id] = record
            if record.status in {"sending", "uploading"}:
                record.status = "unknown"
                self.save(record)
            elif record.kind == "event" and record.status == "handoff":
                record.status = "uncertain"
                self.save(record)
        for path in (*root.glob("*.tmp"), *root.glob("*.blob")):
            try:
                identifier = UUID(path.stem)
            except ValueError:
                continue
            if path.suffix == ".tmp" or identifier not in self.records:
                path.unlink(missing_ok=True)
        self.cleanup()

    @property
    def online(self) -> bool:
        return self.receiver is not None and time.monotonic() < self.lease_until

    def close(self) -> None:
        fcntl.flock(self._lock_file, fcntl.LOCK_UN)
        self._lock_file.close()

    def save(self, record: Record) -> None:
        self.cleanup()
        if record.kind == "event":
            payload_size = len(
                json.dumps(record.payload, ensure_ascii=False, separators=(",", ":")).encode()
            )
            if payload_size > MAX_BODY_BYTES - 256:
                raise SpoolError("relay.eventTooLarge", 413)
        data = record.model_dump_json().encode()
        if record.id not in self.records and len(self.records) >= self.max_records:
            raise SpoolError("relay.capacity", 503)
        target = self.root / f"{record.id}.json"
        temporary = self.root / f"{record.id}.tmp"
        # Charge only growth to the quota; a receipt must be able to replace its larger handoff.
        previous_size = target.stat().st_size if target.exists() else 0
        if len(data) > previous_size:
            self._capacity(len(data) - previous_size)
        with temporary.open("wb") as stream:
            os.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self.records[record.id] = record
        self.changed.set()

    def _capacity(self, additional: int) -> None:
        used = sum(path.stat().st_size for path in self.root.iterdir() if path.is_file())
        if used + additional > self.max_bytes:
            raise SpoolError("relay.capacity", 503)

    def cleanup(self) -> None:
        now = datetime.now(UTC)
        for key, record in list(self.records.items()):
            if record.expires_at <= now:
                (self.root / f"{key}.json").unlink(missing_ok=True)
                (self.root / f"{key}.blob").unlink(missing_ok=True)
                self.records.pop(key, None)

    def blob(self, blob_id: UUID, operation: UUID) -> Path:
        record = self.get(blob_id, "blob")
        if record.status != "ready" or record.payload.get("operation") != str(operation):
            raise SpoolError("relay.blobUnavailable", 404)
        return self.root / f"{blob_id}.blob"

    def put_blob(self, blob_id: UUID, data: bytes, operation: UUID) -> None:
        self.cleanup()
        if blob_id in self.records:
            if self.blob(blob_id, operation).read_bytes() != data:
                raise SpoolError("relay.idConflict")
            return
        self._capacity(len(data) + 1024)
        path = self.root / f"{blob_id}.blob"
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            self.save(
                Record(
                    id=blob_id,
                    kind="blob",
                    status="ready",
                    expires_at=datetime.now(UTC) + timedelta(minutes=30),
                    payload={"operation": str(operation)},
                )
            )
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def get(self, record_id: UUID, kind: str) -> Record:
        self.cleanup()
        record = self.records.get(record_id)
        if record is None or record.kind != kind:
            raise SpoolError("relay.notFound", 404)
        return record

    def lease(self, receiver: UUID) -> UUID:
        if self.online and self.receiver != receiver:
            raise SpoolError("relay.receiverBusy")
        if self.receiver != receiver or not self.online:
            self.generation = uuid4()
        self.receiver = receiver
        self.lease_until = time.monotonic() + 40
        return self.generation

    def pending(self) -> list[Event]:
        self.cleanup()
        now = datetime.now(UTC)
        result = []
        for record in list(self.records.values()):
            if record.kind != "event" or record.status not in {"handoff", "uncertain"}:
                continue
            event = Event.model_validate(record.payload)
            if record.status == "handoff" and (
                event.expires_at <= now or event.generation != self.generation
            ):
                record.status = "uncertain"
                record.expires_at = now + timedelta(hours=24)
                self.save(record)
            result.append(event)
        batch, size = [], 256
        for event in sorted(result, key=lambda event: event.expires_at):
            length = len(event.model_dump_json().encode())
            if size + length > MAX_BODY_BYTES or len(batch) == 20:
                break
            batch.append(event)
            size += length + 1
        return batch

    def delivery_result(self, record: Record) -> DeliveryResult:
        return DeliveryResult.model_validate(
            {
                "id": record.id,
                "status": record.status,
                "message_ids": record.message_ids,
            }
        )

    def remember_conversation(self, identity_key: UUID, payload: dict[str, JsonValue]) -> None:
        self.save(
            Record(
                id=identity_key,
                kind="conversation",
                status="active",
                expires_at=datetime.now(UTC) + timedelta(days=30),
                payload=payload,
            )
        )

    def receipt(self, event_id: UUID, generation: UUID, status: str) -> None:
        record = self.get(event_id, "event")
        if record.payload.get("generation") != str(generation):
            raise SpoolError("relay.bindingChanged")
        if record.status in {"admitted", "rejected"} and record.status != status:
            raise SpoolError("relay.idConflict")
        if record.status == status:
            return
        self.save(record.model_copy(update={
            "status": status,
            "payload": {"generation": str(generation)},
            "expires_at": datetime.now(UTC) + timedelta(hours=24),
        }))

    def serialized_usage(self) -> str:
        return json.dumps({"records": len(self.records), "online": self.online})
