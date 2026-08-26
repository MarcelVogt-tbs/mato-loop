# Memory and Dreaming

MatoLoop projects three memory layers into a normal model request:

1. Canonical current-session context, compacted when necessary.
2. Every audience-visible pinned durable memory in full.
3. Bounded automatic snippets from relevant explicit unpinned durable memories and prior sessions.

Durable memory and prior-session retrieval are independent. A conversation does not need to be processed by Dreaming before it can be found as prior-session context.

Project instructions are a separate optional authoritative layer for deliberately maintained project rules and decisions. Automatic project awareness primarily comes from bounded snippets of other sessions in that project; instructions do not replace the project history.

## Access, Scope, and Trust

Each session has a mutable `memoryMode`:

- `full` can receive memory context, use `memory_retrieve` and `memory_write`, and act as a Dreaming and automatic prior-session source.
- `read_only` can receive memory context and use `memory_retrieve`, but cannot write memories, run Dreaming, or act as an automatic prior-session source.
- `disabled` receives no memory context and exposes neither memory tool, but cannot act as an automatic prior-session source.

The owner memory switch and effective provider and model memory policies can further restrict model-facing memory. Tool support affects memory tools and Dreaming, but not host-side automatic retrieval. The authenticated UI can still search and manage durable memories while these switches are off. Authenticated global session search and explicit permission-controlled `session_browse`, `session_search`, and `session_read` also continue to cover owned `read_only` and `disabled` sessions.

Automatic prior-session retrieval uses only other `full` chat sessions in the same project scope. Project sessions remain inside their project; standalone sessions use standalone sources. The current receiving session may be `full` or `read_only`. Retrieval filters by owner and excludes the current session before ranking. Selecting the model is the release decision for already authorized session messages; durable-memory audience filtering remains independent.

Durable memories and prior-session snippets are untrusted data, never instructions. Conflict priority is:

1. Current user message
2. User-configured project instructions
3. Pinned durable memory
4. Automatically retrieved or explicitly loaded durable memory
5. Prior-session snippet or transcript

## Automatic Context

Every eligible normal turn builds one query of at most 2,000 characters from the latest user message. When that message is short, the query includes the previous user message as follow-up context. The host computes at most one query embedding and shares it across searches for unpinned durable memories and relevant prior sessions. Both searches combine PostgreSQL FTS and local cosine ranking; unavailable or invalid embeddings fall back to FTS.

The ranked results are cached across tool rounds in one agent run to avoid repeated embedding work. Before every later model request, the host reloads pins and revalidates cached automatic memories against current owner, audience, pin, and source state and cached episode references against current owner, project, and source-session mode.

The configurable fixed quotas default to:

- two relevant unpinned memory snippets through `automaticMemorySnippetLimit`;
- three relevant prior-session snippets through `automaticSessionSnippetLimit`;
- two recent-session snippets for first-turn bootstrap or explicit recent-session overview through `recentSessionSnippetLimit`.

An explicit request not to use memory or prior context, and detected requests that only transform supplied text, suppress automatic snippets. The first turn and explicit recent-session overview may add recent snippets; relevance retrieval still uses the same turn query. Duplicate source sessions are removed. Project requests prioritize relevant snippets from distinct project sessions, then non-overlapping recent snippets, then explicit unpinned memories. Standalone requests retain the smaller single relevant-session maximum and give recent snippets precedence.

The complete serialized automatic payload shares one configurable budget, `automaticContextMaxTokens`, defaulting to 1,024 tokens and capped at 10% of usable model input. Usable input is the context window minus maximum output and the safety reserve. The host converts that token limit with the model's characters-per-token estimate and truncates or omits automatic snippets to fit. Fixed quotas are maxima, not guarantees.

Visible pins are serialized in full and remain outside the automatic snippet budget. The final context projection first tries pins plus automatic snippets and can retry with pins only. It fails safely with the normal context-window error if all visible pins and required request context cannot fit; pins are never silently truncated or dropped.

An automatic memory hit contains a short canonical-content preview and stable ID and revision. A relevant-session hit contains the strongest bounded topical anchor, at most one newer ranked topical anchor from the same session, and exact `session_read` arguments for each; a recent hit contains a small tail. These snippets are discovery context, not complete evidence. `memory_retrieve` loads full durable-memory details, while `session_read` loads required transcript detail or attachments under its exact permission boundary.

Explicit durable-memory overview intent uses bounded chronological prefetch and continuation state while still injecting every visible pin in full. `memory_retrieve`, `session_browse`, `session_search`, and `session_read` remain available for deliberate retrieval beyond automatic quotas. Assistant provider metadata records injected durable-memory and prior-session counts without duplicating their content.

## Durable Memory

A durable memory stores canonical content, category, audience, source provenance, revision, evidence links, seen state, `pinned`, and an optional core-identity `pinKind`. Categories are `context`, `fact`, `goal`, `identity`, and `preference`; sources are `manual`, `tool_call`, and `dreaming`. Normalized content hashes prevent case- and whitespace-equivalent duplicates.

Pinned records are supplied in full on every allowed model request. Unpinned `manual` and `tool_call` records can appear automatically as relevant short snippets; full details for every source remain available through `memory_retrieve`. Legacy unpinned `dreaming` records remain valid and manageable but are not injected automatically. Core identity uses only `preferred_name`, `form_of_address`, `pronouns`, `primary_language`, and `primary_residence`; an ordinary pin has no `pinKind`. MatoLoop does not expire or delete memories because of age, non-mention, weak relevance, capacity, exposure, or usage counts.

`memory_write` persists explicitly requested durable information and durable first-person context stated directly in the latest user message. It excludes temporary situations, hypotheticals, quotations, transformation source text, and incidental facts about third parties. An explicit request to always consider the information can pin the result; ordinary remember wording does not pin. Updates and explicit forgetting require an injected or retrieved memory ID and revision and recheck ownership, visibility, and stale-write protection.

`memory_retrieve` supports hybrid `search` and chronological `browse`, with `context` or `write` purpose. Both cover all audience-visible records regardless of pinning. References include the full content, ID, revision, category, audience, pin state, optional `pinKind`, provenance, and timestamps.

The authenticated Memory UI retains full search, filtering, create, edit, pin or unpin, and delete access even when model-facing Memory or Dreaming is disabled. Every update and delete uses the record revision so a stale UI action cannot overwrite or delete a newer version.

## Prior-Session Index

`message_search_chunks` is a derived index of completed, non-error, nonempty user text and final assistant text without tool calls. Tool messages, tool output, reasoning, attachment data, runtime-error responses, and incomplete assistant output are excluded; eligible text accompanying an attachment remains searchable.

Chunking is deterministic and paragraph-aware. Each chunk has generated PostgreSQL FTS and a local E5 vector tied to its content and embedding profile. Persistence replaces eligible message chunks in the same transaction, making FTS available immediately; vector generation remains asynchronous. Undo and session deletion remove derived chunks through canonical-message cascades, and reconciliation repairs missing or stale index data.

Hybrid ranking selects at most one primary anchor per source session and may attach one newer ranked anchor from that session. Authenticated global search deliberately differs from model-facing retrieval: it remains owner-scoped but spans the owner's permitted project scope and memory modes according to the UI search contract.

## Dreaming

Dreaming is an optional extraction and reconciliation pipeline and only processes completed source sessions whose `memoryMode` is `full`. Extraction receives a bounded source-message batch and isolated instructions, with no existing memories, prior-session context, normal agent context, persona, or general tools. It extracts only broad persistent profile facts important enough for future context, such as stable identity, durable relationships, recurring preferences, enduring constraints, and long-term personal goals. Every candidate requires direct supporting evidence from the batch, including a user message.

The host matches each candidate independently against visible memories. Exact normalized duplicates deterministically become `unchanged`; unmatched normal candidates become `create`; only non-removal candidates with possible related memories enter model reconciliation. A removal candidate can target only one normalized exact record. An unmatched or merely related removal candidate is skipped and never reaches reconciliation or creates or removes a record. The action set is exactly:

- `create`: create an independent pinned durable memory.
- `unchanged`: retain the matching record and add evidence.
- `update`: replace one record's canonical content and category.
- `merge`: combine two to fifty records transactionally.
- `remove`: permanently delete one exact matching record.

Removal candidates require direct user evidence that explicitly asks to forget the exact proposition or says it ended, is invalid, or is no longer true. Dreaming never removes a record because of age, non-mention, or weak relevance. A changed fact normally updates rather than removes.

Every newly created Dreaming memory is pinned. `dreamingMaxPinnedMemories` is configurable from 1 through 100 and defaults to 10. Dreaming counts all pinned memories, including manual pins, before creating a record. Manual pinning may exceed the limit. At or above the limit Dreaming creates no new record, but can still classify unchanged candidates and update, merge, or remove existing records. Existing unpinned records are not migrated or automatically deleted.

Public-only source evidence creates `both` memories; private or mixed evidence creates `private_only` memories. Dreaming never creates `public_only`. Core profile fields can receive their fixed `pinKind`; other broad profile facts are ordinary pins.

Automatic Dreaming is off by default. Manual and scheduled Dreaming both require an eligible `full` source session, enabled owner memory, and a suitable tool-capable model. Failed work retains its checkpoint and follows bounded retry behavior.

## Policies and Approvals

Dreaming has exactly two mutation policies, each with `disabled`, `ask`, or `auto`:

- `dreamingUpdatePolicy`, default `auto`, governs updates and pin promotion of existing records.
- `dreamingRemovePolicy`, default `ask`, governs removals.

A merge requires both update and removal permission. If either policy is `disabled`, the merge is skipped. If neither is disabled and either is `ask`, one merge approval is created. Only `auto` plus `auto` applies a merge without approval. Creation has no separate policy and is bounded by the pinned-memory limit. The retired merge and pin policies no longer exist.

An `ask` decision creates a persistent `memory_change` approval that survives backend restart. Its payload stores exact target IDs, revisions, content, category, audience, pin state, optional `pinKind`, evidence hash, source evidence, proposed result, and both policy snapshots. Approval locks and revalidates the source session, direct evidence where removal requires it, current policies, visibility, and every complete target snapshot. Any stale invariant expires the request without partial mutation.

Merge locks all targets in deterministic order, preserves evidence and existing pinning, computes a safe audience, and updates one deterministic survivor before deleting redundant rows in the same transaction. Dream runs report `createdMemories`, `updatedMemories`, and `removedMemories`; approved later mutations update the originating run counters in the same approval transaction.

The redesign migration expires pending approvals created under the retired memory approval contract. Retired merge and pin settings do not carry forward; the current update and removal defaults apply when their new fields are absent.

## API

The authenticated `/api/memories` API provides owner-scoped CRUD, paginated FTS and filters, acknowledgements, Memory settings, and Dreaming runs. JSON fields use camelCase and errors use the central stable error contract.

Create accepts `content`, `category`, `audience`, and optional `pinKind`. Update accepts those fields plus `pinned` and required `expectedRevision`. Delete requires the `expectedRevision` query parameter. Responses include `revision`, pin state, optional `pinKind`, audience, provenance, and timestamps.

Memory settings accept `enabled`, `automaticDreamingEnabled`, `dreamingInstructions`, `dreamingIntervalMinutes`, `dreamingIdleMinutes`, `dreamingMaxBatchMessages`, `dreamingMaxCandidates`, `dreamingUpdatePolicy`, `dreamingRemovePolicy`, `dreamingMaxPinnedMemories`, `automaticMemorySnippetLimit`, `automaticSessionSnippetLimit`, `recentSessionSnippetLimit`, and `automaticContextMaxTokens`. Settings responses additionally expose `pinnedMemoryCount` and effective Dreaming availability fields. Partial updates reject null and unknown fields.

## Ownership and Availability

Durable memories, deduplication, Dreaming state and runs, approvals, and prior-session retrieval are scoped by authenticated owner. Memory APIs provide no administrator override into another user's records. Account deletion cascades canonical memories and derived embeddings. Model and message trust plus durable-memory audiences are release classifications inside one owner's data, not user authorization.

Infinity is internal local infrastructure using the pinned multilingual E5 profile. pgvector stores vectors beside canonical data, so no external vector database is required. Separate durable-memory and prior-session workers generate vectors asynchronously with bounded retries and reconciliation. PostgreSQL FTS keeps automatic and explicit retrieval operational while Infinity is disabled, unavailable, or returning invalid data.
