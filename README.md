# MatoLoop

MatoLoop is a self-hosted, provider-neutral AI chat and agent harness with persistent sessions, internal tools, human-in-the-loop approvals, optional memory, and enforceable web search.

## Current State

The current implementation includes:

- multi-user local accounts with cookie-based authentication and administrator roles
- ChatGPT Codex through device-code OAuth
- OpenAI-compatible Chat Completions and Responses endpoints
- persistent sessions, messages, and agent runs
- one shared model and tool loop for all providers
- owner-isolated sessions, memory, credentials, and MatoLoop Agents
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

The development Agent reloads automatically when its Python sources change. Active in-memory Agent
state, including a running browser, ends during that reload, while persistent Agent data and browser
profiles remain.

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
./scripts/dev.sh logs matoloop-agent
./scripts/dev.sh stop
./scripts/dev.sh down
```

Checks and tests:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
./scripts/dev.sh test rag
```

Individual targets can be selected with `backend`, `frontend`, or `matoloop-agent`:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh test frontend
./scripts/dev.sh check matoloop-agent
./scripts/dev.sh test matoloop-agent
```

`test rag` starts PostgreSQL and Infinity, waits for their health checks, and verifies real German-English hybrid retrieval, trust filtering, and the FTS fallback. The normal test run remains infrastructure-independent.

Checks and tests use temporary virtual-environment volumes, so running them from another Git worktree does not modify the environments of the running development stack. Package downloads are reused from the worktree's named dependency-cache volumes.

Migrations:

```bash
./scripts/dev.sh db current
./scripts/dev.sh db upgrade head
```

`.env.dev` contains the ready-to-run development configuration. Local overrides belong in `.env.dev.override`; use `.env.dev.override.example` as a template.

## Development Data

The development stack uses named volumes:

- `app_data`: backend session trees, credential key, and other MatoLoop runtime data
- `db_data`: PostgreSQL data
- `embedding_cache`: reproducible Infinity model cache
- `backend_uv_data`: Python environment and `uv` cache
- `frontend_node_modules`: installed frontend dependencies
- `frontend_yarn_cache`: Yarn package cache mounted at `/opt/mato-loop/yarn-cache`

Docker prefixes volume names with the Compose project name by default, for example `mato-loop_app_data`. MatoLoop Agent state and its home use the separate host bind mounts `${MATOLOOP_AGENT_DATA:-./data/matoloop-agent}` and `${MATOLOOP_AGENT_HOME:-./data/home}`. The backend and frontend repository bind mount at `/workspace` exists only for HMR and live code changes.

On first startup, Infinity downloads the pinned `intfloat/multilingual-e5-small` revision. This cold start can take several minutes; the backend and lexical durable-memory and prior-session searches remain available. The cache does not require backup. `./scripts/dev.sh doctor` checks pgvector, durable-memory and episode indexing state, and the Infinity health endpoint.

The following command deletes containers and all development named volumes, including the database, users, secrets, and backend session files. It does not delete the separately bind-mounted MatoLoop Agent data or home directories:

```bash
./scripts/dev.sh reset-storage
```

## Production Preparation

`docker-compose.yaml` prepares a combined MatoLoop container, PostgreSQL with pgvector, an internal Infinity CPU service, and a MatoLoop Agent. The prepared `matoloop-agent` image includes OpenSCAD for CAD rendering and headed Chromium for the optional Browser integration. `docker-compose.no-agent.yaml` is the production stack without a MatoLoop Agent; use `--remove-orphans` when switching an existing deployment to it. Published images currently target `linux/amd64` only.

When copying the production configuration, include `apps/matoloop-agent/docker/browser-seccomp.json` at that same relative path beside `docker-compose.yaml` and `.env`. Compose requires this companion security profile; the host must also permit Chromium's unprivileged user-namespace sandbox. See [Browser architecture and operations](docs/browser.md) before enabling the integration.

Copy `.env.example` to `.env`, replace the database password and both image placeholders, and use the same immutable Forgejo CI tag in `MATO_LOOP_IMAGE` and `MATOLOOP_AGENT_IMAGE`. Development publications use `sha-` followed by the first 12 characters of the Git commit ID. Then authenticate to the registry when required and start the stack:

```bash
docker compose pull
docker compose up -d
```

The `matoloop-agent` service has exactly two runtime mounts: `MATOLOOP_AGENT_DATA:/data` for `/data/config.json`, credentials, transfers, and cache data, and `MATOLOOP_AGENT_HOME:/workspace` for the configured home directory. The production template sets these to `/opt/matoloop-agent/data` and `/opt/matoloop-agent/home`. The backend does not access, migrate, or delete the host home directory. The Docker container currently runs as root, so MatoLoop policies are not an operating-system sandbox: mounted scope and host policy determine what it can access.

When upgrading from the former standalone MatoLoop Agent, move or copy its credential and durable state into the new `MATOLOOP_AGENT_DATA` directory before starting `matoloop-agent`. Pair the agent again if that state is not retained. Old environment-variable aliases are unsupported.

Existing installations must complete the [removed Git integration cleanup](docs/user/betrieb-und-datensicherung.md#removed-git-integration) before restarting the upgraded MatoLoop Agent.

The production template stores backend and PostgreSQL data below `/opt/mato-loop` and MatoLoop Agent data below `/opt/matoloop-agent`. It publishes port `10020` on `0.0.0.0`; restrict access with the host firewall and configure TLS through a suitable reverse proxy. Before production use, verify secure cookies, credentials, backups, and restoration for the target environment.

Forgejo CI and the manual release process are documented in [CI and releases](docs/ci-and-releases.md). CI publishes images and Forgejo releases; it does not deploy MatoLoop.

## Accounts and Ownership

The first local account created during setup is an administrator. Administrators can create local users, reset passwords, activate or deactivate accounts, assign or remove the administrator role, and delete deactivated accounts. Administrator status grants account and instance-configuration management only; it does not grant access to another user's sessions, messages, files, memories, credentials, workspaces, approvals, or MatoLoop Agents.

Local authentication is the only implemented sign-in method. The backend already separates the internal `UserAccount` from authentication-provider records and external identities, but no external authentication provider or corresponding login UI is implemented. Future providers can opt into provider-specific just-in-time account creation and explicit self-linking by an authenticated user; identities are never linked merely by matching a username.

Deleting an account removes its database-owned data and backend session trees. It does not remove any files, caches, durable state, or configuration owned by a MatoLoop Agent, including the bundled Compose agent, or data in an external service. Instance-scoped resources are shared intentionally and are not deleted with a user.

## Providers

Two LLM paths are available under **Einstellungen**:

- **ChatGPT Codex**: device-code OAuth with an appropriate ChatGPT account. MatoLoop displays a one-time code for confirmation on the opened ChatGPT sign-in page; no local callback port is required. Available Codex models are synchronized afterward.
- **OpenAI-kompatibel**: base URL, optional API key, and either `Chat Completions` or `Responses API`. MatoLoop reads available models from the compatible `/models` endpoint.

Providers are model transports only. Sessions, tool calls, files, memory, web search, and approvals always pass through the MatoLoop harness.

Each provider can contain multiple models. OpenAI-compatible providers and ChatGPT Codex support automatic model discovery. Newly discovered models start disabled and appear in chat only after explicit activation. Repeated synchronization preserves internal model IDs and runtime settings; missing models are marked unavailable instead of deleted. If a provider does not support discovery, an advanced manual fallback can store a model ID. Providers and models can be enabled independently.

Providers, models, default-model settings, integrations, and tool settings have separate instance and personal scopes. Only administrators can change instance scope or enable personal providers, integrations, and tool settings. Administrators also select which built-in integration adapter types users may configure personally. Instance providers and integrations, including their remote resources, are intentionally shared with all users under the instance policies; personal entries and credentials remain visible only to their owner. Administrators use the **Persönlich** / **Instanz** toggle to edit their own personal scope or the shared instance scope.

Without additional configuration, `web_search` uses the built-in no-key metasearch. It is not an integration and uses `ddgs` to query available public search services. Additional SearXNG instances, the primary provider, and the optional fallback are managed under **Einstellungen > Integrationen** for the current instance or personal scope. The model supplies only the query and cannot override this host selection. Fallback is used only for transient primary-provider failures.

`webfetch` is configured independently of SearXNG under **Einstellungen > Tools**. It fetches concrete HTTP(S) URLs as Markdown, text, or HTML and supports image responses up to 5 MiB. One trust-scoped policy controls all targets. An instance-wide restriction allows only public addresses by default; administrators may disable it to permit every HTTP(S) target reachable from the backend. Models have no separate Webfetch override.

Webfetch follows redirects only after renewed authorization and resolution and pins validated DNS targets. With the public-address restriction enabled, it blocks link-local, metadata, and other non-public addresses. Durable fetched images and rendered PDF pages become revisioned managed files; textual results remain message content. Image-capable models receive current or explicitly referenced images transiently in the provider request; text-only models receive only safe metadata and available text derivatives.

## Working Directories

New sessions start without a working directory. Their first persisted message creates only owner-bound internal session storage under `/data/sessions`; this is not a workspace. Standalone session storage under `/data/sessions/<session-id>` contains `files/`, `revisions/`, and private `attachments/`. Project sessions share `/data/projects/<project-id>/files` and `/data/projects/<project-id>/revisions`, while `/data/projects/<project-id>/sessions/<session-id>/attachments` remains private to one session. Attachment derivatives stay below their source attachment. Durable byte-valued backend tool outputs become revisioned managed files with exact message references; text, JSON, status, and error results remain relational message content. There are no separate generated, downloaded, or derived trees, and the backend has no `/data/home` workspace.

A workspace is an ordinary normalized absolute POSIX path on the selected owner-bound MatoLoop Agent. The required `homeDirectory` is its initial directory suggestion; agents advertise no roots, root IDs, or root revisions. A session stores only its selected agent and current absolute directory. Without both, computer file, process, shell, and local integration operations are unavailable, while chat, backend tools, and persisted session files continue to work.

Git operations use shell commands on the selected, connected MatoLoop Agent with Git installed, under the existing shell permissions. There is no dedicated Git integration or backend clone path.

Authenticated Control Center file requests are owner-bound and may directly access any path available to the selected MatoLoop Agent's operating-system user. This intentionally bypasses model tool policies and approvals; it does not bypass operating-system permissions or the agent's descriptor-based no-follow checks.

The file explorer accepts up to four generic uploads of 64 MiB each. Files are created atomically and never overwrite an existing path; its create dialog adds empty `.txt`, `.md`, or `.scad` files through the same path. Validated PNG, JPEG, WebP, GIF, and static self-contained SVG images up to 5 MiB and UTF-8 text or Markdown up to 1 MiB have previews. Editable UTF-8 files use explicit, revision-checked atomic saves so a concurrent change is never overwritten silently. Normal edits append a revision, and moves preserve file identity and history. Activating a historical revision changes the current file without deleting retained versions; individual revisions cannot be deleted. Deleting the file purges its complete history and may invalidate historical message references. Markdown can be edited as source or through the visual editor; documents with image references remain in source mode to prevent external requests. SVG previews reject scripts, event handlers, animation, XML entities, and external references. Binary STL files have a bounded interactive viewer; configured OpenSCAD source files render an interactive OFF preview from the source view and export STL only on request. Every regular file remains available as a forced download; arbitrary uploaded content is never rendered as active HTML or served inline with a caller-controlled media type.

The authenticated application uses the full viewport without a global top bar. Branding and navigation controls live in the sidebar; when it is hidden, a floating navigation button appears. On chat screens, a separate floating file button is visible only while the explorer is closed. At least 1024 pixels wide, the explorer shares the page with the chat through a resizable Quasar split view; each panel keeps at least 30 percent of the width and the validated ratio is stored locally. Narrower screens use a maximized explorer dialog. A file opens in the integrated viewer with one click and returns to the preserved tree through the back action. Unsaved editor changes require confirmation before leaving. Writable files and directories have direct rename and delete menus; renames change only the basename and never overwrite an existing path.

Successful file writes, copies, and moves use the existing file card; generated session files do not add a separate draft card. A newly generated session file opens automatically in the existing file browser only when the user has not changed its navigation during the run and no dirty editor would be displaced; an already open current file stays selected and refreshes. Text previews provide a direct copy action. A new session is created server-side only with its first message and appears in the sidebar afterward. Unsent text remains separately per session in browser-local storage.

Agent profiles combine an optional persona and separate task instructions with sparse tool-policy overrides for private and public models. Every session selects one profile; the protected instance profile `Allgemein` has no complete policy snapshot and live-inherits the effective defaults. **Einstellungen > Tools** retains personal and instance defaults for built-in tools only; integration tools remain on their integration pages. Technical tool and resource availability, disabled integrations, and remote ACL restrictions remain hard boundaries. File capabilities remain individually controlled; there is no combined read-only/read-write mode.
- Model-facing file tools use `root=files` for backend-managed files and `root=computer` for the selected MatoLoop Agent directory. Backend implementation maps these to the stable internal `session` and `workspace` roots. Agent protocol filesystem operations are all named `filesystem.*` and receive normalized absolute POSIX paths.
- `Einmal erlauben`: applies only to the current action.
- `Für Session erlauben`: creates a separate revocable permission grant.
- `Später`: leaves the request pending and the agent run paused.

## Configuration and Secrets

Infrastructure values such as images, database credentials, ports, and host data paths come from the gitignored production `.env` or the tracked development `.env.dev`. Typed settings documents and the Web UI manage operational settings.

API keys and OAuth tokens are encrypted in PostgreSQL. Personal credentials are owner-scoped; instance credentials belong to shared instance resources. The local Fernet key is stored at `/data/secrets/credential.key` and must be backed up together with the database.

## Documentation

- [User handbook](docs/user/index.md)
- [Coding agent rules](AGENTS.md)
- [Architecture](docs/architecture.md)
- [CI and releases](docs/ci-and-releases.md)
- [Backend](apps/backend/README.md)
- [Frontend](apps/frontend/README.md)

## License

Copyright (C) 2026 MatoLoop contributors.

MatoLoop is licensed under the [GNU Affero General Public License version 3 only](LICENSE) (`AGPL-3.0-only`).
