from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from .filesystem import AgentError, _relative_parts
from .protocol import ArtifactDescriptor, TransferSpec
from .transfer import TransferClient


class SessionCache:
    def __init__(self, data_dir: Path, max_bytes: int) -> None:
        self.root = data_dir / "session-cache"
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = self.root.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("session cache path must be a real directory")
        os.chmod(self.root, 0o700, follow_symlinks=False)
        self._identity = (metadata.st_dev, metadata.st_ino)
        self._lock = threading.RLock()
        self.staging = data_dir / "transfers"

    @staticmethod
    def _cache_parts(artifact: ArtifactDescriptor) -> tuple[str, ...]:
        backend = _relative_parts(artifact.backend_id)
        session = _relative_parts(artifact.session_id)
        relative = _relative_parts(artifact.path)
        if len(backend) != 1 or len(session) != 1 or not relative:
            raise AgentError("cache.invalidDescriptor", "Invalid cache identity")
        return (*backend, *session, *relative)

    def _path(self, artifact: ArtifactDescriptor) -> Path:
        return self.root.joinpath(*self._cache_parts(artifact))

    @contextmanager
    def _parent_fd(
        self, artifact: ArtifactDescriptor, *, create: bool
    ) -> Iterator[tuple[int, str]]:
        parts = self._cache_parts(artifact)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open(self.root, flags)
            metadata = os.fstat(fd)
            if (metadata.st_dev, metadata.st_ino) != self._identity:
                raise OSError
        except OSError as exc:
            if "fd" in locals():
                os.close(fd)
            raise AgentError("cache.invalidPath", "Cache root is unavailable") from exc
        try:
            for part in parts[:-1]:
                try:
                    child = os.open(part, flags, dir_fd=fd)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, 0o700, dir_fd=fd)
                    child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd, parts[-1]
        except OSError as exc:
            raise AgentError("cache.invalidPath", "Cache path is unavailable") from exc
        finally:
            os.close(fd)

    def _check(self, artifact: ArtifactDescriptor) -> bool:
        with self._lock, self._parent_fd(artifact, create=True) as (parent, name):
            try:
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return False
            if stat.S_ISREG(metadata.st_mode):
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                with os.fdopen(descriptor, "rb") as handle:
                    digest = hashlib.file_digest(handle, "sha256").hexdigest()
                if metadata.st_size == artifact.size and digest == artifact.sha256:
                    os.utime(name, dir_fd=parent, follow_symlinks=False)
                    return True
                os.unlink(name, dir_fd=parent)
                return False
            if stat.S_ISLNK(metadata.st_mode):
                os.unlink(name, dir_fd=parent)
                return False
            raise AgentError("cache.invalidPath", "Cache target is unavailable")

    async def ensure(
        self,
        artifact: ArtifactDescriptor,
        transfer: TransferSpec,
        client: TransferClient,
    ) -> dict:
        if await asyncio.to_thread(self._check, artifact):
            return {"cached": True, "size": artifact.size, "sha256": artifact.sha256}
        temporary = await client.download(
            transfer,
            self.staging,
            expected_size=artifact.size,
            expected_sha256=artifact.sha256,
        )
        try:
            await asyncio.to_thread(self._publish, artifact, temporary)
        finally:
            temporary.unlink(missing_ok=True)
        return {"cached": False, "size": artifact.size, "sha256": artifact.sha256}

    def _publish(self, artifact: ArtifactDescriptor, temporary: Path) -> None:
        with self._lock, self._parent_fd(artifact, create=True) as (parent, name):
            os.replace(temporary, name, dst_dir_fd=parent)
            os.chmod(name, 0o600, dir_fd=parent, follow_symlinks=False)
            os.fsync(parent)
            self._evict_unlocked()

    def store(self, artifact: ArtifactDescriptor, source) -> dict:
        with self._lock, self._parent_fd(artifact, create=True) as (parent, name):
            temporary = f".cache-{uuid4().hex}.tmp"
            digest = hashlib.sha256()
            size = 0
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(descriptor, "wb") as destination:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        if size > artifact.size:
                            raise AgentError("cache.invalidContent", "Cached content is too large")
                        digest.update(chunk)
                        destination.write(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
                if size != artifact.size or digest.hexdigest() != artifact.sha256:
                    raise AgentError("cache.invalidContent", "Cached content failed validation")
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
            finally:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
            self._evict_unlocked()
        return {"size": size, "sha256": artifact.sha256}

    @contextmanager
    def open_export(self, artifact: ArtifactDescriptor) -> Iterator[tuple[BinaryIO, int, str]]:
        with self._lock, self._parent_fd(artifact, create=False) as (parent, name):
            try:
                descriptor = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent
                )
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError
            except FileNotFoundError as exc:
                raise AgentError("cache.notFound", "Cached artifact is unavailable") from exc
            except OSError as exc:
                if "descriptor" in locals():
                    os.close(descriptor)
                raise AgentError("cache.invalidPath", "Cached artifact is unavailable") from exc
        handle = os.fdopen(descriptor, "rb")
        try:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if metadata.st_size != artifact.size or digest != artifact.sha256:
                raise AgentError("cache.invalidContent", "Cached artifact failed validation")
            handle.seek(0)
            yield handle, metadata.st_size, digest
        finally:
            handle.close()

    def evict(self) -> int:
        with self._lock:
            self._verify_root()
            return self._evict_unlocked()

    def _verify_root(self) -> None:
        try:
            descriptor = os.open(
                self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            )
        except OSError as exc:
            raise AgentError("cache.invalidPath", "Cache root is unavailable") from exc
        try:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != self._identity:
                raise AgentError("cache.invalidPath", "Cache root is unavailable")
        finally:
            os.close(descriptor)

    def _evict_unlocked(self) -> int:
        files: list[tuple[float, int, Path]] = []
        for directory, names, filenames in os.walk(self.root, topdown=True, followlinks=False):
            base = Path(directory)
            for name in list(names):
                candidate = base / name
                if candidate.is_symlink():
                    candidate.unlink()
                    names.remove(name)
            for name in filenames:
                candidate = base / name
                metadata = candidate.lstat()
                if stat.S_ISREG(metadata.st_mode):
                    files.append((metadata.st_mtime, metadata.st_size, candidate))
                else:
                    candidate.unlink()
        total = sum(size for _, size, _ in files)
        removed = 0
        for _, size, path in sorted(files):
            if total <= self.max_bytes:
                break
            path.unlink(missing_ok=True)
            total -= size
            removed += 1
        self._remove_empty_directories()
        return removed

    def _remove_empty_directories(self) -> None:
        directories = [Path(directory) for directory, _, _ in os.walk(self.root)]
        for directory in sorted(directories, key=lambda item: len(item.parts), reverse=True):
            if directory != self.root:
                try:
                    directory.rmdir()
                except OSError:
                    pass

    def clear(self) -> int:
        with self._lock:
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                metadata = os.fstat(fd)
                if (metadata.st_dev, metadata.st_ino) != self._identity:
                    raise AgentError("cache.invalidPath", "Cache root is unavailable")
                return self._clear_fd(fd)
            finally:
                os.close(fd)

    def _clear_fd(self, fd: int) -> int:
        removed = 0
        for name in os.listdir(fd):
            metadata = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(metadata.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try:
                    removed += self._clear_fd(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=fd)
            else:
                os.unlink(name, dir_fd=fd)
                removed += 1
        os.fsync(fd)
        return removed
