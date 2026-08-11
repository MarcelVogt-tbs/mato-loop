# MatoLoop Execution Agent

Standalone Linux agent for bounded workspace, process, transfer, and sparse session-cache operations. It connects outbound to the MatoLoop backend and has no backend package or database dependency.

## Configuration

```json
{
  "backendUrl": "wss://mato.example/api/execution-agents/connect",
  "agentId": "2ea504cc-e3c6-4e0d-aa05-080328100fe8",
  "name": "Linux Agent",
  "roots": [
    {"id": "4d0a316f-5fb5-4ec5-a5c4-04aed66c5e6d", "label": "Projects", "path": "/workspace"}
  ],
  "dataDir": "/data",
  "processExecutables": {"python": "/usr/local/bin/python"},
  "shellExecutable": "/bin/sh"
}
```

On the first run, supply the one-time pairing token with `MATO_EXECUTION_AGENT_PAIRING_TOKEN` or `--pairing-token`. Prefer the environment because command-line arguments may be visible in process listings and shell history. The agent stores its durable credential in `dataDir/agent-state/credential.json`; later runs need no token. `MATO_EXECUTION_AGENT_TOKEN` and the JSON `token` field remain temporary migration inputs. Remove the pairing token from the environment after pairing. `ws://` is rejected unless `allowInsecure` is explicitly true. HTTP transfers are accepted only on the configured backend origin and `/workspace/transfers/<ticket-id>` route; they use the durable credential plus the per-request transfer ticket. Backend URL, agent ID, and name have equivalent `MATO_EXECUTION_AGENT_*` overrides. Process aliases and the optional shell are resolved to usable canonical regular executables at startup. Neither operation is an operating-system sandbox.

Run locally:

```bash
uv sync
MATO_EXECUTION_AGENT_PAIRING_TOKEN=TOKEN uv run mato-loop-execution-agent --config /path/to/agent.json
uv run mato-loop-execution-agent cache-clear --config /path/to/agent.json
```

Run tests:

```bash
uv run python -m unittest discover -s tests -v
```

Successful pushes to `develop` provide a Linux amd64 archive in the Forgejo Actions run. It is built on Ubuntu 22.04 for glibc 2.35 or newer and does not support musl-based distributions such as Alpine Linux. Download it, verify the adjacent SHA-256 file, extract it, adapt `agent.example.json`, and run `./mato-loop-execution-agent --config ./agent.example.json`. Development artifacts are retained for seven days and do not require a Git tag.

## Protocol v1

The authenticated WebSocket sends `hello`, receives `welcome`, and then accepts strict `request` and `cancel` messages. Results use `response`; process output uses ordered `event` messages with `stdout` or `stderr` event names. Unknown fields and operation arguments are rejected.

Advertised operations are `workspace.stat`, `workspace.list`, `workspace.read`, `workspace.write`, `workspace.mkdir`, `workspace.rename`, `workspace.delete`, `workspace.copy`, `workspace.move`, `workspace.import`, `workspace.export`, `cache.ensure`, `cache.store`, and `cache.clear`. `process.exec` and `shell.run` are advertised only when configured.

Every workspace request includes `rootId`, the revision advertised in `hello`, `workingDirectory`, and a normalized relative POSIX `path`. Imports use authenticated HTTP GET; exports use authenticated HTTP PUT. Transfer content is bounded and SHA-256 validated. Cache descriptors remain authorization-scoped backend inputs, not lookup credentials.
