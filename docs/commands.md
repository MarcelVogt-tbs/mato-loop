# Commands and managed Git worktrees

## Shared command system

`sessions/commands.py` owns the host-registered command catalog, parsing and dispatch.
Providers register one `CommandProvider` from `sessions/command_contracts.py` with a
unique source, one or more commands, an availability callback and a preparation
callback. Command names support letters, digits, underscores and hyphens. Duplicate
sources or names are rejected instead of silently replacing another command.
The Git provider lives in `sessions/git_commands.py`; the generic menu has no Git
dependency. Skill discovery, package installation and executable plugin hooks are
outside this first extension contract.

`POST /commands/catalog` accepts an owned session ID or explicit browser-draft
context. Stored sessions use their server-side workspace; draft discovery does not
create storage or prepare a worktree. Providers return relevant command names and
controlled German reasons for unavailable commands. Responses contain menu metadata,
not execution handlers or model instructions. Preparation rechecks actual state on
submission, and normal agent permissions remain authoritative.

The composer palette opens as a bounded, scrollable overlay above the composer.
Choosing a row by click, Enter or Tab inserts `/name `, retaining existing argument
text and attachments. The palette closes and focus stays in the input. Only another
submission starts the command. Arrow keys navigate enabled entries; Escape dismisses
the palette. Git app action buttons remain direct, explicit agent requests.

## Ownership and configuration

Projects retain their selected owner-bound computer and repository root.
`gitTargetBranch` and `gitWorkspaceMode=local|worktree` supply defaults for new sessions.
The Git app selects or configures the remote; an unambiguous remote or `origin` is
suggested automatically. Repository
discovery runs on that computer, including before a session exists. It is repeated
when opening project settings, changing the computer/path, or explicitly refreshing;
creating a repository later does not require recreating the project. Discovery does
not fetch and never replaces an explicitly saved target with a discovered default.

Session templates contain no Git configuration. The permanent **Arbeitsbereich**
field above the composer shows **Nicht verfügbar**, **Lokal** or **Worktree**.
New project sessions can override the project default before creation. Worktrees
require the configured project computer and repository. Without a workspace the
field shows **Nicht verfügbar**; a selected offline computer does not change an
existing mode. After session creation the mode is immutable and displayed read-only.
The Git app displays the workspace path with a copy action. Opening it in a browser
draft does not create a session or prepare a worktree.

Choosing worktree mode explicitly authorizes preparation on the selected computer.
Session creation in worktree mode creates a local branch under
`matoloop/<owner-id>/<session-id>`. With a selected remote, it first fetches the
configured target branch. Without a remote, it uses the committed local target
branch, not uncommitted working changes. A missing or unborn local target is rejected.
It freezes that base commit and checks it out under
`<agent-data>/worktrees/<project-id>/<session-id>`. No other managed location is supported.
Only the concrete worktree is selected
and granted, never the Agent data directory. Normal computer filesystem and shell
policies still apply, including external-path denials. No model, file browser,
or terminal can use the session before this preparation succeeds. Fetch failure
never falls back to a cached branch. The selected project directory must be the
repository root and the managed worktree directory must be outside that repository.
Computer filesystem denials are checked before preparation. The Agent derives the
destination; callers cannot choose another destination through this operation.
Remote-based preparation requires a network remote (HTTPS, HTTP, SSH or Git transport).
Local paths and `file://` remotes remain available through ordinary authorized shell
operations. Hooks and checkout filters are disabled during automatic preparation.
Linked metadata descendants and alternate object stores are rejected rather than
following them beyond the authorized metadata directory.

The session persists its original repository, remote, target, worktree path, branch,
and initial commit. Worktree computer/path changes are rejected. Repeated preparation
recovers only a completed matching worktree and does not reset existing work.
The Agent keeps a private preparation record with the repository metadata identity,
optional remote fingerprint covering all effective fetch and push URLs, target and base
commit. A process-shared lock and the filesystem mutation lock protect preparation.
Changed inputs, replacement repositories
and incomplete registered checkouts are rejected; they require explicit inspection.
An operation-owned branch left by a failed checkout is reused only while it still
points to the recorded base and is not checked out elsewhere.
Requests with a run ID derive a stable session ID so retries can recover preparation.
Archiving and project deletion retain worktrees. Session deletion offers an unchecked
**Worktree ebenfalls entfernen** checkbox, backed by `DELETE /sessions/{id}?removeWorktree=true`.
Without this explicit choice the worktree remains. The confirmation warns that removal
uses `git worktree remove --force` and permanently discards uncommitted changes,
untracked and ignored files while retaining branches and commits.
Other sessions selecting the directory and active overlapping
runs block removal. Removal failure preserves the session. The Agent revalidates its
durable record and Git identities under the preparation lock and retains the receipt
for safe retries after a lost response or database failure. There is no separate
inventory, removal endpoint, or automatic cleanup.

Repository inspection checks whether the selected local target contains a commit.
Without a remote, the Worktree option is disabled for a missing commit before preparation.

Local worktrees need no remote. Review remains available; publication, pull requests
and remote synchronization require explicit remote setup. Adding a remote later does
not reset the worktree or silently change its session target: adopt it in the Git app.

Worktrees share repository metadata and objects. The original repository must remain
accessible at its existing path. Docker deployments need durable mounts for both
locations. Ignored configuration, dependencies, processes, ports and databases are
not copied or isolated. See [parallel development worktrees](development.md#parallel-worktrees).

## Shared command entry points

The composer palette and Git app use the shared catalog. Telegram registers
transport-compatible command names in its native menu and
includes them in `/help`. Additional channel providers should project this catalog
and preserve the normal authenticated session selection and ingress semantics.

| Command | Task |
| --- | --- |
| `/git_init` | Initialize Git in the selected workspace without committing, adding a remote, or publishing. |
| `/git_review` | Review working changes and unpublished commits without editing or publishing. |
| `/git_merge` | Commit, fetch, merge, review, run relevant checks, publish to the target, and update a clean fast-forwardable original checkout. |
| `/git_pr` | Commit, review, check, publish the session branch, and create or update its pull request through available host tools. |
| `/git_sync` | Fetch and fast-forward the clean original project checkout without switching branches or discarding work. |
| `/git_remote [name] [url]` | Configure only the explicitly requested remote through normal agent tools; ask for missing values. |

Merge and PR commands initially require a managed worktree. Review also works in a
local repository; sync requires a configured or unambiguous remote and target.
Telegram users select an existing coding session with `/sessions` and `/use <ID>`;
messaging commands never create an unrelated empty session. Web submission can
start a new session using its explicit draft configuration. Existing command source,
run-ID deduplication, channel pairing, questions, approvals and outbox delivery apply.
A busy session does not silently queue a messaging publication command for later.
Coding commands are rejected as in-run continuations. Retry revalidates the saved
executor, directory and command context before dispatching a new run.
Opening or selecting in the web palette never starts an agent or creates a session.

The Git app has two retained views: **Änderungen** shows the session-workspace change
list, filter and agent actions; **Einstellungen** shows mode, branch, target and remote.
Existing repositories start in the changes view; missing repositories use settings
for setup. The overview keeps a separate store context from the active editor so
opening a different repository's file cannot replace the session's change list.
Individual paths still open normal editor tabs in comparison mode. Remote address
changes run as agent requests. Addresses must be network URLs
without embedded credentials, query strings or fragments; authentication belongs on
the computer. The app never exposes credential-bearing stored addresses. Existing
sessions explicitly adopt a changed target through `PUT /coding/sessions/{id}/target`,
which checks the preview's URL fingerprint against the current repository. Local
sessions retain the same target guard. Project remote defaults change only when the
session uses that project's configured computer and repository. Init and remote
setup from a worktree draft first create a local session so setup can succeed before
the first remote fetch.

## Agent flow contract

Commands enter the existing AgentRuntime through the ordinary message path. The
canonical user message retains the short command. Host-resolved task instructions
and Git context are snapshotted in its private runtime metadata and projected only
as the selected command's model-facing task. Compaction and model-facing session
retrieval preserve this task text and context. This is not a new tool permission or
workflow engine. Mutation commands switch the built-in Plan selection to Build only
when the session allows that profile; custom profile and tool restrictions remain.
The private metadata key `codingCommand` is retained for compatibility with existing
histories and retries; it carries any prepared command contribution. New commands
also snapshot their command instructions plus arguments as `prompt`. The message API
exposes only that snapshot as `commandPrompt` for the user bubble; execution wrappers
and resolved runtime context remain private. The canonical message content retains
the original invocation for undo and retry. Older messages without a prompt snapshot
keep showing their invocation rather than rebuilding it from the current catalog.

The agent uses ordinary file, shell and available integration tools. Git credentials
and host CLIs such as `gh` belong on the selected computer. Missing PR authentication
or tooling is reported, not supplied by a new implicit integration. Project instructions
continue to govern commit conventions and checks. Review and publication safeguards
are agent instructions plus the existing tool authorization; they are not a Git sandbox
or a deterministic proof of review quality. The task explicitly requires non-force
pushes of the reviewed commit, re-review after target advancement, stopping on conflicts
or failed checks, and preservation of unrelated changes. Known active runs in the
original checkout/worktree prevent starting a publication/sync/init/remote command. Admission
is checked under the existing user-row lock until commit, includes nested directories,
and also blocks ordinary runs overlapping an active publication/sync command. External
processes can still change the repository and must be inspected by the agent.

Git subprocesses run in a separate process group/tree. Cancellation terminates the
tree and bounds pipe cleanup; a dispatched preparation with a lost response,
cancellation or transport timeout has an unknown outcome and must be inspected.

The existing changed-files view compares HEAD with the working tree. It is not a
publication indicator: the agent must also inspect committed differences against the
target. Publication URLs and results remain canonical chat messages, and repeat PR
commands inspect the host for an existing request instead of blindly creating one.
