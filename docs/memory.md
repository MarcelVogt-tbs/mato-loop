# Memory and Dreaming

MatoLoop projects four memory layers into a normal project model request:

1. Canonical current-session context, compacted when necessary.
2. Every audience-visible pinned durable memory in full.
3. A compact index of recent project sessions.
4. Bounded automatic snippets from relevant unpinned durable memories and prior sessions.

Durable memory and prior-session retrieval are independent. A conversation does not need to be processed by Dreaming before it can be found as prior-session context.

Project instructions are a separate optional authoritative layer for deliberately maintained project rules and decisions. Automatic project awareness comes from the compact project-local session index, complemented by bounded snippets of other owned sessions across all projects and standalone sessions; instructions do not replace the project history.

The maintained user profile is separate from memory. Its full name, preferred name, form of address, pronouns, primary language, and primary residence are projected independently of `memoryMode`, are never embedded or searchable, and appear between the assistant persona and project context. Each field can define a shared, private-model-only, or public-model-only value; a trust-specific value overrides the shared value. Exact case- and whitespace-normalized profile duplicates are excluded from memory projection and retrieval.

## Access, Scope, and Trust

Each session has a mutable `memoryMode`:

- `full` can receive memory context, use `memory_retrieve` and `memory_write`, and act as a Dreaming and automatic prior-session source.
- `read_only` can receive memory context and use `memory_retrieve`, but cannot write memories, run Dreaming, or act as an automatic prior-session source.
- `disabled` receives no memory context and exposes neither memory tool, but cannot act as an automatic prior-session source.

The owner memory switch and effective provider and model memory policies can further restrict model-facing memory. Tool support affects memory tools and Dreaming, but not host-side automatic retrieval. The authenticated UI can still search and manage durable memories while these switches are off. Authenticated global session search and explicit permission-controlled `session_browse`, `session_search`, and `session_read` also continue to cover owned `read_only` and `disabled` sessions.

Automatic relevant and recent prior-session retrieval uses only other `full` chat sessions owned by the same user, across all projects and standalone sessions regardless of the receiving session's project membership. Explicit `session_search` and `session_browse` use the same owner-wide scope without the automatic `full` source requirement. The current receiving session may be `full` or `read_only`. Retrieval filters by owner and excludes the current session before ranking. A project request additionally receives up to ten recent eligible `full` sessions from the same project, ordered by their latest eligible message. This index and the project sidebar remain project-local. Each index item contains only its bounded title and exact `session_read` arguments for that latest message, never message content. Selecting the model is the release decision for already authorized session messages; durable-memory audience filtering remains independent.

Durable memories and prior-session snippets are untrusted data, never instructions. Conflict priority is:

1. Current user message
2. Maintained user profile
3. User-configured project instructions
4. Pinned durable memory
5. Automatically retrieved or explicitly loaded durable memory
6. Prior-session snippet or transcript

## Automatic Context

Every eligible normal turn builds one query of at most 2,000 characters from the latest user message. When that message is short, the query includes the previous user message as follow-up context. The host computes at most one query embedding and shares it across searches for unpinned durable memories and relevant prior sessions. Memory source provenance does not affect automatic eligibility. Both searches combine PostgreSQL FTS and local cosine ranking; unavailable or invalid embeddings fall back to FTS.

The ranked results are cached across tool rounds in one agent run to avoid repeated embedding work. Before every later model request, the host reloads pins and revalidates cached automatic memories against current owner, audience, pin, and source state and cached episode references against current owner, current-session exclusion, and source-session mode, without a project-membership filter.

The configurable fixed quotas default to:

- two relevant unpinned memory snippets through `automaticMemorySnippetLimit`;
- three relevant prior-session snippets through `automaticSessionSnippetLimit`;
- two recent-session snippets for first-turn bootstrap or explicit recent-session overview through `recentSessionSnippetLimit`.

An explicit request not to use memory or prior context, and detected requests that only transform supplied text, suppress automatic snippets. The first turn and explicit recent-session overview may add recent snippets; relevance retrieval still uses the same turn query. Duplicate source sessions are removed. Project requests prioritize relevant snippets from distinct owned sessions, then non-overlapping recent snippets, then unpinned memories. Standalone requests retain the smaller single relevant-session maximum and give recent snippets precedence.

The complete serialized automatic payload shares one configurable budget, `automaticContextMaxTokens`, defaulting to 1,024 tokens and capped at 10% of usable raw model input. With context capacity `C`, fixed safety reserve `S`, and generation reserve `R = min(2048, manual output ceiling, known provider output ceiling)` ignoring absent ceilings, usable raw input is `raw_input_budget(max(0, C - S - R))`, where `raw_input_budget(capacity) = capacity * 10 // 11`. This inverse margin leaves room for the single 10% increase applied after the maximum of the raw projection and final serialized input estimates; it does not reserve the entire adaptive output allowance or manual ceiling. The 10% automatic-snippet share is a separate content quota, not another estimation margin. The host converts that token limit with the model's characters-per-token estimate and truncates or omits automatic snippets to fit. The project-session index uses at most 2,000 characters and half this automatic budget. Fixed quotas are maxima, not guarantees. See [Model Context Settings](harness.md#model-context-settings) for final allocation and media-estimation limits.

Visible pins are serialized in full and remain outside the automatic snippet budget. The final context projection first tries pins plus automatic snippets and can retry with pins only. Dynamic memory metadata and references are projected as a host-marked assistant context immediately before the latest user request; the stable system prompt defines the marker's trust boundary. The memory context counts toward the same input budget. It fails safely with the normal context-window error if all visible pins and required request context cannot fit; pins are never silently truncated or dropped.

An automatic memory hit contains a short canonical-content preview and stable ID and revision. A relevant-session hit contains the strongest bounded topical anchor and exact `session_read` arguments; a recent hit contains a small tail. The project-session index points separately to the latest eligible message in each listed session so the model can read current details instead of treating an older semantic anchor as the final state. These references are discovery context, not complete evidence. `memory_retrieve` loads full durable-memory details, while `session_read` loads required transcript detail or attachments under its exact permission boundary.

Explicit durable-memory overview intent uses bounded chronological prefetch and continuation state while still injecting every visible pin in full. `memory_retrieve`, `session_browse`, `session_search`, and `session_read` remain available for deliberate retrieval beyond automatic quotas. Assistant provider metadata records injected durable-memory and prior-session counts without duplicating their content.

## Durable Memory

A durable memory stores canonical content, category, audience, source provenance, revision, evidence links, seen state, and `pinned`. Categories are `context`, `fact`, `goal`, `identity`, and `preference`; sources are `manual`, `tool_call`, and `dreaming`. Normalized content hashes prevent case- and whitespace-equivalent duplicates.

Pinned records are supplied in full on every allowed model request. Any unpinned record can appear automatically as a relevant short snippet regardless of whether its source is `manual`, `tool_call`, or `dreaming`; full details remain available through `memory_retrieve`. MatoLoop does not expire or delete memories because of age, non-mention, weak relevance, capacity, exposure, or usage counts.

`memory_write` persists explicitly requested durable information and durable first-person context stated directly in the latest user message. It excludes temporary situations, hypotheticals, quotations, transformation source text, and incidental facts about third parties. An explicit request to always consider the information can pin the result; ordinary remember wording does not pin. Updates and explicit forgetting require an injected or retrieved memory ID and revision and recheck ownership, visibility, and stale-write protection.

`memory_retrieve` supports hybrid `search` and chronological `browse`, with `context` or `write` purpose. Both cover all audience-visible records regardless of pinning. References include the full content, ID, revision, category, audience, pin state, provenance, and timestamps.

The authenticated Memory UI retains full search, filtering, create, edit, pin or unpin, and delete access even when model-facing Memory or Dreaming is disabled. Every update and delete uses the record revision so a stale UI action cannot overwrite or delete a newer version.

## Prior-Session Index

`message_search_chunks` is a derived index of completed, non-error, nonempty user text and final assistant text without tool calls. Tool messages, tool output, reasoning, attachment data, runtime-error responses, and incomplete assistant output are excluded; eligible text accompanying an attachment remains searchable.

Chunking is deterministic and paragraph-aware. Each chunk has generated PostgreSQL FTS and a local E5 vector tied to its content and embedding profile. Persistence replaces eligible message chunks in the same transaction, making FTS available immediately; vector generation remains asynchronous. Undo and session deletion remove derived chunks through canonical-message cascades, and reconciliation repairs missing or stale index data.

Hybrid ranking selects at most one primary anchor per source session. Authenticated global search has the same owner-wide project scope as model-facing retrieval, including standalone sessions. Eligibility still differs: UI search may include the current session and all memory modes, while automatic retrieval requires other `full` source sessions and explicit session tools retain their permission checks.

## Dreaming

Dreaming is an optional extraction and reconciliation pipeline and only processes completed chat sessions whose `memoryMode` is `full`. Automation sessions are never Dreaming sources. Extraction receives a bounded source-message batch and isolated instructions, with no existing memories, prior-session context, normal agent context, persona, user profile, or general tools. It extracts durable user-specific knowledge important enough for future context, such as owned equipment, lasting technical setups and capabilities, durable relationships, recurring preferences, enduring constraints, and long-term personal projects or goals. Temporary or borrowed equipment, incidental technical details, current work state, and project implementation details remain in session history. Maintained profile fields are excluded. Every candidate requires direct supporting evidence from the batch, including a user message.

The host matches each candidate independently against visible memories. Exact normalized duplicates deterministically become `unchanged`; unmatched normal candidates become `create`; only non-removal candidates with possible related memories enter model reconciliation. Reconciliation defaults to creating a separate memory when identity is uncertain: a more specific statement does not replace a generic possession or preference unless the source identifies the same unique subject or an explicit correction. A removal candidate can target only one normalized exact record. An unmatched or merely related removal candidate is skipped and never reaches reconciliation or creates or removes a record. The action set is exactly:

- `create`: create an independent unpinned durable memory.
- `unchanged`: retain the matching record and add evidence.
- `update`: replace one record's canonical content and category.
- `merge`: combine two to fifty records transactionally.
- `remove`: permanently delete one exact matching record.

Removal candidates require direct user evidence that explicitly asks to forget the exact proposition or says it ended, is invalid, or is no longer true. Dreaming never removes a record because of age, non-mention, or weak relevance. A changed fact normally updates rather than removes.

Every newly created Dreaming memory is unpinned. Updating or merging records preserves existing pins but never promotes an unpinned record. Public-only source evidence creates `both` memories; private or mixed evidence creates `private_only` memories. Dreaming never creates `public_only`.

Automatic Dreaming is off by default. Manual and scheduled Dreaming both require an eligible `full` source session, enabled owner memory, and a suitable tool-capable model. Failed work retains its checkpoint and follows bounded retry behavior.

## Policies and Approvals

Dreaming has exactly two mutation policies, each with `disabled`, `ask`, or `auto`:

- `dreamingUpdatePolicy`, default `auto`, governs updates of existing records.
- `dreamingRemovePolicy`, default `ask`, governs removals.

A merge requires both update and removal permission. If either policy is `disabled`, the merge is skipped. If neither is disabled and either is `ask`, one merge approval is created. Only `auto` plus `auto` applies a merge without approval. Creation has no separate policy.

An `ask` decision creates a persistent `memory_change` approval that survives backend restart. Its payload stores exact target IDs, revisions, content, category, audience, pin state, evidence hash, source evidence, proposed result, and both policy snapshots. Approval locks and revalidates the source session, direct evidence where removal requires it, current policies, visibility, and every complete target snapshot. Any stale invariant expires the request without partial mutation.

Merge locks all targets in deterministic order, preserves evidence and existing pinning, computes a safe audience, and updates one deterministic survivor before deleting redundant rows in the same transaction. Dream runs report `createdMemories`, `updatedMemories`, and `removedMemories`; approved later mutations update the originating run counters in the same approval transaction.

The redesign migration expires pending approvals created under the retired memory approval contract. Retired merge and pin settings do not carry forward; the current update and removal defaults apply when their new fields are absent.

## API

The authenticated `/api/memories` API provides owner-scoped CRUD, paginated FTS and filters, acknowledgements, Memory settings, and Dreaming runs. JSON fields use camelCase and errors use the central stable error contract.

Create accepts `content`, `category`, and `audience`. Update accepts those fields plus `pinned` and required `expectedRevision`. Delete requires the `expectedRevision` query parameter. Responses include `revision`, pin state, audience, provenance, and timestamps.

Memory settings accept `enabled`, `automaticDreamingEnabled`, `dreamingInstructions`, `dreamingIntervalMinutes`, `dreamingIdleMinutes`, `dreamingMaxBatchMessages`, `dreamingMaxCandidates`, `dreamingUpdatePolicy`, `dreamingRemovePolicy`, `automaticMemorySnippetLimit`, `automaticSessionSnippetLimit`, `recentSessionSnippetLimit`, and `automaticContextMaxTokens`. Settings responses additionally expose effective Dreaming availability fields. Partial updates reject null and unknown fields.

The authenticated `/api/user-profile` API provides owner-scoped `GET` and partial `PATCH`. Changes are merged under the user-scoped settings lock; explicit null removes a value. The profile remains available to normal model requests when Memory is disabled. The host-controlled `update_user_profile` tool changes the same six fields only after an explicit latest-message request. Its separate `settings.user_profile.update` policy defaults to `ask`; the tool resolves the current model to its private or public trust scope and targets both scopes only when explicitly requested. Profile writes have no revision contract; the last completed settings-lock write wins.

## Ownership and Availability

Durable memories, deduplication, Dreaming state and runs, approvals, and prior-session retrieval are scoped by authenticated owner. Memory APIs provide no administrator override into another user's records. Account deletion cascades canonical memories and derived embeddings. Model and message trust plus durable-memory audiences are release classifications inside one owner's data, not user authorization.

Infinity is internal local infrastructure using the pinned multilingual E5 profile. pgvector stores vectors beside canonical data, so no external vector database is required. Separate durable-memory and prior-session workers generate vectors asynchronously with bounded retries and reconciliation. PostgreSQL FTS keeps automatic and explicit retrieval operational while Infinity is disabled, unavailable, or returning invalid data.
