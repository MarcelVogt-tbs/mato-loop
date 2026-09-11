from __future__ import annotations

import asyncio
import hashlib
import posixpath
import zipfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from tempfile import SpooledTemporaryFile
from typing import BinaryIO, Literal

MAX_EXPORT_BYTES = 1024 * 1024 * 1024
MAX_EXPORT_ENTRIES = 10_000
EXPORT_CHUNK_BYTES = 1024 * 1024


class FileArchiveError(ValueError):
    def __init__(self, code: str, *, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


@dataclass(frozen=True, slots=True)
class ArchiveEntry:
    path: str
    name: str
    kind: Literal["file", "directory", "symlink", "other"]
    size: int | None


@dataclass(frozen=True, slots=True)
class ArchivePage:
    entries: tuple[ArchiveEntry, ...]
    has_more: bool
    next_offset: int | None = None


@dataclass(slots=True)
class FileArchive:
    content: BinaryIO
    filename: str
    size: int
    sha256: str


async def _thread_call(function: Callable, *args):
    pending = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except BaseException:
                continue
        try:
            pending.result()
        except BaseException:
            pass
        raise


async def _open_file(
    open_file: Callable[[str, int], Awaitable[BinaryIO]], path: str, limit: int
) -> BinaryIO:
    pending = asyncio.create_task(open_file(path, limit))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except BaseException:
                continue
        try:
            handle = pending.result()
        except BaseException:
            pass
        else:
            try:
                handle.close()
            except BaseException:
                pass
        raise


async def create_zip_archive(
    paths: list[str],
    *,
    stat_path: Callable[[str], Awaitable[ArchiveEntry]],
    list_directory: Callable[[str, int], Awaitable[ArchivePage]],
    open_file: Callable[[str, int], Awaitable[BinaryIO]],
    authorize: Callable[[str], Awaitable[bool]] | None = None,
    file_list: bool = False,
    max_source_bytes: int = MAX_EXPORT_BYTES,
    max_output_bytes: int = MAX_EXPORT_BYTES,
    max_entries: int = MAX_EXPORT_ENTRIES,
    root_contents: bool = False,
) -> FileArchive:
    paths = list(dict.fromkeys(paths))
    if not paths:
        raise FileArchiveError("tool.invalidArguments", field="paths")
    if len(paths) > max_entries:
        raise FileArchiveError("file.operationTooLarge")
    if authorize is not None:
        for path in paths:
            if not await authorize(path):
                raise FileArchiveError("permission.denied")

    pending = paths.copy()
    entries: dict[str, ArchiveEntry | None] = dict.fromkeys(paths)
    source_bytes = 0
    for path in pending:
        entry = entries[path]
        if entry is None:
            entry = await stat_path(path)
            if entry.path != path:
                raise FileArchiveError("file.changed")
            entries[path] = entry
        if entry.kind not in {"file", "directory"}:
            raise FileArchiveError("file.unsupportedType")
        if entry.kind == "file":
            if entry.size is None:
                raise FileArchiveError("file.changed")
            source_bytes += entry.size
            if source_bytes > max_source_bytes:
                raise FileArchiveError("file.operationTooLarge")
            continue
        if file_list:
            raise FileArchiveError("file.notAFile", field="paths")
        offset = 0
        while True:
            page = await list_directory(path, offset)
            for child in page.entries:
                expected = posixpath.normpath(posixpath.join(path, child.name))
                if (
                    not child.name
                    or child.name in {".", ".."}
                    or "/" in child.name
                    or "\\" in child.name
                    or child.path != expected
                    or child.path in entries
                ):
                    raise FileArchiveError("file.changed")
                entries[child.path] = child
                if len(entries) > max_entries:
                    raise FileArchiveError("file.operationTooLarge")
                pending.append(child.path)
            if not page.has_more:
                break
            if not page.entries or page.next_offset != offset + len(page.entries):
                raise FileArchiveError("file.changed")
            offset = page.next_offset

    selected = paths[0]
    root_contents = not file_list and (root_contents or selected in {".", "/"})
    base = selected if root_contents else posixpath.commonpath(
        [posixpath.dirname(path) for path in paths]
    )
    filename = (
        f"{posixpath.basename(selected)}.zip"
        if not file_list and not root_contents
        else "download.zip"
    )
    output = SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    archive: zipfile.ZipFile | None = None
    read_bytes = 0
    try:
        archive = zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED)
        for source, entry in entries.items():
            assert entry is not None
            member = posixpath.relpath(source, base or ".")
            if member == ".":
                continue
            if entry.kind == "directory":
                await _thread_call(archive.writestr, member + "/", b"")
            else:
                source_handle = await _open_file(
                    open_file, source, max_source_bytes - read_bytes
                )
                try:
                    target = archive.open(member, "w")
                    try:
                        while chunk := await _thread_call(
                            source_handle.read, EXPORT_CHUNK_BYTES
                        ):
                            read_bytes += len(chunk)
                            if read_bytes > max_source_bytes:
                                raise FileArchiveError("file.operationTooLarge")
                            await _thread_call(target.write, chunk)
                            if output.tell() > max_output_bytes:
                                raise FileArchiveError("file.operationTooLarge")
                    except BaseException:
                        try:
                            target.close()
                        except BaseException:
                            pass
                        raise
                    else:
                        target.close()
                except BaseException:
                    try:
                        source_handle.close()
                    except BaseException:
                        pass
                    raise
                else:
                    source_handle.close()
            if output.tell() > max_output_bytes:
                raise FileArchiveError("file.operationTooLarge")
        await _thread_call(archive.close)
        archive = None
        size = output.tell()
        if size > max_output_bytes:
            raise FileArchiveError("file.operationTooLarge")
        output.seek(0)
        digest = await _thread_call(hashlib.file_digest, output, "sha256")
        output.seek(0)
        return FileArchive(output, filename, size, digest.hexdigest())
    except BaseException:
        try:
            if archive is not None:
                archive.close()
        except BaseException:
            pass
        try:
            output.close()
        except BaseException:
            pass
        raise
