#!/usr/bin/env python3
"""Reject Compose resources belonging to another checkout."""

import json
import subprocess
import sys
from pathlib import Path


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def inspect_instance(config, root):
    ids = run(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={config['name']}"]
    ).split()
    for container in json.loads(run(["docker", "inspect", *ids])) if ids else []:
        directory = container["Config"]["Labels"].get("com.docker.compose.project.working_dir")
        if not directory or Path(directory).resolve() != root:
            raise SystemExit(
                f"Stack {config['name']} belongs to another checkout. No changes made."
            )
    referenced = {volume["name"] for volume in config.get("volumes", {}).values()}
    inventory = run(
        [
            "docker",
            "volume",
            "ls",
            "--format",
            '{{.Name}}\t{{.Label "com.docker.compose.project"}}',
        ]
    )
    volumes = []
    for row in inventory.splitlines():
        name, _, project = row.partition("\t")
        if name in referenced or project == config["name"]:
            volumes.append(name)
    for volume in json.loads(run(["docker", "volume", "inspect", *volumes])) if volumes else []:
        directory = (volume.get("Labels") or {}).get("matoloop.dev.worktree")
        if not directory or Path(directory).resolve() != root:
            raise SystemExit(
                f"Stack {config['name']} has volumes owned by another or unknown checkout. No changes made."
            )


if __name__ == "__main__":
    try:
        inspect_instance(json.load(sys.stdin), Path(sys.argv[1]).resolve())
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError):
        raise SystemExit(
            "Cannot verify development instance ownership. Check Docker and configuration."
        ) from None
