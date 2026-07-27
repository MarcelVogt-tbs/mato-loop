# MatoLoop

MatoLoop is a self-hosted, provider-neutral AI chat and agent harness with persistent sessions, internal tools, human-in-the-loop approvals, optional memory, and enforceable web search.

## Current State

The current implementation includes:

- single-user setup and cookie-based authentication
- ChatGPT Codex through device-code OAuth
- OpenAI-compatible Chat Completions and Responses endpoints
- persistent sessions, messages, and agent runs
- one shared model and tool loop for all providers
- file explorer and working directories under `/data/home`
- persistent permission grants and approval decisions
- built-in web search without an API key and additional SearXNG providers
- language-neutral API error codes and human-readable frontend toasts

## Stack

- FastAPI and Pydantic v2
- asynchronous SQLAlchemy 2.x, PostgreSQL, and Alembic
- pgvector and local Infinity for hybrid memory retrieval
- Vue 3, Quasar, Pinia, and Vue Router
- `uv` and Yarn 4
- Docker Compose

## Development

The development stack is the standard environment for all local work:

```bash
./scripts/dev.sh up --build -d
```

The following endpoints are then available:

- Web UI: `http://localhost:10020`
- Backend: `http://localhost:6020/api`
- OpenAPI: `http://localhost:6020/api/docs`

Useful commands:

```bash
./scripts/dev.sh status
./scripts/dev.sh doctor
./scripts/dev.sh logs backend
./scripts/dev.sh stop
./scripts/dev.sh down
```

Checks and tests:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
./scripts/dev.sh test rag
```

Individual targets can be selected with `backend` or `frontend`:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh test frontend
```

`test rag` starts PostgreSQL and Infinity, waits for their health checks, and verifies real German-English hybrid retrieval, trust filtering, and the FTS fallback. The normal test run remains infrastructure-independent.

Migrations:

```bash
./scripts/dev.sh db current
./scripts/dev.sh db upgrade head
```

Local configuration overrides belong in `.env.dev.override`; use `.env.dev.override.example` as a template.

## Development Data

The development stack uses named volumes:

- `app_data`: agent home, credential key, and other MatoLoop runtime data
- `db_data`: PostgreSQL data
- `embedding_cache`: reproducible Infinity model cache
- `backend_uv_data`: Python environment and `uv` cache
- `frontend_node_modules`: installed frontend dependencies
- `frontend_yarn_cache`: additional frontend cache mounted at `/opt/mato-loop/yarn-cache`

Docker prefixes volume names with the Compose project name by default, for example `mato-loop_app_data`. The repository bind mount at `/workspace` exists only for HMR and live code changes.

On first startup, Infinity downloads the pinned `intfloat/multilingual-e5-small` revision. This cold start can take several minutes; the backend and lexical memory search remain available. The cache does not require backup. `./scripts/dev.sh doctor` checks pgvector, vector dimensions, indexing state, and the Infinity health endpoint.

The following command deletes containers and all development volumes, including the database, users, secrets, and agent files:

```bash
./scripts/dev.sh reset-storage
```

## Production Preparation

`docker-compose.yaml` and `apps/backend/Dockerfile` prepare a combined MatoLoop container, PostgreSQL with pgvector, and an internal Infinity CPU service. The prepared Infinity container targets `linux/amd64`. This is not the development stack and is not started during normal development work.

The prepared production setup uses `./data:/data` and `./db:/var/lib/postgresql/data` bind mounts. Before production use, define and verify TLS, secure cookies, credentials, backups, and restoration for the target environment.

## Providers

Two LLM paths are available under **Einstellungen**:

- **ChatGPT Codex**: device-code OAuth with an appropriate ChatGPT account. MatoLoop displays a one-time code for confirmation on the opened ChatGPT sign-in page; no local callback port is required. Available Codex models are synchronized afterward.
- **OpenAI-kompatibel**: base URL, optional API key, and either `Chat Completions` or `Responses API`. MatoLoop reads available models from the compatible `/models` endpoint.

Providers are model transports only. Sessions, tool calls, files, memory, web search, and approvals always pass through the MatoLoop harness.

Each provider can contain multiple models. OpenAI-compatible providers and ChatGPT Codex support automatic model discovery. Newly discovered models start disabled and appear in chat only after explicit activation. Repeated synchronization preserves internal model IDs and runtime settings; missing models are marked unavailable instead of deleted. If a provider does not support discovery, an advanced manual fallback can store a model ID. Providers, models, and the global LLM runtime can be enabled independently.

Without additional configuration, `web_search` uses the built-in no-key metasearch. It is not an integration and uses `ddgs` to query available public search services. Additional SearXNG instances can be managed under **Einstellungen > Integrationen**; the primary provider and optional fallback are selected under **Einstellungen > Tools**. The model supplies only the query and cannot override this host selection. Fallback is used only for transient primary-provider failures.

`webfetch` is configured independently of SearXNG under **Einstellungen > Tools**. It fetches concrete HTTP(S) URLs as Markdown, text, or HTML and supports image responses up to 5 MiB. By default, each fetch asks for approval. Policies can instead allow public targets, or public targets plus explicitly allowed private origins, automatically. Models can override the complete policy, but the global switch and origin allowlist remain host boundaries.

Webfetch follows redirects only after renewed validation, pins validated DNS targets, and blocks link-local, metadata, and other dangerous addresses. Downloads are stored under `/data/sessions/<session-id>/downloads`. Image-capable models receive current or explicitly referenced images transiently in the provider request; text-only models receive only safe metadata and available text derivatives.

## Working Directories

New sessions start without a working directory. The first message creates internal session storage under `/data/sessions/<session-id>` with the five flat directories `files`, `attachments`, `generated`, `downloads`, and `derived`. Only `files` is mutable; the other directories are host-managed. This storage is not a workspace. The file explorer continues to manage the agent home under `/data/home` and can select an optional workspace directory.

A new session is created server-side only with its first message and appears in the sidebar afterward. Unsent text remains separately per session in browser-local storage.

- `read-only`: only `list_directory` and `read_file` are offered for session storage and an optional workspace.
- `read-write`: write, create, delete, copy, and move are also available. Only `files` is mutable in session storage; `attachments`, `generated`, `downloads`, and `derived` remain read-only and may only be read or used as copy sources.
- File tools use an explicit `root` field with `session` or `workspace`. Without a selected working directory, only `root=workspace` is unavailable.
- `Einmal erlauben`: applies only to the current action.
- `Für Session erlauben`: creates a separate revocable permission grant.
- `Später`: leaves the request pending and the agent run paused.

## Configuration and Secrets

Infrastructure values such as the database connection, ports, and data directory come from environment variables prefixed with `MATO_LOOP_`. Typed settings documents and the Web UI manage operational settings.

API keys and OAuth tokens are encrypted in PostgreSQL. The local Fernet key is stored at `/data/secrets/credential.key` and must be backed up together with the database.

## Documentation

- [Coding agent rules](AGENTS.md)
- [Architecture](docs/architecture.md)
- [Backend](apps/backend/README.md)
- [Frontend](apps/frontend/README.md)
