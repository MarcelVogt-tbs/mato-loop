# MatoLoop Agent

Agent commands run in fresh processes with explicit directories and environments.
The Control Center displays their live output and saved results separately from
the personal interactive terminal. Shell inspection classifies commands without a
recognized write as `shell.read`, even when analysis is incomplete; this is not a
sandbox guarantee.

Standalone agent with a portable connection and dispatch core. Linux provides bounded filesystem, process, POSIX terminal, transfer, OpenSCAD, browser, credential-storage, and session-cache operations. The Windows adapter advertises its native filesystem, PowerShell, search, transfer, and cache operations. It connects outbound to the MatoLoop backend and has no backend package or database dependency.

Platform adapters compose host operations behind the common core. Linux uses normalized absolute POSIX paths; Windows uses forward-slash drive-letter paths such as `C:/Users/Ada/workspace` on the wire. Native Windows paths are converted at the Windows adapter boundary. Unsupported capabilities are not advertised.

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
`shellExecutable`. Unsupported operations are omitted from the advertisement.

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

The systemd system service starts at boot and runs with the installing user's permissions. The
installer uses `sudo` only to manage that service and optional system packages. Repeating `install`
deploys the checkout as it currently exists and repairs an interrupted installation. The same script is
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
Linux system services with systemd. Browser and shared terminal capabilities remain unavailable
when their host prerequisites are declined or unsupported.

## Native Windows installation

Open **Windows PowerShell as Administrator under the account that should own the Agent**.
Copy the Windows installation command from **Einstellungen > Computer**. The script
installs its own `uv` and Python under `%LOCALAPPDATA%\matoloop-agent` and creates
`%USERPROFILE%\mato-loop-agent\workspace` as the default Agent home. A startup
Scheduled Task runs under the installing account, using a password supplied only
to Windows Task Scheduler at installation. The source checkout requires Git and
access to the private MatoLoop repository; an existing checkout can be provided
with `-Source`. Windows PowerShell is the shell for `shell_run`.

After setup, create a computer in the UI and pair from a normal interactive
PowerShell window with `& "$env:LOCALAPPDATA\matoloop-agent\installer.ps1" pair`.
Use the same script with `update`, `status`, `uninstall` or `uninstall -Purge`.
The setup also registers an on-demand maintenance task. **Agent aktualisieren**
in the Computer settings uses that task and the same installer update function;
both tasks run under the installing account without elevation. Task Scheduler
stores the account password, while the Agent's own pairing state uses DPAPI.
Updates require a clean tracked Git branch and fast-forward only. Deinstallation
never removes the workspace. Browser, OpenSCAD, and interactive/shared terminals
are not advertised by the Windows adapter.

Run tests:

```bash
uv run python -m unittest discover -s tests -v
```

## Protocol v1

The authenticated WebSocket sends strict `hello`, `request`, `cancel`, `response`, and `event` messages with `protocolVersion: 1`. Hello advertises `agentId`, `agentVersion`, `computerName`, `platform`, canonical `homeDirectory`, and operations.

Linux filesystem operations use normalized absolute POSIX paths and traverse from an open `/` descriptor with `O_NOFOLLOW` on every component. Windows uses normalized forward-slash drive paths and rejects reparse-point traversal. Copy and move use absolute source and destination paths. Process and shell working directories are absolute. `shell.inspect` returns absolute targets and an inspection revision without root metadata.

The fixed terminal operations are `terminal.open`, `terminal.read`, `terminal.write`, `terminal.resize`, `terminal.close`, and `terminal.run`. Interactive shells and one-shot terminal runs execute with the agent operating-system user's rights. PTYs are live process state and are not persistent terminal sessions.

File size, transfer size, response size, entry count, timeout, cache, and atomic mutation protections remain enforced. The agent is not an operating-system sandbox; deploy it under an appropriate container, VM, operating-system user, and network policy.
