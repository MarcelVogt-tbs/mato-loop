#!/usr/bin/env python3
"""Check that all MatoLoop version declarations match VERSION."""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEMVER = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"


def fail(message: str) -> None:
    raise ValueError(message)


def python_version(path: Path) -> str:
    matches = re.findall(rf'^__version__ = "({SEMVER})"$', path.read_text(), re.MULTILINE)
    if len(matches) != 1:
        fail(f"{path.relative_to(ROOT)} must contain one strict __version__ declaration")
    return matches[0]


def locked_version(path: Path, package_name: str) -> str:
    packages = [
        package
        for package in tomllib.loads(path.read_text()).get("package", [])
        if package.get("name") == package_name
    ]
    if len(packages) != 1 or packages[0].get("source") != {"editable": "."}:
        fail(f"{path.relative_to(ROOT)} must contain one editable {package_name} package")
    version = packages[0].get("version")
    if not isinstance(version, str):
        fail(f"{path.relative_to(ROOT)} has no version for {package_name}")
    return version


def yarn_workspace_metadata(path: Path) -> None:
    matches = re.findall(
        r'^"mato-loop-frontend@workspace:\.":\n(?P<body>(?:  .*\n)+)',
        path.read_text(),
        re.MULTILINE,
    )
    if len(matches) != 1:
        fail(f"{path.relative_to(ROOT)} must contain one mato-loop-frontend workspace entry")
    body = matches[0]
    if not re.search(r"^  version: 0\.0\.0-use\.local$", body, re.MULTILINE):
        fail(f"{path.relative_to(ROOT)} has invalid generated workspace version metadata")
    if not re.search(
        r'^  resolution: "mato-loop-frontend@workspace:\."$', body, re.MULTILINE
    ):
        fail(f"{path.relative_to(ROOT)} has invalid frontend workspace resolution metadata")


def check(expected_tag: str | None = None) -> str:
    errors: list[str] = []
    version_path = ROOT / "VERSION"

    try:
        raw_version = version_path.read_text()
        if not re.fullmatch(rf"{SEMVER}\n", raw_version):
            fail("VERSION must contain exactly one stable SemVer X.Y.Z and a newline")
        version = raw_version.rstrip("\n")
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(str(error)) from None

    if expected_tag is not None and expected_tag != f"v{version}":
        errors.append(f"requested tag {expected_tag!r} does not equal v{version}")

    readers = {
        "apps/backend/pyproject.toml": lambda path: tomllib.loads(path.read_text())["project"][
            "version"
        ],
        "apps/backend/src/mato_loop_backend/__init__.py": python_version,
        "apps/backend/uv.lock": lambda path: locked_version(path, "mato-loop-backend"),
        "apps/frontend/package.json": lambda path: json.loads(path.read_text())["version"],
        "apps/matoloop-agent/pyproject.toml": lambda path: tomllib.loads(path.read_text())[
            "project"
        ]["version"],
        "apps/matoloop-agent/src/matoloop_agent/__init__.py": python_version,
        "apps/matoloop-agent/uv.lock": lambda path: locked_version(
            path, "matoloop-agent"
        ),
    }
    for relative_path, reader in readers.items():
        try:
            declared = reader(ROOT / relative_path)
            if declared != version:
                errors.append(f"{relative_path}: expected {version}, found {declared!r}")
        except (
            OSError,
            UnicodeError,
            KeyError,
            TypeError,
            tomllib.TOMLDecodeError,
            ValueError,
        ) as error:
            errors.append(f"{relative_path}: {error}")

    try:
        yarn_workspace_metadata(ROOT / "apps/frontend/yarn.lock")
    except (OSError, UnicodeError, ValueError) as error:
        errors.append(str(error))

    if errors:
        raise ValueError("\n".join(f"  - {error}" for error in errors))
    return version


def self_test() -> None:
    valid = ("0.0.0", "0.1.0", "10.20.30")
    invalid = (
        "v1.2.3",
        "1.2",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3-rc.1",
        "1.2.3+1",
    )
    assert all(re.fullmatch(SEMVER, value) for value in valid)
    assert not any(re.fullmatch(SEMVER, value) for value in invalid)
    print("Version checker self-test passed.")


def main() -> int:
    if sys.argv[1:] == ["--self-test"]:
        self_test()
        return 0
    if len(sys.argv) > 2 or (
        len(sys.argv) == 2 and not re.fullmatch(rf"v{SEMVER}", sys.argv[1])
    ):
        print(f"Usage: {Path(sys.argv[0]).name} [vX.Y.Z] | --self-test", file=sys.stderr)
        return 2
    try:
        version = check(sys.argv[1] if len(sys.argv) == 2 else None)
    except ValueError as error:
        print(f"Version check failed:\n{error}", file=sys.stderr)
        return 1
    print(f"Version {version} is consistent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
