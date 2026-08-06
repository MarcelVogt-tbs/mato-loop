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
- `src/mato_loop_backend/agent`: model and tool loop, permissions, and approvals
- `src/mato_loop_backend/providers`: Codex and OpenAI-compatible transports
- `src/mato_loop_backend/models`: relational SQLAlchemy models
- `src/mato_loop_backend/settings`: typed operational settings
- `src/mato_loop_backend/integrations/contracts.py`: static adapter and tool contribution contracts
- `src/mato_loop_backend/integrations/search.py`: no-key metasearch and SearXNG with controlled fallback
- `src/mato_loop_backend/integrations/caldav.py`: CalDAV accounts, static tools, resource policies, and encrypted event references
- `src/mato_loop_backend/integrations/caldav_client.py`: bounded asynchronous DAV and iCalendar transport
- `src/mato_loop_backend/integrations/webfetch.py`: SSRF-protected fetching of concrete HTTP(S) URLs
- `src/mato_loop_backend/attachments.py`: validated persistent image storage
- `src/mato_loop_backend/memory`: durable-memory CRUD, optional dreaming, local indexing, and hybrid durable/episodic retrieval
- `src/mato_loop_backend/workspace.py`: canonical paths and agent-home boundary
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

Infinity is not a hard readiness dependency. `/api/health` reports `degraded` when it is unavailable, while FTS, CRUD, and agent runs remain available. `./scripts/dev.sh doctor` displays the extension version, column type, queue states, and Infinity health.

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

Agent file access uses `RootFileService` with the explicit roots `session` and `workspace`. Current session storage is under `/data/sessions/<session-id>`; the optional workspace is under the selected path in `/data/home`. Absolute paths, parent segments, and symlink escapes are forbidden. The session root contains the flat directories `files`, `attachments`, `generated`, `downloads`, and `derived`. Only `files` is mutable; the four host-managed asset directories are readable and can be copy sources.

Webfetch uses a dedicated pinned resolver and a network policy the model cannot override. Persistent downloads are stored under `downloads`, user uploads under `attachments`, and generated content under `generated`. Images are served through authenticated session URLs and embedded transiently only for explicitly image-capable models. Text, document, and audio derivatives are stored under `derived`.

## Checks and Tests

From the repository root:

```bash
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
