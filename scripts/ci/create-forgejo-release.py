#!/usr/bin/env python3
"""Create an idempotent Forgejo release from local Git history."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

SEMVER_TAG = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ("git", *args), check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    return result.stdout.strip()


def previous_tag(tag: str) -> str | None:
    return git("describe", "--tags", "--match", "v*", "--abbrev=0", f"{tag}^", check=False) or None


def release_notes(tag: str) -> str:
    previous = previous_tag(tag)
    revision_range = f"{previous}..{tag}" if previous else tag
    commits = git("log", "--format=- %s (%h)", revision_range)
    heading = f"Changes since {previous}:" if previous else "Changes:"
    return f"{heading}\n\n{commits or 'No changes.'}"


def release_payload(tag: str, commit: str, body: str) -> dict[str, object]:
    return {
        "tag_name": tag,
        "target_commitish": commit,
        "name": tag,
        "body": body,
        "draft": False,
        "prerelease": False,
    }


def request(
    url: str, token: str, method: str = "GET", payload: dict[str, object] | None = None
) -> dict[str, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json", "Authorization": f"token {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    api_request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with OPENER.open(api_request, timeout=30) as response:
        body = json.loads(response.read())
    if not isinstance(body, dict):
        raise ValueError("Forgejo returned an invalid release response")
    return body


def existing_release(url: str, token: str) -> dict[str, object] | None:
    try:
        return request(url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return None
        raise


def validate_existing(existing: dict[str, object], expected: dict[str, object]) -> None:
    keys = ("tag_name", "name", "body", "draft", "prerelease")
    if any(existing.get(key) != expected[key] for key in keys):
        raise ValueError("Existing Forgejo release does not match the requested stable release")
    target = existing.get("target_commitish")
    if target is not None and target != expected["target_commitish"]:
        raise ValueError("Existing Forgejo release points to a different commit")


def self_test() -> None:
    payload = release_payload("v1.2.3", "a" * 40, "Changes:\n\n- Test (abc1234)")
    assert payload["tag_name"] == "v1.2.3"
    assert payload["target_commitish"] == "a" * 40
    assert payload["draft"] is False and payload["prerelease"] is False
    assert SEMVER_TAG.fullmatch("v1.2.3") and not SEMVER_TAG.fullmatch("v1.2")
    validate_existing(payload, payload)
    print("Forgejo release payload self-test passed.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tag", nargs="?")
    parser.add_argument("commit", nargs="?")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.tag or not args.commit:
        parser.error("tag and commit are required")
    if not SEMVER_TAG.fullmatch(args.tag):
        parser.error("tag must be stable SemVer vX.Y.Z")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", args.commit):
        parser.error("commit must be a full Git object ID")

    payload = release_payload(args.tag, args.commit, release_notes(args.tag))
    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    server = os.environ.get("FORGEJO_SERVER_URL", "").rstrip("/")
    repository = os.environ.get("FORGEJO_REPOSITORY", "")
    token = os.environ.get("FORGEJO_TOKEN", "")
    parsed_server = urllib.parse.urlsplit(server)
    if parsed_server.scheme != "https" or not parsed_server.netloc:
        raise ValueError("FORGEJO_SERVER_URL must be an HTTPS URL")
    if len(repository.split("/")) != 2:
        raise ValueError("FORGEJO_REPOSITORY must be owner/name")
    if not token:
        raise ValueError("FORGEJO_TOKEN is required")

    owner, name = (urllib.parse.quote(part, safe="") for part in repository.split("/"))
    tag_path = urllib.parse.quote(args.tag, safe="")
    releases_url = f"{server}/api/v1/repos/{owner}/{name}/releases"
    release_url = f"{releases_url}/tags/{tag_path}"
    existing = existing_release(release_url, token)
    if existing is not None:
        validate_existing(existing, payload)
        print(f"Forgejo release {args.tag} already exists.")
        return 0

    try:
        request(releases_url, token, "POST", payload)
    except urllib.error.HTTPError as error:
        if error.code not in {409, 422}:
            raise
        existing = existing_release(release_url, token)
        if existing is None:
            raise
        validate_existing(existing, payload)
    print(f"Created Forgejo release {args.tag}.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.SubprocessError, urllib.error.URLError, ValueError) as error:
        print(f"Release creation failed: {error}", file=sys.stderr)
        sys.exit(1)
