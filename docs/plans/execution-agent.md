# Execution Agent

Status: planned. This document is independent from `opencode-projects.md`.

## Goal

Add a lightweight Python Execution Agent that performs local operations for MatoLoop. The backend remains the agent orchestrator and continues to own provider communication, the reasoning loop, tool schemas, policies, approvals, session state, memory, persistence, and canonical tool results.

The Execution Agent owns no MatoLoop sessions or projects. It receives only bounded, authorized operations for the executor selected for the current run.

## Responsibility Boundary

The backend remains authoritative for:

- provider requests and the model/tool loop
- static tool names, descriptions, and argument schemas
- argument validation
- policies, approvals, and permission grants
- session and project context
- integration and provider credentials
- session storage and attachments
- controlled errors and canonical `ToolResult` values

The Execution Agent is responsible for:

- workspace access below locally configured roots
- local integration operations assigned to that agent
- temporary files produced by local tools
- a bounded sparse mirror of authorized session files
- later process and shell execution

The agent never receives the complete system prompt, canonical chat history, provider credentials, permission authority, or dynamically supplied tool schemas.

## Session and Run Selection

An Execution Agent is a replaceable executor, not a permanent session owner.

The session stores only the user's mutable preference:

```text
preferred_execution_agent_id
preferred_root_id
working_directory
```

Starting an agent run snapshots the effective executor state into `AgentRun`:

```text
execution_agent_id
execution_agent_name
execution_agent_revision
root_id
root_revision
working_directory
```

Rules:

- The executor can be changed only between runs.
- One run always uses the same agent, root, and working directory.
- Changing the executor clears the selected root and working directory.
- The user selects a root and working directory again on the new agent.
- A disconnect never causes automatic fallback to another agent or the backend.
- Existing session history remains independent from executor changes.
- Path grants bind to agent, root revision, and relative path and therefore do not carry over accidentally.

The project layer may later provide a default executor selection for new sessions. It must be copied into the session preference, not inherited dynamically by existing sessions.

## Default Docker Agent

The development and production stacks include a default connected agent. New sessions use it as their initial executor preference.

The backend remains healthy when no agent is connected:

- provider and backend tools remain available
- normal chat remains available
- executor-dependent tools are omitted
- no work is queued for later delivery

The first implementation may expose only the default agent in the UI. The connection protocol and manager still use stable agent IDs so additional desktop or remote agents can be added without changing the operation contract.

## Storage Separation

Session storage remains in the backend. Workspace storage belongs to the selected Execution Agent.

```text
root=session   -> backend SessionFileService
root=workspace -> selected Execution Agent
```

The current `app_data:/data` mount contains agent home, session storage, and secrets. A separate agent-home volume is required before a standalone agent container receives filesystem access:

```text
app_data        -> backend data, sessions, and secrets
agent_home_data -> /data/home
```

Backend mounts:

```text
app_data:/data
agent_home_data:/data/home
```

Default agent mounts only:

```text
agent_home_data:/workspace
```

The agent must not receive `/data/sessions`, `/data/secrets`, the credential key, or database access.

Existing installations need an explicit, reviewed migration path for files already stored under `app_data:/data/home`.

## Workspace Routing

Keep the existing model-facing file tools and their `root=session|workspace` argument. Do not rename or duplicate the tools.

Examples:

```text
read_file(root=session)   -> backend
read_file(root=workspace) -> Execution Agent
write_file(root=session)   -> backend
write_file(root=workspace) -> Execution Agent
```

Split the current local service responsibility conceptually into:

```text
SessionFileService
AgentWorkspaceService
```

`RootFileService` may remain as the facade used by existing tool handlers and APIs. Its session branch keeps current local behavior, while its workspace branch invokes the run-bound agent.

The first slice does not introduce a generic tool-execution abstraction or change `ToolDefinition`. The existing root dispatch is sufficient for the first executor-backed domain. Extract a general dispatcher only when a second non-workspace tool family is moved to the agent.

## First Workspace Slice

The first vertical implementation contains only:

```text
workspace.list
workspace.read
workspace.write
```

Required behavior:

- paginated directory listing
- bounded UTF-8 line reads
- atomic text writes
- expected SHA-256 revisions for editor saves
- existing whole-file agent write semantics
- normalized relative POSIX paths on the wire
- root containment
- rejection of absolute paths and parent segments
- rejection of symlink traversal
- bounded request and response sizes
- controlled errors without local exception text or absolute paths

Initially unavailable for agent workspaces:

- binary preview
- upload and download
- copy and move
- delete
- cross-root transfer
- shell and process execution

These capabilities are added independently when required. The frontend and effective tool set must not show actions unsupported by the selected agent.

## Agent Roots

Absolute paths remain local to the Execution Agent. The agent advertises only bounded root descriptors:

```json
{
  "id": "root-uuid",
  "label": "Projects",
  "revision": "sha256"
}
```

The backend stores and transports only:

```text
agent ID
root ID
root label
root revision
relative working directory
relative operation path
```

Before every operation the agent rechecks:

- the root still exists
- the advertised root revision still matches
- working directory and path are valid relative paths
- no path component escapes the root
- no symlink traversal occurs
- operation-specific size and count limits

The model never selects an agent ID, root ID, absolute path, or executable path.

## Connection Protocol

The agent opens an outbound WebSocket connection to the backend. The same direction works for the Compose agent and future desktop agents behind NAT.

The initial message vocabulary is deliberately small:

```text
hello
welcome
request
response
```

Example hello:

```json
{
  "type": "hello",
  "protocolVersion": 1,
  "agentId": "agent-uuid",
  "name": "Desktop Agent",
  "operations": [
    "workspace.list",
    "workspace.read",
    "workspace.write"
  ],
  "roots": [
    {
      "id": "root-uuid",
      "label": "Projects",
      "revision": "sha256"
    }
  ]
}
```

Example request:

```json
{
  "type": "request",
  "id": "request-uuid",
  "operation": "workspace.read",
  "arguments": {
    "rootId": "root-uuid",
    "workingDirectory": "project",
    "path": "src/app.py",
    "offset": 1,
    "limit": 2000
  }
}
```

Only the complete transport protocol has a version number. Operation names remain unversioned:

```text
workspace.list
workspace.read
workspace.write
process.exec
shell.run
```

Adding an optional operation or optional field does not require a new protocol version. An incompatible change to common request or response semantics does.

The first slice has no output streaming, durable request queue, reconnect resume, automatic failover, or automatic redelivery.

## Connection and Authentication

The process-local backend connection manager maps stable agent IDs to live sockets and request waiters. Connected state is not persisted as truth.

The default Compose agent may initially use a host-configured bearer credential. External-agent pairing, QR onboarding, token rotation UI, and sharing belong to a later slice.

Security requirements:

- use WSS outside an explicitly local trusted network
- do not put credentials in URLs or query parameters
- store only a hash of high-entropy agent bearer tokens in backend state
- revoke access by closing the live socket and rejecting reconnects
- validate all protocol messages strictly
- ignore unknown advertised operations
- never log request payloads, credentials, file contents, or absolute paths

## Request and Failure Semantics

The backend sends each request at most once. It never automatically retries an ambiguously delivered operation.

For the initial bounded file operations:

- request waiters are process-local
- duplicate request IDs may be rejected or deduplicated in agent memory
- a read interrupted by disconnect fails as unavailable
- a write interrupted after dispatch ends as outcome unknown
- the next run can inspect the current file revision
- backend restart terminates the MatoLoop run under the existing harness contract

Do not add a persistent backend execution queue or local SQLite invocation journal for the first file slice.

A persistent invocation record and local idempotency journal become necessary before enabling shell commands or integration operations whose external effects cannot be reconciled through file revisions.

## Integration Execution

Integration adapters declare one static execution location:

```text
backend
agent
```

Examples:

```text
CalDAV   -> backend
BookStack -> backend
SearXNG  -> backend
OpenSCAD -> agent
local OpenCode service -> agent
```

Add an optional execution agent reference to integration instances:

```text
execution_agent_id
```

Validation rules:

- backend integrations must not have an agent ID
- agent integrations require an agent ID
- an agent integration is offered only when its configured agent matches the current run snapshot
- the configured agent must be connected and advertise the static host-known operation
- switching the session executor rebuilds the effective tool set for the next run

Do not add per-call execution selection, automatic backend fallback, or an agent-override map.

## One Integration Instance per Agent

Local integration configuration is represented by one normal integration instance per agent.

Example:

```text
OpenSCAD Windows
agent: Desktop Windows
binary_path: C:\Program Files\OpenSCAD\openscad.exe
```

```text
OpenSCAD Linux
agent: Linux Workstation
binary_path: /usr/bin/openscad
```

This reuses the existing multi-instance integration model and avoids another override resolver.

Agent-local values such as executable paths:

- remain outside model arguments and prompts
- are stored as bounded operational integration configuration
- are treated as opaque strings by the backend
- are validated according to the selected agent's platform
- must identify an exact regular executable rather than relying on `PATH`
- are rechecked before execution

Connection tests and resource discovery execute at the adapter's static location. OpenSCAD is tested on its assigned agent; CalDAV remains tested in the backend.

The backend continues to own integration schemas, policy, approval, credentials, result validation, controlled errors, and final `ToolResult` creation. Credentials needed by an agent operation are released only for that invocation and are never persisted or logged by the agent.

## Agent-Generated Artifacts

Files produced by an agent-side tool become canonical only after transfer to backend session storage.

Example flow:

```text
local tool creates temporary output
agent computes size and SHA-256
agent uploads the file to the backend
backend validates and atomically stores it under session:generated
backend persists the attachment with the tool result
backend confirms the canonical path and digest
agent retains or removes its temporary copy according to cache policy
```

The agent never returns an absolute local path as a user-visible result. Artifact metadata supplied by the agent is untrusted; the backend recomputes size, digest, and supported file type.

Workspace outputs and session artifacts are distinct:

- project/source changes remain in the agent workspace
- chat attachments, generated models, images, audio, and documents are transferred to backend session storage
- temporary intermediate files remain local and are removed

An artifact-producing tool succeeds only after the backend confirms canonical persistence.

Large artifacts must use a bounded streamed upload rather than Base64 inside a WebSocket JSON message. The exact upload endpoint is deferred until the first artifact-producing integration.

## Sparse Session Mirror

Each agent maintains a disposable sparse local mirror of session files it has produced or downloaded. This avoids repeated backend downloads while keeping the backend canonical.

Suggested layout:

```text
<agent-data>/session-cache/
└── <backend-id>/
    └── <session-id>/
        ├── attachments/...
        ├── generated/...
        ├── downloads/...
        ├── derived/...
        └── files/...
```

The mirror contains only files that the agent actually used. It is not a full session replica and has no background synchronization.

For every authorized artifact input, the backend supplies at least:

```json
{
  "artifactId": "artifact-uuid",
  "sessionId": "session-uuid",
  "path": "generated/uuid/model.stl",
  "filename": "model.stl",
  "size": 123456,
  "sha256": "digest",
  "contentType": "model/stl"
}
```

The agent resolves the mirror path and applies this sequence:

1. Missing file: download from the backend.
2. Wrong size or SHA-256: download and atomically replace.
3. Matching SHA-256: use the local copy and update its modification time.

The digest is cache identity, not authorization. An agent cannot request a backend file merely by knowing a digest or path. The backend authorizes every use and issues the input descriptor for the concrete invocation.

When an agent creates an artifact, it uploads the file first. After backend confirmation, it atomically moves or copies the existing local output into the confirmed session mirror path instead of downloading it again.

Mirror rules:

- backend session listings remain canonical and are never derived from the mirror
- local mirror changes are never synchronized back automatically
- tools that may mutate an input receive a working copy, not the validated cached file
- mirror files use restrictive local permissions
- corrupt entries are deleted and fetched again
- a different agent downloads its own authorized copy
- deleting a backend session need not synchronously reach an offline agent

Keep cleanup simple:

```text
max_session_cache_bytes
```

- update file modification time on a cache hit
- evict oldest files when the configured size is exceeded
- clean on startup and after adding files
- remove empty directories
- provide `cache clear`
- do not add SQLite, manifests, strict delete synchronization, or content deduplication initially

The mirror may be deleted completely at any time without affecting canonical session data.

## Future Process and Shell Execution

The protocol must allow new static operation names, but process execution is not part of the first slice.

Planned operations:

```text
process.exec
shell.run
```

`process.exec` uses an explicit program and argument array without a shell. `shell.run` explicitly selects a supported local shell such as POSIX shell, `cmd.exe`, or PowerShell.

Before enabling either operation, add:

- separate `process.execute` capability and backend policy
- default `ask` behavior
- initially only `allow_once` and `deny`
- output streaming and bounded aggregate output
- execution timeout and backend watchdog
- process-group or Windows Job Object cancellation
- sanitized environment without MatoLoop or integration secrets
- persistent invocation state and local idempotency journal
- controlled stdout, stderr, exit code, and truncation results
- explicit UI disclosure when no operating-system sandbox is active

A workspace root constrains workspace file operations. It does not sandbox an arbitrary process from the rest of the local user account or network.

## Python Application

Keep the agent as an independent Python application:

```text
apps/execution-agent/
├── pyproject.toml
├── README.md
└── src/mato_loop_execution_agent/
    ├── __init__.py
    ├── __main__.py
    ├── connection.py
    ├── protocol.py
    ├── workspace.py
    └── cache.py
```

Initial runtime dependencies:

```text
pydantic
websockets
```

Do not depend on the backend package, FastAPI, SQLAlchemy, provider SDKs, or a desktop framework.

The core should be importable and side-effect free so a future desktop app can embed it or launch it as a subprocess. The desktop toolkit, installer, updater, and OS keychain integration are deferred.

Logging goes to stderr and never includes secrets, complete file content, complete environment blocks, or protocol credentials.

## Delivery Slices

### 1. Storage and Agent Skeleton

- separate `/data/home` from backend session and secret storage
- add the independent Python agent application
- add strict protocol models
- add the default Compose agent
- add a small process-local backend connection manager

### 2. Read-Only Workspace Path

- implement `workspace.list` and `workspace.read`
- route `root=workspace` through the default agent
- retain local `root=session`
- update explorer and effective capability handling
- verify path and symlink boundaries on the agent

### 3. Workspace Writes

- implement `workspace.write`
- preserve atomic writes and revision-checked editor saves
- return outcome unknown on ambiguous disconnect
- do not add automatic retries

### 4. Session Executor Selection

- persist the mutable session preference
- persist immutable run snapshots
- add agent, root, and working-directory selection to the composer
- reset root and working directory after executor changes
- rebuild effective tools for each new run

### 5. Agent-Bound Integrations

- add static backend/agent execution location to integration adapters
- add one integration instance per agent
- add agent-local configuration validation
- implement the first concrete local integration, such as OpenSCAD
- add artifact transfer when the first integration produces session output

### 6. Sparse Session Mirror

- retain confirmed agent-generated artifacts
- cache authorized backend downloads by session-relative path
- validate every hit with size and SHA-256
- add bounded MTime-based eviction and `cache clear`

### 7. Later Execution Capabilities

- add process invocation persistence and local journal
- add `process.exec`
- add cancellation and output streaming
- add `shell.run` only after its policy and isolation UI are explicit

## Explicitly Deferred

- autonomous LLM or provider calls inside the Execution Agent
- dynamic agent-provided tool schemas
- model-selected agents or roots
- executor changes during a run
- automatic failover or redelivery
- durable distributed job queues
- backend-run resume after restart
- exactly-once effect claims
- shell and process execution in the first slice
- complete session replication on agents
- background file synchronization
- strict cache deletion propagation to offline agents
- cache manifests, SQLite cache indexes, and content deduplication
- per-agent configuration override maps inside one integration instance
- moving existing backend network integrations to agents without a concrete need

## Validation

Minimum backend checks:

- workspace tools use the run snapshot, not mutable session preference
- executor switching cannot affect an active run
- changing executor clears root and working directory
- session-root operations never reach an agent
- workspace operations never fall back silently
- unavailable or stale agents remove dependent tools
- unknown agent operations are ignored
- permissions are evaluated before dispatch
- agent responses cannot forge `ToolResult` metadata, file actions, attachments, or error codes
- ambiguous writes produce outcome unknown without retry
- backend tools continue working while all agents are offline

Minimum agent checks:

- invalid roots and paths are rejected
- symlink traversal is rejected
- list and read limits are enforced
- text writes are atomic
- expected revisions prevent stale editor writes
- protocol requests are strictly validated
- absolute local paths never enter normal results or logs
- cache hits require matching size and SHA-256
- corrupt cache files are replaced
- cache eviction remains within the configured size

Minimum integration checks:

- backend adapters reject agent assignment
- agent adapters require exactly one configured agent
- agent integrations are offered only for a matching run executor
- local executable paths are never model arguments
- agent-side connection tests validate the executable locally
- backend remains the canonical credential and result authority

Run repository validation through the Docker development stack after each vertical slice:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```
