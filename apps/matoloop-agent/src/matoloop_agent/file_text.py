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
        raise AgentError.invalid_arguments(
            "Use non-empty oldString and a different newString.", field="oldString"
        )
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
        raise AgentError.invalid_arguments(
            "newString must differ from oldString after newline normalization.", field="newString"
        )
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
    hunks: tuple[tuple[str | None, tuple[str, ...], tuple[str, ...], bool], ...] = ()


def parse_patch(
    patch: str, *, max_bytes: int, max_entries: int, validate_path: Callable[[str], object]
) -> list[PatchOperation]:
    if "\r" in patch or "\0" in patch:
        raise AgentError.invalid_arguments(
            "Use LF patch text without NUL characters.", field="patchText", reason="invalid_format"
        )
    if len(patch.encode("utf-8")) > min(max_bytes, 512 * 1024):
        raise AgentError("operation.tooLarge", "Patch exceeds the operation limit")
    lines = patch.split("\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise AgentError.invalid_arguments(
            "Start with *** Begin Patch and end with *** End Patch on separate lines.",
            field="patchText",
            reason="invalid_format",
        )
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
            raise AgentError.invalid_arguments(
                "Use *** Add File:, *** Update File: or *** Delete File: followed by a path.",
                field="patchText",
                reason="invalid_format",
                line=index,
            )
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
        while index < len(lines) - 1 and (
            not lines[index].startswith("*** ") or lines[index] == "*** End of File"
        ):
            index += 1
        body = lines[start:index]
        if operation == "add":
            for number, line in enumerate(body, start + 1):
                if not line.startswith("+"):
                    raise AgentError.invalid_arguments(
                        "Prefix every added file line, including blank lines, with +.",
                        field="patchText",
                        reason="invalid_format",
                        line=number,
                    )
            content = "\n".join(line[1:] for line in body) + ("\n" if body else "")
            operations.append(PatchOperation(operation, path, content=content))
        elif operation == "delete":
            if body:
                raise AgentError.invalid_arguments(
                    "Delete operations cannot have a body.",
                    field="patchText",
                    reason="invalid_format",
                    line=start + 1,
                )
            operations.append(PatchOperation(operation, path))
        else:
            hunks = []
            body_index = 0
            while body_index < len(body):
                marker = body[body_index]
                locator = None
                if marker == "@@" or marker.startswith("@@ "):
                    locator = marker[3:] if marker.startswith("@@ ") else None
                    body_index += 1
                elif hunks or not marker or marker[0] not in " +-":
                    raise AgentError.invalid_arguments(
                        "Start each update hunk with @@ or @@ followed by an exact locator line.",
                        field="patchText",
                        reason="invalid_format",
                        line=start + body_index + 1,
                    )
                old, new = [], []
                end_of_file = False
                while body_index < len(body) and not body[body_index].startswith("@@"):
                    line = body[body_index]
                    body_index += 1
                    if line == "*** End of File":
                        if body_index != len(body):
                            raise AgentError.invalid_arguments(
                                "Place *** End of File only at the end of an update.",
                                field="patchText",
                                reason="invalid_format",
                                line=start + body_index,
                            )
                        end_of_file = True
                        break
                    if not line or line[0] not in " +-":
                        raise AgentError.invalid_arguments(
                            "Prefix update lines with a space, + or -; "
                            "blank context lines need a space.",
                            field="patchText",
                            reason="invalid_format",
                            line=start + body_index,
                        )
                    if line[0] in " -":
                        old.append(line[1:])
                    if line[0] in " +":
                        new.append(line[1:])
                if not old and not new:
                    raise AgentError.invalid_arguments(
                        "Each hunk must contain context, added or removed lines.",
                        field="patchText",
                        line=start + body_index,
                    )
                hunks.append((locator, tuple(old), tuple(new), end_of_file))
            if hunks and destination is None and all(old == new for _, old, new, _ in hunks):
                raise AgentError.invalid_arguments(
                    "Include at least one actual change in an update.",
                    field="patchText",
                    line=start,
                )
            if not hunks and destination is None:
                raise AgentError.invalid_arguments(
                    "Provide an update hunk or *** Move to: destination.",
                    field="patchText",
                    line=start,
                )
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
        raise AgentError.invalid_arguments(
            "Include at least one file operation.", field="patchText", reason="missing"
        )
    return operations


def apply_hunks(
    content: str, hunks: tuple[tuple[str | None, tuple[str, ...], tuple[str, ...], bool], ...]
) -> str:
    crlf = "\r\n" in content
    normalized = content.replace("\r\n", "\n")
    trailing_newline = normalized.endswith("\n")
    lines = normalized.split("\n") if normalized else []
    if trailing_newline:
        lines.pop()
    cursor = 0
    replacements = []
    for locator, old, new, end_of_file in hunks:
        if locator is not None:
            locations = [i for i in range(cursor, len(lines)) if lines[i] == locator]
            if len(locations) != 1:
                raise AgentError("file.patchContextMismatch", "Patch locator is not unique")
            cursor = locations[0] + 1
        if not old:
            replacements.append((len(lines), 0, new))
            trailing_newline = True
            continue
        matches = [
            i
            for i in range(cursor, len(lines) - len(old) + 1)
            if tuple(lines[i : i + len(old)]) == old
            and (not end_of_file or i + len(old) == len(lines))
        ]
        if len(matches) != 1:
            raise AgentError("file.patchContextMismatch", "Patch context is not unique")
        position = matches[0]
        replacements.append((position, len(old), new))
        cursor = position + len(old)
    # Match against the original file so appended chunks do not hide later edits.
    for position, length, new in reversed(sorted(replacements, key=lambda item: item[0])):
        lines[position : position + length] = new
    updated = "\n".join(lines)
    if lines and trailing_newline:
        updated += "\n"
    return updated.replace("\n", "\r\n") if crlf else updated
