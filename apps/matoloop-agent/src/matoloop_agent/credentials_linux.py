from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Self

from .credentials import CredentialError, CredentialState

MAX_STATE_BYTES = 16 * 1024


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ValueError("non-finite JSON number")


class LinuxCredentialStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "agent-state"
        self._directory_fd: int | None = None
        self._lock_fd: int | None = None

    def __enter__(self) -> Self:
        created = False
        try:
            self.path.mkdir(parents=True, mode=0o700)
            created = True
        except FileExistsError:
            pass
        metadata = self.path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or (not created and stat.S_IMODE(metadata.st_mode) != 0o700)
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
            if created:
                os.fchmod(self._directory_fd, 0o700)
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
