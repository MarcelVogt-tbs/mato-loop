# MatoLoop Agent

The optional [managed shared Bash terminal](SHARED_TERMINAL.md) gives the user and
agent exclusive turns in one real persistent shell. Shared and isolated inspection
use the same heuristic: commands without a recognized write remain `shell.read`,
even when analysis is incomplete. The shared shell is trusted; this is not a
sandbox guarantee. Isolated command execution remains unchanged.

Standalone agent with a portable connection and dispatch core. The current Linux platform adapter provides bounded filesystem, process, POSIX terminal, transfer, OpenSCAD, browser, credential-storage, and session-cache operations. It connects outbound to the MatoLoop backend and has no backend package or database dependency.

Platform adapters compose complete host operations behind the common core. Linux is currently the only implementation. Future macOS and Windows adapters must preserve each operation's security guarantees; Windows support also requires a coordinated extension of the protocol's normalized absolute POSIX path contract and the backend permission model.

The [Linux browser runtime](BROWSER.md) provides a persistent owner-bound headed Chromium,
private graphical viewing, interactive prerequisite setup, and bounded browser actions.

## Configuration

```json
{
  "homeDirectory": "/home/mato/workspace",
  "shellExecutable": "/bin/sh"
}
```

`homeDirectory` is mandatory and canonicalized at startup. The configuration file's parent directory is the agent data directory for credentials, transfers, and cache data. Native lifecycle installations read `${XDG_DATA_HOME:-~/.local/share}/matoloop-agent/data/config.json` by default. `MATOLOOP_AGENT_CONFIG` or `--config` selects another file, including `/data/config.json`.

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

## Native Linux installation

From a MatoLoop checkout, run the interactive installer as a normal user:

```bash
./scripts/install-matoloop-agent.sh
```

Or copy the instance-specific command from **Einstellungen > Computer**:

```bash
curl -fsSL https://mato.example/api/execution-agents/install.sh | sh -s -- --instance-url https://mato.example
```

The bootstrap installs pinned, private `uv` and Python runtimes below
`~/.local/share/matoloop-agent`; it offers to install only the Agent's missing Debian/Ubuntu system
packages and never changes shell profiles. Browser Python, Chromium, and browser host dependencies
are installed later through the browser integration setup. From a
checkout it uses the current source. A standalone script requires `git` to obtain the private
repository because there is no native release package. The setup asks for the Agent home directory
and normal MatoLoop instance URL, derives the WebSocket endpoint, and reuses the Agent's secure
pairing prompt. An `http://` instance enables insecure transport; use `https://` outside a trusted
local network.

The user systemd service runs with the installing user's permissions. Repeating `install` deploys
the checkout as it currently exists and repairs an interrupted installation. The same script is
installed as the `matoloop-agent` command; there is no second wrapper implementation:

```bash
matoloop-agent update
matoloop-agent install
matoloop-agent status
matoloop-agent uninstall
matoloop-agent uninstall --purge
```

`update` requires a clean checkout on a branch with an upstream, runs `git pull --ff-only`, then
prepares source and dependencies before replacing the running version. This is identical for an
existing checkout and an installer-created checkout: the selected branch alone determines whether
the Agent follows stable `main`, bleeding-edge `develop`, or another tracked branch. Use `install`
instead to deploy uncommitted local changes without pulling.
`uninstall` preserves credentials, while `uninstall --purge` removes all managed Agent files. Both
offer to remove only the system packages recorded as newly installed by MatoLoop. Agent
home directories and workspaces are always preserved. The first platform implementation targets
Linux user services with systemd. It does not enable linger automatically. Browser and shared
terminal capabilities remain unavailable when their host prerequisites are declined or unsupported.

Run tests:

```bash
uv run python -m unittest discover -s tests -v
```

## Protocol v1

The authenticated WebSocket sends strict `hello`, `request`, `cancel`, `response`, and `event` messages with `protocolVersion: 1`. Hello advertises `agentId`, `agentVersion`, `computerName`, `platform`, canonical `homeDirectory`, and operations.

Filesystem operations use normalized absolute POSIX paths and traverse from an open `/` descriptor with `O_NOFOLLOW` on every component. Copy and move use absolute source and destination paths. Process and shell working directories are absolute. `shell.inspect` returns absolute targets and an inspection revision without root metadata.

The fixed terminal operations are `terminal.open`, `terminal.read`, `terminal.write`, `terminal.resize`, `terminal.close`, and `terminal.run`. Interactive shells and one-shot terminal runs execute with the agent operating-system user's rights. PTYs are live process state and are not persistent terminal sessions.

File size, transfer size, response size, entry count, timeout, cache, and atomic mutation protections remain enforced. The agent is not an operating-system sandbox; deploy it under an appropriate container, VM, operating-system user, and network policy.
