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
- `src/mato_loop_backend/agent`: model/tool loop, research and memory preparation, tool contracts, permissions, and approvals
- `src/mato_loop_backend/providers`: adapter registry, provider settings workflows, and provider transports
- `src/mato_loop_backend/api/workspace_service.py`: reusable HTTP workspace context and MatoLoop Agent dispatch
- `src/mato_loop_backend/workspace.py`: backend-managed file roots and revisions
- `src/mato_loop_backend/models`: relational models split by aggregate and exported through one package
- `src/mato_loop_backend/settings`: typed operational settings
- `src/mato_loop_backend/integrations/contracts.py`: static adapter and tool contribution contracts
- `src/mato_loop_backend/integrations/search.py`: no-key metasearch and SearXNG with controlled fallback
- `src/mato_loop_backend/integrations/caldav.py`: CalDAV accounts, static tools, resource policies, and encrypted event references
- `src/mato_loop_backend/integrations/caldav_client.py`: bounded asynchronous DAV and iCalendar transport
- `src/mato_loop_backend/integrations/webfetch.py`: SSRF-protected fetching of concrete HTTP(S) URLs
- `src/mato_loop_backend/attachments.py`: validated persistent image storage
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

Infinity is not a hard readiness dependency. `/api/health` reports `degraded` when it is unavailable, while FTS, CRUD, and agent runs remain available. `./scripts/dev.sh doctor` displays the extension version, column type, queue states, and Infinity health.

## Authentication and Ownership

`UserAccount` is the canonical application principal. The first setup account is an administrator; administrator APIs manage local accounts, roles, activation, password resets, and deletion. Administrator authorization does not bypass owner filters and provides no impersonation or access to another user's content.

`LocalAuthProvider` is the only implemented sign-in provider. `AuthenticationProvider`, `UserAuthIdentity`, and `AuthenticatedIdentity` are the foundation for future external providers; there is no external login route or UI yet. External identities are keyed by provider and opaque subject. Future providers may enable just-in-time account creation independently, while existing users link an identity explicitly from an authenticated session; username matching never links accounts.

Sessions and their messages, runs, approvals, grants, notifications, and session trees are owner-bound. Memories, personal settings, credentials, and `ExecutionAgent` records are also owner-scoped. Account deletion cascades relational ownership and removes backend session trees, but it never deletes files, caches, durable state, or configuration owned by a MatoLoop Agent or external service.

## Error Contract

Expected API errors are raised as `ApiError` from `src/mato_loop_backend/api/errors.py`. Responses contain a stable code and, when needed, a field and parameters:

```json
{
  "error": {
    "code": "session.runAlreadyActive"
  }
}
```

```json
{
  "error": {
    "code": "validation.tooShort",
    "field": "password",
    "params": {
      "minLength": 10
    }
  }
}
```

Rules:

- Do not expose German or English user-facing text as part of the API contract.
- Do not return raw Pydantic details, exception text, or provider responses.
- Log technical causes server-side.
- Map provider failures to safe codes such as `provider.authenticationFailed` or `provider.unavailable`.
- Publish agent and SSE errors through the same code contract.
- Use `internal.unexpected` for unknown internal failures.

## Providers and Agent Runtime

Provider adapters transport only model messages, tool schemas, text deltas, and tool calls. The agent runtime executes tools, checks permissions, and persists runs and approvals.

Provider-specific data types must not enter the agent core. Technical provider errors are never exposed as tool output, run errors, or API responses.

A provider can contain multiple model configurations. OpenAI-compatible discovery uses the server-side `/models` endpoint. ChatGPT Codex uses the private Codex model catalog after device-code authentication. Both paths normalize responses to a model ID and a bounded set of metadata. Synchronization preserves internal UUIDs and user settings, marks missing discovered models unavailable, and leaves manual models unchanged. Newly discovered models are not enabled automatically.

AI, integration, and tool documents have instance and personal variants. Instance mutations require an administrator. Instance providers and integrations are shared effective resources; personal providers, integration credentials, and settings remain owner-scoped. Administrators separately gate personal providers, personal tool settings, and personal integrations, including the allowed built-in adapter types. Tool settings contain built-in tool defaults only. The first personal save snapshots the current instance tool settings; that snapshot remains effective until reset and does not follow later instance-default changes, while instance-controlled Webfetch network limits remain hard bounds. Integration settings own connection configuration, search-provider selection, integration and resource defaults, and CalDAV standard resources. Agent profiles combine persona and instructions with sparse private and public overrides that live-inherit the effective tool, integration, and resource defaults. Effective global `disabled`, disabled integrations, and remote ACL restrictions remain hard boundaries.

Agent file access uses `RootFileService` with the stable internal roots `session` and `workspace`. `session` resolves directly to the current owner-bound backend-managed `files/` directory, while `workspace` dispatches through the selected owner-bound `ExecutionAgent` using the session's normalized absolute current directory. MatoLoop Agents advertise no roots or root IDs. The backend has no `/data/home` workspace. Without a selected agent and current directory, computer access is unavailable while chat and session-file access remain available. Model-facing paths remain relative to `files` or `computer`; standalone `filesystem.*` protocol requests use normalized absolute POSIX paths and the agent rejects symlink traversal with descriptor/no-follow checks.

Standalone storage contains `/data/sessions/<session-id>/files`, `/data/sessions/<session-id>/revisions`, and `/data/sessions/<session-id>/attachments`. Project sessions share `/data/projects/<project-id>/files` and `/data/projects/<project-id>/revisions`, while `/data/projects/<project-id>/sessions/<session-id>/attachments` remains private. User message uploads stay in `attachments/`, with their derivatives below the owning attachment. Every durable byte-valued backend tool output becomes a managed file, and its message references the exact revision. Normal edits append revisions, moves preserve file identity and history, and live agent deletion preserves historical references where applicable. Explicit user revision actions are destructive: historical revisions may be deleted, deleting the current revision purges the file and its history, and restoring an older revision makes it current while removing later revisions. These manual actions may invalidate historical message references. Text, JSON, status, and error tool results remain relational `Message.content`.

## Checks and Tests

From the repository root:

```bash
./scripts/dev.sh format backend
./scripts/dev.sh check backend
./scripts/dev.sh test backend
./scripts/dev.sh test rag
```

The same tools can run directly from `apps/backend` when the `uv` environment already exists:

```bash
uv sync --frozen
uv run ruff check .
uv run pytest
```

`test rag` uses the running development stack and verifies Infinity and pgvector with real multilingual vectors. The normal backend test skips these integration tests. New error codes require at least one contract test for the backend response. Changes to path boundaries, credentials, permissions, or provider conversion require targeted unit tests.
