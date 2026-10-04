"""Shared text operations; filesystem adapters own all file access and mutations."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from .errors import AgentError


def decode_text(data: bytes | bytearray) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentError("file.invalidEncoding", "File is not UTF-8 text") from exc


def read_result(
    data: bytes | bytearray, offset: int, limit: int, max_line_length: int, max_response_bytes: int
) -> dict:
    lines = decode_text(data).splitlines()
    selected, truncated = [], []
    used = 0
    for number, line in enumerate(lines[offset - 1 : offset - 1 + limit], offset):
        rendered = f"{number}: {line[:max_line_length]}"
        size = len(json.dumps(rendered).encode()) + (1 if selected else 0)
        if used + size > max_response_bytes:
            if not selected:
                raise AgentError("operation.tooLarge", "A line exceeds the response limit")
            break
        selected.append(rendered)
        used += size
        if len(line) > max_line_length:
            truncated.append(number)
    next_offset = offset + len(selected)
    more = next_offset - 1 < len(lines)
    return {
        "content": "\n".join(selected),
        "linesRead": len(selected),
        "hasMore": more,
        "nextOffset": next_offset if more else None,
        "truncatedLines": truncated,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def edited_text(
    data: bytes, old_text: str, new_text: str, replace_all: bool, expected_sha256: str | None
) -> tuple[str, str]:
    if not old_text or old_text == new_text:
        raise AgentError("request.invalid", "Edit text must be non-empty and different")
    content = decode_text(data)
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise AgentError("file.changed", "File changed since it was read")
    ending = "\r\n" if "\r\n" in content else "\n"

    def adapt(value: str) -> str:
        value = value.replace("\r\n", "\n")
        return value if ending == "\n" else value.replace("\n", "\r\n")

    old, replacement = adapt(old_text), adapt(new_text)
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
    return updated, digest


@dataclass(frozen=True)
class PatchOperation:
    operation: str
    path: str
    destination: str | None = None
    content: str | None = None
    hunks: tuple[tuple[str | None, tuple[str, ...], tuple[str, ...]], ...] = ()


def parse_patch(
    patch: str, *, max_bytes: int, max_entries: int, validate_path: Callable[[str], object]
) -> list[PatchOperation]:
    if "\r" in patch or "\0" in patch:
        raise AgentError("request.invalid", "Patch must use LF text")
    if len(patch.encode("utf-8")) > min(max_bytes, 512 * 1024):
        raise AgentError("operation.tooLarge", "Patch exceeds the operation limit")
    lines = patch.split("\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise AgentError("request.invalid", "Patch markers are invalid")
    operations = []
    index = 1
    while index < len(lines) - 1:
        header = lines[index]
        index += 1
        if header.startswith("*** Add File: "):
            operation, path = "add", header.removeprefix("*** Add File: ")
        elif header.startswith("*** Update File: "):
            operation, path = "update", header.removeprefix("*** Update File: ")
        elif header.startswith("*** Delete File: "):
            operation, path = "delete", header.removeprefix("*** Delete File: ")
        else:
            raise AgentError("request.invalid", "Patch operation marker is invalid")
        if len(path) > 4096:
            raise AgentError("path.invalid", "Patch path is too long")
        validate_path(path)
        destination = None
        if (
            operation == "update"
            and index < len(lines) - 1
            and lines[index].startswith("*** Move to: ")
        ):
            destination = lines[index].removeprefix("*** Move to: ")
            index += 1
            if len(destination) > 4096:
                raise AgentError("path.invalid", "Patch destination is too long")
            validate_path(destination)
        start = index
        while index < len(lines) - 1 and not lines[index].startswith("*** "):
            index += 1
        body = lines[start:index]
        if operation == "add":
            if any(not line.startswith("+") for line in body):
                raise AgentError("request.invalid", "Added file lines must start with +")
            content = "\n".join(line[1:] for line in body) + ("\n" if body else "")
            operations.append(PatchOperation(operation, path, content=content))
        elif operation == "delete":
            if body:
                raise AgentError("request.invalid", "Delete operation cannot have a body")
            operations.append(PatchOperation(operation, path))
        else:
            hunks = []
            body_index = 0
            while body_index < len(body):
                marker = body[body_index]
                if marker != "@@" and not marker.startswith("@@ "):
                    raise AgentError("request.invalid", "Update hunk marker is invalid")
                locator = marker[3:] if marker.startswith("@@ ") else None
                body_index += 1
                old, new = [], []
                changed = False
                while body_index < len(body) and not body[body_index].startswith("@@"):
                    line = body[body_index]
                    body_index += 1
                    if not line or line[0] not in " +-":
                        raise AgentError("request.invalid", "Update hunk line is invalid")
                    if line[0] in " -":
                        old.append(line[1:])
                    if line[0] in " +":
                        new.append(line[1:])
                    changed |= line[0] in "+-"
                if not old or not changed or old == new:
                    raise AgentError("request.invalid", "Update hunk has no exact change")
                hunks.append((locator, tuple(old), tuple(new)))
            if not hunks and destination is None:
                raise AgentError("request.invalid", "Update requires a hunk or move")
            if hunks and destination is not None:
                raise AgentError("request.invalid", "Move cannot include update hunks")
            operations.append(
                PatchOperation(
                    "move" if destination else operation,
                    path,
                    destination=destination,
                    hunks=tuple(hunks),
                )
            )
        if len(operations) > max_entries:
            raise AgentError("operation.tooLarge", "Patch has too many operations")
    if not operations:
        raise AgentError("request.invalid", "Patch has no operations")
    return operations


def apply_hunks(
    content: str, hunks: tuple[tuple[str | None, tuple[str, ...], tuple[str, ...]], ...]
) -> str:
    crlf = "\r\n" in content
    lines = content.replace("\r\n", "\n").split("\n")
    cursor = 0
    for locator, old, new in hunks:
        if locator is not None:
            locations = [i for i in range(cursor, len(lines)) if lines[i] == locator]
            if len(locations) != 1:
                raise AgentError("file.patchContextMismatch", "Patch locator is not unique")
            cursor = locations[0] + 1
        matches = [
            i
            for i in range(cursor, len(lines) - len(old) + 1)
            if tuple(lines[i : i + len(old)]) == old
        ]
        if len(matches) != 1:
            raise AgentError("file.patchContextMismatch", "Patch context is not unique")
        position = matches[0]
        lines[position : position + len(old)] = new
        cursor = position + len(new)
    updated = "\n".join(lines)
    return updated.replace("\n", "\r\n") if crlf else updated
