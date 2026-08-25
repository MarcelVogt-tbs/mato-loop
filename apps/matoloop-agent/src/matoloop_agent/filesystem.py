from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import posixpath
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from .config import AgentConfig


class AgentError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        params: dict[str, str | bool | int | float] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


_RENAME_NOREPLACE = 1
_RENAME_EXCHANGE = 2
_renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
if _renameat2 is not None:
    _renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _renameat2.restype = ctypes.c_int


def _parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value:
        raise AgentError("path.invalid", "Invalid absolute POSIX path")
    if value == "/":
        return ()
    parsed = PurePosixPath(value)
    parts = tuple(value[1:].split("/"))
    if (
        parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise AgentError("path.invalid", "Invalid absolute POSIX path")
    return parts


def _relative_parts(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value or "\\" in value:
        raise AgentError("path.invalid", "Invalid relative POSIX path")
    if value == ".":
        return ()
    parsed = PurePosixPath(value)
    parts = tuple(value.split("/"))
    if (
        parsed.is_absolute()
        or parsed.as_posix() != value
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in value)
    ):
        raise AgentError("path.invalid", "Invalid relative POSIX path")
    return parts


def _path(parts: tuple[str, ...]) -> str:
    return "/" + "/".join(parts)


def _kind(metadata: os.stat_result) -> str:
    if stat.S_ISLNK(metadata.st_mode):
        return "symlink"
    if stat.S_ISDIR(metadata.st_mode):
        return "directory"
    if stat.S_ISREG(metadata.st_mode):
        return "file"
    return "other"


def _fsync(fd: int) -> None:
    os.fsync(fd)


def _rename_no_replace(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    if _renameat2 is None:
        raise AgentError("agent.unsupported", "Atomic rename is unavailable on this Linux host")
    result = _renameat2(
        source_fd,
        os.fsencode(source),
        destination_fd,
        os.fsencode(destination),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    number = ctypes.get_errno()
    if number == errno.EEXIST:
        raise AgentError("path.exists", "Destination already exists")
    if number == errno.EXDEV:
        raise OSError(number, os.strerror(number))
    raise AgentError("path.notFound", "Source or destination parent is unavailable")


def _rename_exchange(source_fd: int, source: str, destination_fd: int, destination: str) -> None:
    if _renameat2 is None:
        raise AgentError("agent.unsupported", "Atomic rename is unavailable on this Linux host")
    result = _renameat2(
        source_fd,
        os.fsencode(source),
        destination_fd,
        os.fsencode(destination),
        _RENAME_EXCHANGE,
    )
    if result == 0:
        return
    number = ctypes.get_errno()
    if number == errno.EXDEV:
        raise OSError(number, os.strerror(number))
    raise AgentError("path.notFound", "Source or destination is unavailable")


class Filesystem:
    def __init__(self, config: AgentConfig) -> None:
        self.max_file_bytes = config.max_file_bytes
        self.max_transfer_bytes = config.max_transfer_bytes
        self.max_entries = config.max_operation_entries
        self.max_response_bytes = max(512, config.max_message_bytes // 2)
        self._lock = threading.RLock()
        with self._root_fd() as root_fd:
            self._root_identity = self._identity(root_fd)

    @staticmethod
    def _identity(fd: int) -> tuple[int, int]:
        metadata = os.fstat(fd)
        return metadata.st_dev, metadata.st_ino

    def resolve_directory(self, path: str) -> dict[str, str]:
        parts = _parts(path)
        with self._root_fd() as root_fd:
            directory_fd = self._open_dir(root_fd, parts)
            os.close(directory_fd)
        if len(path) > 4096:
            raise AgentError("path.invalid", "Directory path is too long")
        return {"path": path}

    def resolve_path_target(
        self, path: str, current_directory: str, *, must_exist: bool
    ) -> dict[str, str | int]:
        candidate = posixpath.normpath(
            path if path.startswith("/") else f"{current_directory}/{path}"
        )
        parts = _parts(candidate)
        if not parts:
            with self._root_fd() as root_fd:
                metadata = os.fstat(root_fd)
            return self._target(candidate, metadata, "directory")
        parent_path = _path(parts[:-1])
        self.resolve_directory(parent_path)
        name = parts[-1]
        if name in {"", ".", ".."}:
            raise AgentError("path.invalid", "Invalid path target")
        metadata = None
        with self._root_fd() as root_fd:
            parent_fd = self._open_dir(root_fd, parts[:-1])
            try:
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    raise OSError
            except FileNotFoundError as exc:
                if must_exist:
                    raise AgentError("path.notFound", "Path target is unavailable") from exc
                metadata = None
            except OSError as exc:
                raise AgentError("path.notFound", "Path target is unavailable") from exc
            finally:
                os.close(parent_fd)
        return self._target(candidate, metadata) if metadata is not None else {"path": candidate}

    @staticmethod
    def _target(
        path: str, metadata: os.stat_result, kind: str | None = None
    ) -> dict[str, str | int]:
        return {
            "path": path,
            "targetKind": kind or ("directory" if stat.S_ISDIR(metadata.st_mode) else "file"),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "mode": metadata.st_mode,
            "size": metadata.st_size,
            "modifiedNs": metadata.st_mtime_ns,
            "changedNs": metadata.st_ctime_ns,
        }

    @contextmanager
    def _root_fd(self) -> Iterator[int]:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            fd = os.open("/", flags)
            if hasattr(self, "_root_identity") and self._identity(fd) != self._root_identity:
                raise OSError
        except OSError as exc:
            if "fd" in locals():
                os.close(fd)
            raise AgentError("path.unavailable", "Filesystem root is unavailable") from exc
        try:
            yield fd
        finally:
            os.close(fd)

    @staticmethod
    def _open_dir(root_fd: int, parts: tuple[str, ...]) -> int:
        fd = os.dup(root_fd)
        try:
            for part in parts:
                child = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
                )
                os.close(fd)
                fd = child
            return fd
        except OSError as exc:
            os.close(fd)
            raise AgentError("path.notDirectory", "Directory is unavailable") from exc

    @staticmethod
    def _metadata(fd: int, name: str) -> os.stat_result:
        try:
            return os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError as exc:
            raise AgentError("path.notFound", "Path not found") from exc

    @contextmanager
    def _parent(self, path: str) -> Iterator[tuple[int, str, tuple[str, ...]]]:
        parts = _parts(path)
        if not parts:
            raise AgentError("path.rootMutation", "The filesystem root is immutable")
        with self._root_fd() as root_fd:
            parent_fd = self._open_dir(root_fd, parts[:-1])
            try:
                yield parent_fd, parts[-1], parts
            finally:
                os.close(parent_fd)

    def stat(self, path: str) -> dict:
        parts = _parts(path)
        with self._lock, self._root_fd() as root_fd:
            if parts:
                parent_fd = self._open_dir(root_fd, parts[:-1])
                try:
                    metadata = self._metadata(parent_fd, parts[-1])
                finally:
                    os.close(parent_fd)
            else:
                metadata = os.fstat(root_fd)
        kind = _kind(metadata)
        return {
            "path": path,
            "kind": kind,
            "size": metadata.st_size if kind == "file" else None,
            "modifiedAt": metadata.st_mtime,
        }

    def list(
        self,
        path: str,
        offset: int,
        limit: int,
    ) -> dict:
        parts = _parts(path)
        with self._lock, self._root_fd() as root_fd:
            directory_fd = self._open_dir(root_fd, parts)
            try:
                names = os.listdir(directory_fd)
                if len(names) > self.max_entries:
                    raise AgentError("operation.tooLarge", "Directory contains too many entries")
                entries = []
                for name in names:
                    _relative_parts(name)
                    metadata = self._metadata(directory_fd, name)
                    kind = _kind(metadata)
                    entries.append(
                        {
                            "name": name,
                            "path": _path((*parts, name)),
                            "kind": kind,
                            "size": metadata.st_size
                            if kind in {"file", "symlink", "other"}
                            else None,
                            "modifiedAt": metadata.st_mtime,
                        }
                    )
            finally:
                os.close(directory_fd)
        entries.sort(
            key=lambda item: (item["kind"] != "directory", item["name"].casefold(), item["name"])
        )
        page = entries[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "entries": page,
            "hasMore": next_offset < len(entries),
            "nextOffset": next_offset if next_offset < len(entries) else None,
        }

    def _open_regular(self, parent_fd: int, name: str) -> tuple[int, os.stat_result]:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError
            return fd, metadata
        except OSError as exc:
            if "fd" in locals():
                os.close(fd)
            raise AgentError("path.notFile", "File is unavailable") from exc

    def read(
        self,
        path: str,
        offset: int,
        limit: int,
        max_line_length: int,
    ) -> dict:
        with (
            self._lock,
            self._parent(path) as (parent, name, _),
        ):
            fd, metadata = self._open_regular(parent, name)
            try:
                if metadata.st_size > self.max_file_bytes:
                    raise AgentError("file.tooLarge", "File exceeds the read limit")
                data = bytearray()
                while chunk := os.read(fd, min(1024 * 1024, self.max_file_bytes + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > self.max_file_bytes:
                        raise AgentError("file.tooLarge", "File exceeds the read limit")
            finally:
                os.close(fd)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise AgentError("file.invalidEncoding", "File is not UTF-8 text") from exc
        raw_lines = text.splitlines()
        selected: list[str] = []
        truncated: list[int] = []
        response_bytes = 0
        for index, line in enumerate(raw_lines[offset - 1 : offset - 1 + limit]):
            line_number = offset + index
            rendered = f"{line_number}: {line[:max_line_length]}"
            projected = len(json.dumps(rendered).encode()) + (1 if selected else 0)
            if response_bytes + projected > self.max_response_bytes:
                if not selected:
                    raise AgentError("operation.tooLarge", "A line exceeds the response limit")
                break
            selected.append(rendered)
            response_bytes += projected
            if len(line) > max_line_length:
                truncated.append(line_number)
        content = "\n".join(selected)
        next_offset = offset + len(selected)
        has_more = next_offset - 1 < len(raw_lines)
        return {
            "content": content,
            "linesRead": len(selected),
            "hasMore": has_more,
            "nextOffset": next_offset if has_more else None,
            "truncatedLines": truncated,
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def write(
        self,
        path: str,
        content: str,
        expected_sha256: str | None,
    ) -> dict:
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes or "\0" in content:
            raise AgentError("file.tooLarge", "Content exceeds the write limit")
        with (
            self._lock,
            self._parent(path) as (parent, name, parts),
        ):
            try:
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                metadata = None
            if metadata is not None and (
                stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
            ):
                raise AgentError("path.notFile", "Target is not a regular file")
            if expected_sha256 is not None:
                if metadata is None:
                    raise AgentError("path.notFound", "File not found")
                current_fd, _ = self._open_regular(parent, name)
                with os.fdopen(current_fd, "rb") as current:
                    digest = hashlib.file_digest(current, "sha256").hexdigest()
                if digest != expected_sha256:
                    raise AgentError("file.changed", "File changed since it was read")
            temporary = f".write-{uuid4().hex}.tmp"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
            preserve_temporary = False
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                if expected_sha256 is None:
                    _rename_no_replace(parent, temporary, parent, name)
                else:
                    staged = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                    _rename_exchange(parent, temporary, parent, name)
                    try:
                        displaced_fd, _ = self._open_regular(parent, temporary)
                        with os.fdopen(displaced_fd, "rb") as displaced:
                            displaced_digest = hashlib.file_digest(displaced, "sha256").hexdigest()
                        if displaced_digest != expected_sha256:
                            raise AgentError("file.changed", "File changed since it was read")
                    except BaseException:
                        preserve_temporary = True
                        try:
                            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        except OSError:
                            raise
                        if (current.st_dev, current.st_ino) != (
                            staged.st_dev,
                            staged.st_ino,
                        ):
                            raise
                        try:
                            _rename_exchange(parent, temporary, parent, name)
                        except BaseException:
                            raise
                        raise
                    os.unlink(temporary, dir_fd=parent)
                _fsync(parent)
            except BaseException:
                if not preserve_temporary:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                raise
        return {
            "path": _path(parts),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    def upsert(
        self,
        path: str,
        content: str,
    ) -> dict:
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes:
            raise AgentError("file.tooLarge", "Content exceeds the write limit")
        if "\0" in content:
            raise AgentError("file.invalidEncoding", "File is not editable UTF-8 text")
        with (
            self._lock,
            self._parent(path) as (parent, name, parts),
        ):
            try:
                metadata = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                metadata = None
            if metadata is not None and (
                stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
            ):
                raise AgentError("path.notFile", "Target is not a regular file")
            temporary = f".upsert-{uuid4().hex}.tmp"
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                _fsync(parent)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent)
                except FileNotFoundError:
                    pass
                raise
        return {
            "path": _path(parts),
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        }

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool,
    ) -> dict:
        if not old_text or old_text == new_text:
            raise AgentError("request.invalid", "Edit text must be non-empty and different")
        with (
            self._lock,
            self._parent(path) as (parent, name, _),
        ):
            fd, metadata = self._open_regular(parent, name)
            with os.fdopen(fd, "rb") as current:
                if metadata.st_size > self.max_file_bytes:
                    raise AgentError("file.tooLarge", "File exceeds the edit limit")
                data = current.read(self.max_file_bytes + 1)
            if len(data) > self.max_file_bytes:
                raise AgentError("file.tooLarge", "File exceeds the edit limit")
            try:
                content = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AgentError("file.invalidEncoding", "File is not UTF-8 text") from exc

            ending = "\r\n" if "\r\n" in content else "\n"

            def adapt(value: str) -> str:
                value = value.replace("\r\n", "\n")
                return value if ending == "\n" else value.replace("\n", "\r\n")

            old = adapt(old_text)
            replacement = adapt(new_text)
            if old == replacement:
                raise AgentError("request.invalid", "Edit text must be different")
            index = content.find(old)
            if index < 0:
                raise AgentError("file.editTextNotFound", "Edit text was not found")
            if not replace_all and content.find(old, index + 1) >= 0:
                raise AgentError("file.editTextNotUnique", "Edit text is not unique")
            updated = (
                content.replace(old, replacement)
                if replace_all
                else content[:index] + replacement + content[index + len(old) :]
            )
            return self.write(
                path,
                updated,
                hashlib.sha256(data).hexdigest(),
            )

    def mkdir(self, path: str, parents: bool) -> dict:
        parts = _parts(path)
        if not parts:
            raise AgentError("path.rootMutation", "The filesystem root is immutable")
        with self._lock, self._root_fd() as root_fd:
            fd = os.dup(root_fd)
            try:
                for index, part in enumerate(parts):
                    try:
                        child = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                        if index == len(parts) - 1 and not parents:
                            os.close(child)
                            raise AgentError("path.exists", "Destination already exists")
                    except FileNotFoundError:
                        if not parents and index != len(parts) - 1:
                            raise AgentError(
                                "path.notDirectory", "Parent directory is unavailable"
                            ) from None
                        os.mkdir(part, 0o700, dir_fd=fd)
                        _fsync(fd)
                        child = os.open(
                            part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                        )
                    except OSError as exc:
                        raise AgentError("path.notDirectory", "Directory is unavailable") from exc
                    os.close(fd)
                    fd = child
            finally:
                os.close(fd)
        return {"path": path}

    def rename(self, path: str, destination: str) -> dict:
        source_parts = _parts(path)
        destination_parts = _parts(destination)
        if not source_parts or not destination_parts:
            raise AgentError("path.rootMutation", "A workspace root is immutable")
        with self._lock, self._root_fd() as root_fd:
            source_parent = self._open_dir(root_fd, source_parts[:-1])
            destination_parent = self._open_dir(root_fd, destination_parts[:-1])
            try:
                metadata = self._metadata(source_parent, source_parts[-1])
                if _kind(metadata) not in {"file", "directory"}:
                    raise AgentError("path.unsupportedType", "Unsupported source type")
                _rename_no_replace(
                    source_parent, source_parts[-1], destination_parent, destination_parts[-1]
                )
                _fsync(source_parent)
                if source_parent != destination_parent:
                    _fsync(destination_parent)
            finally:
                os.close(source_parent)
                os.close(destination_parent)
        return {"path": destination}

    def _usage_at(
        self,
        parent_fd: int,
        name: str,
        forbidden: set[tuple[int, int]] | None = None,
    ) -> tuple[int, int]:
        metadata = self._metadata(parent_fd, name)
        if forbidden and (metadata.st_dev, metadata.st_ino) in forbidden:
            raise AgentError("path.invalidDestination", "Destination is inside the source")
        kind = _kind(metadata)
        if kind == "file":
            if metadata.st_size > self.max_transfer_bytes:
                raise AgentError("operation.tooLarge", "Source exceeds operation limits")
            return 1, metadata.st_size
        if kind == "symlink":
            return 1, metadata.st_size
        if kind != "directory":
            raise AgentError(
                "path.unsupportedType", "Only regular files and directories are supported"
            )
        entries, size = 1, 0
        child_fd = self._open_dir(parent_fd, (name,))
        try:
            for child in os.listdir(child_fd):
                _relative_parts(child)
                child_entries, child_size = self._usage_at(child_fd, child, forbidden)
                entries += child_entries
                size += child_size
                if entries > self.max_entries or size > self.max_transfer_bytes:
                    raise AgentError("operation.tooLarge", "Source exceeds operation limits")
        finally:
            os.close(child_fd)
        return entries, size

    def _delete_at(self, parent_fd: int, name: str, recursive: bool) -> None:
        metadata = self._metadata(parent_fd, name)
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = self._open_dir(parent_fd, (name,))
            try:
                names = os.listdir(child_fd)
                if names and not recursive:
                    raise AgentError("path.notEmpty", "Directory is not empty")
                for child in names:
                    self._delete_at(child_fd, child, True)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=parent_fd)
        elif stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            os.unlink(name, dir_fd=parent_fd)
        else:
            raise AgentError("path.unsupportedType", "Unsupported path type")

    def delete(self, path: str, recursive: bool) -> dict:
        with (
            self._lock,
            self._parent(path) as (parent, name, _),
        ):
            self._usage_at(parent, name)
            self._delete_at(parent, name, recursive)
            _fsync(parent)
        return {}

    def _copy_at(
        self,
        source_parent: int,
        source: str,
        destination_parent: int,
        destination: str,
        usage: list[int],
        forbidden: set[tuple[int, int]],
    ) -> tuple[int, int]:
        metadata = self._metadata(source_parent, source)
        if (metadata.st_dev, metadata.st_ino) in forbidden:
            raise AgentError("path.invalidDestination", "Destination is inside the source")
        usage[0] += 1
        if usage[0] > self.max_entries:
            raise AgentError("operation.tooLarge", "Source exceeds operation limits")
        if stat.S_ISREG(metadata.st_mode):
            source_fd, opened = self._open_regular(source_parent, source)
            try:
                destination_fd = os.open(
                    destination,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=destination_parent,
                )
            except BaseException:
                os.close(source_fd)
                raise
            try:
                with (
                    os.fdopen(source_fd, "rb") as source_handle,
                    os.fdopen(destination_fd, "wb") as destination_handle,
                ):
                    while chunk := source_handle.read(1024 * 1024):
                        usage[1] += len(chunk)
                        if usage[1] > self.max_transfer_bytes:
                            raise AgentError(
                                "operation.tooLarge", "Source exceeds operation limits"
                            )
                        destination_handle.write(chunk)
                    destination_handle.flush()
                    os.fsync(destination_handle.fileno())
            except BaseException:
                try:
                    os.unlink(destination, dir_fd=destination_parent)
                except FileNotFoundError:
                    pass
                raise
            return opened.st_dev, opened.st_ino
        if not stat.S_ISDIR(metadata.st_mode):
            raise AgentError(
                "path.unsupportedType", "Only regular files and directories are supported"
            )
        os.mkdir(destination, 0o700, dir_fd=destination_parent)
        source_fd = self._open_dir(source_parent, (source,))
        opened = os.fstat(source_fd)
        try:
            destination_fd = self._open_dir(destination_parent, (destination,))
        except BaseException:
            os.close(source_fd)
            try:
                os.rmdir(destination, dir_fd=destination_parent)
            except OSError:
                pass
            raise
        try:
            for child in os.listdir(source_fd):
                _relative_parts(child)
                self._copy_at(source_fd, child, destination_fd, child, usage, forbidden)
            _fsync(destination_fd)
        except BaseException:
            self._delete_at(destination_parent, destination, True)
            raise
        finally:
            os.close(source_fd)
            os.close(destination_fd)
        return opened.st_dev, opened.st_ino

    def copy_or_move(
        self,
        source_path: str,
        destination_path: str,
        *,
        move: bool,
    ) -> dict:
        source_parts = _parts(source_path)
        destination_parts = _parts(destination_path)
        if not source_parts or not destination_parts:
            raise AgentError("path.rootMutation", "A workspace root is immutable")
        if destination_parts[: len(source_parts)] == source_parts:
            raise AgentError("path.invalidDestination", "Destination is inside the source")
        with (
            self._lock,
            self._root_fd() as source_root,
            self._root_fd() as destination_root,
        ):
            source_parent = self._open_dir(source_root, source_parts[:-1])
            destination_parent = self._open_dir(destination_root, destination_parts[:-1])
            try:
                forbidden = {
                    (os.fstat(destination_root).st_dev, os.fstat(destination_root).st_ino),
                    (os.fstat(destination_parent).st_dev, os.fstat(destination_parent).st_ino),
                }
                self._usage_at(source_parent, source_parts[-1], forbidden)
                if move:
                    try:
                        _rename_no_replace(
                            source_parent,
                            source_parts[-1],
                            destination_parent,
                            destination_parts[-1],
                        )
                    except OSError as exc:
                        if exc.errno != errno.EXDEV:
                            raise
                    else:
                        _fsync(source_parent)
                        _fsync(destination_parent)
                        return {"path": destination_path}
                temporary = f".copy-{uuid4().hex}.tmp"
                source_temporary = f".move-{uuid4().hex}.tmp"
                source_quarantined = False
                destination_published = False
                try:
                    copied_identity = self._copy_at(
                        source_parent,
                        source_parts[-1],
                        destination_parent,
                        temporary,
                        [0, 0],
                        forbidden,
                    )
                    if move:
                        _rename_no_replace(
                            source_parent,
                            source_parts[-1],
                            source_parent,
                            source_temporary,
                        )
                        source_quarantined = True
                        quarantined = self._metadata(source_parent, source_temporary)
                        if (quarantined.st_dev, quarantined.st_ino) != copied_identity:
                            _rename_no_replace(
                                source_parent,
                                source_temporary,
                                source_parent,
                                source_parts[-1],
                            )
                            source_quarantined = False
                            raise AgentError("file.changed", "Source changed while it was moved")
                    _rename_no_replace(
                        destination_parent, temporary, destination_parent, destination_parts[-1]
                    )
                    destination_published = True
                    _fsync(destination_parent)
                    if move:
                        self._delete_at(source_parent, source_temporary, True)
                        source_quarantined = False
                        _fsync(source_parent)
                except BaseException:
                    if source_quarantined and not destination_published:
                        try:
                            _rename_no_replace(
                                source_parent,
                                source_temporary,
                                source_parent,
                                source_parts[-1],
                            )
                            source_quarantined = False
                        except BaseException:
                            pass
                    if not destination_published:
                        try:
                            self._delete_at(destination_parent, temporary, True)
                        except (AgentError, FileNotFoundError):
                            pass
                    raise
            finally:
                os.close(source_parent)
                os.close(destination_parent)
        return {"path": destination_path}

    def import_file(
        self,
        path: str,
        source: Path,
        overwrite: bool,
        expected_size: int | None = None,
        expected_sha256: str | None = None,
    ) -> dict:
        try:
            source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            metadata = os.fstat(source_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise OSError
        except OSError as exc:
            if "source_fd" in locals():
                os.close(source_fd)
            raise AgentError("path.notFile", "Import source is unavailable") from exc
        if metadata.st_size > self.max_transfer_bytes:
            os.close(source_fd)
            raise AgentError("file.tooLarge", "Transfer exceeds the configured limit")
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(source_fd, "rb") as source_handle:
            with (
                self._lock,
                self._parent(path) as (parent, name, parts),
            ):
                try:
                    existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    existing = None
                if existing is not None:
                    if not overwrite:
                        raise AgentError("path.exists", "Destination already exists")
                    if not stat.S_ISREG(existing.st_mode):
                        raise AgentError("path.notFile", "Destination is not a regular file")
                temporary = f".import-{uuid4().hex}.tmp"
                destination_fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent,
                )
                try:
                    with os.fdopen(destination_fd, "wb") as target:
                        while chunk := source_handle.read(1024 * 1024):
                            size += len(chunk)
                            if size > self.max_transfer_bytes:
                                raise AgentError(
                                    "file.tooLarge", "Transfer exceeds the configured limit"
                                )
                            digest.update(chunk)
                            target.write(chunk)
                        target.flush()
                        os.fsync(target.fileno())
                    if (expected_size is not None and size != expected_size) or (
                        expected_sha256 is not None and digest.hexdigest() != expected_sha256
                    ):
                        raise AgentError(
                            "transfer.invalidContent", "Import content failed validation"
                        )
                    if overwrite:
                        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                    else:
                        _rename_no_replace(parent, temporary, parent, name)
                    _fsync(parent)
                except BaseException:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                    raise
        return {
            "path": _path(parts),
            "size": size,
            "sha256": digest.hexdigest(),
        }

    @contextmanager
    def open_export(self, path: str) -> Iterator[tuple[BinaryIO, int, str]]:
        with (
            self._lock,
            self._parent(path) as (parent, name, _),
        ):
            fd, metadata = self._open_regular(parent, name)
        if metadata.st_size > self.max_transfer_bytes:
            os.close(fd)
            raise AgentError("file.tooLarge", "Transfer exceeds the configured limit")
        handle = os.fdopen(fd, "rb")
        try:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
            handle.seek(0)
            yield handle, metadata.st_size, digest
        finally:
            handle.close()
