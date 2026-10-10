"""Bounded, verified sparse session cache in the private Agent data directory."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from . import windows_handles as handles
from .errors import AgentError
from .protocol import ArtifactDescriptor


class WindowsCache:
    def __init__(self, data_dir: Path, max_bytes: int) -> None:
        self.root = data_dir / "session-cache"
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise AgentError("cache.invalidPath", "Cache root is a reparse point")
        self.staging = data_dir / "transfers"
        self.max_bytes = max_bytes
        self.lock = threading.RLock()

    def _path(self, artifact: ArtifactDescriptor) -> Path:
        parts = (artifact.backend_id, artifact.session_id, *artifact.path.split("/"))
        if any(not p or p in {".", ".."} or "\\" in p or ":" in p for p in parts):
            raise AgentError("cache.invalidDescriptor", "Invalid cache identity")
        path = self.root
        for part in parts[:-1]:
            path = path / part
            if (
                path.exists()
                and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            ):
                raise AgentError("cache.invalidPath", "Cache path is a link")
        return path / parts[-1]

    def _check(self, artifact: ArtifactDescriptor) -> bool:
        path = self._path(artifact)
        if path.exists() and path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise AgentError("cache.invalidPath", "Cache file is a link")
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            valid = (
                path.stat().st_size == artifact.size
                and hashlib.file_digest(handle, "sha256").hexdigest() == artifact.sha256
            )
        if not valid:
            path.unlink()
        return valid

    async def ensure(self, artifact, transfer, client) -> dict:
        if await asyncio.to_thread(self._check, artifact):
            return {"cached": True, "size": artifact.size, "sha256": artifact.sha256}
        temporary = await client.download(
            transfer, self.staging, expected_size=artifact.size, expected_sha256=artifact.sha256
        )
        try:
            await asyncio.to_thread(self._store_path, artifact, temporary)
        finally:
            temporary.unlink(missing_ok=True)
        return {"cached": False, "size": artifact.size, "sha256": artifact.sha256}

    def _store_path(self, artifact, source: Path) -> None:
        with source.open("rb") as handle:
            self.store(artifact, handle)

    def store(self, artifact, source: BinaryIO) -> dict:
        with self.lock:
            path = self._path(artifact)
            if path.is_symlink():
                raise AgentError("cache.invalidPath", "Cache file is a link")
            digest = hashlib.sha256()
            size = 0
            with handles.directory(path.parent.as_posix(), create=True, write=True) as parent:
                with handles.staged_file(parent, path.name, replace=True) as created:
                    with handles.stream(created, "wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            if size > artifact.size:
                                raise AgentError(
                                    "cache.invalidContent", "Cache content exceeds its size"
                                )
                            digest.update(chunk)
                            destination.write(chunk)
                        destination.flush()
                        os.fsync(destination.fileno())
                    if size != artifact.size or digest.hexdigest() != artifact.sha256:
                        raise AgentError("cache.invalidContent", "Cache content failed validation")
            self.evict()
            return {"size": size, "sha256": artifact.sha256}

    @contextmanager
    def open_export(self, artifact) -> Iterator[tuple[BinaryIO, int, str]]:
        if not self._check(artifact):
            raise AgentError("cache.notFound", "Cached file is unavailable")
        with self._path(artifact).open("rb") as handle:
            yield handle, artifact.size, artifact.sha256

    def evict(self) -> int:
        with self.lock:
            files = []
            for directory, names, filenames in os.walk(self.root, followlinks=False):
                names[:] = [name for name in names if not (Path(directory) / name).is_symlink()]
                for name in filenames:
                    path = Path(directory) / name
                    if path.is_file() and not path.is_symlink():
                        stat = path.stat()
                        files.append((stat.st_mtime, stat.st_size, path))
            total = sum(size for _, size, _ in files)
            removed = 0
            for _, size, path in sorted(files):
                if total <= self.max_bytes:
                    break
                if any(root in path.parents for root in getattr(self, "protected_paths", ())):
                    continue
                path.unlink(missing_ok=True)
                total -= size
                removed += 1
            return removed

    def clear(self) -> int:
        with self.lock:
            if getattr(self, "protected_paths", None):
                raise AgentError("cache.busy", "Active skills are using the cache")
            if self.root.is_symlink():
                raise AgentError("cache.invalidPath", "Cache directory is a link")
            count = 0
            for child in self.root.iterdir():
                if child.is_dir() and not child.is_symlink():
                    count += sum(len(files) for _, _, files in os.walk(child))
                    shutil.rmtree(child)
                else:
                    child.unlink()
                    count += 1
            return count
