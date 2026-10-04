"""Bounded Windows search without external command-line dependencies."""

from __future__ import annotations

import asyncio
import fnmatch
import re
import stat
from pathlib import Path

from . import windows_handles as handles
from .errors import AgentError
from .filesystem_windows import WindowsFilesystem
from .windows_paths import absolute, native


class WindowsSearch:
    def __init__(self, filesystem: WindowsFilesystem) -> None:
        self.filesystem = filesystem

    def _files(self, path: str):
        metadata = self.filesystem.stat(path)
        if metadata["kind"] == "file":
            yield Path(native(path)), path, metadata["size"]
            return
        if metadata["kind"] != "directory":
            raise AgentError("path.unsupportedType", "Search requires a file or directory")
        visited = 0

        def traverse(directory: int, wire: str):
            nonlocal visited
            for name, attributes, size, _ in handles.names(directory):
                visited += 1
                if visited > self.filesystem.max_entries:
                    raise AgentError("operation.tooLarge", "Search exceeds the entry limit")
                candidate = f"{wire.rstrip('/')}/{name}"
                try:
                    absolute(candidate)
                except ValueError:
                    continue
                if name.startswith(".") or attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    continue
                if attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    with handles.child(directory, name, directory=True) as child:
                        yield from traverse(child, candidate)
                else:
                    yield Path(native(candidate)), candidate, size

        with handles.directory(path) as root:
            yield from traverse(root, path)

    async def glob(self, path: str, pattern: str) -> dict:
        return await asyncio.to_thread(self._glob, path, pattern)

    def _glob(self, path: str, pattern: str) -> dict:
        matches = []
        for candidate, wire, _size in self._files(path):
            relative = wire[len(path.rstrip("/")) + 1 :]
            if fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(
                candidate.name, pattern
            ):
                matches.append(wire)
                if len(matches) >= 100:
                    return {"paths": sorted(matches), "truncated": True}
        return {"paths": sorted(matches), "truncated": False}

    async def grep(self, path: str, pattern: str, include: str | None) -> dict:
        return await asyncio.to_thread(self._grep, path, pattern, include)

    def _grep(self, path: str, pattern: str, include: str | None) -> dict:
        try:
            matcher = re.compile(pattern)
        except re.error as exc:
            raise AgentError("request.invalid", "Invalid grep pattern") from exc
        matches = []
        truncated = False
        for candidate, wire, size in self._files(path):
            if include and not fnmatch.fnmatchcase(candidate.name, include):
                continue
            if size > self.filesystem.max_file_bytes:
                continue
            try:
                data = self.filesystem._read_bytes(wire, self.filesystem.max_file_bytes)
                if b"\0" in data:
                    continue
                lines = data.decode("utf-8").splitlines()
            except (UnicodeError, AgentError):
                continue
            for number, line in enumerate(lines, 1):
                if matcher.search(line):
                    matches.append({"path": wire, "line": number, "text": line[:2000]})
                    if len(matches) >= 100:
                        truncated = True
                        break
            if truncated:
                break
        return {"matches": matches, "truncated": truncated}
