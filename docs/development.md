# Development and Documentation Maintenance

Use `./scripts/dev.sh` from the repository root for all development containers, checks, tests, and migrations. Production Compose files are deployment templates, not development environments.

## Start and inspect

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh status
./scripts/dev.sh logs backend
./scripts/dev.sh logs matoloop-agent
./scripts/dev.sh doctor
```

Requirements on the development host are Bash, Python 3, Git, sha256sum, and Docker Compose v2.24 or newer. `up` starts detached, waits for service readiness (up to 15 minutes), and prints the worktree, stack name, Web UI URL and API URL. `-d` remains accepted but is redundant. Use `logs` to follow output. The handbook is at `/docs/` on the Web UI and OpenAPI at `/api/docs` on the backend. `up --pwa --build` enables the installable web-app development mode. Mobile PWA tests require HTTPS.

Python and frontend sources reload from the repository mount. Agent Python changes restart its in-memory runtime, including running browsers; durable Agent data and profiles remain.

## Parallel worktrees

Run the same commands inside each worktree. `dev.sh` derives the project name from the checkout path; Docker assigns available host ports. `up` and `status` show the current URLs through `docker compose port`. Ports can change when containers are recreated, including after `down`. Container ports remain 6020 and 10020; set explicit host ports in `.env.dev.override` only when stable URLs are needed.

Each project has its own containers, network, instance volumes, installed dependencies, development image tags and session cookie name. Only reproducible download caches are shared between worktrees of the same Git repository. Agent state and pairing use the project-local `matoloop_agent_data` named volume; source code remains bind-mounted for live reload. `status` shows the checkout, URLs and service health; `doctor` additionally checks backend/frontend responses, pgvector and embeddings. Commands reject a project whose containers or instance volumes belong to another checkout, and shared caches with missing or foreign repository labels.

The first Web UI visit offers the normal account setup. Create a separate test account, configure a model, and pair this instance's Agent through the normal setup workflow. Existing accounts, provider credentials and pairing state are not copied. Starting a container does not prove that its Agent is paired; inspect `logs matoloop-agent` when testing Agent functionality. See the [handbook](user/index.md) for application configuration.

Configuration precedence is shell environment, `.env.dev.override`, then tracked `.env.dev`. The path-derived project name is the default. Use `.env.dev.override` for deliberate instance overrides, for example:

```dotenv
MATO_LOOP_COMPOSE_PROJECT_NAME=mato-loop-my-task
MATO_LOOP_HOST_BACKEND_PORT=16020
MATO_LOOP_HOST_FRONTEND_PORT=16021
```

Choose unused host ports and unique project/image names. Relative bind paths resolve within that checkout. `compose` accepts `-f <overlay>` and `--profile <name>` before its subcommand; configure instance overrides in the file above rather than passing project/directory/environment-file flags. `config --format json` is available for automation. Resolved configuration can contain credentials; do not publish its output.

Existing stacks are not adopted automatically. Keep an older stack on its original checkout and development script until you deliberately migrate it. For new development, start an isolated stack and use the normal account/model/Agent setup; do not reuse an old project name or Agent state. To preserve old application data, back it up and restore it into a separately verified isolated instance. Unlabeled or foreign volumes are rejected, not relabeled or deleted. Before moving a checkout, remove its containers from the old path; the new path derives a different stack name.

`dev.sh check` and `test` mask installed dependency environments with temporary anonymous volumes removed with their containers; download caches are shared across repository worktrees. Start the full stack only for checks that need it. A documentation-only `compose run --no-deps backend` needs neither the Agent nor exposed service ports.

Agent tests use a disposable source copy inside the container, including current uncommitted code and a host-resolved version stamp. Only that copy is made readable to the isolated browser user; private checkout permissions remain unchanged and external worktree Git metadata need not be mounted.

Backend and Agent development containers keep Python bytecode and lint/test caches under `/tmp`, not in the host checkout. This also applies to running services, preventing root-owned caches from blocking later worktree removal. Existing host caches are not repaired automatically.

## Data

The selected Compose project owns `app_data` (backend files and secrets), `db_data`, `backend_uv_data` (installed backend environment), `frontend_node_modules`, `matoloop_agent_dependency_data` (installed Agent environment), `matoloop_agent_data` (Agent state and pairing), and `matoloop_agent_workspace` (Agent home at `/workspace`). The development stack does not use `MATOLOOP_AGENT_DATA` or `MATOLOOP_AGENT_HOME`.

`dev.sh` derives a shared cache prefix from the absolute Git common directory, so linked worktrees share downloads but independent clones do not. It verifies repository and cache-kind labels before use and creates missing external cache volumes only for `up`, `run`, or `create`. Compose teardown cannot delete these external volumes. Python downloads use `shared_uv_cache`, Yarn uses `shared_yarn_cache`, and Infinity's pinned Hugging Face Hub model files use `shared_embedding_cache`. Installed environments stay isolated; Python uses copy mode across volumes. Hugging Face authentication and other process caches remain outside the shared Hub directory. Infinity's first model download can take minutes; lexical search remains available during embedding degradation. Do not store credentials in shared caches or manually remove cache files while clients are running.

`stop` stops services; `down` removes containers while retaining data. **`reset-storage` removes containers and configured instance volumes, including accounts, database, secrets, backend files, Agent state, pairing and the Agent workspace.** It prints the affected instance and requires typing its project name. Non-interactive callers must explicitly pass `reset-storage --yes`. `down --volumes` also requires confirmation. Both retain shared caches. Never use these commands as incidental cleanup. After a reset, set up the account and pair the Agent again. Host bind directories are not deleted or migrated automatically.

### Reclaim worktree storage

1. Run `./scripts/dev.sh cleanup` to list Compose stacks and labeled worktree volumes without changing anything. Use `cleanup --project <stack-name>` to list one stack's resource IDs. For Docker's host-wide size summary, use `docker system df`.
2. Explicitly stop the intended stack with its own `./scripts/dev.sh stop`. Cleanup refuses running containers; it never stops a stack automatically.
3. Run `cleanup --project <stack-name> --dependencies` to remove its stopped containers and rebuildable dependency/cache volumes, retaining application data. **Use `--all` only to permanently delete the stopped stack, its volumes and networks, including accounts, database, secrets, Agent state, pairing, and workspace.** Back up needed data first. Type the stack name to confirm; `--yes` is only for explicitly authorized non-interactive deletion.

All logic lives in `dev.sh`. It checks container/volume worktree ownership and repository labels and rechecks resource metadata after confirmation. Only the current checkout may use legacy containers/volumes without repository labels; other or removed worktrees require repository labels. Cleanup retains networks without verified repository/worktree labels; they do not block ordinary Compose commands. Unknown containers/volumes are rejected, not adopted. Docker removals never use force, so running containers and attached volumes/networks are protected. Cleanup can be partial if Docker refuses a removal: inspect before retrying. Shared caches, images and host files remain; there is no automatic or global pruning.

Existing per-worktree caches are not copied or removed automatically. After switching a checkout to this configuration, new containers populate the shared caches once; old Python download files may remain inside the local dependency volumes, and old `embedding_cache`/`frontend_yarn_cache` volumes may be detached. Once the instance is explicitly stopped, `cleanup --dependencies` can remove those rebuildable volumes without touching application data. Moving the Git common directory changes the shared cache identity and leaves the previous caches untouched.

## Checks, migrations, and evaluations

```bash
./scripts/dev.sh check backend
./scripts/dev.sh test backend
./scripts/dev.sh check frontend
./scripts/dev.sh test frontend
./scripts/dev.sh check matoloop-agent
./scripts/dev.sh test matoloop-agent
./scripts/dev.sh test rag
./scripts/dev.sh db current
./scripts/dev.sh db upgrade head
```

Omit the target to check/test all components. `test rag` starts PostgreSQL and Infinity and verifies real hybrid retrieval, trust filtering, and lexical fallback. Normal tests do not require that infrastructure. Rebuild an affected development image after system dependency changes; bind-mounted source does not update installed binaries.

Generate migrations with `./scripts/dev.sh db revision --autogenerate -m "description"`, then review the complete generated diff. See [AGENTS.md](../AGENTS.md) for validation scope and migration rules.

```bash
./scripts/eval.sh help
./scripts/eval.sh prompts --help
./scripts/eval.sh session-cache --help
./scripts/eval.sh file-tools --help
./scripts/eval.sh project-context --help
./scripts/eval.sh session-summary --help
./scripts/eval.sh memory-extraction --check
./scripts/eval.sh coding-comparison --help
./scripts/eval.sh browser-performance --help
./scripts/eval.sh shell-decider --check
```

Evaluator arguments are forwarded unchanged. Browser-performance uses the running backend; the other targets use disposable containers. Shell-decider adds the evaluation-only Compose overlay and builds its image on demand. Raw outputs stay under ignored `data/` paths; link published conclusions from the [documentation index](index.md).

`memory-extraction` compares the pre-adoption extraction prompt with the short experimental addition or, with `--refined`, the adopted artifact/user boundary. It uses the production default candidate limit, message serializer, provider adapter, candidate validator, and automatic Dreaming reasoning selection. `--reasoning-effort` selects a supported explicit level instead; recorded requests include the resulting effort and thinking switch. It defaults to `qwen3.8-27b` at `http://192.168.1.102:13306/v1` and three repetitions; `--holdout` selects independently worded cases. Use a new `--output` path for each run; existing artifacts are never overwritten. Requests are serial and tool calls are scored but never executed. No application database, settings, sessions, or memories are accessed. `--check` checks paired inputs and reasoning settings, verifies that the refined prompt equals the production prompt, and rejects empty output for a positive case without contacting the model.

`session-summary` compares existing conversation snippets with source-linked summaries using the enabled `halogen-qwen3.8-flash-next` model by default (`--model` selects another exact model ID). It uses native retrieval and read handlers over synthetic project sessions in rollback-only transactions, including corrections, unverified claims, quoted instructions, and a continued session. Both variants share the default automatic context budget and tool access. Generation uses the production incremental prompt, tool schema, evidence validator, and bounded projection, passing only the previous summary and new messages for the continued session. It makes paid provider requests; `--check` only checks scoring without model requests. No Dreaming settings, existing sessions, or durable memories are changed. This is a provider-level retrieval comparison, not a persistent Dreaming worker or summary-ranking benchmark.

`file-tools` runs one real three-turn Build session against the connected development Agent whose home is `/workspace`. It verifies create/overwrite, exact editing, an intentional failed edit followed by repair, patch update/move/delete, independent final bytes, stable tool order and observed provider cache reuse. It defaults to the enabled GPT-6.1 Sol Ultrafast configuration; `--model` selects another enabled model. It removes its successful scratch directory and test session, never existing workspaces. On failure, inspect the unique scratch path in `data/file-tools-acceptance.json`; incomplete effects may remain. Cache hits are observations, not guarantees or a performance comparison.

## Documentation

Follow the [documentation rules in AGENTS.md](../AGENTS.md#documentation). Choose the reader's task before editing: learning the first workflow, completing a procedure, looking up a contract, or understanding a decision. Do not mix all four into a feature page.

The handbook owns German usage and operations instructions; technical topic pages own English developer contracts. `architecture.md` explains system boundaries and links to those contracts. Keep release-specific instructions in the handbook's [upgrade guide](user/aktualisierung.md), and link dated evidence separately from current contracts in [the index](index.md). A new page is justified by an independent reader task, not by every new feature or code change.

These rules apply [Diátaxis](https://diataxis.fr/), Google's [minimum viable documentation](https://google.github.io/styleguide/docguide/best_practices.html), and [progressive disclosure](https://developers.google.com/tech-writing/two/large-docs). They are not word-count targets: complete reference and recovery instructions may be longer than an introduction.

README and technical documentation are English. The handbook in `docs/user/`, its navigation, and application UI are German. Quote actual German UI labels in technical documents. Keep dated reports historical and compare current operational claims with code and configuration before changing them.

MkDocs builds only `docs/user/`; technical documents and component READMEs are repository documentation. The normal backend check includes a strict handbook build. For handbook-only changes, use the narrower build:

```bash
./scripts/dev.sh compose run --rm --no-deps \
  --env UV_LINK_MODE=copy --volume /opt/mato-loop/backend/venv \
  backend sh -lc 'uv sync --frozen --group docs && uv run --no-sync mkdocs build --strict --config-file /workspace/mkdocs.yml --site-dir /tmp/mato-loop-docs'
```

Use an isolated project as described above when running from a separate worktree. The output stays in the disposable container. A strict MkDocs build is not a whole-repository or external-link checker; review links and heading anchors outside the handbook separately.

The development backend builds the served handbook at startup. Markdown edits do not trigger Python reload. Refresh an already-running development preview with:

```bash
./scripts/dev.sh compose exec -T backend sh -lc 'uv run --no-sync mkdocs build --strict --config-file /workspace/mkdocs.yml --site-dir /tmp/mato-loop-static/docs && rm -f /tmp/mato-loop-static/docs/404.html'
```

Production images package the handbook during their build. Review navigation, code blocks, and edited pages in the generated site before publishing.
