"""Normalized drive-letter paths used on the Windows wire protocol."""

import re
from pathlib import PurePosixPath

_DRIVE = re.compile(r"^[A-Z]:/(?:[^/]+(?:/[^/]+)*)?$")
_INVALID = set('<>:"|?*')
_RESERVED = re.compile(r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.I)


def absolute(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
    ):
        raise ValueError("invalid absolute path")
    if value.startswith("/"):
        if PurePosixPath(value).as_posix() != value or (
            value != "/" and any(p in {"", ".", ".."} for p in value[1:].split("/"))
        ):
            raise ValueError("invalid absolute path")
    elif not _DRIVE.fullmatch(value) or any(
        p in {".", ".."}
        or p.endswith((" ", "."))
        or _RESERVED.fullmatch(p)
        or any(c in _INVALID for c in p)
        for p in value[3:].split("/")
        if p
    ):
        raise ValueError("invalid absolute path")
    return value


def native(value: str) -> str:
    if value.startswith("/"):
        raise ValueError("Windows agent requires a drive-letter path")
    return absolute(value).replace("/", "\\")
