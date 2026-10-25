# MatoLoop Backend

The MatoLoop backend uses FastAPI, Pydantic v2, asynchronous SQLAlchemy, PostgreSQL, and Alembic. It provides REST and SSE interfaces and contains the provider-neutral agent runtime.

## Development

The backend normally runs as part of the Docker development stack from the repository root:

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh logs backend
```

The following endpoints are then available:

- API: `http://localhost:6020/api`
- OpenAPI: `http://localhost:6020/api/docs`
- Liveness: `http://localhost:6020/api/health/live`
- Readiness: `http://localhost:6020/api/health`

The backend container uses reload mode and mounts the source tree through `/workspace`. The Python environment and `uv` cache live in the `backend_uv_data` named volume.

## Structure

- `src/mato_loop_backend/api`: routers, dependencies, and the central error contract
- `src/mato_loop_backend/auth`: local authentication, login sessions, and credential storage
- `src/mato_loop_backend/sessions`: session workflows and API payload serialization
- `src/mato_loop_backend/agent`: model/tool loop, context preparation, delegation, compaction, and run lifecycle
- `src/mato_loop_backend/tools`: typed tool definitions, catalog, and handlers
- `src/mato_loop_backend/permissions`: policy resolution, typed matchers, grants, and approvals
- `src/mato_loop_backend/providers`: adapter registry, provider settings workflows, and provider transports
- `src/mato_loop_backend/api/workspace_service.py`: reusable HTTP workspace context and MatoLoop Agent dispatch
- `src/mato_loop_backend/files`: backend-managed storage, revisions, root access, and file operations
- `src/mato_loop_backend/execution`: owner-bound Agent connections, dispatch, terminals, and browsers
- `src/mato_loop_backend/models`: relational models split by aggregate and exported through one package
- `src/mato_loop_backend/settings`: typed operational settings
- `src/mato_loop_backend/integrations/<product>/`: product adapters, clients, and services; the root contains shared contracts, registry, and cross-integration coordination
- `src/mato_loop_backend/integrations/search.py`: no-key metasearch and provider fallback; SearXNG's adapter and HTTP client live in `integrations/searxng/`
- `src/mato_loop_backend/web/fetch.py`: bounded fetching of concrete HTTP(S) URLs with configurable public-address enforcement
- `src/mato_loop_backend/media`: validated attachments, processing, and provider media projection
- `src/mato_loop_backend/memory`: durable-memory CRUD, optional dreaming, local indexing, and hybrid durable/episodic retrieval
- `alembic`: migrations and Alembic runtime
- `tests`: backend unit and contract tests

## Database and Migrations

The development stack runs `alembic upgrade head` automatically when the backend starts. Migrations can also be inspected or run directly:

```bash
./scripts/dev.sh db current
./scripts/dev.sh db history
./scripts/dev.sh db upgrade head
./scripts/dev.sh db check
```

Every relational schema change requires an Alembic migration generated only from the current SQLAlchemy metadata:

```bash
./scripts/dev.sh db revision --autogenerate -m "short description"
```

Review the generated diff completely. Adjust generated revisions manually for renames, data migrations, extensions, and other operations Alembic cannot detect safely; fully handwritten revision files are not allowed. At minimum, `upgrade head` and `check` must then succeed. Operational settings remain typed Pydantic documents and additive fields require defaults.

The initial migration enables pgvector and creates `memory_embeddings` as vector storage and a persistent queue. The episodic-memory migration adds deterministic `message_search_chunks` with its own persistent vector queue. Existing memories and eligible prior-session text are indexed asynchronously after backend startup; migrations never call Infinity. The active profile uses `intfloat/multilingual-e5-small`, 384 dimensions, and the E5 prefixes `query: ` and `passage: `. Content hashes and profile fingerprints prevent stale vectors from being used.

The multi-user migration promotes the earliest existing local account to administrator, adds account state and administrator roles, scopes memories and credentials, and introduces authentication-provider and external-identity records. The backend performs no workspace migration at startup.

Infinity is not a hard readiness dependency. `/api/health` retains top-level `status: "ok"` with `embedding: "degraded"` when it is unavailable, while FTS, CRUD, and agent runs remain available. Database or pgvector failure returns readiness HTTP 503. `./scripts/dev.sh doctor` displays the extension version, column type, queue states, and Infinity health.

## Authentication and Ownership

`UserAccount` is the canonical application principal. The first setup account is an administrator; administrator APIs manage local accounts, roles, activation, password resets, and deletion. Administrator authorization does not bypass owner filters and provides no impersonation or access to another user's content.

`LocalAuthProvider` is the only implemented sign-in provider. `AuthenticationProvider`, `UserAuthIdentity`, and `AuthenticatedIdentity` are the foundation for future external providers; there is no external login route or UI yet. External identities are keyed by provider and opaque subject. Future providers may enable just-in-time account creation independently, while existing users link an identity explicitly from an authenticated session; username matching never links accounts.

Sessions and their messages, runs, approvals, grants, notifications, and session trees are owner-bound. Memories, personal settings, credentials, and `ExecutionAgent` records are also owner-scoped. Account deletion cascades relational ownership and removes backend session trees, but it never deletes files, caches, durable state, or configuration owned by a MatoLoop Agent or external service.

## Error Contract

Expected API errors are raised as `ApiError` from `src/mato_loop_backend/api/errors.py`. Responses contain a stable code and, when needed, a field and parameters:

```json
{
  "error": {
    "code": "session.runAlreadyActive",
    "message": "The session request could not be completed."
  }
}
```

```json
{
  "error": {
    "code": "validation.tooShort",
    "message": "The request contains invalid data.",
    "field": "password",
    "params": {
      "minLength": 10
    }
  }
}
```

Rules:

- Include the central catalog's controlled English `error.message` fallback. Frontend localization uses the stable code, field, and parameters rather than comparing that text.
- Do not return raw Pydantic details, exception text, or provider responses.
- Log technical causes server-side.
- Map provider failures to safe codes such as `provider.authenticationFailed` or `provider.unavailable`.
- Publish agent and SSE errors through the same code contract.
- Use `internal.unexpected` for unknown internal failures.

## Providers and Agent Runtime

Provider adapters transport only model messages, tool schemas, text deltas, and tool calls. The agent runtime executes tools, checks permissions, and persists runs and approvals.

Provider-specific data types must not enter the agent core. Technical provider errors are never exposed as tool output, run errors, or API responses.

A provider can contain multiple model configurations. OpenAI-compatible discovery uses the server-side `/models` endpoint. ChatGPT Codex uses the private Codex model catalog after device-code authentication. Both paths normalize responses to a model ID and a bounded set of metadata. Synchronization preserves internal UUIDs and user settings, marks missing discovered models unavailable, and leaves manual models unchanged. Newly discovered models are not enabled automatically.

AI, integration, and tool documents have instance and personal variants. Instance mutations require an administrator. Instance providers and integrations are shared effective resources; personal entries and credentials remain owner-scoped. Administrators gate personal settings and adapter availability. Models are configured independently of agent profiles under model defaults and session templates. Built-in tool presets and permitted personal overrides belong to the tool policy layer. Each integration owns its private/public capability and resource permissions; personal integrations may add sparse profile-specific overrides. Technical availability, Webfetch network restrictions, ownership, remote ACLs, and delegation ceilings remain hard bounds. See [session templates](../../docs/session-templates.md) and [integration policies](../../docs/integrations.md#resource-policies).

Agent file access uses `RootFileService` with internal roots `session` and `workspace`, exposed to models as `files` and `computer`. Session files resolve to the owner-bound backend store; computer access dispatches through the selected owner-bound `ExecutionAgent` and normalized absolute current directory. Agents advertise no roots or root IDs. Without a selected agent and directory, computer access is unavailable while chat and managed files remain available. Linux requests use absolute POSIX paths with descriptor/no-follow traversal; Windows uses forward-slash drive paths and rejects reparse points. See [the Agent boundary](../../docs/architecture.md#matoloop-agent-boundary).

Browser file transport uses camel-case payload fields and lowercase operation names. `POST /browsers/{integrationId}/upload-source` takes the browser binding from query parameters and accepts `{source:{scope:"session"|"filesystem",path}}`. Filesystem uploads call `integration.browser.upload` with `browserId` and an absolute `path`. The structured `browser_upload` model tool accepts `root` (`files` or `computer`), `path`, a known `tabId` and `ref`, and `observe`; it authorizes the source and browser action together, stages a frozen source, revalidates after waits, and deletes temporary staging. Browser downloads are polled with `integration.browser.files`; each item contains `id`, `filename`, `status`, optional size/hash, and the opaque `origin` plus `controlEpoch` captured when the event began. The origin records the controller active when Playwright's actual download event starts; it does not causally attribute the download to an earlier click, timer, request, or page action. Ready downloads stream through `integration.browser.file` into existing backend session storage under owner quotas and upload limits, with SHA-256 verification before publication and staging deletion only after success. Results provide `root=files`, `path`, `fileId`, `revision`, `size`, and `sha256`. Model origins require browser-use permission, rechecked after transfer; ask remains `permission_required` until foreground approval. Failed transfers require an explicit retry. Browser transfers support 1 GiB per file and 2 GiB aggregate worker staging, separately from the 64 MiB observation limit. No computer Ablage mirror or workspace synchronization is created.

Standalone storage is `/data/sessions/<session-id>/{files,revisions}`; project sessions use `/data/projects/<project-id>/sessions/<session-id>/{files,revisions}`. Uploads, processing companions, and durable tool bytes all belong to that session's managed revision store. During a run, tools and users see the same current bytes; terminal finalization snapshots one final revision per changed file. Moves preserve identity and history. Activating a retained revision does not erase other versions. Deleting a file purges its history and explicitly owned companions. Text, JSON, status, and error tool results remain relational `Message.content`. See [managed-file contracts](../../docs/harness.md#typed-resource-matchers).

## Checks and Tests

From the repository root:

```bash
./scripts/dev.sh format backend
./scripts/dev.sh check backend
./scripts/dev.sh test backend
./scripts/dev.sh test rag
```

Checks use the Docker development wrapper. For handbook-only validation and preview refresh, see [documentation maintenance](../../docs/development.md#documentation).

`test rag` uses the running development stack and verifies Infinity and pgvector with real multilingual vectors. The normal backend test skips these integration tests. New error codes require at least one contract test for the backend response. Changes to path boundaries, credentials, permissions, or provider conversion require targeted unit tests.
