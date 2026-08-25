# MatoLoop Agent

Standalone Linux agent for bounded filesystem, process, transfer, Git, OpenSCAD, and session-cache operations. It connects outbound to the MatoLoop backend and has no backend package or database dependency.

## Configuration

```json
{
  "homeDirectory": "/home/mato/workspace",
  "gitExecutable": "/usr/bin/git",
  "allowNetwork": true,
  "processExecutables": {"python": "/usr/bin/python3"},
  "shellExecutable": "/bin/sh"
}
```

`homeDirectory` is mandatory and canonicalized at startup. The configuration file's parent directory is the agent data directory for credentials, transfers, Git state, and cache data. Native installations read `${XDG_DATA_HOME:-~/.local/share}/matoloop-agent/config.json` by default. `MATOLOOP_AGENT_CONFIG` or `--config` selects another file, including `/data/config.json`.

Run `matoloop-agent pair` to enter the backend WebSocket URL, agent ID, and one-time pairing token. Non-interactive pairing uses `MATOLOOP_AGENT_BACKEND_URL`, `MATOLOOP_AGENT_ID`, and `MATOLOOP_AGENT_PAIRING_TOKEN`. `ws://` requires `allowInsecure: true`.

Run locally:

```bash
uv sync
uv run matoloop-agent --config config.example.json pair
uv run matoloop-agent --config config.example.json run
uv run matoloop-agent --config config.example.json cache-clear
```

Run tests:

```bash
uv run python -m unittest discover -s tests -v
```

## Protocol v1

The authenticated WebSocket sends strict `hello`, `request`, `cancel`, `response`, and `event` messages with `protocolVersion: 1`. Hello advertises `agentId`, `agentVersion`, `computerName`, `platform`, canonical `homeDirectory`, and operations.

Filesystem operations use normalized absolute POSIX paths and traverse from an open `/` descriptor with `O_NOFOLLOW` on every component. Copy and move use absolute source and destination paths. Process, shell, and Git repository working directories are absolute; Git pathspecs remain relative. `shell.inspect` returns absolute targets and an inspection revision without root metadata.

File size, transfer size, response size, entry count, timeout, cache, and atomic mutation protections remain enforced. The agent is not an operating-system sandbox; deploy it under an appropriate container, VM, operating-system user, and network policy.
