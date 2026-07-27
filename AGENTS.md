# AGENTS.md

This file defines the mandatory working rules for coding agents in MatoLoop.

## Goal

- MatoLoop is a self-hosted, provider-neutral AI chat and agent harness.
- Sessions, messages, agent runs, approvals, and permission grants are persistent core data.
- ChatGPT Codex and OpenAI-compatible APIs are LLM transports only.
- MatoLoop controls tool execution, file access, memory, web search, and human-in-the-loop approvals.
- New sessions start without a working directory. The first persisted message creates only the internal session storage under `/data/sessions`; it is not a workspace.

## Repository

- `apps/backend`: FastAPI, Pydantic, SQLAlchemy, Alembic, provider adapters, and agent runtime.
- `apps/frontend`: Vue 3, Quasar, Pinia, and Vue Router.
- `docs`: architecture and operations decisions.
- `docker-compose.dev.yaml`: local development environment with separate frontend and backend services.
- `docker-compose.yaml`: prepared production stack.
- `scripts/dev.sh`: unified entry point for development, checks, tests, and migrations.

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
- The development stack consists of `db`, `backend`, and `frontend`.
- Persistent development data lives in the `db_data` and `app_data` named volumes.
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
- Log technical errors server-side without exposing secrets or tokens.

## Providers and Agent

- Provider adapters transport messages, tool schemas, text deltas, and tool calls; they never execute tools.
- All providers use the same MatoLoop agent and permission loop.
- Model arguments must not bypass runtime policies or path boundaries.
- Canonicalize file paths and constrain them to `/data/home`.
- Symlinks must not bypass path boundaries.
- Disallowed actions create persistent approval requests with `allow_once`, `allow_session`, or `deny`.
- Never expose unfiltered external provider responses or exception text as API errors or toasts.

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
./scripts/dev.sh test backend
./scripts/dev.sh test frontend
./scripts/dev.sh db current
```

For UI changes, verify at least desktop and mobile in light and dark mode. For API errors, verify both the backend contract and the human-readable frontend message.
