"""Branch-independent source versions, stamped before packaging."""

import argparse
import re
import subprocess
from datetime import UTC, datetime
from functools import cache
from pathlib import Path

VERSION_PATTERN = r"[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9a-f]{8}"
SOURCE_ROOT = Path(__file__).resolve().parents[5]


def source_version(source: Path) -> str:
    source = source.resolve()
    if not (source / ".git").exists():
        raise FileNotFoundError(f"No Git checkout at {source}")
    git = ["git", "-c", f"safe.directory={source}", "-C", str(source)]
    timestamp, commit = subprocess.check_output(
        [*git, "show", "-s", "--format=%ct %H", "HEAD"], text=True, stderr=subprocess.PIPE
    ).split()
    date = datetime.fromtimestamp(int(timestamp), UTC).date()
    changed = subprocess.check_output(
        [*git, "status", "--porcelain", "--untracked-files=normal"],
        text=True,
        stderr=subprocess.PIPE,
    ).strip()
    return f"{date}-{commit[:8]}" + ("-dirty" if changed else "")


@cache
def get_version() -> str:
    checkout = (
        Path(__file__).resolve()
        == SOURCE_ROOT / "libs/python/version/src/matoloop_version/__init__.py"
    )
    if checkout and (SOURCE_ROOT / ".git").exists():
        try:
            return source_version(SOURCE_ROOT)
        except (OSError, subprocess.CalledProcessError):
            return "unknown"
    stamp = Path(__file__).with_name("build-version.txt")
    if stamp.is_file():
        value = stamp.read_text(encoding="ascii").strip()
        return value if re.fullmatch(VERSION_PATTERN + r"(?:-dirty)?", value) else "unknown"
    return "unknown"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    value = source_version(args.source)
    if args.output:
        args.output.write_text(value + "\n", encoding="ascii")
    else:
        print(value)
