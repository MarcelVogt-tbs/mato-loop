from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from mato_loop_backend.config import get_runtime_config


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
