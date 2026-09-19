# AGENTS.md

This file defines the mandatory working rules for coding agents in MatoLoop.

## Goal

- MatoLoop is a self-hosted, provider-neutral AI chat and agent harness.
- Sessions, messages, agent runs, approvals, and permission grants are persistent core data.
- ChatGPT Codex and OpenAI-compatible APIs are LLM transports only.
- MatoLoop controls tool execution, file access, memory, web search, and human-in-the-loop approvals.
- New sessions start without a working directory. The first persisted message creates only the owner-bound internal session tree under `/data/sessions`; it is not a workspace. Every workspace is an ordinary absolute path on a selected owner-bound MatoLoop Agent.

## Repository

- `apps/backend`: FastAPI, Pydantic, SQLAlchemy, Alembic, provider adapters, and agent runtime.
- `apps/frontend`: Vue 3, Quasar, Pinia, and Vue Router.
- `apps/matoloop-agent`: standalone MatoLoop Agent for filesystem, process, transfer, and local-integration operations.
- `docs`: architecture and operations decisions.
- `docker-compose.dev.yaml`: local development environment with separate frontend, backend, and `matoloop-agent` services.
- `.env.example`: production Compose configuration template for published Forgejo images.
- `docker-compose.yaml`: prepared production stack with a MatoLoop Agent.
- `docker-compose.no-agent.yaml`: prepared production stack without a MatoLoop Agent.
- `scripts/dev.sh`: unified entry point for development, checks, tests, and migrations.
- `docs/ci-and-releases.md`: Forgejo runner, branch, image, and release contract.

## Language and Style

- Write repository documentation, `AGENTS.md`, READMEs, code comments, docstrings, logs, internal exception messages, technical identifiers, and static host-controlled LLM text in English.
- Static host-controlled LLM text includes system and repair prompts, prompt fragments, tool and schema descriptions, and model-facing host markers.
- Write visible UI text in German. Backend-generated labels and messages that are rendered directly by the German UI are UI text.
- Dynamic user, persona, memory, source, and tool data may use any language.
- Exact quoted German UI labels, German intent-detection patterns, and multilingual test fixtures are allowed where they preserve German UI usability or verify language-agnostic behavior.
- Write prompt contracts in simple English. Keep them provider-neutral, concise, explicit, and free of idioms, redundancy, and contradictions so medium-sized local instruction models can follow them reliably and the same contract works unchanged with cloud models.
- Prefer small, vertical, verifiable changes.
- Prefer Quasar components and existing utilities over new custom CSS or abstractions.
- Support desktop, mobile, light mode, and dark mode.
- Document new architecture decisions in `docs/architecture.md` or a small topical file under `docs`.

## Development Environment

- Use only `docker-compose.dev.yaml` through `./scripts/dev.sh` for development work.
- Start the prepared production stack only when explicitly requested.
- The development stack consists of `db`, `embedding`, `backend`, `frontend`, and `matoloop-agent`.
- Backend development data lives in the `db_data` and `app_data` named volumes. MatoLoop Agent state and home use the configured host bind mounts.
- Python and frontend dependencies also live in named volumes.
- The repository bind mount at `/workspace` exists only for HMR and live code changes.
- `./scripts/dev.sh reset-storage` deletes persistent development data and must never be run incidentally.

## Backend

- Use SQLAlchemy 2.x with asynchronous sessions.
- Keep API, service, provider, agent, and persistence layers separate.
- Use typed Pydantic models at system boundaries.
- Manage transactions in the service or application layer; settings objects do not commit themselves.
- Every relational schema change requires an Alembic migration.
- Generate every Alembic migration from the current SQLAlchemy metadata only with `./scripts/dev.sh db revision --autogenerate -m "<description>"`, then review the generated diff completely.
- Adjust generated migrations manually for renames, data migrations, extensions, and other operations Alembic cannot detect safely; do not create fully handwritten revision files.
- Additive settings fields require Pydantic defaults.
- Do not bind the agent core to provider-specific SDK types.
- Model product integrations as normal product/service adapters; keep protocols such as MCP as reusable transport details. Generic connectors remain separate fallbacks and must not expose product profiles.
- Log technical errors server-side without exposing secrets or tokens.

## Providers and Agent

- Provider adapters transport messages, tool schemas, text deltas, and tool calls; they never execute tools.
- All providers use the same MatoLoop agent and permission loop.
- Model arguments must not bypass runtime policies or path boundaries.
- Constrain backend file access to the current owner-bound session tree under `/data/sessions`; dispatch the internal `root=workspace` only through the selected owner-bound `ExecutionAgent` and the session's normalized absolute current directory.
- Symlinks must not bypass path boundaries.
- Disallowed actions create persistent approval requests with `allow_once`, `allow_session`, or `deny`.
- Never expose unfiltered external provider responses or exception text as API errors or toasts.
- `shell.read` means no recognized mutation, not guaranteed side-effect-free execution. Unknown effects and incomplete analysis are an accepted workflow tradeoff, not an unresolved sandbox requirement. Follow the decision in `docs/architecture.md`; review enforcement regressions, and revisit the tradeoff only with new evidence or a changed threat model.

## Error Contract

- Error responses contain a stable, language-neutral `error.code`.
- Transfer dynamic values through `error.params`; field-related errors may include `error.field`.
- The backend adds a controlled English `error.message` fallback for channels without localization.
- English fallbacks come only from the central error catalog; raw Pydantic, provider, and exception text is never exposed.
- The frontend translates error codes centrally in `apps/frontend/src/utils/api-errors.js`.
- The central Axios response interceptor displays Axios errors as negative Quasar toasts.
- Persistent agent and runtime errors appear as MatoLoop chat responses; SSE only accelerates delivery of that canonical state.
- Non-persistable SSE transport errors and errors outside Axios use the same formatter and toast helper.
- Unknown codes must fall back to a safe message based on HTTP status.
- Frontend code must not compare error text or render structured error objects directly.

Example:

```json
{
  "error": {
    "code": "validation.tooShort",
    "message": "The value is too short.",
    "field": "password",
    "params": {
      "minLength": 10
    }
  }
}
```

## Frontend

- Use stores for server state and components for presentation and user interaction.
- Display effective runtime policy state, not only stored desired values.
- Use `useConfirm` or `v-confirm` for destructive user actions.
- Use Quasar dialogs for input and decisions beyond simple confirmation.
- Show local success messages with Quasar Notify; do not duplicate API error notifications.
- Keep dialogs and drawers fully usable on small viewports.
- Avoid optimistic UI state that remains after a failed request.

## Secrets

- Secrets do not belong in settings documents, logs, error responses, or the repository.
- Store API keys and OAuth tokens encrypted in `credentials`.
- Store the Fernet key in the data directory at `/data/secrets/credential.key`.

## Validation

Prefer checks through the Docker development stack:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```

Targeted checks are also available:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh check frontend
./scripts/dev.sh check matoloop-agent
./scripts/dev.sh test backend
./scripts/dev.sh test frontend
./scripts/dev.sh test matoloop-agent
./scripts/dev.sh db current
```

For UI changes, verify at least desktop and mobile in light and dark mode. For API errors, verify both the backend contract and the human-readable frontend message.

## CI and Releases

- Pull requests run complete checks and image builds without registry or release secrets and never publish artifacts.
- Feature branch pushes run tests only. The default branch `develop` publishes mutable `develop` images and immutable `sha-<12>` image tags. `main` has no mutable publication channel.
- Add one `Release-Note: <category>: <text>` Git trailer at the end of a non-merge commit for each noteworthy change. Supported categories are `Added`, `Changed`, `Fixed`, and `Security`; use concise English text. Multiple trailers must be contiguous in the final commit-message paragraph. Commits without a trailer are omitted from release notes. Add `Release-Bump: minor` or `Release-Bump: major` when required; releases default to a patch bump.
- Normal commits do not change `VERSION` or package version declarations. The release script updates every version declaration together.
- The first release may use the already synchronized `VERSION` only while no local or remote `v*` release tag exists. Every later release version must be greater.
- Start a release manually in Forgejo Actions from `develop` or from a clean, current local `develop` branch with `./scripts/release.sh`; the script selects the version, fast-forwards release-only `main`, and pushes atomically. An explicit `X.Y.Z` remains available for exceptional releases. Do not create release tags manually.
- Release images use `vX.Y.Z`, `X.Y.Z`, and `latest`. Published artifacts currently target `linux/amd64` only.
- CI publishes images and Forgejo releases only. It does not deploy, mutate deployment storage, or clean runner Docker storage.
