from __future__ import annotations

import ctypes
import errno
import fnmatch
import hashlib
import json
import os
import posixpath
import stat
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO
from uuid import uuid4

from .config import AgentConfig
from .errors import AgentError
from .file_text import apply_hunks, decode_text, edited_text, parse_patch, read_result

_RENAME_NOREPLACE = 1
_RENAME_EXCHANGE = 2
_MAX_EXECUTABLE_LINKS = 40
_renameat2 = (
    getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
    if sys.platform.startswith("linux")
    else None
)
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

    @staticmethod
    def _set_ownership(fd: int, owner: os.stat_result) -> None:
        if os.geteuid() == 0:
            os.fchown(fd, owner.st_uid, owner.st_gid)

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
        target, _ = self._resolve_path_target(
            path, current_directory, must_exist=must_exist, allow_symlink=False
        )
        return target

    def resolve_executable_targets(
        self, path: str, current_directory: str
    ) -> list[dict[str, str | int]]:
        targets = []
        seen = set()
        for _ in range(_MAX_EXECUTABLE_LINKS):
            target, destination = self._resolve_path_target(
                path, current_directory, must_exist=False, allow_symlink=True
            )
            targets.append(target)
            if destination is None:
                if len(targets) > 1 and "mode" in target and not stat.S_ISREG(target["mode"]):
                    raise AgentError(
                        "path.unsupportedType", "Executable link target is not a regular file"
                    )
                return targets
            path = posixpath.normpath(
                destination
                if destination.startswith("/")
                else f"{posixpath.dirname(target['path'])}/{destination}"
            )
            if path in seen:
                break
            seen.add(path)
        raise AgentError("path.unsupportedType", "Executable symbolic link chain is invalid")

    def _resolve_path_target(
        self,
        path: str,
        current_directory: str,
        *,
        must_exist: bool,
        allow_symlink: bool,
    ) -> tuple[dict[str, str | int], str | None]:
        candidate = posixpath.normpath(
            path if path.startswith("/") else f"{current_directory}/{path}"
        )
        _parts(candidate)
        metadata = None
        destination = None
        with self._root_fd() as root_fd:
            # Validate traversed components before lexical normalization can erase a link/..
            absolute = path if path.startswith("/") else f"{current_directory}/{path}"
            if "\\" in absolute or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in absolute):
                raise AgentError("path.invalid", "Invalid path target")
            name = absolute.rsplit("/", 1)[-1] or "."
            try:
                parent_fd = self._open_dir(
                    root_fd,
                    tuple(part for part in absolute.split("/")[1:-1] if part),
                    path_only=True,
                )
            except AgentError as exc:
                if not isinstance(exc.__cause__, FileNotFoundError):
                    raise
                if must_exist:
                    raise AgentError("path.notFound", "Path target is unavailable") from exc
                return {"path": candidate}, None
            try:
                metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode):
                    if not allow_symlink:
                        raise AgentError(
                            "path.unsupportedType", "Shell path target is a symbolic link"
                        )
                    destination = os.readlink(name, dir_fd=parent_fd)
            except FileNotFoundError as exc:
                if must_exist:
                    raise AgentError("path.notFound", "Path target is unavailable") from exc
                metadata = None
            except PermissionError as exc:
                raise AgentError("path.accessDenied", "Path access was denied") from exc
            except OSError as exc:
                raise AgentError("path.unavailable", "Path target is unavailable") from exc
            finally:
                os.close(parent_fd)
        target = self._target(candidate, metadata) if metadata is not None else {"path": candidate}
        return target, destination

    def inspect_toolchain_directory(
        self,
        directory: str,
        pattern: str | None,
        *,
        include_hidden: bool = False,
        max_targets: int = 256,
    ) -> list[dict]:
        """Inspect one literal toolchain directory, never recursively or through directory links."""
        targets = []
        listing = []
        requested = directory
        with self._root_fd() as root_fd:
            # The standard usr-merge aliases are the only directory-link exception.
            if directory in {"/bin", "/sbin"}:
                try:
                    metadata = os.stat(directory[1:], dir_fd=root_fd, follow_symlinks=False)
                    destination = (
                        os.readlink(directory[1:], dir_fd=root_fd)
                        if stat.S_ISLNK(metadata.st_mode)
                        else None
                    )
                except OSError as exc:
                    raise AgentError(
                        "path.unavailable", "Toolchain directory is unavailable"
                    ) from exc
                if stat.S_ISLNK(metadata.st_mode):
                    if destination not in {"usr" + directory, "/usr" + directory}:
                        raise AgentError(
                            "path.unsupportedType", "Toolchain directory link is invalid"
                        )
                    targets.append(self._target(directory, metadata))
                    directory = "/usr" + directory
            directory_fd = self._open_dir(root_fd, _parts(directory))
            try:
                directory_target = self._target(directory, os.fstat(directory_fd))
                targets.append(directory_target)
                with os.scandir(directory_fd) as entries:
                    for index, entry in enumerate(entries):
                        if index >= self.max_entries:
                            raise AgentError(
                                "process.inspectionLimitExceeded",
                                "Shell inspection exceeds its limits",
                            )
                        if pattern is None and entry.name.startswith(".") and not include_hidden:
                            continue
                        if pattern is not None and (
                            (entry.name.startswith(".") and not pattern.startswith("."))
                            or not fnmatch.fnmatchcase(entry.name, pattern)
                        ):
                            continue
                        metadata = os.stat(entry.name, dir_fd=directory_fd, follow_symlinks=False)
                        entry_path = f"{directory}/{entry.name}"
                        _parts(entry_path)
                        entry_target = self._target(entry_path, metadata)
                        if entry.name.startswith("."):
                            entry_target["kind"] = "path"
                        listing.append(entry_target)
                        if pattern is not None:
                            # Default ls can traverse a command-line directory symlink. Prove
                            # one-hop file links only; chained links keep the normal hard denial.
                            mode = metadata.st_mode
                            if stat.S_ISLNK(mode):
                                link = os.readlink(entry.name, dir_fd=directory_fd)
                                target = self.resolve_path_target(link, directory, must_exist=True)
                                if posixpath.dirname(
                                    target["path"]
                                ) != directory or posixpath.basename(target["path"]).startswith(
                                    "."
                                ):
                                    target["kind"] = "path"
                                listing.append(target)
                                mode = target["mode"]
                            if not stat.S_ISREG(mode):
                                raise AgentError(
                                    "path.unsupportedType",
                                    "Toolchain operand is not a regular file",
                                )
            except OSError as exc:
                raise AgentError("path.unavailable", "Toolchain metadata is unavailable") from exc
            finally:
                os.close(directory_fd)
        if (
            pattern is not None
            and not any(c in pattern for c in "*?[")
            and not any(t["path"] == f"{directory}/{pattern}" for t in listing)
        ):
            missing = {"path": f"{directory}/{pattern}"}
            if pattern.startswith("."):
                missing["kind"] = "path"
            listing.append(missing)
        if requested != directory:
            listing.extend(
                {**target, "path": requested + target["path"][len(directory) :]}
                for target in listing[:]
                if target["path"].startswith(directory + "/")
            )
        listing.sort(key=lambda target: (target["path"], target.get("kind", "")))
        directory_target["listingFingerprint"] = hashlib.sha256(
            json.dumps(listing, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if pattern is None or len(targets) + len(listing) > max_targets:
            if pattern is not None:
                for target in targets:
                    target["kind"] = "directory"
            # Preserve visible hidden names and external link destinations for policy checks.
            listing = [target for target in listing if target.get("kind") == "path"]
        targets.extend(listing)
        targets = list(
            {(target["path"], target.get("kind", "")): target for target in targets}.values()
        )
        if len(targets) > max_targets:
            raise AgentError(
                "process.inspectionLimitExceeded", "Shell inspection exceeds its limits"
            )
        return sorted(targets, key=lambda target: target["path"])

    @staticmethod
    def _target(
        path: str, metadata: os.stat_result, kind: str | None = None
    ) -> dict[str, str | int]:
        return {
            "path": path,
            "targetKind": kind or ("directory" if stat.S_ISDIR(metadata.st_mode) else "file"),
            "device": metadata.st_dev,
            "rdev": metadata.st_rdev,
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
    def _open_dir(root_fd: int, parts: tuple[str, ...], *, path_only: bool = False) -> int:
        fd = os.dup(root_fd)
        # Inspection needs search permission, not directory enumeration; content operations
        # retain readable descriptors for listing and durability (fsync).
        flags = (
            (os.O_PATH if path_only else os.O_RDONLY)
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
            | os.O_CLOEXEC
        )
        try:
            for part in parts:
                child = os.open(part, flags, dir_fd=fd)
                os.close(fd)
                fd = child
            return fd
        except FileNotFoundError as exc:
            os.close(fd)
            raise AgentError("path.notFound", "Directory was not found") from exc
        except PermissionError as exc:
            os.close(fd)
            raise AgentError("path.accessDenied", "Directory access was denied") from exc
        except NotADirectoryError as exc:
            os.close(fd)
            raise AgentError("path.notDirectory", "Directory is unavailable") from exc
        except OSError as exc:
            os.close(fd)
            if exc.errno == errno.ELOOP:
                raise AgentError("path.notDirectory", "Directory is unavailable") from exc
            raise AgentError("path.unavailable", "Directory is unavailable") from exc

    @staticmethod
    def _metadata(fd: int, name: str) -> os.stat_result:
        try:
            return os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise AgentError("path.notFound", "Path not found") from exc
        except PermissionError as exc:
            raise AgentError("path.accessDenied", "Path access was denied") from exc
        except OSError as exc:
            raise AgentError("path.unavailable", "Path is unavailable") from exc

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
            fd = os.open(
                name,
                os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(fd)
                raise AgentError("path.notFile", "Target is not a regular file")
            return fd, metadata
        except FileNotFoundError as exc:
            raise AgentError("path.notFound", "File was not found") from exc
        except PermissionError as exc:
            raise AgentError("path.accessDenied", "File access was denied") from exc
        except AgentError:
            raise
        except OSError as exc:
            if "fd" in locals():
                os.close(fd)
            if exc.errno == errno.ELOOP:
                raise AgentError("path.notFile", "Target is not a regular file") from exc
            raise AgentError("path.unavailable", "File is unavailable") from exc

    def read(
        self,
        path: str,
        offset: int,
        limit: int,
        max_line_length: int,
    ) -> dict:
        data = self._read_bytes(path, self.max_file_bytes)
        return read_result(data, offset, limit, max_line_length, self.max_response_bytes)

    def _read_bytes(self, path: str, limit: int) -> bytes:
        with (
            self._lock,
            self._parent(path) as (parent, name, _),
        ):
            fd, metadata = self._open_regular(parent, name)
            try:
                if metadata.st_size > limit:
                    raise AgentError("file.tooLarge", "File exceeds the read limit")
                data = bytearray()
                while chunk := os.read(fd, min(1024 * 1024, limit + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > limit:
                        raise AgentError("file.tooLarge", "File exceeds the read limit")
            finally:
                os.close(fd)
        return bytes(data)

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
                    self._set_ownership(handle.fileno(), metadata or os.fstat(parent))
                    os.fchmod(handle.fileno(), metadata.st_mode & 0o777 if metadata else 0o600)
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
                            preserve_temporary = False
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
                    self._set_ownership(handle.fileno(), metadata or os.fstat(parent))
                    os.fchmod(handle.fileno(), metadata.st_mode & 0o777 if metadata else 0o600)
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
        expected_sha256: str | None = None,
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
            updated, digest = edited_text(data, old_text, new_text, replace_all, expected_sha256)
            return self.write(
                path,
                updated,
                digest,
            )

    def _read_patch_file(self, path: str) -> tuple[str, bytes, str]:
        with self._parent(path) as (parent, name, _):
            fd, metadata = self._open_regular(parent, name)
            with os.fdopen(fd, "rb") as current:
                if metadata.st_size > self.max_file_bytes:
                    raise AgentError("file.tooLarge", "File exceeds the patch limit")
                data = current.read(self.max_file_bytes + 1)
        if len(data) > self.max_file_bytes:
            raise AgentError("file.tooLarge", "File exceeds the patch limit")
        content = decode_text(data)
        return content, data, hashlib.sha256(data).hexdigest()

    def _path_must_be_absent(self, path: str) -> None:
        with self._parent(path) as (parent, name, _):
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
        raise AgentError("path.exists", "Destination already exists")

    def _delete_revision(self, path: str, expected_sha256: str) -> None:
        with self._parent(path) as (parent, name, _):
            current_fd, _ = self._open_regular(parent, name)
            os.close(current_fd)
            temporary = f".delete-{uuid4().hex}.tmp"
            placeholder = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=parent,
            )
            os.close(placeholder)
            staged = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
            preserve_temporary = False
            source_staged = False
            try:
                _rename_exchange(parent, temporary, parent, name)
                source_staged = True
                displaced_fd, _ = self._open_regular(parent, temporary)
                with os.fdopen(displaced_fd, "rb") as displaced:
                    digest = hashlib.file_digest(displaced, "sha256").hexdigest()
                if digest != expected_sha256:
                    preserve_temporary = True
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
                        _rename_exchange(parent, temporary, parent, name)
                        source_staged = False
                        preserve_temporary = False
                    raise AgentError("file.changed", "File changed since it was read")
                os.unlink(temporary, dir_fd=parent)
                try:
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                    if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
                        os.unlink(name, dir_fd=parent)
                except FileNotFoundError:
                    pass
                _fsync(parent)
            except BaseException:
                if source_staged:
                    preserve_temporary = True
                    try:
                        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
                            _rename_exchange(parent, temporary, parent, name)
                            preserve_temporary = False
                    except OSError:
                        pass
                if not preserve_temporary:
                    try:
                        os.unlink(temporary, dir_fd=parent)
                    except FileNotFoundError:
                        pass
                raise

    def _move_revision(self, path: str, destination: str, expected_sha256: str) -> None:
        source_parts = _parts(path)
        destination_parts = _parts(destination)
        with self._root_fd() as root:
            source_parent = self._open_dir(root, source_parts[:-1])
            destination_parent = self._open_dir(root, destination_parts[:-1])
            temporary = f".move-{uuid4().hex}.tmp"
            try:
                placeholder = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=source_parent,
                )
                os.close(placeholder)
                staged = os.stat(temporary, dir_fd=source_parent, follow_symlinks=False)
                preserve_temporary = False
                source_staged = False
                try:
                    _rename_exchange(source_parent, temporary, source_parent, source_parts[-1])
                    source_staged = True
                    displaced_fd, _ = self._open_regular(source_parent, temporary)
                    with os.fdopen(displaced_fd, "rb") as displaced:
                        digest = hashlib.file_digest(displaced, "sha256").hexdigest()
                    if digest != expected_sha256:
                        preserve_temporary = True
                        current = os.stat(
                            source_parts[-1], dir_fd=source_parent, follow_symlinks=False
                        )
                        if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
                            _rename_exchange(
                                source_parent, temporary, source_parent, source_parts[-1]
                            )
                            preserve_temporary = False
                        raise AgentError("file.changed", "File changed since it was read")
                    _rename_no_replace(
                        source_parent,
                        temporary,
                        destination_parent,
                        destination_parts[-1],
                    )
                    source_staged = False
                    try:
                        current = os.stat(
                            source_parts[-1], dir_fd=source_parent, follow_symlinks=False
                        )
                        if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
                            os.unlink(source_parts[-1], dir_fd=source_parent)
                    except FileNotFoundError:
                        pass
                    _fsync(source_parent)
                    if source_parent != destination_parent:
                        _fsync(destination_parent)
                except BaseException:
                    if source_staged:
                        try:
                            current = os.stat(
                                source_parts[-1], dir_fd=source_parent, follow_symlinks=False
                            )
                            if (current.st_dev, current.st_ino) == (staged.st_dev, staged.st_ino):
                                _rename_exchange(
                                    source_parent, temporary, source_parent, source_parts[-1]
                                )
                                preserve_temporary = False
                        except OSError:
                            preserve_temporary = True
                    if not preserve_temporary:
                        try:
                            os.unlink(temporary, dir_fd=source_parent)
                        except FileNotFoundError:
                            pass
                    raise
            finally:
                os.close(source_parent)
                os.close(destination_parent)

    def patch(self, patch: str, expected_sha256: dict[str, str]) -> dict:
        operations = parse_patch(
            patch,
            max_bytes=self.max_transfer_bytes,
            max_entries=self.max_entries,
            validate_path=_parts,
        )
        if len(expected_sha256) > self.max_entries:
            raise AgentError("operation.tooLarge", "Patch has too many revisions")
        for path, digest in expected_sha256.items():
            if len(path) > 4096:
                raise AgentError("path.invalid", "Patch revision path is too long")
            _parts(path)
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise AgentError("request.invalid", "Patch revision is invalid")
        revision_paths = {item.path for item in operations if item.operation != "add"}
        if set(expected_sha256) != revision_paths:
            raise AgentError("request.invalid", "Patch revisions do not match its source files")
        touched = [
            path
            for item in operations
            for path in (item.path, item.destination)
            if path is not None
        ]
        if len(touched) != len(set(touched)):
            raise AgentError("request.invalid", "Patch paths must be unique")

        prepared = []
        prepared_bytes = 0
        with self._lock:
            for item in operations:
                if item.operation == "add":
                    self._path_must_be_absent(item.path)
                    content = item.content or ""
                    encoded = content.encode("utf-8")
                    if len(encoded) > self.max_file_bytes or "\0" in content:
                        raise AgentError("file.tooLarge", "Added file exceeds the write limit")
                    digest = hashlib.sha256(encoded).hexdigest()
                else:
                    content, data, digest = self._read_patch_file(item.path)
                    if digest != expected_sha256[item.path]:
                        raise AgentError("file.changed", "File changed since it was read")
                    if item.operation in {"update", "move"}:
                        content = apply_hunks(content, item.hunks)
                        encoded = content.encode("utf-8")
                        if len(encoded) > self.max_file_bytes or "\0" in content:
                            raise AgentError(
                                "file.tooLarge", "Patched file exceeds the write limit"
                            )
                        digest = hashlib.sha256(encoded).hexdigest()
                    else:
                        encoded = data
                    if item.destination is not None:
                        self._path_must_be_absent(item.destination)
                        source_parent = os.stat(
                            _path(_parts(item.path)[:-1]), follow_symlinks=False
                        )
                        destination_parent = os.stat(
                            _path(_parts(item.destination)[:-1]), follow_symlinks=False
                        )
                        if source_parent.st_dev != destination_parent.st_dev:
                            raise AgentError(
                                "path.invalidDestination",
                                "Patch moves must stay on one filesystem",
                            )
                size = len(encoded)
                prepared_bytes += size
                if prepared_bytes > self.max_transfer_bytes:
                    raise AgentError("operation.tooLarge", "Patch files exceed the operation limit")
                prepared.append((item, content, digest, size))

            results = []
            for item, content, digest, size in prepared:
                try:
                    if item.operation == "add":
                        self.write(item.path, content, None)
                    elif item.operation == "delete":
                        self._delete_revision(item.path, expected_sha256[item.path])
                    else:
                        if item.hunks:
                            self.write(item.path, content, expected_sha256[item.path])
                        if item.destination is not None:
                            self._move_revision(item.path, item.destination, digest)
                except Exception as exc:
                    # Index is zero-based; only fully returned operations count as completed.
                    params: dict[str, str | bool | int | float] = {
                        "completedOperations": len(results),
                        "totalOperations": len(prepared),
                        "failedOperationIndex": len(results),
                        "path": item.path,
                    }
                    if item.destination is not None:
                        params["destination"] = item.destination
                    raise AgentError(
                        "file.patchIncomplete",
                        "The patch did not complete. Files may have changed, including in the "
                        "failed operation. Reread the affected paths before retrying.",
                        params,
                    ) from exc
                result = {"operation": item.operation, "path": item.path}
                if item.destination is not None:
                    result["destination"] = item.destination
                result.update({"sha256": digest, "size": size})
                results.append(result)
        return {"files": results}

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
                        temporary = f".mkdir-{uuid4().hex}.tmp"
                        os.mkdir(temporary, 0o700, dir_fd=fd)
                        child = None
                        try:
                            child = os.open(
                                temporary,
                                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=fd,
                            )
                            self._set_ownership(child, os.fstat(fd))
                            os.fchmod(child, 0o700)
                            _rename_no_replace(fd, temporary, fd, part)
                            _fsync(fd)
                        except BaseException:
                            if child is not None:
                                os.close(child)
                            try:
                                os.rmdir(temporary, dir_fd=fd)
                            except FileNotFoundError:
                                pass
                            raise
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
        *,
        check_bytes: bool = True,
    ) -> tuple[int, int]:
        metadata = self._metadata(parent_fd, name)
        if forbidden and (metadata.st_dev, metadata.st_ino) in forbidden:
            raise AgentError("path.invalidDestination", "Destination is inside the source")
        kind = _kind(metadata)
        if kind == "file":
            if check_bytes and metadata.st_size > self.max_transfer_bytes:
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
                child_entries, child_size = self._usage_at(
                    child_fd, child, forbidden, check_bytes=check_bytes
                )
                entries += child_entries
                size += child_size
                if entries > self.max_entries or (check_bytes and size > self.max_transfer_bytes):
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
            self._usage_at(parent, name, check_bytes=False)
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
                    self._set_ownership(destination_handle.fileno(), os.fstat(destination_parent))
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
            self._set_ownership(destination_fd, os.fstat(destination_parent))
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
                        self._set_ownership(target.fileno(), existing or os.fstat(parent))
                        os.fchmod(target.fileno(), existing.st_mode & 0o777 if existing else 0o600)
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
