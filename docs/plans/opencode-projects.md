# OpenCode-Backed Projects

Status: deferred. Implement the prerequisite work selected by the user before starting this plan.

## Goal

Add projects as the persistent grouping above chat sessions. A project provides shared context and may provide a default working directory. Standard project workspaces require an owner-bound Execution Agent and one of its advertised roots. OpenCode projects use a configured OpenCode server for remote directory browsing, code-aware planning, and sequential plan execution.

OpenCode remains an integration. MatoLoop remains authoritative for users, projects, chat sessions, project context, plan files, execution queues, and UI state.

## Decisions

- A project groups multiple related MatoLoop sessions.
- Sessions receive bounded, relevant context from other sessions in the same project.
- A project may define a default working directory only together with its workspace source.
- A session inherits that directory and may switch at runtime to any directory from the same source.
- Standard projects use an explicitly selected Execution Agent owned by the project owner and one advertised root from that agent. There is no backend-local project workspace.
- OpenCode projects use directories below the configured OpenCode server root.
- Normal chat continues to use the MatoLoop agent.
- MatoLoop invokes OpenCode through project-bound planning and execution operations.
- OpenCode planning produces persistent Markdown plans in the originating MatoLoop session storage.
- Users select one or more plans in the project tools panel and enqueue them for immediate FIFO execution.
- A project queue pauses on the first failed or outcome-unknown execution.
- Git diff, terminal access, commits, pushes, and a generic remote-agent runtime are not part of the initial implementation.

## Project Model

Add a `projects` table:

```text
id                         UUID primary key
user_id                    UUID, required
name                       varchar(240), required
project_type               default | opencode
integration_id             UUID, nullable
default_working_directory  varchar(1000), nullable
execution_agent_id         UUID, nullable
execution_root_id          UUID, nullable
created_at                 timestamptz
updated_at                 timestamptz
```

Constraints:

- A default project has no integration ID. Workspace-backed default projects require an Execution Agent and root owned by the same user; chat-only default projects keep all three workspace fields null.
- An OpenCode project has an OpenCode integration ID and a default working directory.
- OpenCode projects have no Execution Agent or Execution Agent root binding.
- Project type and integration binding are immutable after creation. A different source requires a new project.
- Project deletion is allowed only while the project has no sessions.
- Disabling an OpenCode integration makes dependent projects unavailable but does not delete them.
- Deleting or changing the endpoint of an integration is blocked while projects reference it. Credential rotation remains allowed.

Add a required `project_id` to `chat_sessions`. Keep the existing session `working_directory` column and change its meaning to an optional runtime override:

```text
effective working directory = session override or project default
```

The project fixes the source, not the exact runtime directory:

- A workspace-backed standard-project session may select another directory inside the same owned Execution Agent root.
- A chat-only standard project has no workspace. Adding a workspace to it is deferred; create a workspace-backed project with an owned agent and root instead.
- An OpenCode-project session may select another directory below that integration's server root.
- A session cannot switch between Execution Agents, roots, local and OpenCode sources, or OpenCode integrations without moving to another project.
- Working-directory changes remain blocked while the session has an active run.

Retain `chat_sessions.user_id`. Use a composite ownership constraint so a session cannot reference another user's project.

## Migration

Implementation of this migration is deferred until the backfill groups existing session bindings by owned Execution Agent and root without inventing a backend-local source. The eventual migration must preserve sessions whose owners have no agent or root as chat-only.

Generate the migration from SQLAlchemy metadata and review it completely.

The data migration must:

1. Create one standard project named `Allgemein` for every existing user.
2. Assign every existing session to its owner's project.
3. Preserve existing Execution Agent and root bindings with their `chat_sessions.working_directory` as session overrides. Do not infer a backend workspace or bind an agent from a host path.
4. Make `chat_sessions.project_id` required only after backfilling.

Create `Allgemein` during initial user setup for new installations.

## Project Context

Reuse the existing episodic retrieval pipeline instead of adding a project summary system.

- Restrict automatic prior-session candidates to the current `project_id`.
- Keep semantic and lexical ranking, trust filtering, private-integration filtering, and existing excerpt budgets.
- Keep complete sibling-session reads separately permission-controlled.
- Keep durable memories user-scoped unless a separate project-memory requirement is introduced.
- Add only compact project metadata to normal agent context: project name, project type, and effective working-directory label.

This gives sessions awareness of relevant project discussions without injecting complete sibling transcripts.

## Working Directory Adapters

Introduce a narrow read-side adapter boundary used by both the file explorer and MatoLoop planning reads:

```python
class WorkingDirectoryAdapter(Protocol):
    capabilities: WorkingDirectoryCapabilities

    async def list_directories(self, path: str): ...
    async def validate_directory(self, working_directory: str): ...
    async def list_entries(
        self,
        working_directory: str,
        path: str,
        offset: int,
        limit: int,
    ): ...
    async def read_text(
        self,
        working_directory: str,
        path: str,
        offset: int,
        limit: int,
    ): ...
    async def read_bytes(
        self,
        working_directory: str,
        path: str,
        limit: int,
    ): ...
```

Initial implementations:

- `ExecutionAgentWorkingDirectoryAdapter`
- `OpenCodeWorkingDirectoryAdapter`

Do not add mutation, Git, terminal, or agent-execution methods to this interface. Standard-project mutations continue through the existing Execution Agent workspace services. OpenCode working directories are initially read-only from MatoLoop; code changes occur only through OpenCode plan execution.

Capabilities must independently describe listing, text reads, byte previews, downloads, and mutations. The frontend and agent tool schemas use the effective capability set instead of assuming every working directory is writable and downloadable.

Keep backend-managed files separate. `root=session` exposes only `files`: standalone sessions own `/data/sessions/<session-id>/files` and project sessions share `/data/projects/<project-id>/files`. Immutable message assets remain private to their session and are available only through attachment APIs.

The public UI term becomes **Arbeitsverzeichnis**. Existing internal API paths and the `root=workspace` identifier may remain for compatibility; do not perform a repository-wide naming refactor.

## OpenCode Integration

Add a host-registered OpenCode integration configured with:

- display name
- canonical base URL
- Basic Auth username, default `opencode`
- optional encrypted Basic Auth password
- discovered server root and bounded server version metadata

The URL must not contain credentials, query parameters, or fragments. Passwords belong only in `credentials`.

Before activation, validate:

```http
GET /global/health
GET /path
GET /agent
GET /file?path=.
```

The root returned by `/path` is host state. Never expose remote absolute paths to the browser or model.

HTTP requirements:

- use `httpx` already installed in the backend
- use `trust_env=False`
- do not forward Basic Auth across redirects
- apply bounded connect/read timeouts and response-size limits
- map authentication, availability, malformed-response, and response-size failures to controlled MatoLoop errors
- never expose upstream bodies, exception text, credentials, or absolute paths

The integration is considered active only after successful validation. Installed OpenCode tools remain catalog-only until an enabled instance exists.

## OpenCode Directory Discovery

An OpenCode project can select any directory below the server root discovered during integration setup.

Use:

```http
GET /file?directory=<server-root>&path=<relative-path>
```

The browser sends only source-relative paths. The backend revalidates each selected directory through the configured server and stores a normalized relative path as the project default.

OpenCode file entries currently provide name, path, type, ignored state, and an absolute path. Discard the absolute path. Validate every returned path as an expected descendant and bound the number and total size of entries.

OpenCode file content is available through:

```http
GET /file/content?directory=<working-directory>&path=<relative-file>
```

Initial limitations:

- no remote editor
- no remote download
- no remote mutation through MatoLoop file tools
- no file size or modification time when OpenCode does not provide them
- text preview is not byte-exact because current OpenCode responses trim text
- binary preview requires strict Base64 decoding and MatoLoop's existing media validation

Pin and contract-test the supported OpenCode API because its file surface is currently experimental.

## Project-Bound Planning Tool

Add one OpenCode integration tool:

```text
opencode_create_plan(title, brief)
```

The model controls only the title and compact implementation brief. MatoLoop derives the project, integration, working directory, credentials, OpenCode agent, and permissions from host state.

The tool is callable only when:

- the current session belongs to an OpenCode project
- the bound integration is enabled and current
- the effective working directory remains valid
- the session mode allows creation of the local plan artifact

Planning flow:

1. The MatoLoop agent clarifies the feature in the normal chat.
2. The MatoLoop agent creates a compact handoff brief using bounded project context.
3. MatoLoop creates an OpenCode session in the effective working directory.
4. MatoLoop invokes the built-in OpenCode `plan` agent with mutation and shell tools disabled.
5. OpenCode inspects the current code through its own read/search tools and returns one complete Markdown plan.
6. MatoLoop validates and bounds the final text.
7. MatoLoop stores the plan under `session:files/plans/<plan-id>.md`.
8. MatoLoop persists project-plan metadata and returns a normal file action so the plan can be opened.

Do not parse the Markdown into executable steps. The complete plan is the execution input.

## Plans

Add a `project_plans` table:

```text
id                   UUID primary key
project_id           UUID, required
session_id           UUID, required
title                varchar(240), required
storage_path         varchar(500), required
working_directory    varchar(1000), required
planning_session_id  varchar(200), nullable
created_at           timestamptz
updated_at           timestamptz
```

Plans remain physical Markdown files in their originating session storage. The database stores bounded metadata and the relative path only.

The plan list must handle a missing or manually deleted file as a controlled unavailable plan instead of crashing or executing stale metadata.

## Execution Queue

Add a `project_plan_executions` table:

```text
id                    UUID primary key
project_id            UUID, required
plan_id               UUID, required
queue_position        bigint, required
status                queued | running | waiting_permission |
                      completed | failed | cancelled | outcome_unknown
plan_revision         char(64), required
plan_snapshot         text, required and bounded
working_directory     varchar(1000), required
opencode_session_id   varchar(200), nullable
pending_permission    jsonb, nullable
result_summary        text, nullable
error_code            varchar(120), nullable
error_params          jsonb, nullable
created_at            timestamptz
started_at            timestamptz, nullable
completed_at          timestamptz, nullable
```

When users enqueue selected plans, snapshot the current plan content, SHA-256 revision, and working directory. Later plan edits do not silently alter an already queued execution.

Queue behavior:

- persistent FIFO order per project
- one active execution per project
- each plan gets a fresh OpenCode build session
- pass the complete plan once; OpenCode performs its internal sequential tool loop
- start the next item only after the current OpenCode session becomes idle successfully
- pause the queue on the first failed or outcome-unknown execution
- keep later items queued while paused
- user action is required to retry, skip, cancel, or resume
- cancellation requests OpenCode session abort and never rolls back completed effects
- backend restart marks a running execution `outcome_unknown` and leaves the queue paused
- never automatically replay an operation whose outcome is unknown

OpenCode permission requests are not silently approved. Persist the current remote permission request on the execution and expose `once`, `always`, and `reject` decisions in the OpenCode management panel.

## Project UI

Keep the existing route shape initially:

```text
/?project=<project-id>
/?project=<project-id>&session=<session-id>
```

The navigation gains:

- current-project selection
- project creation, rename, and empty-project deletion
- sessions for the selected project
- new-session creation inside the selected project

Store new-session drafts per project. Existing persisted-session drafts remain keyed by session ID.

Replace the visible file-explorer action with **Projektwerkzeuge**. Reuse the existing desktop splitter and mobile maximized dialog. Add a small panel with Quasar tabs:

- **Dateien**
- **OpenCode**, only for OpenCode projects

The OpenCode tab provides:

- plans from all sessions in the project
- originating session and working-directory display
- plan opening
- multi-selection
- enqueue action
- FIFO state
- pause/resume and cancellation
- pending OpenCode permission decisions
- controlled completion and error summaries

Keep the existing workspace Pinia store for file state. Add separate project and project-plan stores instead of turning the file store into a generic application framework.

## Delivery Phases

### 1. Project Foundation

- project model, migration, and backfill
- project CRUD and ownership checks
- session-to-project relation
- project selection and project-scoped new-session drafts
- same-project episodic retrieval

### 2. Inherited Working Directories

- project default and session override resolution
- active-run guards
- Execution Agent adapter around existing workspace services
- UI terminology changed to **Arbeitsverzeichnis**

### 3. OpenCode Integration and Remote Browsing

- URL and encrypted Basic Auth configuration
- connection validation and root discovery
- OpenCode directory selection during project creation
- OpenCode read adapter
- read-only remote explorer and planning reads

### 4. Plan Creation

- `project_plans` model
- project-bound `opencode_create_plan` tool
- OpenCode plan-agent client flow
- persistent Markdown artifacts
- project-tools plan list

### 5. Sequential Execution

- execution model and persistent FIFO coordinator
- immutable plan snapshots
- OpenCode build sessions
- event, permission, cancellation, and restart handling
- queue controls in the OpenCode project panel

## Explicitly Deferred

- Git diff and repository status UI
- commits, pushes, branches, or worktrees
- interactive terminal or PTY
- editing or downloading files through OpenCode's file API
- Markdown step parsing
- automatic continuation after a failed plan
- full project transcript injection
- project-level durable memory
- generic remote-agent execution protocol
- remote MatoLoop implementation

A later remote MatoLoop integration may implement the proven working-directory read adapter. Do not add placeholders or a generic execution adapter before that requirement exists.

## Validation

Minimum backend coverage:

- project ownership, migration backfill, empty-project deletion, and integration references
- project working-directory inheritance and runtime override
- same-project retrieval with existing trust boundaries
- OpenCode URL/auth validation and secret non-exposure
- remote directory containment, malformed paths, bounded lists, strict Base64, and upstream error mapping
- remote mutation and download denial
- project-bound planning tool and plan artifact persistence
- FIFO ordering, one active execution, failure pause, cancellation, and restart outcome handling

Minimum frontend coverage:

- project-scoped session lists and drafts
- standard/OpenCode project creation
- inherited and overridden working-directory display
- capability-driven file controls
- project-tools tabs
- plan multi-selection and queue controls
- pending OpenCode permission display

Run repository checks through the Docker development stack:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```

For UI phases, verify desktop and mobile in light and dark mode.
