"""Windows filesystem operations; all user paths reject reparse-point traversal."""

from __future__ import annotations

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
from .config import AgentConfig
from .errors import AgentError
from .file_text import apply_hunks, decode_text, edited_text, parse_patch, read_result
from .windows_paths import absolute, native


class WindowsFilesystem:
    def __init__(self, config: AgentConfig) -> None:
        self.max_file_bytes = config.max_file_bytes
        self.max_transfer_bytes = config.max_transfer_bytes
        self.max_entries = config.max_operation_entries
        self.max_response_bytes = max(512, config.max_message_bytes // 2)
        self._lock = threading.RLock()

    @staticmethod
    def _checked(value: str, *, missing_leaf: bool = False) -> Path:
        # ponytail: shell operand inspection is path-based; filesystem RPCs use handles.
        path = Path(native(value))
        drive = Path(path.anchor)
        if not drive.is_dir():
            raise AgentError("path.notFound", "Drive is unavailable")
        current = drive
        for index, component in enumerate(path.parts[1:], start=1):
            current /= component
            try:
                metadata = current.lstat()
            except FileNotFoundError as exc:
                if missing_leaf and index == len(path.parts) - 1:
                    return path
                raise AgentError("path.notFound", "Path is unavailable") from exc
            if metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise AgentError("path.unsupportedType", "Reparse points are not traversable")
            if index < len(path.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise AgentError("path.notDirectory", "Parent is not a directory")
        return path

    def resolve_directory(self, path: str) -> dict:
        with handles.directory(path):
            pass
        return {"path": absolute(path)}

    def stat(self, path: str) -> dict:
        if len(path) == 3:
            self.resolve_directory(path)
            return {
                "path": path,
                "kind": "directory",
                "size": None,
                "modifiedAt": Path(native(path)).stat().st_mtime,
            }
        with handles.parent(path) as (parent, name):
            item = next(
                (
                    value
                    for value in handles.names(parent)
                    if value[0].casefold() == name.casefold()
                ),
                None,
            )
        if item is None:
            raise AgentError("path.notFound", "Path is unavailable")
        _, attributes, size, modified = item
        kind = (
            "symlink"
            if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
            else "directory"
            if attributes & stat.FILE_ATTRIBUTE_DIRECTORY
            else "file"
        )
        return {
            "path": path,
            "kind": kind,
            "size": size if kind == "file" else None,
            "modifiedAt": modified,
        }

    def list(self, path: str, offset: int, limit: int) -> dict:
        entries = []
        with handles.directory(path) as directory:
            for name, attributes, size, modified in handles.names(directory):
                if len(entries) >= self.max_entries:
                    raise AgentError("operation.tooLarge", "Directory contains too many entries")
                child_path = f"{path.rstrip('/')}/{name}"
                try:
                    absolute(child_path)
                except ValueError:
                    continue
                kind = (
                    "symlink"
                    if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
                    else "directory"
                    if attributes & stat.FILE_ATTRIBUTE_DIRECTORY
                    else "file"
                )
                entries.append(
                    {
                        "name": name,
                        "path": child_path,
                        "kind": kind,
                        "size": size if kind != "directory" else None,
                        "modifiedAt": modified,
                    }
                )
        entries.sort(
            key=lambda entry: (
                entry["kind"] != "directory",
                entry["name"].casefold(),
                entry["name"],
            )
        )
        selected = entries[offset : offset + limit]
        next_offset = offset + len(selected)
        return {
            "entries": selected,
            "hasMore": next_offset < len(entries),
            "nextOffset": next_offset if next_offset < len(entries) else None,
        }

    def _read_bytes(self, path: str, limit: int) -> bytes:
        with handles.parent(path) as (parent, name), handles.child(parent, name) as file:
            with handles.stream(file) as reader:
                data = reader.read(limit + 1)
        if len(data) > limit:
            raise AgentError("file.tooLarge", "File exceeds the limit")
        return data

    def read(self, path: str, offset: int, limit: int, max_line_length: int) -> dict:
        data = self._read_bytes(path, self.max_file_bytes)
        return read_result(data, offset, limit, max_line_length, self.max_response_bytes)

    def _publish(
        self, path: str, data: bytes, expected_sha256: str | None, *, overwrite: bool
    ) -> dict:
        if len(data) > self.max_transfer_bytes:
            raise AgentError("file.tooLarge", "File exceeds the limit")
        with self._lock, handles.parent(path, write=True) as (parent, name):
            if expected_sha256 is not None:
                with handles.child(parent, name) as existing, handles.stream(existing) as reader:
                    if hashlib.file_digest(reader, "sha256").hexdigest() != expected_sha256:
                        raise AgentError("file.changed", "File changed since it was read")
            with handles.staged_file(parent, name, replace=overwrite) as created:
                with handles.stream(created, "wb") as writer:
                    writer.write(data)
                    writer.flush()
                    os.fsync(writer.fileno())
                if expected_sha256 is not None:
                    with (
                        handles.child(parent, name) as existing,
                        handles.stream(existing) as reader,
                    ):
                        if hashlib.file_digest(reader, "sha256").hexdigest() != expected_sha256:
                            raise AgentError("file.changed", "File changed since it was read")
        return {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    def write(self, path: str, content: str, expected_sha256: str | None) -> dict:
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes or "\0" in content:
            raise AgentError("file.tooLarge", "Content exceeds the write limit")
        return self._publish(path, data, expected_sha256, overwrite=expected_sha256 is not None)

    def upsert(self, path: str, content: str) -> dict:
        data = content.encode("utf-8")
        if len(data) > self.max_file_bytes or "\0" in content:
            raise AgentError("file.tooLarge", "Content exceeds the write limit")
        return self._publish(path, data, None, overwrite=True)

    def edit(
        self,
        path: str,
        old_text: str,
        new_text: str,
        replace_all: bool,
        expected_sha256: str | None,
    ) -> dict:
        data = self._read_bytes(path, self.max_file_bytes)
        updated, digest = edited_text(data, old_text, new_text, replace_all, expected_sha256)
        return self.write(path, updated, digest)

    def patch(self, patch: str, expected_sha256: dict[str, str]) -> dict:
        prepared = parse_patch(
            patch,
            max_bytes=self.max_transfer_bytes,
            max_entries=self.max_entries,
            validate_path=absolute,
        )
        revisions = {item.path for item in prepared if item.operation != "add"}
        if revisions != expected_sha256.keys():
            raise AgentError("request.invalid", "Patch revisions do not match its files")
        paths = [p for item in prepared for p in (item.path, item.destination) if p]
        if len({p.casefold() for p in paths}) != len(paths):
            raise AgentError("request.invalid", "Patch paths must be unique")
        results = []
        with self._lock:
            for item in prepared:
                action, path, destination = item.operation, item.path, item.destination
                try:
                    if action == "add":
                        result = self.write(path, item.content or "", None)
                    else:
                        data = self._read_bytes(path, self.max_file_bytes)
                        digest = hashlib.sha256(data).hexdigest()
                        if digest != expected_sha256[path]:
                            raise AgentError("file.changed", "File changed since it was read")
                        if action == "delete":
                            self.delete(path, False)
                            result = {"path": path, "sha256": digest, "size": len(data)}
                        elif destination is not None:
                            self.rename(path, destination)
                            result = {"path": path, "sha256": digest, "size": len(data)}
                        else:
                            content = apply_hunks(decode_text(data), item.hunks)
                            result = self.write(path, content, digest)
                except Exception as exc:
                    raise AgentError(
                        "file.patchIncomplete",
                        "Patch did not complete",
                        {
                            "completedOperations": len(results),
                            "totalOperations": len(prepared),
                            "failedOperationIndex": len(results),
                            "path": path,
                        },
                    ) from exc
                results.append(
                    {
                        "operation": "move" if destination else action,
                        "path": path,
                        **({"destination": destination} if destination else {}),
                        "sha256": result["sha256"],
                        "size": result["size"],
                    }
                )
        return {"files": results}

    def mkdir(self, path: str, parents: bool) -> dict:
        if len(path) == 3:
            raise AgentError("path.rootMutation", "Drive root cannot be created")
        if parents:
            with handles.directory(path, create=True):
                pass
        else:
            with (
                handles.parent(path, write=True) as (parent, name),
                handles.child(parent, name, directory=True, create=True),
            ):
                pass
        return {"path": path}

    def copy_or_move(self, path: str, destination: str, *, move: bool) -> dict:
        if len(path) == 3 or len(destination) == 3:
            raise AgentError("path.rootMutation", "Drive root cannot be modified")
        with self._lock:
            kind = self.stat(path)["kind"]
            if move:
                with (
                    handles.parent(path) as (source_parent, source_name),
                    handles.parent(destination, write=True) as (target_parent, target_name),
                    handles.child(
                        source_parent, source_name, directory=kind == "directory", delete=True
                    ) as source,
                ):
                    handles.rename(source, target_parent, target_name)
            elif kind == "file":
                with (
                    handles.parent(path) as (source_parent, source_name),
                    handles.parent(destination, write=True) as (target_parent, target_name),
                    handles.child(source_parent, source_name) as source,
                    handles.child(
                        target_parent, target_name, create=True, write=True, delete=True
                    ) as target,
                ):
                    try:
                        with (
                            handles.stream(source) as reader,
                            handles.stream(target, "wb") as writer,
                        ):
                            if os.fstat(reader.fileno()).st_size > self.max_transfer_bytes:
                                raise AgentError(
                                    "operation.tooLarge", "Copy exceeds the transfer limit"
                                )
                            shutil.copyfileobj(reader, writer)
                    except BaseException:
                        handles.remove(target)
                        raise
            elif kind == "directory":
                if destination.casefold().startswith(path.rstrip("/").casefold() + "/"):
                    raise AgentError(
                        "path.invalidDestination", "Cannot copy a directory into itself"
                    )
                count = 0
                total_bytes = 0

                def copy_tree(source_handle: int, target_handle: int) -> None:
                    nonlocal count, total_bytes
                    for name, attributes, _, _ in handles.names(source_handle):
                        count += 1
                        if count > self.max_entries:
                            raise AgentError("operation.tooLarge", "Copy contains too many entries")
                        if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                            raise AgentError("path.unsupportedType", "Cannot copy a reparse point")
                        if attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                            with handles.child(source_handle, name, directory=True) as src:
                                with handles.child(
                                    target_handle,
                                    name,
                                    directory=True,
                                    create=True,
                                    write=True,
                                ) as dst:
                                    copy_tree(src, dst)
                        else:
                            with handles.child(source_handle, name) as src:
                                with handles.child(
                                    target_handle, name, create=True, write=True
                                ) as dst:
                                    with (
                                        handles.stream(src) as reader,
                                        handles.stream(dst, "wb") as writer,
                                    ):
                                        total_bytes += os.fstat(reader.fileno()).st_size
                                        if total_bytes > self.max_transfer_bytes:
                                            raise AgentError(
                                                "operation.tooLarge",
                                                "Copy exceeds the transfer limit",
                                            )
                                        shutil.copyfileobj(reader, writer)

                with (
                    handles.parent(path) as (source_parent, source_name),
                    handles.parent(destination, write=True) as (target_parent, target_name),
                    handles.child(source_parent, source_name, directory=True) as source,
                    handles.child(
                        target_parent, target_name, directory=True, create=True, write=True
                    ) as target,
                ):
                    copy_tree(source, target)
            else:
                raise AgentError("path.unsupportedType", "Cannot copy this path")
        return {"path": destination}

    def rename(self, path: str, destination: str) -> dict:
        return self.copy_or_move(path, destination, move=True)

    def delete(self, path: str, recursive: bool) -> dict:
        if len(path) == 3:
            raise AgentError("path.rootMutation", "Drive root cannot be removed")

        def remove_tree(directory_handle: int) -> None:
            for name, attributes, _, _ in handles.names(directory_handle):
                if attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise AgentError("path.unsupportedType", "Cannot remove a reparse point")
                if attributes & stat.FILE_ATTRIBUTE_DIRECTORY:
                    with handles.child(
                        directory_handle, name, directory=True, delete=True
                    ) as child:
                        remove_tree(child)
                        handles.remove(child)
                else:
                    with handles.child(directory_handle, name, delete=True) as child:
                        handles.remove(child)

        with self._lock, handles.parent(path) as (parent, name):
            is_directory = self.stat(path)["kind"] == "directory"
            with handles.child(parent, name, directory=is_directory, delete=True) as target:
                if is_directory and recursive:
                    remove_tree(target)
                handles.remove(target)
        return {"path": path}

    def import_file(
        self, path: str, source: Path, overwrite: bool, expected_size: int, expected_sha256: str
    ) -> dict:
        if expected_size > self.max_transfer_bytes:
            raise AgentError("file.tooLarge", "File exceeds the transfer limit")
        with self._lock, handles.parent(path, write=True) as (parent, name):
            digest, size = hashlib.sha256(), 0
            with handles.staged_file(parent, name, replace=overwrite) as created:
                with source.open("rb") as reader, handles.stream(created, "wb") as writer:
                    while chunk := reader.read(1024 * 1024):
                        size += len(chunk)
                        if size > expected_size:
                            raise AgentError(
                                "transfer.invalidContent", "Transferred file is too large"
                            )
                        digest.update(chunk)
                        writer.write(chunk)
                    writer.flush()
                    os.fsync(writer.fileno())
                if size != expected_size or digest.hexdigest() != expected_sha256:
                    raise AgentError(
                        "transfer.invalidContent", "Transferred file failed validation"
                    )
        return {"path": path, "sha256": expected_sha256, "size": size}

    @contextmanager
    def open_export(self, path: str) -> Iterator[tuple[BinaryIO, int, str]]:
        with handles.parent(path) as (parent, name), handles.child(parent, name) as file:
            with handles.stream(file) as reader:
                metadata = os.fstat(reader.fileno())
                if metadata.st_size > self.max_transfer_bytes:
                    raise AgentError("file.tooLarge", "File exceeds the transfer limit")
                digest = hashlib.file_digest(reader, "sha256").hexdigest()
                reader.seek(0)
                yield reader, metadata.st_size, digest
