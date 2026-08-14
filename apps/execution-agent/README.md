# MatoLoop Execution Agent

Standalone Linux agent for bounded workspace, process, transfer, and sparse session-cache operations. It connects outbound to the MatoLoop backend and has no backend package or database dependency.

## Configuration

```json
{
  "name": "Linux Agent",
  "roots": [
    {"id": "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d", "label": "Projects", "path": "/workspace"}
  ],
  "homeDirectory": "/workspace",
  "dataDir": "/data",
  "gitExecutable": "/usr/bin/git",
  "allowNetwork": false,
  "processExecutables": {"python": "/usr/local/bin/python"},
  "shellExecutable": "/bin/sh"
}
```

The CLI reads runtime settings from `config.json` in the current directory by default. `MATO_EXECUTION_AGENT_CONFIG` or `--config` can select another file. Run `pair` in a terminal to enter the backend WebSocket URL, computer ID, and hidden one-time pairing token. The durable credential stores the URL and ID under `dataDir/agent-state/credential.json`; `run` waits for it and reloads a rotated key before reconnecting. For non-interactive startup, `MATO_EXECUTION_AGENT_BACKEND_URL`, `MATO_EXECUTION_AGENT_ID`, and `MATO_EXECUTION_AGENT_PAIRING_TOKEN` must all be set. Remove the token after pairing. `MATO_EXECUTION_AGENT_TOKEN` and the JSON `token` field remain temporary migration inputs. `ws://` is rejected unless `allowInsecure` is explicitly true. HTTP transfers are accepted only on the paired backend origin and `/workspace/transfers/<ticket-id>` route; they use the durable credential plus the per-request transfer ticket. The optional name override remains `MATO_EXECUTION_AGENT_NAME`. Process aliases, the optional shell, and optional `gitExecutable` are resolved to usable canonical regular executables at startup. Git operations are advertised only when `gitExecutable` is set. HTTPS clones additionally require both local `allowNetwork` and request-level `allowNetwork`; local clone sources must remain inside configured roots. These operations are not an operating-system sandbox.

Run locally:

```bash
uv sync
cp config.example.json config.json
uv run mato-loop-execution-agent pair
uv run mato-loop-execution-agent run
uv run mato-loop-execution-agent cache-clear
```

Run tests:

```bash
uv run python -m unittest discover -s tests -v
```

Successful pushes to `develop` provide a Linux amd64 archive in the Forgejo Actions run. It is built on Ubuntu 22.04 for glibc 2.35 or newer and does not support musl-based distributions such as Alpine Linux. Download it, verify the adjacent SHA-256 file, extract it, copy `config.example.json` to `config.json`, adapt it, run `./mato-loop-execution-agent pair`, and then run `./mato-loop-execution-agent`. Development artifacts are retained for seven days and do not require a Git tag.

## Protocol v2

The authenticated WebSocket sends `hello`, receives `welcome`, and then accepts strict `request` and `cancel` messages. Optional `homeDirectory` is canonicalized inside a configured root and advertised with `homeRootId` and `homeRootRevision`. Results use `response`; process output uses ordered `event` messages with `stdout` or `stderr` event names. Unknown fields and operation arguments are rejected.

Advertised operations include `system.info`, `filesystem.resolve`, `shell.inspect`, the workspace operations, and the cache operations. `process.exec`, `shell.run`, and the six `integration.git.*` operations are advertised only when configured. Git supports bounded clone, status, diff, log, add, and commit operations on normal repository roots; it rejects symlinked `.git` directories and Git files. `system.info` returns a bounded basic host profile and optional root-constrained disk usage. `shell.inspect` performs best-effort direct path detection only and returns `analysisComplete: false` for dynamic or unsupported shell syntax.

Every workspace request includes `rootId`, the revision advertised in `hello`, `workingDirectory`, and a normalized relative POSIX `path`. Imports use authenticated HTTP GET; exports use authenticated HTTP PUT. Transfer content is bounded and SHA-256 validated. Cache descriptors remain authorization-scoped backend inputs, not lookup credentials.

`filesystem.resolve` returns a canonical absolute path with root identity and root-relative path. `process.exec`, `shell.run`, and `shell.inspect` require absolute `currentDirectory`, `rootId`, and `rootRevision`; stale, mismatched, symlinked, or out-of-root directories are rejected. These local path boundaries are not an operating-system sandbox.
