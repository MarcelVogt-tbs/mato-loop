from __future__ import annotations

import fcntl
import os
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, TextIO
from uuid import UUID, uuid4

from mato_loop_backend.config import get_runtime_config
from mato_loop_backend.session_storage import SessionStorage, SessionStorageError


class WorkspaceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FileReadResult:
    content: str
    lines_read: int
    has_more: bool
    next_offset: int | None
    truncated_lines: tuple[int, ...]


class FileRoot(StrEnum):
    SESSION = "session"
    WORKSPACE = "workspace"


@dataclass(frozen=True, slots=True)
class RootFileEntry:
    name: str
    path: str
    kind: str
    size: int | None
    modified_at: float


@dataclass(frozen=True, slots=True)
class RootFileListResult:
    entries: tuple[RootFileEntry, ...]
    has_more: bool
    next_offset: int | None


@dataclass(frozen=True, slots=True)
class TreeUsage:
    entries: int
    bytes: int


class RootFileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorkspaceService:
    def __init__(self, home: Path | None = None) -> None:
        self.home = (home or get_runtime_config().agent_home).resolve()
        self.home.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | None, *, base: str | None = None) -> Path:
        raw = (relative_path or ".").strip()
        if Path(raw).is_absolute():
            raise WorkspaceError("workspace.absolutePathNotAllowed", "Absolute Pfade sind nicht erlaubt")
        root = self.home if not base else (self.home / base).resolve()
        try:
            root.relative_to(self.home)
        except ValueError as exc:
            raise WorkspaceError(
                "workspace.invalidWorkingDirectory", "Ungültiges Arbeitsverzeichnis"
            ) from exc
        raw_candidate = root / raw
        candidate = Path(
            os.path.normpath(raw_candidate.parent.resolve(strict=False) / raw_candidate.name)
        )
        try:
            candidate.relative_to(self.home)
        except ValueError as exc:
            raise WorkspaceError(
                "workspace.pathOutsideHome", "Pfad liegt außerhalb des Agent-Homes"
            ) from exc
        return candidate

    def resolve_read_target(self, relative_path: str | None, *, base: str | None = None) -> Path:
        candidate = self.resolve(relative_path, base=base)
        try:
            target = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise WorkspaceError("workspace.pathNotFound", "Pfad nicht gefunden") from exc
        try:
            target.relative_to(self.home)
        except ValueError as exc:
            raise WorkspaceError(
                "workspace.pathOutsideHome", "Pfad liegt außerhalb des Agent-Homes"
            ) from exc
        return target

    def relative(self, path: Path) -> str:
        value = path.absolute().relative_to(self.home)
        return value.as_posix() if value.parts else "."

    def list_entries(self, relative_path: str | None = None) -> list[dict[str, Any]]:
        directory = self.resolve(relative_path)
        resolved_directory = directory.resolve(strict=False)
        try:
            resolved_directory.relative_to(self.home)
        except ValueError as exc:
            raise WorkspaceError(
                "workspace.pathOutsideHome", "Pfad liegt außerhalb des Agent-Homes"
            ) from exc
        if not resolved_directory.exists() or not resolved_directory.is_dir():
            raise WorkspaceError("workspace.directoryNotFound", "Ordner nicht gefunden")
        entries = []
        for item in sorted(
            resolved_directory.iterdir(),
            key=lambda value: (not value.is_dir(), value.name.lower()),
        ):
            is_symlink = item.is_symlink()
            is_directory = item.is_dir() and not is_symlink
            stat = item.lstat()
            entries.append(
                {
                    "name": item.name,
                    "path": self.relative(directory / item.name),
                    "kind": "directory" if is_directory else "symlink" if is_symlink else "file",
                    "size": stat.st_size if not is_directory else None,
                    "modifiedAt": stat.st_mtime,
                    "hasChildren": bool(next(item.iterdir(), None)) if is_directory else False,
                }
            )
        return entries

    def create_directory(self, parent: str | None, name: str) -> dict[str, Any]:
        clean_name = name.strip()
        if not clean_name or clean_name in {".", ".."} or "/" in clean_name or "\\" in clean_name:
            raise WorkspaceError("workspace.invalidDirectoryName", "Ungültiger Ordnername")
        directory = self.resolve(clean_name, base=parent)
        directory.mkdir(parents=False, exist_ok=False)
        return {"name": directory.name, "path": self.relative(directory), "kind": "directory"}

    def delete(self, relative_path: str) -> None:
        target = self.resolve(relative_path)
        if target == self.home:
            raise WorkspaceError("workspace.homeDeleteForbidden", "Das Agent-Home kann nicht gelöscht werden")
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        else:
            raise WorkspaceError("workspace.pathNotFound", "Pfad nicht gefunden")

    @staticmethod
    def _read_bounded_line(handle: TextIO, max_line_length: int) -> tuple[str, bool] | None:
        chunk = handle.readline(max_line_length + 2)
        if chunk == "":
            return None
        has_newline = chunk.endswith("\n")
        value = chunk[:-1] if has_newline else chunk
        if value.endswith("\r"):
            value = value[:-1]
        truncated = len(value) > max_line_length
        display_value = value[:max_line_length]
        while not has_newline and len(chunk) == max_line_length + 2:
            chunk = handle.readline(max_line_length + 2)
            if chunk == "":
                break
            has_newline = chunk.endswith("\n")
        return display_value, truncated

    def read_file(
        self,
        relative_path: str,
        *,
        base: str | None,
        offset: int,
        limit: int,
        max_line_length: int = 2_000,
    ) -> FileReadResult:
        path = self.resolve(relative_path, base=base)
        if not path.is_file() or path.is_symlink():
            raise WorkspaceError("workspace.fileNotFound", "Datei nicht gefunden")
        lines: list[str] = []
        truncated_lines: list[int] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for _ in range(1, offset):
                    if self._read_bounded_line(handle, max_line_length) is None:
                        break
                for line_number in range(offset, offset + limit):
                    line = self._read_bounded_line(handle, max_line_length)
                    if line is None:
                        break
                    value, truncated = line
                    lines.append(f"{line_number}: {value}")
                    if truncated:
                        truncated_lines.append(line_number)
                has_more = self._read_bounded_line(handle, max_line_length) is not None
        except UnicodeDecodeError as exc:
            raise WorkspaceError(
                "workspace.invalidEncoding", "Datei ist nicht als UTF-8 lesbar"
            ) from exc
        lines_read = len(lines)
        return FileReadResult(
            content="\n".join(lines),
            lines_read=lines_read,
            has_more=has_more,
            next_offset=offset + lines_read if has_more else None,
            truncated_lines=tuple(truncated_lines),
        )

    def write_file(self, relative_path: str, content: str, *, base: str | None) -> str:
        path = self.resolve(relative_path, base=base)
        if path.exists() and path.is_symlink():
            raise WorkspaceError("workspace.symlinkWriteForbidden", "Symlinks dürfen nicht beschrieben werden")
        if not path.parent.exists():
            raise WorkspaceError("workspace.parentNotFound", "Elternordner fehlt")
        path.write_text(content, encoding="utf-8")
        return self.relative(path)


class RootFileService:
    """Access a session root and an optional selected working directory."""

    MAX_LIST_LIMIT = 1_000
    SESSION_QUOTA_BYTES = 500 * 1024 * 1024
    MAX_OPERATION_ENTRIES = 10_000
    MAX_OPERATION_BYTES = 256 * 1024 * 1024

    def __init__(
        self,
        session_id: UUID,
        *,
        session_storage: SessionStorage | None = None,
        workspace_home: Path | None = None,
        working_directory: str | None = None,
    ) -> None:
        if not isinstance(session_id, UUID):
            raise TypeError("session_id muss hostseitig als UUID uebergeben werden")
        self.session_id = session_id
        self.session_storage = session_storage or SessionStorage()
        self.workspace_home = (workspace_home or get_runtime_config().agent_home).absolute()
        self.working_directory = working_directory

    @staticmethod
    def _parts(relative_path: str) -> tuple[str, ...]:
        if not isinstance(relative_path, str):
            raise RootFileError("file.invalidPath", "Ungueltiger relativer Pfad")
        if relative_path == ".":
            return ()
        if not relative_path or "\\" in relative_path:
            raise RootFileError("file.invalidPath", "Ungueltiger relativer POSIX-Pfad")
        if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in relative_path):
            raise RootFileError("file.invalidPath", "Steuerzeichen sind in Pfaden nicht erlaubt")
        parsed = PurePosixPath(relative_path)
        parts = tuple(relative_path.split("/"))
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parts):
            raise RootFileError("file.invalidPath", "Ungueltiger relativer POSIX-Pfad")
        return parts

    @staticmethod
    def _relative_path(parts: tuple[str, ...]) -> str:
        return "/".join(parts) if parts else "."

    def normalize_relative_path(self, relative_path: str) -> str:
        return self._relative_path(self._parts(relative_path))

    def root_identity(self, root: FileRoot | str) -> str:
        selected_root = self._selected_root(root)
        self._root_path(selected_root)
        if selected_root is FileRoot.SESSION:
            return str(self.session_id)
        assert self.working_directory is not None
        return self.working_directory

    @staticmethod
    def _metadata(path: Path) -> os.stat_result:
        try:
            return path.lstat()
        except FileNotFoundError as exc:
            raise RootFileError("file.pathNotFound", "Pfad nicht gefunden") from exc

    @classmethod
    def _ensure_directory(cls, path: Path, *, code: str = "file.directoryNotFound") -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError as exc:
            raise RootFileError(code, "Ordner nicht gefunden") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise RootFileError("file.symlinkForbidden", "Symlinks werden nicht verfolgt")
        if not stat.S_ISDIR(metadata.st_mode):
            raise RootFileError(code, "Ordner nicht gefunden")

    @classmethod
    def _ensure_no_symlinks(
        cls,
        root: Path,
        parts: tuple[str, ...],
        *,
        allow_missing_final: bool = False,
        allow_final_symlink: bool = False,
    ) -> Path:
        cls._ensure_directory(root)
        current = root
        for index, part in enumerate(parts):
            current = current / part
            final = index == len(parts) - 1
            try:
                metadata = current.lstat()
            except FileNotFoundError:
                if allow_missing_final and final:
                    return current
                raise RootFileError("file.pathNotFound", "Pfad nicht gefunden") from None
            if stat.S_ISLNK(metadata.st_mode) and not (allow_final_symlink and final):
                raise RootFileError("file.symlinkForbidden", "Symlinks werden nicht verfolgt")
            if not final and not stat.S_ISDIR(metadata.st_mode):
                raise RootFileError("file.pathNotFound", "Pfad nicht gefunden")
        return current

    def _workspace_root(self) -> Path:
        if self.working_directory is None:
            raise RootFileError(
                "file.workspaceNotSelected", "Es ist kein Arbeitsverzeichnis ausgewaehlt"
            )
        try:
            parts = self._parts(self.working_directory)
        except RootFileError as exc:
            raise RootFileError(
                "file.invalidWorkingDirectory", "Ungueltiges Arbeitsverzeichnis"
            ) from exc
        try:
            return self._ensure_no_symlinks(self.workspace_home, parts)
        except RootFileError as exc:
            raise RootFileError(
                "file.invalidWorkingDirectory", "Ungueltiges Arbeitsverzeichnis"
            ) from exc

    def _root_path(self, root: FileRoot | str) -> Path:
        selected_root = self._selected_root(root)
        if selected_root is FileRoot.SESSION:
            try:
                return self.session_storage.session_path(self.session_id, require_exists=True)
            except SessionStorageError as exc:
                raise RootFileError("file.sessionNotProvisioned", "Session-Root fehlt") from exc
        return self._workspace_root()

    @staticmethod
    def _selected_root(root: FileRoot | str) -> FileRoot:
        try:
            return FileRoot(root)
        except ValueError as exc:
            raise RootFileError("file.invalidRoot", "Ungueltiger Datei-Root") from exc

    @contextmanager
    def _session_operation_lock(
        self, *roots: FileRoot
    ) -> Iterator[None]:
        if FileRoot.SESSION not in roots:
            yield
            return
        session_root = self._root_path(FileRoot.SESSION)
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(session_root, flags)
        except OSError as exc:
            raise RootFileError("file.sessionNotProvisioned", "Session-Root fehlt") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISDIR(metadata.st_mode):
                raise RootFileError("file.sessionNotProvisioned", "Session-Root fehlt")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    @staticmethod
    def _tree_usage(root: Path, *, operation_limits: bool) -> TreeUsage:
        entries = 0
        size_bytes = 0
        pending = [root]
        while pending:
            path = pending.pop()
            metadata = RootFileService._metadata(path)
            if stat.S_ISLNK(metadata.st_mode):
                if operation_limits:
                    raise RootFileError(
                        "file.symlinkForbidden", "Symlinks werden nicht verfolgt"
                    )
                size_bytes += metadata.st_size
            elif stat.S_ISREG(metadata.st_mode):
                size_bytes += metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode):
                pending.extend(path.iterdir())
            elif operation_limits:
                raise RootFileError(
                    "file.unsupportedType", "Nicht unterstuetzter Dateityp"
                )
            else:
                size_bytes += metadata.st_size
            entries += 1
            if operation_limits and (
                entries > RootFileService.MAX_OPERATION_ENTRIES
                or size_bytes > RootFileService.MAX_OPERATION_BYTES
            ):
                raise RootFileError(
                    "file.operationTooLarge",
                    "Quelle ueberschreitet das Limit fuer Dateioperationen",
                )
        return TreeUsage(entries=entries, bytes=size_bytes)

    def _operation_usage(self, source: Path) -> TreeUsage:
        return self._tree_usage(source, operation_limits=True)

    def _session_usage(self) -> int:
        session_root = self._root_path(FileRoot.SESSION)
        return self._tree_usage(session_root, operation_limits=False).bytes

    def session_usage(self) -> int:
        """Return the physical usage of the complete current session tree."""

        return self._session_usage()

    def _assert_session_quota(
        self, additional_bytes: int, *, replaced_bytes: int = 0
    ) -> None:
        used_bytes = self._session_usage()
        if used_bytes - replaced_bytes + additional_bytes > self.SESSION_QUOTA_BYTES:
            raise RootFileError(
                "file.sessionQuotaExceeded", "Speicherlimit der Session ist erreicht"
            )

    def resolve(self, root: FileRoot | str, relative_path: str) -> Path:
        parts = self._parts(relative_path)
        return self._ensure_no_symlinks(self._root_path(root), parts)

    def _resolve_destination(self, root: FileRoot | str, relative_path: str) -> Path:
        parts = self._parts(relative_path)
        self._assert_mutable(root, parts)
        if not parts:
            raise RootFileError("file.rootMutationForbidden", "Ein Datei-Root ist unveraenderlich")
        root_path = self._root_path(root)
        parent = self._ensure_no_symlinks(root_path, parts[:-1])
        self._ensure_directory(parent, code="file.parentNotFound")
        target = parent / parts[-1]
        if target.exists() or target.is_symlink():
            raise RootFileError("file.pathAlreadyExists", "Zielpfad existiert bereits")
        return target

    @staticmethod
    def _assert_mutable(root: FileRoot | str, parts: tuple[str, ...]) -> None:
        try:
            selected_root = FileRoot(root)
        except ValueError as exc:
            raise RootFileError("file.invalidRoot", "Ungueltiger Datei-Root") from exc
        if not parts:
            raise RootFileError("file.rootMutationForbidden", "Ein Datei-Root ist unveraenderlich")
        if selected_root is FileRoot.SESSION and (parts[0] != "files" or len(parts) == 1):
            raise RootFileError(
                "file.sessionMutationForbidden",
                "Im Session-Root sind nur Pfade unter files veraenderlich",
            )

    def list_entries(
        self,
        root: FileRoot | str,
        relative_path: str = ".",
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> RootFileListResult:
        if isinstance(offset, bool) or offset < 0:
            raise RootFileError("file.invalidPagination", "Ungueltiger Listen-Offset")
        if isinstance(limit, bool) or not 1 <= limit <= self.MAX_LIST_LIMIT:
            raise RootFileError("file.invalidPagination", "Ungueltiges Listen-Limit")
        parts = self._parts(relative_path)
        directory = self._ensure_no_symlinks(self._root_path(root), parts)
        self._ensure_directory(directory)

        entries: list[RootFileEntry] = []
        for item in directory.iterdir():
            metadata = item.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                kind = "symlink"
                size: int | None = metadata.st_size
            elif stat.S_ISDIR(metadata.st_mode):
                kind = "directory"
                size = None
            elif stat.S_ISREG(metadata.st_mode):
                kind = "file"
                size = metadata.st_size
            else:
                kind = "other"
                size = metadata.st_size
            item_parts = (*parts, item.name)
            entries.append(
                RootFileEntry(
                    name=item.name,
                    path=self._relative_path(item_parts),
                    kind=kind,
                    size=size,
                    modified_at=metadata.st_mtime,
                )
            )
        entries.sort(key=lambda entry: (entry.kind != "directory", entry.name.casefold(), entry.name))
        page = tuple(entries[offset : offset + limit])
        next_offset = offset + len(page)
        has_more = next_offset < len(entries)
        return RootFileListResult(
            entries=page,
            has_more=has_more,
            next_offset=next_offset if has_more else None,
        )

    def list_directory(
        self,
        root: FileRoot | str,
        relative_path: str = ".",
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> RootFileListResult:
        return self.list_entries(root, relative_path, offset=offset, limit=limit)

    def read_file(
        self,
        root: FileRoot | str,
        relative_path: str,
        *,
        offset: int = 1,
        limit: int = 2_000,
        max_line_length: int = 2_000,
    ) -> FileReadResult:
        if isinstance(offset, bool) or offset < 1 or isinstance(limit, bool) or limit < 1:
            raise RootFileError("file.invalidPagination", "Ungueltiger Leseausschnitt")
        if isinstance(max_line_length, bool) or max_line_length < 1:
            raise RootFileError("file.invalidPagination", "Ungueltige Zeilenlaenge")
        path = self.resolve(root, relative_path)
        metadata = self._metadata(path)
        if not stat.S_ISREG(metadata.st_mode):
            raise RootFileError("file.fileNotFound", "Datei nicht gefunden")

        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise RootFileError("file.fileNotFound", "Datei nicht gefunden") from exc
        lines: list[str] = []
        truncated_lines: list[int] = []
        try:
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                for _ in range(1, offset):
                    if WorkspaceService._read_bounded_line(handle, max_line_length) is None:
                        break
                for line_number in range(offset, offset + limit):
                    line = WorkspaceService._read_bounded_line(handle, max_line_length)
                    if line is None:
                        break
                    value, truncated = line
                    lines.append(f"{line_number}: {value}")
                    if truncated:
                        truncated_lines.append(line_number)
                has_more = WorkspaceService._read_bounded_line(handle, max_line_length) is not None
        except UnicodeDecodeError as exc:
            raise RootFileError("file.invalidEncoding", "Datei ist nicht als UTF-8 lesbar") from exc
        lines_read = len(lines)
        return FileReadResult(
            content="\n".join(lines),
            lines_read=lines_read,
            has_more=has_more,
            next_offset=offset + lines_read if has_more else None,
            truncated_lines=tuple(truncated_lines),
        )

    def write_file(self, root: FileRoot | str, relative_path: str, content: str) -> str:
        if not isinstance(content, str):
            raise TypeError("content muss Text sein")
        selected_root = self._selected_root(root)
        parts = self._parts(relative_path)
        self._assert_mutable(selected_root, parts)
        encoded_content = content.encode("utf-8")
        with self._session_operation_lock(selected_root):
            root_path = self._root_path(selected_root)
            parent = self._ensure_no_symlinks(root_path, parts[:-1])
            self._ensure_directory(parent, code="file.parentNotFound")
            target = parent / parts[-1]
            replaced_bytes = 0
            if target.exists() or target.is_symlink():
                metadata = self._metadata(target)
                if stat.S_ISLNK(metadata.st_mode):
                    raise RootFileError(
                        "file.symlinkForbidden", "Symlinks werden nicht verfolgt"
                    )
                if not stat.S_ISREG(metadata.st_mode):
                    raise RootFileError("file.notAFile", "Ziel ist keine regulaere Datei")
                replaced_bytes = metadata.st_size
            if selected_root is FileRoot.SESSION:
                self._assert_session_quota(
                    len(encoded_content), replaced_bytes=replaced_bytes
                )

            temporary = parent / f".{parts[-1]}.{uuid4().hex}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded_content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if selected_root is FileRoot.SESSION:
                    # The second check includes the temporary file and detects external growth.
                    current_replaced_bytes = 0
                    if target.exists() or target.is_symlink():
                        current_metadata = self._metadata(target)
                        if stat.S_ISLNK(current_metadata.st_mode):
                            raise RootFileError(
                                "file.symlinkForbidden", "Symlinks werden nicht verfolgt"
                            )
                        if not stat.S_ISREG(current_metadata.st_mode):
                            raise RootFileError(
                                "file.notAFile", "Ziel ist keine regulaere Datei"
                            )
                        current_replaced_bytes = current_metadata.st_size
                    self._assert_session_quota(
                        0, replaced_bytes=current_replaced_bytes
                    )
                os.replace(temporary, target)
                target.chmod(0o600)
                self._fsync_directory(parent)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
        return self._relative_path(parts)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def create_directory(self, root: FileRoot | str, relative_path: str) -> str:
        selected_root = self._selected_root(root)
        parts = self._parts(relative_path)
        with self._session_operation_lock(selected_root):
            target = self._resolve_destination(selected_root, relative_path)
            try:
                target.mkdir(mode=0o700)
            except FileExistsError as exc:
                raise RootFileError(
                    "file.pathAlreadyExists", "Zielpfad existiert bereits"
                ) from exc
            self._fsync_directory(target.parent)
        return self._relative_path(parts)

    def delete_path(
        self,
        root: FileRoot | str,
        relative_path: str,
        *,
        recursive: bool,
    ) -> None:
        selected_root = self._selected_root(root)
        parts = self._parts(relative_path)
        self._assert_mutable(selected_root, parts)
        with self._session_operation_lock(selected_root):
            target = self._ensure_no_symlinks(
                self._root_path(selected_root), parts, allow_final_symlink=True
            )
            metadata = self._metadata(target)
            if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
                target.unlink()
            elif stat.S_ISDIR(metadata.st_mode):
                if recursive:
                    shutil.rmtree(target)
                else:
                    try:
                        target.rmdir()
                    except OSError as exc:
                        raise RootFileError(
                            "file.directoryNotEmpty",
                            "Ordner ist nicht leer; recursive ist erforderlich",
                        ) from exc
            else:
                raise RootFileError(
                    "file.unsupportedType", "Nicht unterstuetzter Dateityp"
                )
            self._fsync_directory(target.parent)

    @staticmethod
    def _copy_regular_file(source: Path, destination: Path) -> None:
        source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        destination_flags = (
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        )
        source_descriptor = os.open(source, source_flags)
        try:
            destination_descriptor = os.open(destination, destination_flags, 0o600)
            try:
                with os.fdopen(source_descriptor, "rb", closefd=False) as source_handle:
                    with os.fdopen(destination_descriptor, "wb") as destination_handle:
                        shutil.copyfileobj(source_handle, destination_handle)
                        destination_handle.flush()
                        os.fsync(destination_handle.fileno())
            except BaseException:
                destination.unlink(missing_ok=True)
                raise
        finally:
            os.close(source_descriptor)

    @classmethod
    def _copy_tree(cls, source: Path, destination: Path) -> None:
        metadata = cls._metadata(source)
        if stat.S_ISLNK(metadata.st_mode):
            raise RootFileError("file.symlinkForbidden", "Symlinks werden nicht verfolgt")
        if stat.S_ISREG(metadata.st_mode):
            cls._copy_regular_file(source, destination)
            return
        if not stat.S_ISDIR(metadata.st_mode):
            raise RootFileError("file.unsupportedType", "Nicht unterstuetzter Dateityp")
        destination.mkdir(mode=0o700)
        try:
            for item in source.iterdir():
                cls._copy_tree(item, destination / item.name)
        except BaseException:
            shutil.rmtree(destination)
            raise

    def _copy_to_destination(
        self,
        source: Path,
        destination: Path,
        destination_root: FileRoot,
        *,
        released_session_bytes: int = 0,
    ) -> None:
        temporary = destination.parent / f".{destination.name}.{uuid4().hex}.tmp"
        try:
            self._copy_tree(source, temporary)
            self._operation_usage(temporary)
            if destination_root is FileRoot.SESSION:
                # The temporary copy is already part of session usage at this point.
                self._assert_session_quota(0, replaced_bytes=released_session_bytes)
            if destination.exists() or destination.is_symlink():
                raise RootFileError("file.pathAlreadyExists", "Zielpfad existiert bereits")
            temporary.rename(destination)
            self._fsync_directory(destination.parent)
        finally:
            if temporary.exists() or temporary.is_symlink():
                if temporary.is_dir() and not temporary.is_symlink():
                    shutil.rmtree(temporary)
                else:
                    temporary.unlink(missing_ok=True)

    def copy_path(
        self,
        source_root: FileRoot | str,
        source_path: str,
        destination_root: FileRoot | str,
        destination_path: str,
    ) -> str:
        selected_source_root = self._selected_root(source_root)
        selected_destination_root = self._selected_root(destination_root)
        source_parts = self._parts(source_path)
        destination_parts = self._parts(destination_path)
        with self._session_operation_lock(selected_source_root, selected_destination_root):
            source = self._ensure_no_symlinks(
                self._root_path(selected_source_root), source_parts
            )
            source_usage = self._operation_usage(source)
            destination = self._resolve_destination(
                selected_destination_root, destination_path
            )
            try:
                destination.relative_to(source)
            except ValueError:
                pass
            else:
                raise RootFileError(
                    "file.invalidDestination", "Ziel liegt innerhalb der Quelle"
                )
            if selected_destination_root is FileRoot.SESSION:
                self._assert_session_quota(source_usage.bytes)
            self._copy_to_destination(source, destination, selected_destination_root)
        return self._relative_path(destination_parts)

    def move_path(
        self,
        source_root: FileRoot | str,
        source_path: str,
        destination_root: FileRoot | str,
        destination_path: str,
    ) -> str:
        selected_source_root = self._selected_root(source_root)
        selected_destination_root = self._selected_root(destination_root)
        source_parts = self._parts(source_path)
        self._assert_mutable(selected_source_root, source_parts)
        destination_parts = self._parts(destination_path)
        with self._session_operation_lock(selected_source_root, selected_destination_root):
            source = self._ensure_no_symlinks(
                self._root_path(selected_source_root), source_parts
            )
            source_usage = self._operation_usage(source)
            destination = self._resolve_destination(
                selected_destination_root, destination_path
            )
            try:
                destination.relative_to(source)
            except ValueError:
                pass
            else:
                raise RootFileError(
                    "file.invalidDestination", "Ziel liegt innerhalb der Quelle"
                )

            if selected_destination_root is FileRoot.SESSION:
                additional_bytes = (
                    0
                    if selected_source_root is FileRoot.SESSION
                    else source_usage.bytes
                )
                self._assert_session_quota(additional_bytes)
            current_usage = self._operation_usage(source)
            if selected_destination_root is FileRoot.SESSION:
                additional_bytes = (
                    0
                    if selected_source_root is FileRoot.SESSION
                    else current_usage.bytes
                )
                self._assert_session_quota(additional_bytes)
            try:
                source.rename(destination)
            except OSError as exc:
                if exc.errno != getattr(os, "EXDEV", 18):
                    raise
                released_session_bytes = (
                    current_usage.bytes
                    if selected_source_root is FileRoot.SESSION
                    and selected_destination_root is FileRoot.SESSION
                    else 0
                )
                self._copy_to_destination(
                    source,
                    destination,
                    selected_destination_root,
                    released_session_bytes=released_session_bytes,
                )
                if source.is_dir():
                    shutil.rmtree(source)
                else:
                    source.unlink()
            self._fsync_directory(source.parent)
            if source.parent != destination.parent:
                self._fsync_directory(destination.parent)
        return self._relative_path(destination_parts)
