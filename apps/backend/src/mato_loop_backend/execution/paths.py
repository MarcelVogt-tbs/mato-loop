"""Canonical absolute paths exchanged with owner-bound computers."""

import re
from pathlib import PurePosixPath
from uuid import UUID

_DRIVE = re.compile(r"^[A-Z]:/(?:[^/]+(?:/[^/]+)*)?$")
_INVALID_WINDOWS = set('<>:"|?*')
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)


def session_temporary_directory(base: str, session_id: UUID) -> str:
    return absolute_path(f"{base.rstrip('/')}/{session_id}")


def absolute_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or any(
        ord(character) < 32 or 127 <= ord(character) <= 159 for character in value
    ):
        raise ValueError("invalid absolute computer path")
    if value.startswith("/"):
        if PurePosixPath(value).as_posix() != value or (
            value != "/" and any(part in {"", ".", ".."} for part in value[1:].split("/"))
        ):
            raise ValueError("invalid absolute computer path")
    elif not _DRIVE.fullmatch(value) or any(
        part in {"", ".", ".."}
        or part.endswith((" ", "."))
        or _RESERVED.fullmatch(part)
        or any(character in _INVALID_WINDOWS for character in part)
        for part in value[3:].split("/") if part
    ):
        raise ValueError("invalid absolute computer path")
    return value


def windows_path(value: str) -> bool:
    return not value.startswith("/")


def is_absolute_path(value: str) -> bool:
    try:
        absolute_path(value)
        return True
    except ValueError:
        return False


def parts(value: str) -> tuple[str, ...]:
    absolute_path(value)
    return (value[:3], *(part for part in value[3:].split("/") if part)) if windows_path(value) else PurePosixPath(value).parts


def within(path: str, parent: str) -> bool:
    left, right = parts(path), parts(parent)
    if windows_path(path) != windows_path(parent):
        return False
    if windows_path(path):
        left, right = tuple(p.casefold() for p in left), tuple(p.casefold() for p in right)
    return left[: len(right)] == right


def parent(path: str) -> str:
    absolute_path(path)
    if windows_path(path):
        return path.rsplit("/", 1)[0] if path.count("/") > 1 else path[:3]
    return str(PurePosixPath(path).parent)
