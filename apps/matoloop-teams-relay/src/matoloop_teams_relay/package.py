from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

from matoloop_teams_protocol.app_package import build_app_package


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("Use an absolute HTTPS URL without credentials.")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a personal-scope MatoLoop Teams app package."
    )
    parser.add_argument("--app-id", type=UUID, required=True)
    parser.add_argument("--website", type=https_url, required=True)
    parser.add_argument("--privacy-url", type=https_url, required=True)
    parser.add_argument("--terms-url", type=https_url, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    content = build_app_package(
        args.app_id, website=args.website, privacy_url=args.privacy_url, terms_url=args.terms_url,
    )
    with args.output.open("xb") as output:
        output.write(content)
    print(f"Teams app package written to {args.output}")


if __name__ == "__main__":
    main()
