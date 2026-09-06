# Lean Agent Harness

This document defines the target architecture for the lean OpenCode-inspired agent harness. It complements [architecture.md](architecture.md); its runtime, tool, permission, and context boundaries are mandatory.

## Runtime and Coordinator

MatoLoop manages runs and run-bound agent-action approvals through exactly one process-local run and approval coordinator. The backend therefore uses one worker. The coordinator owns active run tasks and waiters for open agent-action approvals. Persistent background approvals such as `memory_change` do not use a live waiter. The coordinator is neither a distributed scheduler nor a queue and uses no cross-process locks.

- A run continues only in the coordinator that started it.
- An approval decision can resolve only a live waiter in that coordinator.
- Multiple backend workers, parallel coordinator instances, and transfer of a run to another process are unsupported.
- Persistent run state serves API, UI, and audit. It is not a Python stack checkpoint or executable queue.
- Graceful shutdown and process termination must not execute a tool twice.

Agent-action approvals and session grants are persistent, but run restart resume is explicitly not guaranteed. After backend restart, previously active or waiting runs end terminally as interrupted or failed and their open agent-action approvals expire. Deciding a persisted agent-action approval without a live waiter returns `approval.runInactive`; it does not resume the old run or create a grant. The user starts a new run when needed. Fully persisted session grants created before restart remain valid. Persistent `memory_change` approvals are not run-bound, survive restart, and execute through their own transactional resolver.

Approvals and agent questions have no inactivity timeout. A live run waits until the user responds, explicitly cancels the run or deletes the session, or the backend process stops. Browser and SSE disconnects do not resolve or expire these interactions.

This is a deliberate first-version boundary. Restart resume would require persistent tool checkpoints, idempotent execution, leases, and a recovery strategy; it must not be simulated by reloading an `AgentRun`.

User cancellation is an explicit run operation and is never inferred from SSE disconnect. The cancel endpoint requests cancellation of the process-local task and returns immediately; SSE publishes the later persisted terminal state. Repeated calls do not cancel cleanup again. Completed runs return idempotently. Provider streams and child processes are interrupted, and dispatched MatoLoop Agent requests receive a best-effort cancel message. The runtime does not wait for a mutation result: an operating-system file operation that already started may still finish, and its late result is ignored. Successfully streamed text and public reasoning become an interrupted assistant message; without visible output, an empty interruption marker is stored. Hidden output from the initial required-search phase remains excluded. Completed tool effects are not reversed. The run's open agent-action approvals expire and incomplete tool calls receive terminal errors before `message.interrupted` and then `run.cancelled` are published. Graceful backend shutdown is not an explicit user cancellation and creates no such message marker.

## Tool Contract

A tool is a host capability with a stable name, description, typed input schema, and asynchronous execution. Providers receive only registry-exported schemas and return normalized tool calls. They never execute tools.

Research tools return structured results with safe URLs. `web_search` labels content as snippets; `webfetch` preserves safe final URL, content type, output format, final HTTP status, and page content in a structured envelope. Transient context shortening may reduce only snippet or page content and must never create partial JSON or URLs. Final-answer links are model-generated best effort; the host neither adds them nor treats them as proof. The frontend may show derived public metadata in a sources panel.

`session_tool_result_read` reads a persisted current-session tool result by message ID without executing the original tool again. The host returns fixed chunks of at most 12,000 characters from a zero-based offset and reports continuation through `hasMore` and `nextOffset`. Historical omission markers and shortened current-session tool messages returned by `session_read` contain a host-generated `toolResultReference` with exact retrieval arguments. Shortened tool messages from another approved session contain no unusable current-session reference.

Every eligible normal turn uses one bounded latest-message or short-follow-up query for automatic unpinned-memory and prior-session retrieval. The host shares one query embedding across both hybrid searches and falls back to FTS; durable-memory source provenance does not affect eligibility. Relevant prior-session snippets come only from other `full` sessions in the same project scope; a first turn or explicit recent-session overview can additionally use the recent-session quota. Project requests also receive a compact index of up to ten recent `full` sessions, ordered by each session's latest eligible message and containing only titles plus exact `sessionReadArguments`. They prioritize up to the configured maximum of distinct relevant project sessions, default three, before non-overlapping recent snippets and unpinned memories. A relevant reference contains only the strongest topical anchor. Receiving `full` and `read_only` sessions may use these references, while `disabled` sessions receive none. An explicit opt-out or detected supplied-text transformation suppresses automatic context. References are untrusted discovery data, not complete transcript evidence. Explicit discovery and reading remain separate operations: `session_browse` lists recent prior sessions, `session_search` performs topic discovery, and separately permission-controlled `session_read` supplies omitted details. These tools and authenticated UI search remain available for owned `read_only` and `disabled` source sessions even though automatic retrieval excludes them. Selecting the model releases already authorized session content; durable-memory audiences remain separately filtered. `session_read` can project a bounded window around `around_message_id` and continue forward with `nextAfterMessageId`. Models must not invent these IDs; within one run, a continuation must use the latest cursor returned for that target session. An unavailable or stale cursor fails closed with exact retry arguments. Its 50,000-character page budget advances only through messages actually returned, so continuation never skips an omitted record.

The tool registry is the only name resolver for tool calls. It rejects duplicate names during construction, validates input before execution, and exports only tools available for the session, effective policies, and effective integrations. Unknown tools, invalid arguments, and execution failures become stable tool errors. Exception text and secrets never enter model context or API responses.

Tool groups are host-controlled catalog and UI metadata. They do not grant access and have no policy inheritance. A tool declares zero or more security actions with stable capabilities, controlled UI labels, and safe defaults. Tool settings expose separate `private` and `public` personal and instance defaults for built-in tools only; integration-provided tools do not enter that page. The selected agent profile contributes sparse overrides chosen from the run's frozen trust level. Missing profile entries inherit the current effective defaults rather than failing closed or forming a complete policy snapshot. Instance-controlled tool availability is an independent hard cap. Webfetch uses one `network.fetch` action; its instance-wide public-address restriction is a separate technical boundary.

Permission evaluation validates immutable host boundaries first. `disabled` then denies even when a matching grant exists, `auto` allows only inside those boundaries, and `ask` checks an exact typed session grant before creating an approval. After an approval, the host reloads policy and boundary state before allowing the effect. Groups are never permission matcher inputs.

Integration and resource defaults are keyed by the bound integration UUID, an adapter-generated stable resource key, and the action capability. An absent resource default inherits the integration default, which ultimately inherits the action default. For each trust level, an agent profile may sparsely override the global capability, one integration, or one resource; the most specific present profile override wins, while an absent override live-inherits the effective default for that target. The permission request keeps this policy target separate from its typed grant matcher, so a calendar-level default can govern an operation while a session grant remains limited to one concrete event.

Integration settings own sparse integration and resource defaults in the current instance or personal scope, alongside technical configuration and CalDAV standard resources. These execution defaults are not technical-only hard limits and may be replaced by a profile override. Every action accepts `disabled`, `ask`, and `auto`; invalid persisted modes fail closed. Technical tool availability, disabled integrations, and remote service ACLs are hard limits that no default, profile override, approval, or grant can raise.

Configured integrations resolve through a static host catalog. An adapter can contribute statically implemented tools or values to a configuration field declared extensible by a base tool. Such values do not automatically become model arguments. Adapters cannot load foreign handlers or arbitrary remote schemas. Prompt fragments come only from adapter code; names, responses, and other external data are never treated as instructions.

A contributed tool with configurable actions must provide a host-side authorization factory. The registry validates every produced permission request against the actions declared by that tool before its handler runs. Integration settings cannot declare capabilities or authorization behavior.

The effective tool set is bound for exactly one model request. Schema export, option validation, and subsequent execution use the same binding. Calls outside this set are rejected even when a global tool definition with that name exists. Before external effects, handlers reload revocable integration, tool, and capability configuration.

Bindings retain their source integration ID and a canonical revision of that integration instance. Deletion, disabling, credential replacement, or configuration changes invalidate an already offered contributed tool. Host-selected provider options are bound to the same request; execution never silently switches a stale call to a different provider.

Every execution receives host context containing at least run ID, current session ID, registry-independent services, and `Context.ask`. Tools do not access coordinator waiters, approval tables, or provider objects directly. `Context.ask` accepts a typed permission request and centrally performs this sequence:

1. Check immutable host policy.
2. Check a matching persistent session grant.
3. If still needed, create a persistent approval and wait on the process-local coordinator.
4. Return typed `allow_once`, `allow_session`, or `deny`.
5. Only for `allow_session` and a live run, persist the grant in the approval transaction and then resume the waiter.

Model arguments cannot bypass registry selection, `Context.ask`, capability boundaries, or resource matchers. The tool performs effects only after an allowing result.

This permission sequence also governs commands shown in Control Center Terminal Auto mode. Terminal Confirm mode is a separate direct-user authorization path: the attached user may edit, execute, or deny the proposed call, no reusable grant is created, and ordinary directory and shell-policy matchers do not authorize that command. Session, run, owner, agent, current-directory, and shell-inspection bindings are still revalidated before execution.

Agent profiles are persistent instance- or user-scoped settings. Every session stores a profile ID; the reserved instance profiles `Allgemein`, `Plan`, and `Build` cannot be renamed or deleted, and `Allgemein` is selected for new sessions. A profile stores an optional persona, separate task instructions, a persona mode, and sparse capability, integration, and resource overrides for private and public models. `Allgemein` starts with an empty profile persona and empty override maps, so it uses the shared persona and live-inherits the effective Tool and Integration defaults. `Plan` automatically permits `shell.read`, disables computer file mutations, `shell.write`, and mutating Git tools without creating approval requests, and automatically writes only its canonical managed `plans/<session-id>/plan.md` file. `Build` allows workspace file mutations and fully analyzed shell actions inside the selected current directory, while paths outside it and confidential paths retain approval boundaries. The persona mode includes or omits the shared persona; a non-empty profile persona then contributes only role, tone, character, and working style, while task instructions remain a separate prompt block in either mode. The runtime snapshots the selected profile ID on each run and applies it consistently to schema binding and authorization. Instance administrators may disable personal profiles. Session grants created from `ask` bind to the selected profile and frozen trust level, so neither switching profiles nor switching between private and public models reuses the grant. Technical availability, disabled integrations, confidential-path restrictions, and remote resource ACLs remain hard boundaries for every profile.

## Typed Resource Matchers

A permission consists of a capability and typed resource matcher. Security decisions never depend on free-form display strings, string-prefix comparisons, or tool names. Matchers have unique types and validated fields such as canonical path, session ID, defined session-search scope, or shell token sequence.

- Path matchers contain explicit `session` or `workspace` root and compare relative paths component by component inside the host-bound root; string-prefix matching is forbidden.
- Computer directory matchers use canonical `{agentId,path}` JSON and compare normalized absolute POSIX paths component by component. They bound model directory entry independently from file or process policy; string-prefix matching is forbidden.
- Session matchers compare UUIDs and distinguish the current session from an explicitly named other session.
- Session-search matchers represent browse and search as one collection resource. A grant to read one session does not allow either discovery operation.
- URL matchers use the SHA-256 hash of the complete canonical URL. Display metadata is persisted separately without query values.
- Persisted matchers deserialize losslessly to the same typed matcher.
- Approval display title, summary, and risk are metadata, not matching inputs.
- Integration-operation matchers compare the exact integration UUID, canonical binding-revision hash, optional opaque resource key, and host-generated operation key. Remote labels and URLs are never matcher inputs.
- Shell matchers use the host-generated `shell.read` or `shell.write` class and semantic token prefixes. Persistent allowlists are class-specific. Session grants compare token sequences and bind them to the same agent and absolute current directory. Disabled capabilities, hard shell-zone switches, and filesystem `deny` are evaluated before grants.
- Terminal Confirm commands do not use a resource matcher or persistent grant. The user's terminal decision applies only to the bound pending call.
- Image-edit matchers retain direct attachment provenance separately from a bounded host-controlled authorization lineage. Descendants inherit lineage roots; unrelated images, sessions, targets, and trust levels do not match.

Selecting a model is the release decision for integration data already authorized by ownership, integration access, tool policy, and approvals. Public models do not require a second session-wide private-data release.

For Shared File Tools, the model uses `root=files` for the backend-managed revisioned root and `root=computer` for the current absolute directory on the selected owner-bound MatoLoop Agent. The registry translates these to internal `session` and `workspace` identifiers before permission checks. Model paths never start with a root-name prefix; the standalone `filesystem.*` request receives a normalized absolute POSIX path. A standalone session uses its session UUID as managed-file root identity; project sessions share the project UUID root. Computer access is unavailable without an agent and selected directory, while managed files remain available. MatoLoop Agent filesystem traversal uses descriptors and no-follow checks on every path component.

Standalone physical storage is `/data/sessions/<session-id>/{files,revisions}`. Project sessions share project-level `files/` and `revisions/`. Uploads, processing companions, and durable backend artifacts all use that managed revision store.

Published backend-managed files have full-byte revisions. During a run, creates, copies, replacements, and durable tool outputs write the normal current file, not a private copy or numbered intermediate revision. Tools and users read the same live bytes. A run owner stored in the existing revision index blocks competing file and subtree mutations; unrelated files remain independently editable. Completion, failure, cancellation, and restart recovery snapshot one final revision per changed file; unchanged final bytes create no revision. Existing files retain their original snapshot before the first run edit. Publication is idempotent and per-file, using index receipts rather than a separate byte journal. Directory creation and permitted rename/move are immediate and preserve file identity. Current bytes and retained revisions share the root quota, with space reserved for final snapshots. Browser/API replacements retain expected-SHA conflict protection.

Deleting a managed file purges its complete history and explicitly owned derived files; ownership is never inferred from similar filenames or paths. Individual revisions cannot be deleted. Restore selects an older revision as current for the selected managed file only, removes only that file's later revision snapshots, and invalidates only its local caches without creating another revision. Separate managed companions, their histories, and ownership metadata remain unchanged, even when their source revision is removed. Competing mutations of run-owned files are rejected; restore waits for finalization even for the owning run. Removed revision numbers are never reused. Old message references survive edits and moves only while their referenced revisions remain; deletion and restore truncation make removed references unavailable.

Historical file access is an authenticated user feature, not a model tool. The viewer opens retained versions in separate read-only tabs keyed by root, file identity, and revision, independently of the current file. Safe standalone formats use their historical bytes; dependency-based OpenSCAD history shows source only, without rendering against current dependencies or substituting current geometry. Models read only live current file content; attachment and provider reads reject superseded references rather than loading old bytes.

Managed reads use `session.files.read`; writes, edits, directory creation, copy destinations, and both sides of a managed move use `session.files.write`. Both capabilities default to `auto` for private and public models. Saved policies and profile overrides remain effective; an explicit `filesystem.read` rule also governs managed reads until a `session.files.read` rule replaces it at that scope. Workspace policies and Plan-profile path restrictions are unchanged.

Managed deletion uses `filesystem.delete` with `ask` for both private and public models, even if a configured policy is `auto`; disabled policies and host boundaries still deny. The ordinary typed grant and approval loop applies, including matching session grants, rather than unconditional repeated confirmation. Direct owner-authenticated UI deletion remains a separate user action.

Each `ExecutionAgent` has home and external filesystem policies with `auto`, `ask`, or `deny`, defaulting to `auto` and `ask`. Hard boolean shell switches control the home and external zones, and both default to enabled. Every zone identified by shell inspection must be enabled; incomplete analysis also requires the external shell switch. Bash tree-sitter syntax errors and unresolved path checks fail closed. Syntactically valid commands are otherwise `shell.read` unless a known direct or nested mutation is recognized; unknown program semantics are an accepted classification risk rather than an operating-system sandbox. Managed-file mutations continue to use `session.files.write`. Explicit reusable-writing requests create one ordinary `root=files` file per artifact unless the user requests `root=computer`; edits read and update the same path. Messages may carry a metadata-only active-file snapshot, and matching writes are rejected while it is dirty.

For `webfetch`, the effective `network.fetch` policy is evaluated for every initial URL and redirect. `ask` requests approval for the exact URL, while `auto` permits the request without approval. The target is resolved again after approval, and the validated addresses are pinned for the request. Tool availability and the instance-wide public-address restriction remain hard bounds. The restriction blocks every non-public address by default; disabling it deliberately permits every HTTP(S) target reachable from the backend.

User uploads and processing companions use revisioned managed files. Run output descriptors remain private JSON under `Message.provider_data._matoloop` until terminal finalization; pending attachments are not public `MessageAttachment` rows, and pending file actions are omitted from API responses. Finalization publishes deduplicated final attachment references with exact root, file ID, revision, and path snapshot; publication failures leave pending output metadata private. The frontend opens or refreshes canonical final outputs after terminal state, not individual tool completion, and respects intervening navigation. Text, JSON, status, and error results remain relational `Message.content`. Computer mutations remain MatoLoop-Agent-owned unless bytes are imported by the backend.

OpenSCAD OFF geometry and inspection PNGs/contact sheets are ephemeral preview data consumed inline, not public file artifacts or numbered revisions. Persisted tool results retain diagnostics, geometry metadata, and visual-verification findings without preview-file references. Explicit STL exports remain durable managed outputs under the same terminal publication contract.

The binding baseline for session tools is:

| Action | Resource | Result without grant |
| --- | --- | --- |
| Read session history | owned eligible session | effective `session.read` policy |
| Read persisted tool result | current run session | effective `session.tool_result.read` policy |
| Browse or search sessions | session-search resource | effective `session.search` policy |

`session_tool_result_read` accepts only tool messages from the same session. Browse and search share one collection grant; reading a discovered session still requires its separate exact session grant. A suitable session grant can allow a later identical request but never expands matcher type or scope. These unconfigured session-history read capabilities default to `auto` for private models and `ask` for public models.

## History and Request Context

Canonical history is the persistent normalized sequence of session messages, including assistant tool calls and results. It remains complete and available in stable order. Provider payloads may be persisted additionally but never replace normalized history.

Request context is a transient projection for one model request. It can contain system instructions, every visible pin in full, bounded automatic snippets from relevant unpinned memories and prior sessions, explicitly retrieved details, a compaction summary, and a budgeted slice of canonical current-session history. Automatic snippets share a separate serialized budget and can be dropped to fit; pins cannot be truncated or dropped, so projection fails safely when they cannot fit with required context. Shortening, reordering, and provider conversion never modify or delete canonical messages.

When context omits a historical tool result, it inserts a structured reference to the canonical message ID. The model reads required details through `session_tool_result_read` instead of rerunning the tool. Results of that read are not duplicated in compaction summaries.

Tool call and result are indivisible when slicing context. A request cannot begin with an orphaned tool result or call without result unless it is the active call awaiting execution. The latest user request remains in context.

## Compaction

Compaction creates a persistent marked summary of a completed canonical-history prefix. The summary has an unambiguous coverage boundary so the request builder selects exactly that summary and messages after it. It never replaces, edits, or deletes covered messages.

A summary preserves goals, relevant facts, decisions, and open items stated in user and assistant chat text. Tool calls, tool outputs, attachment metadata, and approval details remain only in canonical history. A structurally invalid or empty model result is rejected; if no configured model produces a valid summary, the host persists a marked deterministic fallback and the transcript displays a warning.

Compaction triggers from estimated input budget, not message count. Repeated compaction must not reinsert already compacted prefixes without bounds. The implementation deterministically chooses the active summary among generations.

## Model Context Settings

Every model configuration has explicit context-window and maximum-output limits as positive token counts with safe defaults. Model and provider arguments cannot increase these host limits.

For one model request:

```text
input_budget = context_window_tokens - max_output_tokens - safety_reserve_tokens
```

System instructions, tool schemas, memories, summaries, and history all count against `input_budget`. Before provider access, the request builder estimates usage, keeps output reserve free, and compacts or shortens only the transient projection. If minimum context still does not fit, the run ends with a stable context error instead of sending an oversized request.

Provider metadata can supply useful defaults, but explicit validated model settings are authoritative. Changes apply to new model requests and never rewrite old runs or canonical history.

## Acceptance Tests

The following independent cases are automated when their symbols are introduced. Until then they intentionally are not importing or skipped tests, so the suite does not prescribe an API that does not yet exist.

| ID | Given | When | Then |
| --- | --- | --- | --- |
| `COORD-01` | Coordinator with a live approval waiter | Matching decision arrives | Exactly that waiter resolves once and the run continues. |
| `COORD-02` | Persisted pending agent-action approval without process-local waiter | Decision arrives | API returns `approval.runInactive`, approval expires, and no grant is created. |
| `COORD-03` | Persisted `queued`, `running`, and `waiting_approval` runs plus a completed run | Backend starts | Only active states terminate; their pending agent-action approvals expire and completed runs and grants remain. |
| `COORD-04` | Two concurrent decisions for one approval | Both reach coordinator | At most one wins; tool effect and grant happen at most once. |
| `COORD-05` | Active run with provider request or approval open | User cancels once or repeatedly | Task is cancelled once, cleanup finishes, and exactly persisted `cancelled` state is published. |
| `TOOL-01` | Two tool definitions with same name | Registry builds | Build fails deterministically. |
| `TOOL-02` | Registered tool with invalid arguments | Registry executes call | Handler is not entered and a stable validation error results. |
| `TOOL-03` | Tool request not preallowed | Tool calls `Context.ask` | Effect occurs only after `allow_once` or `allow_session`, never after `deny`. |
| `TOOL-04` | Model argument tries to override capability or matcher | Call validates | Host permission remains unchanged. |
| `MATCH-01` | Path grant for `project/a` | Agent requests `project/ab` | Grant does not match. |
| `MATCH-02` | Serialized matcher of each type | It is deserialized | Type and semantics are preserved. |
| `FILE-01` | Private-model session with a selected working directory and default policy | Agent reads within it | Access occurs without approval. |
| `FILE-02` | Session without computer directory | Agent uses `root=computer` | Call returns `file.workspaceNotSelected`; approval cannot create binding. |
| `FILE-03` | Symlink inside file root | Agent reads, copies, or moves through it | Permission and execution reject `file.symlinkForbidden`. |
| `FILE-04` | Multiline or large UTF-8 file | `read` receives `offset` and `limit` | Result contains exact numbered slice and continuation hint. |
| `FILE-05` | Two sessions belong to one project | Either mutates `root=files` | Both resolve the same project UUID root and observe the shared file state. |
| `WEBFETCH-01` | Effective `network.fetch` policy is `ask` | Model requests URL | Exact-URL approval is requested, then the target is resolved and validated again before HTTP. |
| `WEBFETCH-02` | Effective `network.fetch` policy is `auto` | Model requests URL | The target is resolved, validated, and fetched without approval. |
| `WEBFETCH-03` | Redirect to another URL | Fetch handles redirect | The target repeats authorization, DNS validation, and address pinning. |
| `WEBFETCH-04` | Public-address restriction enabled and target is non-public | Model requests fetch | Access remains blocked regardless of grants or profile override. |
| `WEBFETCH-05` | Response exceeds the configured limit or has an unsupported MIME type | Body streams | Fetch ends with stable error before persistence. |
| `WEBFETCH-06` | HTML with semantic main content, navigation, and relative links | Tool creates Markdown or text | Relevant root is converted, links resolve against final URL, and result reports status, input type, and output format. |
| `WEBFETCH-07` | Target returns non-success HTTP status | Fetch ends | Model gets `webfetch.requestFailed` and safe `statusCode`, never external error text or body. |
| `TRANSCRIPT-01` | Supported public YouTube URL has published captions | Tool fetches transcript | One normalized video approval covers bounded allowlisted watch, player, and timed-text requests; timestamped text and track metadata return. |
| `TRANSCRIPT-02` | Requested caption language is unavailable | Tool selects a track | The advertised original/default track returns with `languageFallback=true`; no translation or transcription request occurs. |
| `TRANSCRIPT-03` | Caption URL, redirect, XML, live state, or response bounds violate the contract | Tool processes provider data | The request fails closed with a stable `transcript.*` error and exposes no provider body or signed URL. |
| `RESEARCH-01` | Search mode `disabled` | Normal request builds | `web_search` and instructions are absent; independent `webfetch` still follows policy. |
| `RESEARCH-02` | Search mode `available` and web tools offered | Model researches or answers directly | Normal loop uses automatic tool choice, streaming, and finalization without host final block. |
| `RESEARCH-03` | Search mode `required` without successful current-turn search | Initial request builds | Only `web_search` is offered with `tool_choice=required`; text and reasoning are hidden. |
| `RESEARCH-04` | Forced initial search succeeds with no results | Host handles result | Gate is satisfied; result need not contain hits or links. |
| `RESEARCH-05` | `required` gate satisfied | Next request builds | Tool set, automatic choice, prompts, budgets, streaming, and finalization match `available`. |
| `RESEARCH-06` | `available` or completed `required` | Search, page fetch, and transcript fetch are called repeatedly | `max_research_tool_calls` limits their combined count. |
| `RESEARCH-07` | Identical research call already succeeded | Model repeats it | No network request; compact `alreadyCompleted` result returns and next request finalizes without research tools. |
| `RESEARCH-08` | Current turn used research and final answer visibly links some research URLs | Answer finalizes | Host preserves text and stores only policy, use flag, and visibly linked current URLs without claiming support. |
| `RESEARCH-09` | Final Markdown link canonically matches approved current research URL | Frontend renders | Only allowlisted link becomes an inline source chip; other links render normally. |
| `RESEARCH-10` | SearXNG returns oversized response or markup snippets | Host reads and normalizes | Response stops at 256 KiB; accepted snippets are bounded local plain text. |
| `RESEARCH-11` | Model searches current, latest, or today information | Prompt uses frozen local timestamp | Query omits a year or derives it from timestamp; explicit historical and exact-name years remain. |
| `FILE-06` | Backend tool returns or updates durable bytes during a run | Run completes, fails, is cancelled, or is recovered | One final revision per changed file is published, with no numbered intermediate or unchanged revision; public output references appear only at terminal finalization. |
| `FILE-07` | Historical message references a file revision | File is edited, moved, deleted, or restored later | Retained revisions resolve to original bytes after edits/moves; deletion or restore truncation makes removed references unavailable, and removed numbers are not reused. |
| `ATTACH-01` | User uploads a message attachment | Original persists; text/document companions persist | Managed files retain exact revision references. Audio WAV conversion is transient for transcription and playback fallback; no new audio companion is stored. Legacy companions remain intact for existing references. |
| `ATTACH-02` | Active model supports image input | Next request builds | The current or referenced image is transferred transiently, never logged as Base64. |
| `ATTACH-03` | Active model lacks image input | Tool returns an image | The managed file remains available and the model receives only a text marker. |
| `SESSION-01` | Run in session A | Session-read tool reads A | `Context.ask` creates no approval. |
| `SESSION-02` | Run in A without matching grant | Session-read reads B | Exact session matcher approval is created. |
| `SESSION-03` | Run with read grant for B | Session-search searches sessions | Read grant does not match and search approval is created. |
| `HISTORY-01` | Canonical history with tool call and result | Small request context builds | Database history is unchanged and request has no orphaned tool message. |
| `HISTORY-02` | Valid summary through boundary N | Next request builds | Exactly summary plus messages after N are used. |
| `COMPACT-01` | History exceeds input budget | Compaction succeeds | Boundary summary persists and canonical messages remain. |
| `COMPACT-02` | History exceeds input budget | Compaction fails | History and previous summary remain and run ends safely. |
| `CONTEXT-01` | Context window, output limit, and safety reserve | Request builds | System, tools, memories, summary, and history fit input budget. |
| `CONTEXT-02` | Window too small for minimum context | Request builds | No provider call and stable context error. |
| `CONTEXT-03` | Same current tool output projected for different model windows | Requests build | Tool budget scales to model-dependent compaction threshold; canonical tool message is unchanged. |

The restart boundary is tested independently in `apps/backend/tests/test_harness_restart_contract.py`. Registry, session tools, matchers, compaction, and model context settings receive executable tests when their public contracts exist.
