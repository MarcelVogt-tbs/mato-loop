"""Owner-bound, DPAPI-encrypted pairing state on Windows."""

from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import stat
from ctypes import wintypes
from pathlib import Path
from typing import Self
from uuid import uuid4

from .credentials import CredentialError, CredentialState


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]


def _protect(data: bytes, *, decrypt: bool = False) -> bytes:
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    output = _Blob()
    operation = (
        ctypes.windll.crypt32.CryptUnprotectData
        if decrypt
        else ctypes.windll.crypt32.CryptProtectData
    )
    operation.restype = wintypes.BOOL
    args = (
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0 if decrypt else 0x01,
        ctypes.byref(output),
    )
    if not operation(*args):
        raise CredentialError("Windows credential protection failed")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)


class WindowsCredentialStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "agent-state"
        self.lock = None

    def __enter__(self) -> Self:
        self.path.mkdir(parents=True, exist_ok=True)
        if self.path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise CredentialError("Credential directory must not be a link")
        self.lock = (self.path / "credential.lock").open("a+b")
        self.lock.seek(0)
        if not self.lock.read(1):
            self.lock.write(b"0")
            self.lock.flush()
        self.lock.seek(0)
        msvcrt.locking(self.lock.fileno(), msvcrt.LK_LOCK, 1)
        return self

    def __exit__(self, *_: object) -> None:
        if self.lock is not None:
            self.lock.seek(0)
            msvcrt.locking(self.lock.fileno(), msvcrt.LK_UNLCK, 1)
            self.lock.close()

    def load(self) -> CredentialState | None:
        path = self.path / "credential.dat"
        if path.is_symlink():
            raise CredentialError("Credential file must not be a link")
        try:
            encrypted = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(encrypted) > 32 * 1024:
            raise CredentialError("Credential state is too large")
        try:
            return CredentialState.model_validate(json.loads(_protect(encrypted, decrypt=True)))
        except (ValueError, TypeError) as exc:
            raise CredentialError("Credential state is invalid") from exc

    def save(self, state: CredentialState) -> None:
        value = {"version": 1, "backendUrl": state.backend_url, "agentId": state.agent_id}
        if state.access_key is not None:
            value["accessKey"] = state.access_key.get_secret_value()
            value["lastPairingTokenHash"] = state.last_pairing_token_hash
        if state.pending is not None:
            value["pending"] = {
                "accessKey": state.pending.access_key.get_secret_value(),
                "pairingToken": state.pending.pairing_token.get_secret_value(),
            }
        data = json.dumps(value, separators=(",", ":")).encode("utf-8")
        if len(data) > 16 * 1024:
            raise CredentialError("Credential state is too large")
        target = self.path / "credential.dat"
        if target.is_symlink():
            raise CredentialError("Credential file must not be a link")
        temporary = self.path / f".credential-{uuid4().hex}"
        try:
            temporary.write_bytes(_protect(data))
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self) -> None:
        (self.path / "credential.dat").unlink(missing_ok=True)
