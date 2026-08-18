# MatoLoop

MatoLoop is a self-hosted, provider-neutral AI chat and agent harness with persistent sessions, internal tools, human-in-the-loop approvals, optional memory, and enforceable web search.

## Current State

The current implementation includes:

- multi-user local accounts with cookie-based authentication and administrator roles
- ChatGPT Codex through device-code OAuth
- OpenAI-compatible Chat Completions and Responses endpoints
- persistent sessions, messages, and agent runs
- one shared model and tool loop for all providers
- owner-isolated sessions, memory, credentials, and Execution Agents
- persistent permission grants and approval decisions
- scheduled automations with durable run history, notifications, and optional session output
- built-in web search without an API key and additional SearXNG providers
- CalDAV accounts with per-calendar read, write, and delete policies
- language-neutral API error codes and human-readable frontend toasts

## Stack

- FastAPI and Pydantic v2
- asynchronous SQLAlchemy 2.x, PostgreSQL, and Alembic
- pgvector and local Infinity for hybrid durable-memory and prior-session retrieval
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
- User documentation: `http://localhost:10020/docs/`
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

- `app_data`: backend session trees, credential key, and other MatoLoop runtime data
- `db_data`: PostgreSQL data
- `embedding_cache`: reproducible Infinity model cache
- `backend_uv_data`: Python environment and `uv` cache
- `frontend_node_modules`: installed frontend dependencies
- `frontend_yarn_cache`: additional frontend cache mounted at `/opt/mato-loop/yarn-cache`

Docker prefixes volume names with the Compose project name by default, for example `mato-loop_app_data`. The repository bind mount at `/workspace` exists only for HMR and live code changes.

On first startup, Infinity downloads the pinned `intfloat/multilingual-e5-small` revision. This cold start can take several minutes; the backend and lexical durable-memory and prior-session searches remain available. The cache does not require backup. `./scripts/dev.sh doctor` checks pgvector, durable-memory and episode indexing state, and the Infinity health endpoint.

The following command deletes containers and all development volumes, including the database, users, secrets, backend session files, and Execution Agent state and cache. It does not delete the separately bind-mounted Execution Agent workspace:

```bash
./scripts/dev.sh reset-storage
```

## Production Preparation

`docker-compose.yaml` prepares a combined MatoLoop container, PostgreSQL with pgvector, an internal Infinity CPU service, and an Execution Agent. The prepared agent image includes OpenSCAD for CAD rendering. With no image variables set, `docker compose build` builds MatoLoop and the agent locally. `docker-compose.no-agent.yaml` is the standalone production stack without the agent; use `--remove-orphans` when switching an existing deployment to it. Set `MATO_LOOP_IMAGE` and `MATO_LOOP_EXECUTION_AGENT_IMAGE` to consume published images instead. Published images currently target `linux/amd64` only.

The Compose Execution Agent mounts `${MATO_EXECUTION_AGENT_WORKSPACE:-./data/home}` at `/workspace`. Despite its host-side name, this is only an external directory owned by the bundled agent. The backend program does not access, migrate, or delete it; back it up and clean it up separately.

The prepared production setup uses `./data:/data` and `./db:/var/lib/postgresql/data` bind mounts. Before production use, define and verify TLS, secure cookies, credentials, backups, and restoration for the target environment.

Forgejo CI and the manual release process are documented in [CI and releases](docs/ci-and-releases.md). CI publishes images and Forgejo releases; it does not deploy MatoLoop.

## Accounts and Ownership

The first local account created during setup is an administrator. Administrators can create local users, reset passwords, activate or deactivate accounts, assign or remove the administrator role, and delete deactivated accounts. Administrator status grants account and instance-configuration management only; it does not grant access to another user's sessions, messages, files, memories, credentials, workspaces, approvals, or Execution Agents.

Local authentication is the only implemented sign-in method. The backend already separates the internal `UserAccount` from authentication-provider records and external identities, but no external authentication provider or corresponding login UI is implemented. Future providers can opt into provider-specific just-in-time account creation and explicit self-linking by an authenticated user; identities are never linked merely by matching a username.

Deleting an account removes its database-owned data and backend session trees. It does not remove any files, caches, durable state, or configuration owned by an Execution Agent, including the bundled Compose agent, or data in an external service. Instance-scoped resources are shared intentionally and are not deleted with a user.

## Providers

Two LLM paths are available under **Einstellungen**:

- **ChatGPT Codex**: device-code OAuth with an appropriate ChatGPT account. MatoLoop displays a one-time code for confirmation on the opened ChatGPT sign-in page; no local callback port is required. Available Codex models are synchronized afterward.
- **OpenAI-kompatibel**: base URL, optional API key, and either `Chat Completions` or `Responses API`. MatoLoop reads available models from the compatible `/models` endpoint.

Providers are model transports only. Sessions, tool calls, files, memory, web search, and approvals always pass through the MatoLoop harness.

Each provider can contain multiple models. OpenAI-compatible providers and ChatGPT Codex support automatic model discovery. Newly discovered models start disabled and appear in chat only after explicit activation. Repeated synchronization preserves internal model IDs and runtime settings; missing models are marked unavailable instead of deleted. If a provider does not support discovery, an advanced manual fallback can store a model ID. Providers, models, and the global LLM runtime can be enabled independently.

Providers, models, integrations, and tool settings have separate instance and personal scopes. Only administrators can change instance scope or enable personal providers, integrations, and tool overrides. Administrators also select which built-in integration adapter types users may configure personally. Instance providers and integrations, including their remote resources, are intentionally shared with all users under the instance policies; personal entries and credentials remain visible only to their owner. Administrators use the **Persönlich** / **Instanz** toggle to edit their own personal scope or the shared instance scope.

Without additional configuration, `web_search` uses the built-in no-key metasearch. It is not an integration and uses `ddgs` to query available public search services. Additional SearXNG instances can be managed under **Einstellungen > Integrationen**; the primary provider and optional fallback are selected under **Einstellungen > Tools**. The model supplies only the query and cannot override this host selection. Fallback is used only for transient primary-provider failures.

`webfetch` is configured independently of SearXNG under **Einstellungen > Tools**. It fetches concrete HTTP(S) URLs as Markdown, text, or HTML and supports image responses up to 5 MiB. By default, each fetch asks for approval. Policies can instead allow public targets, or public targets plus explicitly allowed private origins, automatically. Models can override the complete policy, but the global switch and origin allowlist remain host boundaries.

Webfetch follows redirects only after renewed validation, pins validated DNS targets, and blocks link-local, metadata, and other dangerous addresses. Downloads are stored under `/data/sessions/<session-id>/downloads`. Image-capable models receive current or explicitly referenced images transiently in the provider request; text-only models receive only safe metadata and available text derivatives.

## Working Directories

New sessions start without a working directory. The first message creates owner-bound internal session storage under `/data/sessions/<session-id>` with the five flat directories `files`, `attachments`, `generated`, `downloads`, and `derived`. Only `files` is mutable; the other directories are host-managed. This storage is not a workspace, and the backend has no `/data/home` workspace.

Every `root=workspace` operation targets a root advertised by the selected, owner-bound Execution Agent. An agent may advertise multiple roots and an optional `homeDirectory` inside one root. The home initializes new sessions; each session then retains its selected root, root-relative working directory, and canonical current directory independently. Without a selected agent and root, workspace and agent-local operations are unavailable, while chat, backend tools, and persisted session files continue to work.

The file explorer accepts up to four generic uploads of 64 MiB each. Files are created atomically and never overwrite an existing path; its create dialog adds empty `.txt`, `.md`, or `.scad` files through the same path. Validated PNG, JPEG, WebP, GIF, and static self-contained SVG images up to 5 MiB and UTF-8 text or Markdown up to 1 MiB have previews. Editable UTF-8 files use explicit, revision-checked atomic saves so a concurrent change is never overwritten silently. Markdown can be edited as source or through the visual editor; documents with image references remain in source mode to prevent external requests. SVG previews reject scripts, event handlers, animation, XML entities, and external references. Binary STL files have a bounded interactive viewer; configured OpenSCAD source files render an interactive OFF preview from the source view and export STL only on request. Every regular file remains available as a forced download; arbitrary uploaded content is never rendered as active HTML or served inline with a caller-controlled media type.

The authenticated application uses the full viewport without a global top bar. Branding and navigation controls live in the sidebar; when it is hidden, a floating navigation button appears. On chat screens, a separate floating file button is visible only while the explorer is closed. At least 1024 pixels wide, the explorer shares the page with the chat through a resizable Quasar split view; each panel keeps at least 30 percent of the width and the validated ratio is stored locally. Narrower screens use a maximized explorer dialog. A file opens in the integrated viewer with one click and returns to the preserved tree through the back action. Unsaved editor changes require confirmation before leaving. Writable files and directories have direct rename and delete menus; renames change only the basename and never overwrite an existing path.

A new session is created server-side only with its first message and appears in the sidebar afterward. Unsent text remains separately per session in browser-local storage.

- `read-only`: only shared read operations (`read`, `glob`, `grep`, and file export) are offered for session storage and an optional computer workspace.
- `read-write`: write, create, delete, copy, and move are also available. Only `files` is mutable in session storage; `attachments`, `generated`, `downloads`, and `derived` remain read-only and may only be read or used as copy sources.
- File tools use an explicit `root` field with `session` or `workspace`. `root=session` always means the backend session tree; `root=workspace` always means the selected owned Execution Agent root and is unavailable without that agent and root.
- `Einmal erlauben`: applies only to the current action.
- `Für Session erlauben`: creates a separate revocable permission grant.
- `Später`: leaves the request pending and the agent run paused.

## Configuration and Secrets

Infrastructure values such as the database connection, ports, and data directory come from environment variables prefixed with `MATO_LOOP_`. Typed settings documents and the Web UI manage operational settings.

API keys and OAuth tokens are encrypted in PostgreSQL. Personal credentials are owner-scoped; instance credentials belong to shared instance resources. The local Fernet key is stored at `/data/secrets/credential.key` and must be backed up together with the database.

## Documentation

- [User handbook](docs/user/index.md)
- [Coding agent rules](AGENTS.md)
- [Architecture](docs/architecture.md)
- [CI and releases](docs/ci-and-releases.md)
- [Backend](apps/backend/README.md)
- [Frontend](apps/frontend/README.md)
