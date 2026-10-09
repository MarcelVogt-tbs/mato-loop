#!/usr/bin/env python3
"""Check whether a develop build still matches the current branch head."""

import json
import os
import sys
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def main() -> None:
    server = os.environ["CI_API_URL"].rstrip("/")
    if urllib.parse.urlsplit(server).scheme != "https":
        raise SystemExit("CI_API_URL must use HTTPS")
    repository = "/".join(
        urllib.parse.quote(part, safe="") for part in os.environ["CI_REPOSITORY"].split("/")
    )
    request = urllib.request.Request(
        f"{server}/repos/{repository}/branches/develop",
        headers={"Authorization": f"token {os.environ['CI_TOKEN']}"},
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
        commit = json.load(response)["commit"]
        remote_sha = commit.get("sha") or commit["id"]
    current = remote_sha == os.environ["GITHUB_SHA"]
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        print(f"current={str(current).lower()}", file=output)
    if not current:
        print(f"Skipping stale develop revision {os.environ['GITHUB_SHA']}; current commit is {remote_sha}.")


def self_test() -> None:
    from io import StringIO
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from unittest import TestCase
    from unittest.mock import patch
    from urllib.error import URLError

    with TemporaryDirectory() as directory:
        output = Path(directory) / "output"
        with patch.dict(os.environ, {
            "CI_API_URL": "https://forge.example.test/api",
            "CI_REPOSITORY": "owner/repo",
            "CI_TOKEN": "synthetic",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_OUTPUT": str(output),
        }):
            for key in ("id", "sha"):
                for revision in ("a" * 40, "b" * 40):
                    with patch("urllib.request.OpenerDirector.open", return_value=StringIO(
                        json.dumps({"commit": {key: revision}}),
                    )):
                        main()
            with patch("urllib.request.OpenerDirector.open", side_effect=URLError("unavailable")):
                with TestCase().assertRaises(URLError):
                    main()
        assert output.read_text(encoding="utf-8") == "current=true\ncurrent=false\n" * 2
    print("Develop revision check self-test passed.")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        self_test()
    else:
        main()
