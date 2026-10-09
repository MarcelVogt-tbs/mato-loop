#!/usr/bin/env python3
"""Check the source version and, optionally, its release tag."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "libs/python/version/src"))
from matoloop_version import source_version  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tag", nargs="?")
    args = parser.parse_args()
    version = source_version(ROOT)
    if args.tag and (args.tag != version or version.endswith("-dirty")):
        parser.error(f"Release tag must match the clean source version {version}")
    print(version)


if __name__ == "__main__":
    main()
