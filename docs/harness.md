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

Automatic episodic retrieval places compact, trust-filtered excerpts from up to two latest eligible prior sessions in the same project scope into the first user turn of a new session when memory mode allows it. A detected explicit opt-out suppresses this bootstrap. Later explicit latest-session intent uses recency, while explicit references to earlier work use hybrid topic retrieval. Generic follow-ups inject no repeated episode. Each reference contains session metadata, one bounded anchor preview, a bounded visible excerpt, and exact `sessionReadArguments`; it is untrusted and is not complete transcript evidence. Explicit discovery and reading remain separate operations: `session_browse` lists the most recently active prior sessions, while `session_search` performs literal topic discovery. Separately permission-controlled `session_read` remains available for omitted details. Session titles inherit the first persisted user message's trust level and follow the same public/private release boundary. `session_read` can project a bounded window around `around_message_id` and continue forward with `nextAfterMessageId`. Models must not invent these IDs; within one run, a continuation must use the latest cursor returned for that target session. An unavailable or stale cursor fails closed with exact retry arguments. Its 50,000-character page budget advances only through messages actually returned, so continuation never skips an omitted record.

The tool registry is the only name resolver for tool calls. It rejects duplicate names during construction, validates input before execution, and exports only tools available for the session, effective policies, and effective integrations. Unknown tools, invalid arguments, and execution failures become stable tool errors. Exception text and secrets never enter model context or API responses.

Tool groups are host-controlled catalog and UI metadata. They do not grant access and have no policy inheritance. A tool declares zero or more security actions. Each configurable action has a stable capability, a controlled UI label, and a safe default execution policy. Global overrides use `disabled`, `ask`, or `auto`; domain-specific policies such as webfetch remain typed when a generic mode would lose resource semantics. Disabling any action required by a tool removes that tool from the effective set.

Permission evaluation validates immutable host boundaries first. `disabled` then denies even when a matching grant exists, `auto` allows only inside those boundaries, and `ask` checks an exact typed session grant before creating an approval. After an approval, the host reloads policy and boundary state before allowing the effect. Groups are never permission matcher inputs.

Global action policies may have integration-resource overrides stored in `ToolSettings`. An override is keyed by the bound integration UUID, an adapter-generated stable resource key, and the action capability. Missing overrides inherit the global action policy. A globally disabled action is a hard boundary and cannot be enabled by a resource override. The permission request keeps this policy target separate from its typed grant matcher, so a calendar-level policy can govern an operation while a session grant remains limited to one concrete event.

Integration actions additionally support a sparse instance default between the global and resource levels. New resources inherit the instance default, which initially inherits the static action default. Every action declares its allowed policy modes; invalid or disallowed persisted modes fail closed. Remote service privileges are transient hard limits evaluated after local authorization and cannot be raised by policies or grants.

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

Resource policies are global installation settings. They are not approval-derived grants and are not agent profiles. If persistent agent profiles are added later, they may carry the same override shape as an additional resolver layer; no placeholder agent identity or policy fields exist before that aggregate is implemented. Global disabled actions and resources remain hard boundaries for every agent.

## Typed Resource Matchers

A permission consists of a capability and typed resource matcher. Security decisions never depend on free-form display strings, prefix comparisons, or tool names. Matchers have unique types and validated fields such as canonical path, session ID, or defined session-search scope.

- Path matchers contain explicit `session` or `workspace` root and compare relative paths component by component inside the host-bound root; string-prefix matching is forbidden.
- Computer directory matchers use canonical `{agentId,path}` JSON and compare normalized absolute POSIX paths component by component. They bound model directory entry independently from file or process policy; string-prefix matching is forbidden.
- Session matchers compare UUIDs and distinguish the current session from an explicitly named other session.
- Session-search matchers represent browse and search as one collection resource. A grant to read one session does not allow either discovery operation.
- URL matchers use the SHA-256 hash of the complete canonical URL. Display metadata is persisted separately without query values.
- Persisted matchers deserialize losslessly to the same typed matcher.
- Approval display title, summary, and risk are metadata, not matching inputs.
- Integration-operation matchers compare the exact integration UUID, canonical binding-revision hash, optional opaque resource key, and host-generated operation key. Remote labels and URLs are never matcher inputs.
- Read-only shell matchers compare the exact host-generated command scope, including agent and current directory. The MatoLoop Agent's read-only classification remains a hard boundary and is rechecked even when a grant matches.
- Image-edit matchers retain direct attachment provenance separately from a bounded host-controlled authorization lineage. Descendants inherit lineage roots; unrelated images, sessions, targets, and trust levels do not match.

Selecting a model is the release decision for integration data already authorized by ownership, integration access, tool policy, and approvals. Public models do not require a second session-wide private-data release.

For Shared File Tools, the model uses `root=files` for the backend-managed revisioned root and `root=computer` for the current absolute directory on the selected owner-bound MatoLoop Agent. The registry translates these to internal `session` and `workspace` identifiers before permission checks. Model paths never start with a root-name prefix; the standalone `filesystem.*` request receives a normalized absolute POSIX path. A standalone session uses its session UUID as managed-file root identity; project sessions share the project UUID root. Computer access is unavailable without an agent and selected directory, while managed files remain available. MatoLoop Agent filesystem traversal uses descriptors and no-follow checks on every path component.

Standalone physical storage is `/data/sessions/<session-id>/{files,revisions}`. Project sessions share project-level `files/` and `revisions/`. Uploads, processing companions, and durable backend artifacts all use that managed revision store.

Every backend-managed file state has a stored full-byte revision. Create and copy establish the initial revision, replace appends one, and rename or move preserves file identity and history. Deleting a file purges its complete history; individual revisions cannot be deleted. Restoring an older revision selects it as current and removes later revisions without creating another revision. Current bytes and retained revision bytes share the root quota. Browser/API replacements retain expected-SHA conflict protection.

Each `ExecutionAgent` has home filesystem, external filesystem, home shell, and external shell policies with `auto`, `ask`, or `deny`. Defaults are `auto`, `ask`, `ask`, and `ask` respectively. Shell inspection applies the strictest policy across the current directory and every resolved target; incomplete analysis also applies the external shell policy. The `process.execute` capability remains separate. Managed-file mutations continue to use `session.files.write`. Explicit reusable-writing requests create one ordinary `root=files` file per artifact unless the user requests `root=computer`; edits read and update the same path. Messages may carry a metadata-only active-file snapshot, and matching writes are rejected while it is dirty.

For `webfetch`, effective model policy evaluates every initial URL and redirect before DNS or HTTP. `ask` requests approval for every URL, `auto_public` automatically allows public targets and asks for allowlisted private origins, and `auto_allowed` automatically allows both classes. Network classification after permission remains immutable host policy. No grant can open a private origin not on the allowlist, link-local address, or metadata address.

User uploads, processing companions, and durable byte-valued backend tool outputs are revisioned managed files. Each message attachment references an exact root, file ID, revision, and path snapshot. Text, JSON, status, and error results remain relational `Message.content`. Computer mutations remain MatoLoop-Agent-owned unless bytes are imported by the backend.

The binding baseline for session tools is:

| Action | Resource | Result without grant |
| --- | --- | --- |
| Read session history | current run session | `allow` |
| Read session history | another session | `ask` |
| Read persisted tool result | current run session | `allow` |
| Browse or search sessions | session-search resource | `ask` |

`current-session read allow` is a narrow host rule, not a persistent wildcard grant. `session_tool_result_read` accepts only tool messages from the same session. `other session read ask` and session discovery `ask` apply even when the model supplies a known session ID or harmless-looking query. Browse and search share one collection grant; reading a discovered session still requires its separate exact session grant. A suitable session grant can allow a later identical request but never expands matcher type or scope.

## History and Request Context

Canonical history is the persistent normalized sequence of session messages, including assistant tool calls and results. It remains complete and available in stable order. Provider payloads may be persisted additionally but never replace normalized history.

Request context is a transient projection for one model request. It can contain system instructions, released pinned and retrieved durable memories, compact prior-session references, a compaction summary, and a budgeted slice of canonical current-session history. Shortening, reordering, and provider conversion never modify or delete canonical messages.

When context omits a historical tool result, it inserts a structured reference to the canonical message ID. The model reads required details through `session_tool_result_read` instead of rerunning the tool. Results of that read are not duplicated in compaction summaries.

Tool call and result are indivisible when slicing context. A request cannot begin with an orphaned tool result or call without result unless it is the active call awaiting execution. The latest user request remains in context.

## Compaction

Compaction creates a persistent marked summary of a completed canonical-history prefix. The summary has an unambiguous coverage boundary so the request builder selects exactly that summary and messages after it. It never replaces, edits, or deletes covered messages.

A summary preserves goals, relevant facts, decisions, and open items stated in user and assistant chat text. Tool calls, tool outputs, attachment metadata, and approval details remain only in canonical history. Failed compaction leaves existing history and the latest valid summary unchanged.

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
| `FILE-01` | Session with a selected working directory | Agent reads within it | Access occurs without approval. |
| `FILE-02` | Session without computer directory | Agent uses `root=computer` | Call returns `file.workspaceNotSelected`; approval cannot create binding. |
| `FILE-03` | Symlink inside file root | Agent reads, copies, or moves through it | Permission and execution reject `file.symlinkForbidden`. |
| `FILE-04` | Multiline or large UTF-8 file | `read` receives `offset` and `limit` | Result contains exact numbered slice and continuation hint. |
| `FILE-05` | Two sessions belong to one project | Either mutates `root=files` | Both resolve the same project UUID root and observe the shared file state. |
| `WEBFETCH-01` | Global `ask` policy | Model requests URL | No DNS or HTTP occurs before approval. |
| `WEBFETCH-02` | `auto_public` and public target | Model fetches URL | Target is SSRF-validated and fetched without approval. |
| `WEBFETCH-03` | Redirect to another URL | Fetch handles redirect | Target repeats policy, DNS validation, and address pinning. |
| `WEBFETCH-04` | Private target not on allowlist or metadata target | Model requests fetch | Access remains blocked regardless of grants or model override. |
| `WEBFETCH-05` | Response over 5 MiB or unsupported MIME type | Body streams | Fetch ends with stable error before persistence. |
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
| `FILE-06` | Backend tool returns durable bytes | Tool result persists | A managed file revision and an exact message reference persist together. |
| `FILE-07` | Historical message references a file revision | File is edited, moved, or deleted later | The reference still resolves to the original bytes. |
| `ATTACH-01` | User uploads a message attachment and a companion is created | Both persist | Both are revisioned managed files and the message stores exact references. |
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
