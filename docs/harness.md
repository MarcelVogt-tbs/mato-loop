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

User cancellation is an explicit run operation and is never inferred from SSE disconnect. The cancel endpoint requests cancellation of the process-local task and waits for its persisted terminal state. Repeated calls do not cancel cleanup again. Completed runs return idempotently. Successfully streamed text and public reasoning become an interrupted assistant message; without visible output, an empty interruption marker is stored. Hidden output from the initial required-search phase remains excluded. Completed tool effects are not reversed. The run's open agent-action approvals expire and incomplete tool calls receive terminal errors before `message.interrupted` and then `run.cancelled` are published. Graceful backend shutdown is not an explicit user cancellation and creates no such message marker.

## Tool Contract

A tool is a host capability with a stable name, description, typed input schema, and asynchronous execution. Providers receive only registry-exported schemas and return normalized tool calls. They never execute tools.

Research tools return structured results with safe URLs. `web_search` labels content as snippets; `webfetch` preserves safe final URL, content type, output format, final HTTP status, and page content in a structured envelope. Transient context shortening may reduce only snippet or page content and must never create partial JSON or URLs. Final-answer links are model-generated best effort; the host neither adds them nor treats them as proof. The frontend may show derived public metadata in a sources panel.

`session_tool_result_read` reads a persisted current-session tool result by message ID without executing the original tool again. The host returns fixed chunks of at most 12,000 characters from a zero-based offset and reports continuation through `hasMore` and `nextOffset`. Historical omission markers and shortened current-session tool messages returned by `session_read` contain a host-generated `toolResultReference` with exact retrieval arguments. Shortened tool messages from another approved session contain no unusable current-session reference.

Automatic episodic retrieval may place bounded, trust-filtered prior-session excerpts directly in request context when the session memory mode allows it. These excerpts are untrusted and do not grant access to the complete transcript. Explicit discovery and reading remain separate operations: `session_browse` lists the most recently active prior sessions, while `session_search` performs literal topic discovery. Both return a bounded untrusted preview and exact `sessionReadArguments`; the model must use separately permission-controlled `session_read` before relying on details beyond the supplied excerpt or preview. Session titles inherit the first persisted user message's trust level and follow the same public/private release boundary. `session_read` can project a bounded window around `around_message_id` and continue forward with `nextAfterMessageId`. Its 50,000-character page budget advances only through messages actually returned, so continuation never skips an omitted record.

The tool registry is the only name resolver for tool calls. It rejects duplicate names during construction, validates input before execution, and exports only tools available for the session, mode, and effective integrations. Unknown tools, invalid arguments, and execution failures become stable tool errors. Exception text and secrets never enter model context or API responses.

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
- Computer directory matchers use canonical `{agentId,path,rootId,rootRevision}` JSON and compare absolute paths component by component. They bound directory entry independently from file or process policy; string-prefix matching is forbidden.
- Session matchers compare UUIDs and distinguish the current session from an explicitly named other session.
- Session-search matchers represent browse and search as one collection resource. A grant to read one session does not allow either discovery operation.
- URL matchers use the SHA-256 hash of the complete canonical URL. Display metadata is persisted separately without query values.
- Persisted matchers deserialize losslessly to the same typed matcher.
- Approval display title, summary, and risk are metadata, not matching inputs.
- Integration-operation matchers compare the exact integration UUID, canonical binding-revision hash, optional opaque resource key, and host-generated operation key. Remote labels and URLs are never matcher inputs.
- Read-only shell matchers compare the exact host-generated command scope, including agent, root revision, and working directory. The Execution Agent's read-only classification remains a hard boundary and is rechecked even when a grant matches.
- Image-edit matchers retain direct attachment provenance separately from a bounded host-controlled authorization lineage. Descendants inherit lineage roots; unrelated images, sessions, targets, and trust levels do not match.

Private integration data uses a separate session release. Sensitive integration tools require `integration.private_data.release` before a public model can receive a result. Successful use marks the session conservatively; later public requests recheck the revocable session grant before context projection and provider transport. This is deliberately a session-level boundary, not field-level taint tracking.

For the Shared Tools `read`, `glob`, and `grep`, the host resolves the root before permission checks. `root=session` is always bound to the current backend session tree; `root=workspace` is always bound to the selected owner-bound Execution Agent, advertised root, and working directory. It is unavailable without that agent and root, while session files remain available. File tools never follow symlinks. In the session root, `attachments`, `generated`, `downloads`, and `derived` are readable and may be copy sources, but are immutable; only `files` permits mutations.

For `webfetch`, effective model policy evaluates every initial URL and redirect before DNS or HTTP. `ask` requests approval for every URL, `auto_public` automatically allows public targets and asks for allowlisted private origins, and `auto_allowed` automatically allows both classes. Network classification after permission remains immutable host policy. No grant can open a private origin not on the allowlist, link-local address, or metadata address.

Tool attachments are persistent core data of their tool message. Binary data is stored atomically in flat host-managed session directories, validated against MIME type, magic bytes, decoder, and quota, and removed after transaction failure. API, SSE, and canonical message content contain no Base64 data or physical storage paths. Provider images exist only in transient request projections.

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

Request context is a transient projection for one model request. It can contain system instructions, released pinned and retrieved durable memories, bounded prior-session episodes, a compaction summary, and a budgeted slice of canonical current-session history. Shortening, reordering, and provider conversion never modify or delete canonical messages.

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
| `FILE-01` | Read-only session with working directory | Agent reads within it | Access occurs without approval. |
| `FILE-02` | Session without workspace | Agent uses `root=workspace` | Call returns `file.workspaceNotSelected`; approval cannot create binding. |
| `FILE-03` | Symlink inside file root | Agent reads, copies, or moves through it | Permission and execution reject `file.symlinkForbidden`. |
| `FILE-04` | Multiline or large UTF-8 file | `read` receives `offset` and `limit` | Result contains exact numbered slice and continuation hint. |
| `FILE-05` | File under a host-managed session asset directory | Agent copies it to `session:files` or workspace | Copy is allowed; write, move, and delete at source remain blocked. |
| `WEBFETCH-01` | Global `ask` policy | Model requests URL | No DNS or HTTP occurs before approval. |
| `WEBFETCH-02` | `auto_public` and public target | Model fetches URL | Target is SSRF-validated and fetched without approval. |
| `WEBFETCH-03` | Redirect to another URL | Fetch handles redirect | Target repeats policy, DNS validation, and address pinning. |
| `WEBFETCH-04` | Private target not on allowlist or metadata target | Model requests fetch | Access remains blocked regardless of grants or model override. |
| `WEBFETCH-05` | Response over 5 MiB or unsupported MIME type | Body streams | Fetch ends with stable error before persistence. |
| `WEBFETCH-06` | HTML with semantic main content, navigation, and relative links | Tool creates Markdown or text | Relevant root is converted, links resolve against final URL, and result reports status, input type, and output format. |
| `WEBFETCH-07` | Target returns non-success HTTP status | Fetch ends | Model gets `webfetch.requestFailed` and safe `statusCode`, never external error text or body. |
| `RESEARCH-01` | Search mode `disabled` | Normal request builds | `web_search` and instructions are absent; independent `webfetch` still follows policy. |
| `RESEARCH-02` | Search mode `available` and web tools offered | Model researches or answers directly | Normal loop uses automatic tool choice, streaming, and finalization without host final block. |
| `RESEARCH-03` | Search mode `required` without successful current-turn search | Initial request builds | Only `web_search` is offered with `tool_choice=required`; text and reasoning are hidden. |
| `RESEARCH-04` | Forced initial search succeeds with no results | Host handles result | Gate is satisfied; result need not contain hits or links. |
| `RESEARCH-05` | `required` gate satisfied | Next request builds | Tool set, automatic choice, prompts, budgets, streaming, and finalization match `available`. |
| `RESEARCH-06` | `available` or completed `required` | Search and fetch are called repeatedly | `max_research_tool_calls` limits their combined count. |
| `RESEARCH-07` | Identical research call already succeeded | Model repeats it | No network request; compact `alreadyCompleted` result returns and next request finalizes without research tools. |
| `RESEARCH-08` | Current turn used research and final answer visibly links some research URLs | Answer finalizes | Host preserves text and stores only policy, use flag, and visibly linked current URLs without claiming support. |
| `RESEARCH-09` | Final Markdown link canonically matches approved current research URL | Frontend renders | Only allowlisted link becomes an inline source chip; other links render normally. |
| `RESEARCH-10` | SearXNG returns oversized response or markup snippets | Host reads and normalizes | Response stops at 256 KiB; accepted snippets are bounded local plain text. |
| `RESEARCH-11` | Model searches current, latest, or today information | Prompt uses frozen local timestamp | Query omits a year or derives it from timestamp; explicit historical and exact-name years remain. |
| `ATTACH-01` | Webfetch returns valid supported image | Tool result persists | Message and attachment metadata persist together under session `downloads`. |
| `ATTACH-02` | Active model supports image input | Next request builds | Image is transferred transiently, never persisted or logged as Base64. |
| `ATTACH-03` | Active model lacks image input | Webfetch returns image | Image remains visible and model receives only a text marker. |
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
