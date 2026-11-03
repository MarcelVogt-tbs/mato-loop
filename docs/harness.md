# Agent runtime reference

This reference owns run lifecycle, execution and context contracts. Start with [architecture.md](architecture.md) for the system map. Action declarations and reusable consent are defined in [tool-permissions.md](tool-permissions.md); integration-specific binding and policy live in [integrations.md](integrations.md).

## Runtime and Coordinator

The backend runs **one worker** with one process-local run/approval coordinator. Persistent run records are not resumable Python checkpoints or a distributed work queue. A run and its live approval waiters continue only in the process that started them.

- On backend restart, active/waiting runs end as interrupted or failed and pending agent-action approvals expire. An approval without a live waiter returns `approval.runInactive` and creates no grant.
- Fully persisted session grants survive restart. Independent `memory_change` approvals survive and resolve transactionally without a live run.
- Approvals and questions have no inactivity timeout. Browser/SSE disconnect does not decide them or cancel the run.
- Explicit cancellation interrupts providers, child work and dispatched operations best effort, persists visible partial text and terminal tool results, and expires pending run interactions. Repeated cancellation must not interrupt cleanup again.
- Completed effects remain; late file/process effects may still finish. Neither restart nor uncertain dispatch permits automatic replay.

The [run-continuation contract](run-continuations.md) defines direct input, pending-interaction replacement and provider replay. Title generation is independent, tool-free and must not block the agent. Manual naming wins over a pending generated title.

`BackgroundProcessing` shares lifecycle and error handling for the message queue,
automations and Dreaming. Each processor has an independent loop and database sessions;
long-running Dreaming work cannot hold up message delivery. Their existing service and
persistence contracts remain separate; this is not a new job framework.

### Message queue

The browser submits ordinary messages through the existing message endpoints with
`enqueue: true`. Without this opt-in, API and messaging callers retain direct admission.
Pending requests and upload bytes persist separately from canonical messages and active runs;
they are not model context. The queue is independent of task type and profile and has no
scheduled delivery times, workflow phases or completion tools.

Per-session identity positions define FIFO order. Delivery uses ordinary owner/session locks
and `AgentRuntime.start()`: consuming an entry and creating its message/run commit together.
The client request UUID deduplicates pending requests and admitted runs. Owner-scoped
change events and initial/reconnect loads reconcile the existing queue UI; IndexedDB remains only the
pre-acknowledgment/offline buffer, including legacy entries and their uploads. Accepted
requests continue without an open browser. Logout clears the local buffer, not accepted
backend requests. Session deletion cascades pending requests and uploads.

Active runs, approvals and questions delay the next delivery. Admission conflicts wait;
other admission failures pause the head entry. Changing the selected computer/directory
requires restoring and resending that entry. An optional predecessor ID requires a completed
run in the same session; failed/cancelled predecessors pause dependent requests rather than
authorizing their effects. Resuming does not bypass this check. Removing a pending request
also removes its pending dependent follow-up; removal loses the race once a run exists.
The frontend uses the optional `after_run_id` message field for an explicit opt-in
[`/git-finish` follow-up](commands.md#agent-flow-contract), saving both messages together
locally before sending either. The backend generates no Git follow-ups. Queued commands
capture their Git binding on admission and revalidate it at dispatch.
Session admissions run independently, including messages arriving while another admission
waits on a computer. At most one admission per session is in flight; shared background
shutdown cancels these tasks together with the processors. Ordinary database locks remain
the authority for admission and FIFO order.

## Profile Budgets

`maxToolCalls` accepts a strict positive integer or `null` for unlimited. Booleans, zero, strings and fractions are invalid. A normal run freezes the resolved limit in its input message's private `_matoloop.maxToolCallsByRun`, keyed by run UUID. Retry adds its current limit without rewriting the failed run's snapshot; legacy single-value snapshots remain readable.

| Shipped profile | Calls per run | Integrations available by default |
| --- | --- | --- |
| Allgemein | 25 | Yes |
| Plan | Unlimited | No |
| Build | Unlimited | No |
| Sysadmin | Unlimited | No |

Every attempted call counts, including failed, invalid, denied and research calls. A batch exceeding the remaining budget is rejected in full before handlers execute. Exhaustion retains the previous still-available schemas in order, uses `tool_choice=auto` for transport compatibility, and permits one tool-free final response. The host independently rejects execution despite that transport option. A valid partial result records `agent.toolCallLimitReached` and unfinished work; simulated executable-call envelopes are not accepted as successful final text.

Children inherit the parent's frozen limit and count independently, not against a shared remaining balance. Unlimited calls do not disable request/tool timeouts, context bounds, cancellation, bounded repair, repeated-failure protection or child limits. There is no global monetary or elapsed-time ceiling; request/research counts remain metrics.

Unchanged failed calls are keyed by tool, canonical arguments and relevant computer context. One correction opportunity is allowed without repeating the effect; another unchanged failure terminates unless budget finalization applies. A successful mutation may change preconditions; an unrelated read does not clear failures. Truncated call batches are rejected before effects. Repeating successful research returns compact `alreadyCompleted` evidence without network access and removes research tools from the next request.

## Native Subagents

Plan and Build expose `task` with one to four scoped assignments per call. At most four children may be active per main session, including queued work and approval/input waits. Completed, failed and cancelled runs free their slots; there is no lifetime spawn limit. A batch needing more free slots is rejected before any task starts or receives a correction, with the available slot count. A child cannot delegate. Failure of one child does not discard successful siblings.

Children are ordinary persisted, owner-bound sessions with immutable parent/run/call provenance. Child numbers increase within each parent run, including when a provider reuses a tool-call ID. They inherit project, model, effective trust, reasoning, computer and directory. They do not copy the conversation or automatic Memory/history retrieval. Task instructions define their deliverable; the parent's main-specific workflow is not injected. Each child owns its database session.

Omit `session_id` to create a child. Supply a returned `sessionId` to reuse a direct child of the same owned main session, including children created by earlier parent runs. A finished child starts another run with its conversation and existing model/profile/computer binding. An active child receives a persisted parent-agent instruction through the direct-message mechanism, never a second concurrent run. Delivery confirmation is not task completion. Follow-up runs intersect current offered/spawn-time authority with the child's retained ceilings; their source run/call is recorded without changing original spawn provenance.

Normally `task` joins its children. A direct user message interrupts that wait and expires pending child approvals/questions. Those children skip the superseded tool batch and pause in the same run until the parent sends a correction with `task(session_id=…)` or explicitly stops them with `stop=true`. Task results identify them with `awaitingInstruction`; other children continue. A stop requires a direct child session belonging to the current parent run and does not start another run. Outstanding results are appended as persisted, host-origin evidence, never substituted into an earlier tool response. An intermediate answer does not finish the parent run: it waits again for results or another direct message. Stops and failures cancel and join outstanding children. See [run continuations](run-continuations.md).

**Authority cannot widen:** the frozen ceiling intersects offered and spawn-time profile/integration authority, including concrete enabled integration UUIDs. Identical saved boundaries are deduplicated; distinct restrictions remain binding. Every effect also checks stricter live policy. New connections or newly enabled tools do not expand an existing child's authority. Instructions such as “do not edit” narrow the task but do not create an OS sandbox. Corrections cannot undo completed effects and may wait for an active tool call to finish.

Children share the workspace. They provision internal storage, but their model file tools are computer-only; media/artifact creation and skill authoring tools are filtered out. Normal revision/dirty-file guards apply. Assign non-overlapping changes; a separate child session is not file isolation.

Child approvals/questions belong to the main session and executing child run. Only the child waits; run consent remains child-local, while session consent uses the existing profile/trust/resource namespace. The inspector is read-only; independent sends, retries, settings and mutations fail with `session.readOnly`. Cancellation joins child startup/cleanup, expires requests and preserves completed effects. Parent history deletion cleans dependent sessions/storage. Children are excluded from ordinary lists, global search, episode indexing and Dreaming.

Undo and failed-turn retry also remove the reused-child history suffix beginning with an instruction from the removed parent run. Earlier child history remains; completed file or external effects are not reverted.

Results contain bounded text and exact session/run/message references. Deferred results share the context output budget and retain retrieval references when omitted. Failed/budget-limited results may include bounded recent useful evidence; excerpts remain untrusted, not proof of success. Parent tools can inspect authorized child transcripts.

## Tool Contract

A host registry owns unique tool names, concise descriptions, typed Pydantic inputs, categorized actions and async handlers. It rejects duplicate names and validates arguments before handlers. Unknown/unoffered calls and execution failures become stable errors; exception text and credentials never enter API/model output. Model-provided reasons are optional display metadata, not authorization.

Tool validation errors identify the affected field and a controlled `error.params.reason`, with expected types, bounds or host-defined correction hints when available. Custom argument rules, post-resolution checks and execution-agent errors retain these details instead of collapsing to an unexplained `tool.invalidArguments`. Patch syntax errors include a 1-based patch line when known; exact, unique context matching remains required. Rejected values, raw validation messages and credential contents are never included. Built-in tools and dynamic MCP schemas use the same error details, including nested settings validation. Remote HTTP failures retain the status code, distinguish remote request rejection from local argument validation, and never expose response bodies. MCP `isError` results retain their original content and are recorded as `mcp.callFailed`, not successful tool calls. An error does not prove that a remote operation had no effects; unknown outcomes still require inspection before retrying.

File mutation inputs follow OpenCode's compact contract: `edit_file` takes `path`, `oldString`, `newString` and optional `replaceAll`; `write_file` takes `path` and complete `content`; `apply_patch` takes `patchText`. Edit/write default to `root=computer`; explicit `root=files`, protected text and administrator options remain available. Parent directories must exist. Writing creates or overwrites; use exact-text editing for partial changes. Models do not supply revision hashes. Changes apply to the current tool-execution state, not a whole-file revision retained from an earlier model read. The Agent retains its mutation lock and publication-time revision checks for edits/patches; external changes can still race, and a multi-file patch can partially apply. Dirty active files, no-follow boundaries and all permission checks remain binding. Direct user editor/API saves still require their expected revision.

`delete_path(elevated=true)` uses the existing administrator authentication and separate `filesystem.delete` authorization for one computer path. Its approval shows whether deletion is recursive; the structured administrator worker calls the normal no-follow filesystem deletion, not a shell command. The Agent must support this administrator operation. Deletion is irreversible and recursive deletion can partially complete before an error or interruption. Ordinary deletion remains the default; known OS permission failures may justify explicit elevation, but do not authorize it.

Native Linux file operations do not traverse symbolic links, including common `/sys/class` paths. Blocked traversal reports `file.symlinkForbidden`, not a missing directory; administrator execution retains the same restriction. Regular virtual files can be read without relying on a nonzero reported size. System diagnostics beyond native file support may use shell execution only under its independent permissions and target checks, never to override denied consent or hard policy boundaries.

Repair presentation is host-only metadata. Argument/batch repairs, safe edit-precondition failures, supported transient retries and required-workflow repairs stay neutral while the run continues. A successful same-tool/same-target call marks its pending repair recovered; earlier attempts in that repair chain are superseded. Unresolved repairs become visible when the run ends. Denials, user questions and uncertain/partial effects remain visible immediately. Canonical calls/results and model projection are unchanged by repair presentation; a successful unrelated tool does not hide a pending failure.

One immutable effective tool set belongs to each model request. Schema export, argument validation and execution use that set. Availability depends on profile, session, model capabilities, integrations and advertised operations. Inert catalog hints for unavailable capabilities cannot execute or create approvals. Stable registry order favors prefix caching; enforcement may narrow the offer. For deferred integration schemas, see [tool loading](integrations.md#effective-tool-set).

Every operation authorizes its actual effects/resources before access or mutation. Host metadata preflight may discover read-only resource identity/ACL information without releasing content to the model. `Context.ask` applies hard boundaries, concrete policy and matching grants before persisting an approval and waiting. The approval transaction records an allowed scope before resuming. After approval, policy, binding, resource and revision state are revalidated. Denial stops the dependent operation; a copy denied at source does not ask for destination access.

Group names, UI labels, external descriptions and model arguments never grant authority. Tool handlers use host context and services rather than coordinator internals or provider SDK objects. [tool-permissions.md](tool-permissions.md) defines the exact defaults, overrides and declaration rules.

### Profiles and workflow

Core profiles have protected IDs/names and workflow definitions. Administrators may override access settings; custom profiles inherit one core base, not another custom profile. Sparse absence inherits; explicit null/false/empty values replace where valid. Global disablement remains binding. Disabling personal customization retains stored values but ignores their effect. Integration permissions remain on connections, independently of these gates.

Plan asks for recognized file, shell and session-file writes/deletes, but its instructions still forbid implementation. Its analysis temp directory is not permission to change a project. Build implements only an actual user request, not merely a profile switch or approval. Sysadmin inspects state and requests authorization for changes. All three automatically allow ordinary computer reads, even for public models. Plan/Build ask for file access outside the workspace; Sysadmin allows that area. Shell is workspace-independent.

Plan and Build offer `secret_input` by default for [protected credential input](questions.md). Explicit tool restrictions still apply; input grants no action rights.

Plan and Build share a provider-neutral response contract: answer or outcome first, short answers to questions, normally 3-7 plan steps, and implementation summaries limited to results, relevant checks and blockers. Settled decisions, work narration and code already written to files are not repeated unless requested. Explicit requests for detail and necessary risk, failure or verification information take precedence over length targets; execution and verification remain unchanged. Derived profiles inherit this guidance unless they replace `instructions`.

`delegationEnabled`, `sessionWritePaths`, repository instruction loading and temporary-file access are inherited workflow fields. Shipped Plan/Build enable delegation; all shipped profiles have no additional managed write-path restriction (`sessionWritePaths=null`). A custom non-null list restricts managed writes to those normalized relative paths; `[]` denies them. Models and reasoning are separately resolved through [defaults/templates](session-templates.md).

The optional checklist is message-backed reported progress, not authority or independent verification. Successful snapshots replace earlier state chronologically. Compaction seeds only the covered checklist checkpoint; later results supersede it, and current checklist output is not silently shortened.

### Repository instructions

Plan/Build enable automatic `AGENTS.md` discovery. It reads only authorized regular files on the selected owner-bound computer, within the current-directory/verified Git boundary and relevant read/change targets. It never follows symlinks or creates speculative approvals. Navigation, shell or write grants do not authorize instruction reads.

A bounded process-local cache reuses historical target-to-directory discovery, isolated by owner, session, computer and working directory. Current tool targets are always inspected afresh. Before effects, instruction discovery includes the historical directory set and uses the model snapshot's aggregate file budget, so unchanged size omissions do not defer tools. Git boundaries and candidate instruction files, including previously missing files, are checked again with current read permissions; cached historical targets need no additional classification requests. Instruction contents and permission decisions are not cached. External changes to a historical target's file/directory kind are rediscovered when that path is targeted again or its discovery entry is evicted.

Discovery is bounded to 64 directories, 32 KiB per file and 64 KiB rendered contents. Whole files are fitted to remaining request budget, never partly applied. Missing permissions or budget produce an incompleteness notice. New/changed applicable instructions defer a mutation and the remaining batch until the next model request includes them; unchanged budget omissions do not repeatedly defer. More specific rules override broader ones below their directory but cannot override host rules, profile limits or the latest user request.

Summaries preserve decisions and remaining work, not authorization inferred from old plans or retrieved data. Profile reminder placement follows the [prompt construction contract](#prompt-construction-and-cache-stability).

## Prompt construction and cache stability

Preserve the longest **unchanged, still-authorized** provider prefix, not a permanently frozen prompt. Logical assembly is shared; adapters own protocol-specific message/schema encoding.

### Assembly order

1. **System context:** host/security and applicable tool contracts, identity/persona/profile authority and maintained user/project context precede selected-computer data, active skills and workflow state. Project context includes the configured computer ID and directory, separately from the session's current directory or worktree; context alone does not authorize access. Budget-fitted current automation definitions and repository instructions append to this system message. These changing sections can invalidate later history; they are not an immutable prefix.
2. **Retained-context boundary:** optional compaction summary, covered checklist checkpoint and eligible tool snapshots precede retained history. The checkpoint advances with compaction, not each new user message.
3. **Chronological turns:** Memory envelopes precede their originating user turns; persisted message timestamps, profile reminders and workspace-change reminders stay with those turns. Only the latest profile reminder defines the current workflow. Calls/results remain paired, with eligible reasoning replay and ephemeral images anchored to their source exchange. A fixed initialization marker precedes first-turn Memory when needed to preserve the provider checkpoint.
4. **Current budget reminder:** remaining-call/exhaustion state appends as host-marked runtime context after history, before request estimation. It is not a new user task or the boundary used to shorten current tool evidence.

The implementation owners are `agent/prompts.py` (system parts/reminders), `agent/projection.py` and `agent/context.py` (turns, checkpoints, fitting), `agent/turn_context.py` (optional context/preparation), and `agent/run_support.py` (ephemeral images).

### Stability and isolation rules

- **Do not rewrite earlier turns from current state.** Profile instructions use persisted `agentProfileInstructions`; workspace-change reminders use the originating turn's `workspaceContext` snapshot, refreshed for the latest turn on retry, never for earlier turns. Memory envelopes use their own turn snapshots and live source revalidation, as defined in [memory.md](memory.md#automatic-context). Invalidated context is omitted, never retained merely for caching. Compacted image sources may fall back to the request tail; reasoning replay requires its exact provider/model/credential/endpoint/trust/privacy scope.
- **Freeze retrieved session summaries with their turn.** Dreaming may update the current snapshot, but that does not replace cached tool-round context or historical Memory envelopes. History invalidation changes the source revision; summary regeneration alone does not. Summary reading hints stay in the turn-local envelope, and disabled-summary requests keep the existing prompt contract unchanged. [Session-summary tests](../apps/backend/tests/test_session_summaries.py) compare Chat Completions and Responses prefixes across tool rounds and database-loaded follow-ups.
- **Compare serialized input, not just object equality.** Chat Completions and Responses encode tool arguments with sorted JSON keys, including nested objects, so JSONB roundtrips do not change equivalent argument strings. Preserve semantic array/tool order and omit host-only projection fields from provider payloads.
- **Avoid unnecessary schema churn.** Plan/Build share focused availability defaults. Discoveries belong to the session, not the selected profile; current profile availability and delegation limits still restrict them. Stable eligible tools retain registry/discovery order; required search, resource policy and availability still restrict offers. Budget exhaustion retains previously offered still-available schemas with `tool_choice=auto`; the executor independently prohibits effects. Schemas in a server's chat template may precede history, so legitimate new discoveries can invalidate its prefix.
  Adding explicit administrator options to `delete_path` and its shared file guidance intentionally changes the stable prefix for requests offering these contracts; subsequent rounds retain the same schemas and guidance.
- **Append delegated updates.** Parent-agent instructions and deferred child results retain their originating messages on replay. Changing the stable delegation contract/schema intentionally invalidates older prefixes; follow-up instructions and results do not rewrite retained exchanges.
  The `task` stop field and paused-child contract intentionally invalidate prefixes from earlier versions. Corrections still append messages and preserve the same active child run and retained history.
- **Reuse preparation, not authority.** The latest identical prepared request may serve estimation and completion within its model turn; changed inputs/bindings require preparation again. The process-local privacy cache has a 64 MiB accounted-byte limit and owner/session-specific marker keys. It can reuse exact-text scans across runs, but every request has its own restoration map. Cache entries, including originals, stay only in memory until eviction/restart, never logs or persistence; rotation of the credential key changes marker identities. Authorization and source visibility remain live.
- **Keep routing adapter-local.** The session UUID supplies `prompt_cache_key` where supported. Codex also sends that same value as `session-id`; other adapters do not receive this Codex-specific header. Its first successful `x-codex-turn-state` response header is replayed unchanged across that user turn's requests and retries. New user messages, run restarts and changed provider/session/model/tier/auth/trust/privacy bindings discard it. This opaque token is process-local and never persisted or logged. Routing hints do not guarantee cache retention or isolation by the provider.
- **Append supported reasoning changes.** Codex models explicitly advertising `supports_reasoning_effort_updates` keep the initial request-level effort and append positional `configuration_update` items. Host-authored base/effective effort snapshots are persisted with assistant messages and reconstructed after JSONB roundtrips. They contain no opaque reasoning, also work with privacy enabled, and require the same provider/model/credential/endpoint/trust/privacy scope plus resolved API model and service tier. A changed binding or compacted-away lineage starts a new baseline; older turns are not rewritten. Unsupported models retain request-level effort changes. Refresh model discovery to obtain this capability; it is not inferred from model names.

`contextPreparation` in model performance diagnostics separates settings/history, Memory, tool selection, PDF preparation and context projection. Projection includes request preparation and repository-instruction checks; these nested durations must not be summed as exclusive stages. Preparation counts distinguish full transformations from exact-input reuse. Empty optional repository context does not trigger another transformation or alter the transmitted prompt. File/source revalidation and budget checks remain live. Output-token replay, Responses Lite, extended cache TTL, prewarming and WebSocket transport are not enabled by these mechanisms.

Regression tests in normal backend CI protect [serialized prefixes across tool rounds and live-to-persisted follow-ups](../apps/backend/tests/test_context_manager_contract.py), [argument serialization](../apps/backend/tests/test_provider_conversion.py), [reasoning scope](../apps/backend/tests/test_provider_reasoning.py), [preparation and adapter-local routing](../apps/backend/tests/test_provider_service.py), [privacy scan reuse and isolation](../apps/backend/tests/test_pseudonymization.py), [cross-run session keys and budget finalization](../apps/backend/tests/test_runtime_budget.py), and [repository-discovery request counts and live permission checks](../apps/backend/tests/test_execution_tools.py).

Run affected tests when changing these boundaries. Under unchanged authorization and context constraints, compare the retained serialized prefix and schemas without normalizing away differences; intentional invalidation needs an explicit reason. Prefer deterministic prefix and scan/request-count assertions to provider latency or cache-hit thresholds. The [dated session-cache evaluation](session-cache-evaluation-2026-10.md) records the live serialization regression and measured fix, not current performance guarantees or a new validation run.

Host/profile prompt changes intentionally invalidate older prefixes. Automatic follow-ups
append ordinary turns with host provenance. Workspace occupancy is not injected or inferred
from stored run statuses or file timestamps. Legacy private occupancy metadata is ignored,
intentionally removing that projection from retained prefixes. A stable instruction requires
preserving concurrent changes, checking current files and waiting on tool-confirmed temporary
blockers; it grants no access to other conversations.

## Typed Resource Matchers

Permission matching uses validated typed identities, never labels or free-form string prefixes. [Reusable approvals](tool-permissions.md#reusable-approvals) is the authoritative category/scope reference.

| Resource | Identity and boundary |
| --- | --- |
| Managed file | `session_path`: root, owner-bound session identity, relative POSIX path |
| Computer file | `execution_path`: computer UUID and normalized absolute path |
| Elevated file | `administrator_path`: exact computer/path |
| Navigation | `execution.directory`: computer and authorized directory tree; no file/shell rights |
| Normal shell | Exact command/computer/directory/environment scope; explicit option may choose computer-wide consent |
| Integration | Connection UUID, canonical binding revision and stable resource key |
| Session | Exact eligible owned session UUID; browse/search use a separate collection scope |
| URL | Hash of complete canonical URL; redirects authorize separately |

Verified ordinary files use exact scopes; directories/unknown types explicitly include descendants. A direct parent may be offered as a broader file scope. Sensitive, elevated and exact-action restrictions cannot be widened this way. Write includes ordinary read on the same scope/lifetime; delete and sensitive read are independent. Disabled policy wins over grants. File, shell and navigation grants do not substitute for one another.

`allow_once` is run-bound consent, shown as **Für diesen Lauf erlauben**. `allow_session` reuses the same scope across runs until revocation. Non-reusable sensitive actions bind exact arguments and reviewed revisions. Consent with edited arguments uses their final validated values. Display names do not participate in matching.

### Managed files

`root=files` exposes the session-managed store; `root=computer` selects ordinary paths on the bound Agent. Internal permission names are `session` and `workspace`. Managed paths reject absolute paths/parent traversal. Another session's files require the same owner and current project membership.

Physical storage is `/data/sessions/<session-id>/{files,revisions}` or `/data/projects/<project-id>/sessions/<session-id>/{files,revisions}`. Session moves retain identity and complete history; foreign references block unsafe move/deletion. Computer bytes are not managed revisions. Cross-root copying uses bounded, hash-verified transfers, never an implicit move/synchronization.

Uploads, persistent companions and durable tool outputs share the revision store. A run writes current bytes visible to tools/users, locks affected files against competing changes, and snapshots one final revision per changed file on completion, failure, cancellation or recovery. Unchanged bytes create no revision. Idempotent per-file receipts support final publication. Output descriptors remain private until terminal publication; partial publication must not expose pending attachments as complete.

Deleting a managed file irreversibly purges all revisions and explicitly owned companions. Activation of a retained revision changes current bytes without deleting history. User historical viewers use exact retained bytes; dependency-based OpenSCAD history is source-only. Model reads use current content, not historical bytes. Dirty active-file metadata blocks matching model writes; browser/API saves retain expected-SHA conflict checks.

The optional per-user quota counts current files, revisions and reserved final snapshots across owned sessions. Lowering it preserves existing reads/deletions but blocks growth. Computer files and relational messages are outside this quota. Preview/format/provider limits are independent of the 1 GiB transfer/upload ceiling; processing may consume more RAM than input size.

`provide_file` publishes an owner-bound reference to original storage, not an implicit import. Tool result attachments are already shown with download controls; the model must not provide them again in the same response. Provision has its own policy and path grants; confidential filenames alone do not invoke the normal sensitive-read prompt. Hard computer zones, ownership and disabled provision still apply. Computer references retain their original computer even after session switches. ZIP downloads reject unsafe/incomplete trees, bound both source and archive output to 1 GiB, and do not promise an atomic whole-tree snapshot.

### Computer execution

Each Agent has home/external filesystem policies (`auto`/`ask`/`deny`), initially auto/ask, plus hard shell-zone switches. Directory choice creates navigation rights, not action rights. File access outside the working directory additionally checks `working_directory.external`. Shell does not use that rule; all inspected target zones must permit it, and incomplete analysis additionally needs the external zone. Ownership, no-follow/no-reparse and sensitive-data boundaries remain.

Linux shell inspection parses before authorization and returns every recognized `requiredCategories` entry. Removal/clearing needs delete; ordinary overwriting is write. Mixed commands need all categories. Known nested shell/container/xargs forms and direct SSH remote commands are inspected within bounds; unresolved operands remain incomplete, not a sandbox. Container and remote paths cannot be verified as host paths. SSH inspection joins remote arguments as SSH does, checks explicit `-i` identity paths locally, stays incomplete and emits no reusable command prefix. Unsupported SSH options, dynamic wrappers and explicit command/forwarding configuration retain write classification; implicit SSH configuration, stdin scripts and custom wrapper scripts are not analyzed. Unknown programs may remain `shell.read`, even with incomplete analysis: this is the accepted [shell tradeoff](architecture.md#matoloop-agent-boundary).

Invalid syntax returns `process.invalidSyntax`; command/enumeration/target limits fail with `process.inspectionLimitExceeded`. Metadata serialization caps may mark extraction incomplete but never skip later recognized mutations or hard paths. Missing required targets retain absolute identities and mark incompleteness; access/link failures remain hard failures. Unsupported ANSI-C path quoting is rejected rather than guessed. Inspection revisions are rechecked immediately before execution; changes require fresh preparation.

Verified real `/dev/null` output and narrowly bounded installed-tool metadata may retain non-mutating classification. Input use and `<>` are not exempt. These exceptions do not grant access or bypass zone/revision/sensitive-data checks. Optional read/write token prefixes cannot authorize delete. Explicit-environment and elevated commands retain exact command/context consent without the computer-wide option.

Each `shell_run` starts a fresh process in the selected directory. `cd`, variables and aliases are call-local; `change_directory` updates persistent session/run state. Personal interactive terminal state never supplies model state. Startup binds the directory through an opened descriptor; executable path validation is not atomic descriptor-bound exec. `timeout` is optional seconds, `0 < timeout <= 3600`; omission uses local default 60, bounded by local maximum 3600. Failure/timeout returns controlled status and bounded output, not automatic retry. Cancellation terminates the process group best effort; effects remain.

Elevated operations dispatch through the dedicated `administrator.run` path with independent authorization and reinspection. Password input supplies authentication only and never appears in tool arguments/logs. Process output is filtered for injected values and recognized authentication headers before live or canonical publication; transformed secrets are not contained by textual redaction.

An Agent's session scratch subtree is readable by default; `temporaryFilesEnabled` adds writes, not deletion. Plan/Build/Sysadmin enable it. The model is instructed to use it for temporary scripts, logs and intermediate files, including explicit program output paths, unless the user explicitly requests another location; normal permissions still apply. Shell calls can prepare the directory automatically; the model must reuse it when present and confirm it is missing before creating it with authorization. It does not grant other scratch/worktree access or override computer/tool/delegation limits. Older Agents without advertised support get no implicit scratch grant. New required result fields use the general `executionAgent.updateRequired` error; compatible older operations remain usable, without requiring exact version equality.

## History and Request Context

Canonical messages remain complete in PostgreSQL. Each provider request uses a transient projection containing host instructions/persona/profile/project context, the selected summary/checklist checkpoint, authorized Memory, retained history and current tool exchanges. Tool/result pairs remain intact, and the latest user request remains available.

Memory visibility and per-turn envelope replay are defined in [memory.md](memory.md). Automatic snippets can be dropped to fit; all visible pins must fit intact or the request fails safely. Effective model selection releases already-authorized current-session content; pseudonymization remains separate.

Tool output may be shortened or replaced with exact `session_tool_result_read` references without changing canonical content. That reader accepts only current-session tool messages and fixed pages of at most 12,000 characters, reapplies current visibility/protection, and never executes the original tool. Readers of other sessions retain their own exact session authorization. Search discovery consent does not authorize reading a discovered session.

Large Webfetch content first uses a 15,000-character head/tail window plus provenance and a retrieval reference. Later fitting shares available space among current outputs; metadata, exit status and truncation remain explicit. Structured envelopes stay valid, but shortened JSON/CSV/XML/NDJSON inside a content string need not be a complete parseable source. Checklist output and credential handling retain their separate protection.

Opt-in account/resource snapshots may reuse bounded fields from authorized canonical results after compaction. They retain source, observation time, binding and effective policy; directory snapshots expire after 30 minutes. Each is at most 2,000 serialized characters, all at most 8,000 and 5% of usable input. Oversized snapshots are skipped, never silently truncated, and stale/disabled resources grant no authority.

## Compaction

Compaction persists a marked summary of a completed contiguous prefix. It never modifies canonical messages, splits a tool batch, covers pending work or replays effects. It retains the latest user request and at least two complete current-turn batches. A covered latest request is restored only as a projection anchor, not repeatedly summarized as new history.

Summary input includes chat goals, decisions and open work plus allowlisted operational metadata and exact evidence references, not arbitrary full tool output, arguments or reasoning. Up to 6,000 characters of tool evidence and a separate 6,000 of already-authorized `session_read` chat excerpts may be supplied. A deterministic evidence block of up to 2,000 characters, with trust/coverage markers and retrieval anchors, is appended. Successful tool execution is not proof of task success.

The summary model falls back to the active model and then a marked deterministic summary. One 20-second deadline, or a shorter configured timeout, covers model attempts/retries. Expiry cancels provider work; external cancellation still propagates. Required context that still cannot fit fails safely. The transcript exposes fallback status and persisted summary generations.

## Model Context Settings

`contextWindowTokens` is capacity, with a configuration minimum of two tokens, not a guarantee a request can fit. Discovered models follow endpoint-reported context unless manually overridden. Codex prefers its allowed maximum; a local server's configured context takes precedence over theoretical capacity. Without metadata the fallback is 128,000. Explicit synchronization refreshes provider-following values; deployment alone does not rewrite them.

`maxOutputTokens=null` means Auto; a strict positive integer is a manual ceiling, not a reservation. Context changes do not clamp or rewrite it. `discoveredMaxOutputTokens` is a nullable authoritative provider cap; currently only OpenRouter's documented `top_provider.max_completion_tokens` supplies one. Names, context size and generation defaults are not evidence of a cap.

Let `C` be context, `S` fixed safety (default 4,096), `M` optional manual ceiling and `H` optional known provider cap. Omit absent limits from minima:

```text
R = min(2048, M, H)
raw_input_budget(capacity) = capacity * 10 // 11
input_budget = raw_input_budget(max(0, C - S - R))
trigger_budget = min(input_budget,
    raw_input_budget(max(0, floor(C * compaction_trigger_ratio) - S - R)))
```

`R` reserves a useful generation turn, not the entire output ceiling. The inverse `10/11` leaves room for a single later 10% increase. All framing, tools, media, Memory and history count. A smaller selected model can require compaction; a protected minimum that cannot fit produces a controlled error before generation.

Every whole-request fit uses local `ProviderService.prepare_request`, the same media, privacy, continuation, schema conversion and serialization as completion, without credentials or transport I/O. To decide compaction, a copied projection shortens current tool bodies while retaining unavoidable metadata; canonical content is unchanged. Each turn retains only its latest prepared candidate with request-local restoration state.

Text-only compatible Chat Completions may calibrate through the same provider's `/tokenize` endpoint: at most three bounded, no-redirect counting requests per model turn. Counts are keyed to prepared payload and provider binding; unsupported/transient failures back off and use estimates. Other transports/media retain heuristics. A matching final server count replaces the estimate; otherwise:

```text
I = max(raw_projection_estimate, raw_final_serialized_estimate)
guardedI = ceil(I * 1.1)
output = min(C - S - guardedI, M, H, Q)
```

`Q` is an optional helper ceiling. Safety and proportional margin apply once. A nonpositive allowance or one below the required reserve fails before generation; the provider boundary does not recursively compact. When `M`, `H` and `Q` are absent, compatible transports omit output-limit options and let the provider apply its default. A present limit is reduced to fit. Codex always omits generation options.

Text uses the configured character/token ratio; binary/base64 is not counted as ordinary text. Each image reserves 2,048 input tokens; audio/video/unknown opaque continuation reserves 8,192. Valid scope-matching Responses reasoning usage plus 256 framing tokens replaces that opaque fallback. These are estimates, not tokenizer guarantees or hidden output caps. Large media/templates may still need manual ceilings or fail upstream.

Helpers keep explicit small ceilings: titles start at 2,048 with one repair up to 4,096; compaction targets 256–2,048 and reserves that effective target in full; the model connection test uses 1,024, all subject to transport/manual/provider limits. Auto allocation changes neither reasoning selection nor thinking behavior.

Host diagnostics distinguish computed `allocatedOutputTokens` from actual `transmittedMaxOutputTokens`; an omitted option is null even when local allowance is known. Estimation source is `server`, `server-calibrated` or `estimate`, separately from actual provider usage. Diagnostics are not proof a provider enforces a local allowance.

### One-Time Auto Migration

Revision `49e718887770` resets existing model output ceilings to Auto, including manual/legacy values, without a backup. Downgrade removes later ceilings rather than recovering old ones. Context/reasoning and other settings remain unchanged. Operational steps are in the [upgrade guide](user/aktualisierung.md#modell-ausgabelimits), not in routine configuration instructions.

## Acceptance Tests

Executable backend tests, rather than a duplicate prose case inventory, are the acceptance reference:

- [Restart lifecycle](../apps/backend/tests/test_harness_restart_contract.py)
- [Permission boundaries](../apps/backend/tests/test_permission_policy_contract.py)
- [Context fitting and compaction](../apps/backend/tests/test_context_manager_contract.py)
- [Native child lifecycle](../apps/backend/tests/test_native_subagent_integration.py) and [read-only child sessions](../apps/backend/tests/test_subagent_session_integration.py)

Use [supported development checks](development.md#checks-migrations-and-evaluations). Opt-in PostgreSQL child coverage uses:

```bash
./scripts/dev.sh test backend --subagent-db tests/test_native_subagent_integration.py tests/test_subagent_session_integration.py
```

## Coding comparison reports

`./scripts/eval.sh coding-comparison` compares existing MatoLoop metadata with an OpenCode export. Versioned JSON/Markdown retain source hashes, identity and comparable metrics. Quality remains unknown without independent checks from both artifacts; duration, tokens and self-reports do not prove correctness. Raw reports stay under ignored `data/`; publish lasting conclusions as dated evidence, separate from current contracts.
