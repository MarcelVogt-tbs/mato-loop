#!/usr/bin/env python3
"""Create an idempotent Forgejo release from local Git history."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SEMVER_TAG = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
RELEASE_NOTE_CATEGORIES = ("Added", "Changed", "Fixed", "Security")
RELEASE_LIST_MARKER = "<!-- release-list -->"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


OPENER = urllib.request.build_opener(NoRedirect)


def git(*args: str, check: bool = True, strip: bool = True) -> str:
    result = subprocess.run(
        ("git", *args), check=check, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    return result.stdout.strip() if strip else result.stdout


def previous_tag(tag: str, revision: str) -> str | None:
    return (
        git(
            "describe",
            "--tags",
            "--match",
            "v[0-9]*",
            "--exclude",
            tag,
            "--abbrev=0",
            revision,
            check=False,
        )
        or None
    )


def parse_release_note(value: str, commit: str) -> tuple[str, str]:
    category, separator, text = value.partition(":")
    category = category.strip()
    text = text.strip()
    if separator != ":" or category not in RELEASE_NOTE_CATEGORIES or not text or "\n" in text:
        expected = "Added, Changed, Fixed, or Security followed by a colon and text"
        raise ValueError(f"Invalid Release-Note in {commit[:12]}; expected {expected}")
    return category, text


def collect_release_notes(revision_range: str) -> dict[str, list[str]]:
    notes = {category: [] for category in RELEASE_NOTE_CATEGORIES}
    log = git(
        "log",
        "--reverse",
        "--no-merges",
        "--format=%H%x00%(trailers:key=Release-Note,valueonly,separator=%x1f)%x1e",
        revision_range,
        strip=False,
    )
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        commit, separator, values = record.partition("\x00")
        if separator != "\x00":
            raise ValueError("Git returned malformed release-note data")
        for value in filter(None, values.split("\x1f")):
            category, text = parse_release_note(value, commit)
            notes[category].append(text)
    return notes


def render_release_notes(notes: dict[str, list[str]]) -> str:
    sections = []
    for category in RELEASE_NOTE_CATEGORIES:
        entries = notes[category]
        if entries:
            sections.append(f"## {category}\n\n" + "\n".join(f"- {entry}" for entry in entries))
    return "\n\n".join(sections) or "No notable changes."


def release_notes(tag: str, revision: str) -> str:
    previous = previous_tag(tag, revision)
    revision_range = f"{previous}..{revision}" if previous else revision
    return render_release_notes(collect_release_notes(revision_range))


def update_changelog(index_path: Path, tag: str, body: str) -> Path:
    index = index_path.read_text(encoding="utf-8")
    if index.count(RELEASE_LIST_MARKER) != 1:
        raise ValueError(f"Expected one release list marker in {index_path}")
    link = f"- [{tag}](changelog/{tag}.md)"
    if link in index:
        raise ValueError(f"Changelog already contains {tag}")

    release_path = index_path.parent / "changelog" / f"{tag}.md"
    if release_path.exists():
        raise ValueError(f"Release notes already exist: {release_path}")

    updated_index = index.replace(RELEASE_LIST_MARKER, f"{RELEASE_LIST_MARKER}\n{link}")
    release_path.parent.mkdir(exist_ok=True)
    release_path.write_text(f"# {tag}\n\n{body}\n", encoding="utf-8")
    index_path.write_text(updated_index, encoding="utf-8")
    return release_path


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
    notes = {category: [] for category in RELEASE_NOTE_CATEGORIES}
    category, text = parse_release_note("Fixed: Keep queued messages", "a" * 40)
    notes[category].append(text)
    notes["Added"].append("Add mail integration")
    body = render_release_notes(notes)
    assert body == (
        "## Added\n\n- Add mail integration\n\n## Fixed\n\n- Keep queued messages"
    )
    assert render_release_notes({category: [] for category in RELEASE_NOTE_CATEGORIES}) == (
        "No notable changes."
    )
    index = f"# Changelog\n\n## Versionen\n\n{RELEASE_LIST_MARKER}\n"
    link = "- [v1.2.3](changelog/v1.2.3.md)"
    with tempfile.TemporaryDirectory() as directory:
        index_path = Path(directory) / "changelog.md"
        index_path.write_text(index, encoding="utf-8")
        release_path = update_changelog(index_path, "v1.2.3", body)
        assert link in index_path.read_text(encoding="utf-8")
        update_changelog(index_path, "v1.2.4", body)
        assert index_path.read_text(encoding="utf-8").index("v1.2.4") < (
            index_path.read_text(encoding="utf-8").index("v1.2.3")
        )
        assert release_path.read_text(encoding="utf-8") == f"# v1.2.3\n\n{body}\n"

    payload = release_payload("v1.2.3", "a" * 40, body)
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
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--dry-run", action="store_true")
    operation.add_argument("--print-notes", action="store_true")
    operation.add_argument("--write-changelog", type=Path)
    parser.add_argument("--check-notes", metavar="REVISION")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if args.check_notes:
        if args.tag or args.commit or args.dry_run or args.print_notes or args.write_changelog:
            parser.error("--check-notes cannot be combined with other operations")
        collect_release_notes(args.check_notes)
        print("Release notes are valid.")
        return 0
    if not args.tag or not args.commit:
        parser.error("tag and commit are required")
    if not SEMVER_TAG.fullmatch(args.tag):
        parser.error("tag must be stable SemVer vX.Y.Z")
    if not re.fullmatch(r"[0-9a-fA-F]{40,64}", args.commit):
        parser.error("commit must be a full Git object ID")

    body = release_notes(args.tag, args.commit)
    if args.print_notes:
        print(body)
        return 0
    if args.write_changelog:
        release_path = update_changelog(args.write_changelog, args.tag, body)
        print(f"Created release notes in {release_path}.")
        return 0

    payload = release_payload(args.tag, args.commit, body)
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
