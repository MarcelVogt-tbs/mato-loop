# MatoLoop Agent

The optional [managed shared Bash terminal](SHARED_TERMINAL.md) uses a native
Readline integration for editable, revision-bound agent proposals in a real
persistent shell. Shared and isolated inspection use the same heuristic: commands
without a recognized write remain `shell.read`, even when analysis is incomplete.
The mutable shared shell is trusted; this is not a sandbox guarantee. Isolated
command execution remains unchanged.

Standalone Linux agent for bounded filesystem, process, POSIX terminal, transfer, OpenSCAD, and session-cache operations. It connects outbound to the MatoLoop backend and has no backend package or database dependency.

## Configuration

```json
{
  "homeDirectory": "/home/mato/workspace",
  "shellExecutable": "/bin/sh"
}
```

`homeDirectory` is mandatory and canonicalized at startup. The configuration file's parent directory is the agent data directory for credentials, transfers, and cache data. Native installations read `${XDG_DATA_HOME:-~/.local/share}/matoloop-agent/config.json` by default. `MATOLOOP_AGENT_CONFIG` or `--config` selects another file, including `/data/config.json`.

Personal terminal.open and terminal.run require POSIX PTYs and a configured
`shellExecutable`. Managed shared Bash is a separate capability, independent of
that configured shell. Unsupported operations are omitted from the advertisement.

Run `matoloop-agent pair` to enter the backend WebSocket URL, agent ID, and one-time pairing token. Non-interactive pairing uses `MATOLOOP_AGENT_BACKEND_URL`, `MATOLOOP_AGENT_ID`, and `MATOLOOP_AGENT_PAIRING_TOKEN`. `ws://` requires `allowInsecure: true`.

Run locally:

```bash
uv sync
uv run matoloop-agent --config config.example.json pair
uv run matoloop-agent --config config.example.json run
uv run matoloop-agent --config config.example.json cache-clear
```

## Native Ubuntu development installation

From a MatoLoop checkout, run the interactive installer as a normal user:

```bash
./scripts/install-matoloop-agent.sh
```

When run from a MatoLoop checkout, the installer uses that checkout so local Agent changes can be
tested. A standalone copy of the script instead clones
`https://git.devmato.net/devmato/MatoLoop.git` into `~/mato-loop-agent/source`. It asks for the Agent
data directory, home/workspace directory, and normal MatoLoop instance URL, then derives the
WebSocket endpoint and reuses the Agent's secure pairing prompt for the computer ID and one-time
token. Defaults place runtime data under `~/mato-loop-agent` and connect to
`http://127.0.0.1:10020`. An `http://` instance enables insecure transport; use `https://` outside a
trusted local network. Path and URL prompts use Readline, so their prefilled values can be edited
with the cursor, Home, and End keys.

The user systemd service runs with the installing user's permissions. The installer can also
update a clone it created, repair an interrupted installation, show its status, or remove its
managed state:

```bash
./scripts/install-matoloop-agent.sh update
./scripts/install-matoloop-agent.sh repair
./scripts/install-matoloop-agent.sh status
./scripts/install-matoloop-agent.sh uninstall
```

Updates require a clean installer-managed clone and use a fast-forward-only Git pull before
reinstalling dependencies and restarting the service. Existing development checkouts are never
updated by the installer; update them manually and run `repair`.

Uninstalling never removes an existing checkout, `uv`, shared system packages, or a pre-existing
workspace. It removes a clone created by the installer; the interactive uninstall can separately
remove an installer-created workspace. This helper currently targets Ubuntu or Debian hosts with
systemd and `apt-get`; the native Agent itself remains unsandboxed and should not run as root.

Run tests:

```bash
uv run python -m unittest discover -s tests -v
```

## Protocol v1

The authenticated WebSocket sends strict `hello`, `request`, `cancel`, `response`, and `event` messages with `protocolVersion: 1`. Hello advertises `agentId`, `agentVersion`, `computerName`, `platform`, canonical `homeDirectory`, and operations.

Filesystem operations use normalized absolute POSIX paths and traverse from an open `/` descriptor with `O_NOFOLLOW` on every component. Copy and move use absolute source and destination paths. Process and shell working directories are absolute. `shell.inspect` returns absolute targets and an inspection revision without root metadata.

The fixed terminal operations are `terminal.open`, `terminal.read`, `terminal.write`, `terminal.resize`, `terminal.close`, and `terminal.run`. Interactive shells and one-shot terminal runs execute with the agent operating-system user's rights. PTYs are live process state and are not persistent terminal sessions.

File size, transfer size, response size, entry count, timeout, cache, and atomic mutation protections remain enforced. The agent is not an operating-system sandbox; deploy it under an appropriate container, VM, operating-system user, and network policy.
