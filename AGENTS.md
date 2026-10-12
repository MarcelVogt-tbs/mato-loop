# AGENTS.md

This file defines the mandatory working rules for coding agents in MatoLoop.

## Goal

- MatoLoop is a self-hosted, provider-neutral AI chat and agent harness.
- Sessions, messages, agent runs, approvals, and permission grants are persistent core data.
- ChatGPT Codex and OpenAI-compatible APIs are LLM transports only.
- MatoLoop controls tool execution, file access, memory, web search, and human-in-the-loop approvals.
- New sessions have no working directory unless an explicit selection or project default supplies one. Sending the first message or opening a session-bound Control Center app creates owner-bound internal storage under `/data/sessions/<session-id>` or `/data/projects/<project-id>/sessions/<session-id>`; it is not a workspace. Every workspace is an ordinary absolute path on a selected owner-bound MatoLoop Agent.

## Repository

- `apps/backend`: FastAPI, Pydantic, SQLAlchemy, Alembic, provider adapters, and agent runtime.
- `apps/frontend`: Vue 3, Quasar, Pinia, and Vue Router.
- `apps/matoloop-agent`: standalone MatoLoop Agent for filesystem, process, transfer, and local-integration operations.
- `docs`: architecture and operations decisions.
- `docker-compose.dev.yaml`: local development environment with separate frontend, backend, and `matoloop-agent` services.
- `.env.example`: production Compose configuration template for published GHCR images.
- `docker-compose.yaml`: prepared production stack with a MatoLoop Agent.
- `docker-compose.no-agent.yaml`: prepared production stack without a MatoLoop Agent.
- `scripts/dev.sh`: unified entry point for development, checks, tests, and migrations.
- `scripts/eval.sh`: entry point for opt-in evaluations through the development stack.
- `docker-compose.eval.yaml`: evaluation-only overlay for the development Compose configuration.
- `.github/workflows`: GitHub Actions checks and publication; `.forgejo/workflows` retains Forgejo support.
- `docs/ci-and-releases.md`: CI runner, branch, image, release, and Forgejo mirror contract.

## Language and Style

- Write repository documentation, `AGENTS.md`, READMEs, code comments, docstrings, logs, internal exception messages, technical identifiers, and static host-controlled LLM text in English.
- Exception: write the user and administrator handbook under `docs/user/` in German, matching the application UI. Keep its MkDocs navigation and UI labels German. Technical documentation and READMEs remain English.
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
- Run evaluations through `./scripts/eval.sh`; it delegates to `./scripts/dev.sh compose` and adds `docker-compose.eval.yaml` only for evaluation-specific services.
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
- Put every integration adapter in its own package under `apps/backend/src/mato_loop_backend/integrations/<product>/`, even when it needs only `adapter.py`. Keep its client and product-specific service in that package. Keep only shared contracts, registry, execution, and cross-integration coordination at the `integrations/` root. Do not add root-level adapter files or compatibility re-exports for moved internal modules.
- Log technical errors server-side without exposing secrets or tokens.

## Providers and Agent

- Provider adapters transport messages, tool schemas, text deltas, and tool calls; they never execute tools.
- All providers use the same MatoLoop agent and permission loop.
- Model arguments must not bypass runtime policies or path boundaries.
- New or changed tool and integration actions must follow `docs/tool-permissions.md`: declare an explicit category on each `ToolAction`, reuse existing capability meanings, and authorize concrete operations and resources before effects.
- Constrain model-facing backend file access to the current owner-bound standalone or project-session tree; dispatch the internal `root=workspace` only through the selected owner-bound `ExecutionAgent` and the session's normalized absolute current directory. The host-owned skill library under `/data/skills` is accessed only by the scope-checked skill service; model tools receive session or execution-agent working copies, never library paths.
- Symlinks must not bypass path boundaries.
- Actions requiring approval create persistent requests with `allow_once`, `allow_session`, or `deny`. Hard policy denials do not become overridable approval requests.
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
    "message": "The request contains invalid data.",
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
- Use `useConfirm` or `v-confirm` for destructive user actions. Dismissing an individual notification needs no confirmation because its source message, session, or pending request remains unchanged; remove the notice from the UI only after successful server deletion.
- Use Quasar dialogs for input and decisions beyond simple confirmation.
- Show local success messages with Quasar Notify; do not duplicate API error notifications.
- Keep dialogs and drawers fully usable on small viewports.
- Do not add tooltips by default. Use them only when they provide necessary information not already conveyed by visible labels or content; preserve accessible names independently of tooltips.
- Avoid optimistic UI state that remains after a failed request.

## Secrets

- Secrets do not belong in settings documents, logs, error responses, or the repository.
- Store API keys and OAuth tokens encrypted in `credentials`.
- Store the Fernet key in the data directory at `/data/secrets/credential.key`.

## Validation

### Test scope for small fixes

- Default to existing tests and checks. A code change does not automatically require new test code. Existing runnable coverage satisfies requirements for an executable check, including Ponytail's check requirement; do not add a separate self-check just to satisfy it.
- Before adding a test, identify the exact missing protection: which observed bug could return without any existing test failing? If no concrete gap is identified, add no test.
- For a small bug fix, add at most one regression case with one observable assertion. Prefer extending an existing test over creating another setup. Mock only unavoidable boundaries.
- Reproduce the reported failure only. Do not add neighboring scenarios, reload simulations, input-immutability checks, metadata checks, or internal ordering assertions unless they are necessary to detect that failure.
- If added test code exceeds the implementation diff, reconsider it before editing. Reduce the setup and assertions, reuse existing coverage, or omit the new test when existing checks sufficiently verify the change. Line count is a review trigger, not a hard limit.
- Additional cases or test infrastructure require a concrete independent correctness risk or an explicit user request. Security, permissions, input validation, and data-loss protections must remain adequately covered.
- Review the test diff for unnecessary scope before the first validation run, not after the user questions it.

### Running checks

- Before validation, choose the smallest checks supported by `./scripts/dev.sh` that verify the changed behavior. Identify what a failure would cause you to change. If the script only supports a whole target suite, use it without adding test infrastructure just to narrow execution.
- When changing system dependencies, update the development Dockerfile, production Dockerfile, and native CI installation together. Rebuild the affected development image with `./scripts/dev.sh compose build <service>` before validation; bind-mounted code does not update installed binaries.
- Changes to shared prompts, tool schemas, settings defaults, or error contracts require the complete affected target test suite before handoff. Update affected expectations in the same change, and distinguish local validation from CI verification.
- A failing baseline run is optional. Run it to resolve uncertainty about the cause or whether the regression test detects the bug, not merely to complete a red-green sequence.
- Use known suite duration plus a timeout margin; allow a generous timeout for full suites of unknown duration. After interruption or truncated output, inspect captured results and execution state before deciding whether another run is necessary.
- Repeat a check only after a change that can affect its result or to investigate a specific unresolved failure. Formatting-only changes require formatting/lint checks, not another functional test run.
- Broaden validation only for a concrete risk that narrower checks do not cover or an explicit user requirement. Preserve necessary security, permission, input-validation, and data-loss coverage; those categories alone do not justify additional suites.
- Before repeating or broadening a check, state the specific reason in one short sentence. Stop when the selected checks pass. Committing or writing the final response is not a reason to rerun them.
- Documentation-only changes need a diff review, not application tests or builds.

Run checks through the Docker development stack. Repository-wide checks, when warranted:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```

Prefer the relevant target:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh check frontend
./scripts/dev.sh check matoloop-agent
./scripts/dev.sh test backend
./scripts/dev.sh test frontend
./scripts/dev.sh test matoloop-agent
./scripts/dev.sh db current
```

For UI changes, verify the affected interaction. Check desktop and mobile when layout or responsive behavior is affected, and light and dark mode when theme-dependent appearance is affected; do not automatically require all four combinations for every UI change. If visual verification is unavailable, state that briefly and request screenshots only to resolve a concrete visual uncertainty. For API errors, verify both the backend contract and the human-readable frontend message.

## CI and Releases

- Pull requests run complete checks and image builds without registry or release secrets and never publish artifacts.
- Feature branch pushes run tests only. Both `develop` and `main` use `YYYY-MM-DD-<SHA8>` versions derived from the UTC commit date and Git SHA, with `-dirty` for local changes. The shared helper lives in `libs/python/version`. Stamp the version before packaging; Python/npm package versions are fixed packaging placeholders.
- Add one `Release-Note: <category>: <text>` Git trailer at the end of a non-merge commit for each noteworthy change. Supported categories are `Added`, `Changed`, `Fixed`, and `Security`; use concise English text. Multiple trailers must be contiguous in the final commit-message paragraph. Commits without a trailer are omitted from release notes. There are no version bumps.
- Start a release manually in GitHub Actions from `develop` or from a clean, current local `develop` branch with `./scripts/release.sh`. GitHub's manual workflow uses `GITHUB_TOKEN` and publishes the new release commit directly in the same workflow; its pushes do not trigger CI. The retained Forgejo workflow supports the same process when Forgejo is the primary host; disable Actions on the pull mirror. It commits dated notes, checks the result, fast-forwards release-only `main`, tags the final commit, and pushes atomically. Do not create release tags manually.
- Both publication channels use the same immutable date/SHA image tags; moving aliases are `develop` and `latest`. Publication workflows are serialized so the same commit is not published twice concurrently. Published artifacts currently target `linux/amd64` only.
- Agent setup and updates follow the running backend's full commit SHA, exposed by `/api/execution-agents/installation-target`. Freeze this target once per operation; do not select branches or require version equality to connect. Dirty or unknown backend builds cannot supply a managed installation target; local checkouts remain an explicit development path. Native and Docker updates stage and verify the exact commit without modifying user checkouts.
- Managed Docker Agents support WebUI updates through their container launcher. Updates survive container restarts, but recreating a container restores its image version. Keep credentials and workspaces in their existing mounts. Reuse the shared container browser setup for builds and updates.
- CI publishes images and releases only. GitHub is the primary host and Forgejo is a Git pull mirror. Shared CI helpers use neutral names and receive `CI_API_URL`, `CI_REPOSITORY`, and `CI_TOKEN` from the platform workflow. CI does not deploy, mutate deployment storage, or clean runner Docker storage.
