# Commands and managed Git worktrees

## Shared command system

`sessions/commands.py` owns the host-registered command catalog, parsing and dispatch.
Providers register one `CommandProvider` from `sessions/command_contracts.py` with a
unique source, one or more commands, an availability callback and a preparation
callback. Command names use lowercase letters, digits and hyphens. The parser maps
underscores to hyphens in invocations without changing arguments. Duplicate
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

Session templates contain no Git configuration. Before session creation, the **Arbeitsbereich**
field above the composer offers **Lokal**, **Neuer Worktree**, and registered project worktrees.
New project sessions can override the project default before creation. Worktrees
require the configured project computer and repository. Without a workspace the
field shows **Nicht verfügbar**; a selected offline computer does not change an
existing selection. A worktree is an ordinary session working directory, not a session
lock or ownership relation. Sessions can change computer or directory through normal
settings and tools, including leaving a missing worktree. The Git app offers registered
worktrees as directory choices. Switching preserves the conversation and leaves all files
in their original directory. Template worktree support gates creation and selection,
not later template changes, directory changes or session deletion.
Existing-session management follows the session's computer and Git inventory, even
after project defaults change. **Lokal** is Git's main checkout; new creation still
uses the configured project computer and repository. Initial creation rejects a
different selected directory instead of silently replacing it.
The Git app displays the workspace path with a copy action. Opening it in a browser
draft does not create a session or prepare a worktree.

Choosing worktree mode explicitly authorizes preparation on the selected computer.
Session creation in worktree mode creates a local branch named `worktree-<eight-hex-id>`.
An occupied destination or branch selects another short ID, with at most ten candidates.
Candidates derive from the independent creation UUID so repeated requests authorize
the same paths; the Agent records its choice before preparation. It never adopts or
overwrites an occupied destination or branch. Existing worktrees retain their names.
Preparation uses the committed local target branch without fetching,
regardless of remote setup. A missing or unborn local target is rejected.
It freezes that base commit and checks it out under
`<agent-data>/worktrees/<project-id8>/<eight-hex-id>`, using the same worktree ID
as the branch. The project folder uses the first eight hex digits of the project UUID;
collisions or unrelated existing directories select `-2`, `-3`, and so on. A private
`.matoloop-project` marker identifies the full project UUID so later worktrees reuse
the folder, independently of session selection or project names. Repository inspection
proposes the folder without creating it; preparation revalidates it under its lock.
The project prefix only groups newly created directories; it does not
restrict Git discovery or workspace selection to that project. Existing worktrees keep
their recorded paths and branches, including flat short paths and the original
`<project-id>/<session-id>` layout. No migration or renaming is performed. The backend
checks each exact candidate's filesystem policy before preparation. New creation uses
only the short-project/short-worktree layout and requires explicit permitted destinations.
Update the Backend and Agent together: historical creation layouts, remote-based
preparation and receipt-based removal protocols are no longer supported. Existing
worktrees remain selectable and removable through Git inventory without preparation.
Creation and switching use the normal working-directory selection and navigation grant.
Only the concrete worktree is selected and granted, never its project parent or the
Agent data directory. Existing action grants are not copied to the new path. Normal
computer filesystem and shell policies still apply, including external-path denials.
No model, file browser, or terminal can use the session before this preparation succeeds.
The selected project directory must be the repository root and the managed worktree
directory must be outside that repository. The Backend authorizes exact candidates;
the Agent validates the fixed layout and selects an unoccupied destination under its lock.
Hooks and checkout filters are disabled during automatic preparation.
Linked metadata descendants and alternate object stores are rejected rather than
following them beyond the authorized metadata directory.

The session persists its original repository, remote, target and selected worktree path.
Git is the source of truth for inventory and the current branch; no shared target registry
or database migration is needed. Target overrides remain session-scoped and apply only
to the matching computer and repository; entering another repository ignores the old
target. Git commands resolve worktree membership from Git, including when the current
directory is a subdirectory or the conversation was forked. Stored workspace modes
do not block ordinary session operations.
Repeated preparation
recovers only a completed matching worktree and does not reset existing work.
The Agent keeps a private preparation record with the repository metadata identity,
target and base commit. A process-shared lock and the filesystem mutation lock protect preparation.
New worktrees use an independent creation identity in the existing preparation record;
the legacy `sessionId` wire field carries that key. Short-path recovery checks its marker.
Changed inputs, replacement repositories
and incomplete registered checkouts are rejected; they require explicit inspection.
An existing short branch without a completed registered worktree requires inspection;
it is never adopted after an uncertain checkout failure.
Requests with a run ID derive a stable session ID so retries can recover preparation.
Archiving and session deletion retain worktrees. The Git app's compact status rows open
separate workspace, branch and remote dialogs. The workspace dialog removes any registered
non-main worktree without selecting it first, after explicit deletion consent, preserving
branches and conversations. Pending, untracked and ignored files are lost on confirmed
forced removal. The original checkout, locked worktrees, detached HEADs and overlapping
active runs block removal. Before removing a worktree used by sessions, the backend
validates the local project checkout and prepares normal directory grants. Affected
sessions, including those in its subdirectories, then return to that local checkout.
Session deletion has no worktree-removal option and needs no reachable Agent or external
working directory.

Workspace and branch dialogs use searchable compact lists: selecting a row only marks it
and reveals its full path/name in the detail area. Workspace rows show their parent directory
and use a location icon for the active workspace, without an active-color highlight.
Workspaces activate through their per-row menu, including the local checkout; branches
retain the shared switch button.
Session links and dirty status appear only for the marked workspace. Per-row menus retain
independent removal and current-branch rename actions. Occupied branches show their
worktree name and its full path in details, but cannot be switched to. Target-branch editing
is separate from branch switching. Display shortening does not rename branches or paths.

Opening the workspace dialog or explicitly refreshing its settings requests worktree
details. `git.changes` with `statusOnly` reports pending tracked and non-ignored untracked
changes without generating diffs or executing Git filters; submodule contents remain
excluded, as in the changes view. Failed or unsupported checks
remain unknown, never clean. The backend checks only inventory paths allowed by the
computer filesystem policy and derives session links from owner, computer and current
directory containment, including subdirectories. No session-worktree assignment is stored.
Session chips navigate to conversations; they do not select, reserve or own a worktree.

`git.repository` reports registered worktrees and local branches. `git.worktree` and
`git.worktree.remove` remain the creation/removal operations; `git.branch` supplies
local create/switch/rename/delete. Explicit branch creation starts at the current HEAD
without switching branches. The authenticated browser validates project, owner,
computer, paths and Git membership before dispatch. The session workspace endpoint
checks template availability and active runs. Branch switching refuses dirty trees and
does not overwrite ignored files. Removal of a branch requires it to be unused and
contained in the selected target. Deletion checks that target, not the branch's upstream,
and removes only the checked commit so a concurrently advanced branch is preserved.
Management status checks disable
Git filters just like change inspection. Published branches are not renamed when known through
tracking configuration or remote-tracking refs. No fetch is performed to discover them.
No automatic cleanup or synchronization of branch names across sessions is needed.

Repository inspection checks whether the selected local target contains a commit.
The new-worktree option is disabled for a missing local commit before preparation.

Local worktrees need no remote. Review remains available; publication, pull requests
and remote synchronization require explicit remote setup. Adding a remote later does
not reset the worktree or silently change its session target: adopt it in the Git app.

Worktrees share repository metadata and objects. The original repository must remain
accessible at its existing path. Docker deployments need durable mounts for both
locations. Ignored configuration, dependencies, processes, ports and databases are
not copied or isolated. See [parallel development worktrees](development.md#parallel-worktrees).

## Shared command entry points

The composer palette and Git app use the shared catalog. Telegram registers
command names with hyphens mapped to underscores in its native menu and `/help`.
Incoming Telegram names resolve through the shared parser. Additional channel
providers should project this catalog and preserve the normal authenticated session
selection and ingress semantics.

| Command | Task |
| --- | --- |
| `/git-init` | Initialize Git in the selected workspace without committing, adding a remote, or publishing. |
| `/git-review` | Review working changes and unpublished commits without editing or publishing. |
| `/git-commit` | Inspect and explicitly commit selected task changes without fetching or pushing. |
| `/git-merge` | Merge the local target into the worktree, review, check, and fast-forward the clean original checkout. No working-change commit, fetch or push. |
| `/git-push` | Publish the existing current branch without committing, fetching or merging. |
| `/git-pr` | Fetch and integrate the remote target, review, check, publish and create or update the PR. No working-change commit. |
| `/git-sync` | Fetch and fast-forward the clean original project checkout without switching branches or discarding work. |
| `/git-remote [name] [url]` | Configure only the explicitly requested remote through normal agent tools; ask for missing values. |

Merge and PR commands initially require a managed worktree. Review also works in a
local repository; sync requires a configured or unambiguous remote and target.
Review checks correctness and unnecessary complexity and reports only; it never repairs
project files. Temporary check files outside the project remain subject to normal permissions.
Merge and PR commands stop on conflicts, dirty trees, findings or failed checks without
repairing them. Necessary merge commits are authorized by the merge/PR request;
pending working changes are committed only by an explicit commit request.
Telegram users select an existing coding session with `/sessions` and `/use <ID>`;
messaging commands never create an unrelated empty session. Web submission can
start a new session using its explicit draft configuration. Existing command source,
run-ID deduplication, channel pairing, questions, approvals and outbox delivery apply.
A busy session does not silently queue a messaging publication command for later.
Coding commands are rejected as in-run continuations. Retry revalidates the saved
executor, directory and command context before dispatching a new run.
Saved command names with underscores remain readable for retries and workspace
admission; new preparations and retries store canonical hyphenated names.
Opening or selecting in the web palette never starts an agent or creates a session.

The Git app has two views: **Änderungen** for changes and agent requests and **Einstellungen**
for workspace selection/removal, branch management, the session target and remote configuration.
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
