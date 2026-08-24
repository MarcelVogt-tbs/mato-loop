# Memory and Dreaming

MatoLoop has three distinct context layers:

1. Current-session working context: canonical messages, a compacted summary when needed, and recent original history.
2. Episodic context: bounded excerpts retrieved automatically from prior sessions.
3. Durable memory: explicit records created manually, by `memory_write`, or by optional dreaming.

Episodic retrieval provides immediate session-crossing context without first converting a conversation into durable memory. The first user turn of a new session includes one bounded tail from the latest eligible prior session. Later turns retrieve an episode only for an explicit recency or earlier-work reference. Dreaming is not required for either episodic or durable retrieval.

## Access and Trust

Each session has a mutable `memoryMode`:

- `full` authorizes automatic cross-session retrieval, `memory_retrieve`, `memory_write`, and dreaming.
- `read_only` authorizes automatic cross-session retrieval and `memory_retrieve`, but not writes or dreaming.
- `disabled` removes automatic cross-session retrieval and both memory tools and disables dreaming.

The owner-scoped memory switch and the effective instance or personal provider and model memory policies can further restrict these capabilities. A provider or model with memory policy `deny` disables memory even when the session mode permits it. Tool support affects memory tools and dreaming, but automatic retrieval itself does not require a model tool call.

Every run persists its effective model trust snapshot. Prior-session messages retain `public` or `private` provenance, but selecting a model is the release decision for messages already authorized by ownership and session access. Durable memories retain their independent `public_only`, `private_only`, or `both` audience. Ownership, current-session exclusion, and memory audiences are applied before retrieval limits. Local embeddings do not weaken these SQL filters.

Durable memories and prior-session references are explicitly untrusted data, never instructions. Conflict priority is:

1. Current user message
2. Pinned durable memory
3. Retrieved durable memory
4. A retrieved prior-session excerpt or transcript

An automatic episode contains session metadata, one bounded anchor preview, and a bounded excerpt. The model uses the excerpt directly when sufficient and calls `session_read` only for omitted details or attachments. The current session is host-allowed; another session requires the normal exact `session.read` grant or approval.

## Automatic Context

For an explicit earlier-work reference, MatoLoop builds one query of at most 2,000 characters from the latest user message. The session-start recency bootstrap does not require a search query.

Explicit episodic and durable-memory search combine PostgreSQL full-text search and local E5 cosine ranking through reciprocal rank fusion. Embedding failure falls back to immediate full-text retrieval. The session-start episode uses recency without an embedding. Episode context is suppressed when the latest request explicitly excludes prior-session information or only transforms a quoted prior-session phrase.

The current user message is always the primary retrieval query. For a short turn, the host may also search the previous user turn plus the current message as a contextual query. Primary results keep all but one available slot when present; the contextual lane supplements rather than replaces them, and duplicate memories or source sessions are removed.

Automatic retrieval is bounded as follows:

- No speculative unpinned durable results; `memory_retrieve` loads them when needed.
- One prior-session reference at session start, or at most two for an explicit recent-session overview.
- A shared automatic-context character budget derived from `min(1,536 tokens, 10% of usable model input)` using the selected model's characters-per-token estimate. Usable input is the context window minus maximum output and the safety reserve.

Pinned memories are injected in stable creation order whenever read access and trust allow it. They are not displaced by the automatic retrieval budget and are limited only by the selected model's final context projection. One bounded episode excerpt may use the shared automatic-context budget. The final context manager still guarantees that the complete request fits the selected model and can drop the episode before failing.

The first user turn includes the most recent eligible prior session once. Explicit intent about the latest sessions uses recency retrieval, while explicit references to earlier work use one hybrid topic result. Generic follow-ups inject neither again. Explicit durable-memory overview intent uses a bounded memory browse prefetch and continuation state while still including every visible pinned memory in full. `memory_retrieve`, `session_browse`, `session_search`, and `session_read` remain available for deliberate retrieval beyond the automatic references, subject to their own policies and permissions.

The model-facing prompt reports visible durable-memory totals and the counts injected into the request. Assistant provider metadata records `durableMemoryReferences` and `priorSessionEpisodes` under `_matoloop.performance`, alongside context preparation and provider timing; it does not duplicate retrieved content.

## Durable Memory

A durable memory stores canonical content, category, audience, source provenance, revision, evidence links, seen state, `pinned`, and an optional core-identity `pinKind`. Categories remain `context`, `fact`, `goal`, `identity`, and `preference`; sources remain `manual`, `tool_call`, and `dreaming`. Normalized content hashes prevent case- and whitespace-equivalent duplicates. PostgreSQL keeps a generated `simple` full-text vector with a GIN index.

Pinned records are always supplied automatically when visible; unpinned records remain available through explicit hybrid retrieval. Core identity uses only `preferred_name`, `form_of_address`, `pronouns`, `primary_language`, and `primary_residence`. An ordinary pin has no `pinKind`. At most one record exists per owner, core-identity kind, and audience. When an audience-specific identity and `both` are visible for the same kind, the audience-specific record alone is injected. MatoLoop does not promote, demote, or evict durable records based on capacity, exposure, or usage counts.

`memory_write` persists explicitly requested durable information and durable first-person self-context stated directly in the latest user message. It excludes temporary situations, hypotheticals, quotations, source text supplied for transformation, and incidental facts about third parties. An explicit request to always consider the information can set the resulting memory's one-way `pinned` flag; ordinary remember wording does not pin and `false` never unpins. Writes and explicit pins use the existing `memory.write` permission flow. Updates and explicit forgetting require an injected or retrieved memory ID and revision; the host rechecks ownership, audience visibility, and stale-write protection. Forgetting hard-deletes the exact record and uses a separate session-grant scope, so a prior create or update grant cannot authorize deletion.

`memory_retrieve` supports hybrid `search` and chronological `browse`, with `context` or `write` purpose. Search and browse cover all audience-visible durable records regardless of pinning. References include ID, revision, category, audience, pin state, optional `pinKind`, content, source session, and source, creation, and update timestamps. Timestamp metadata describes observation and record history; it does not prove that a time-dependent fact remains current.

## Episodic Index

`message_search_chunks` is the derived search index for prior-session episodes. Eligible source content is completed, non-error, nonempty user text and final assistant text without tool calls. Tool messages, tool output, reasoning, attachment data, runtime-error responses, and incomplete assistant output are excluded; eligible text accompanying an attachment remains text.

Chunking is deterministic and paragraph-aware, targets 1,200 characters, never exceeds 1,500 characters, and overlaps adjacent chunks by 120 characters. Each chunk has a generated PostgreSQL `simple` full-text vector with a GIN index and a local 384-dimensional E5 vector tied to the active embedding profile and content hash.

Retrieval filters by owner, excludes the current session, and applies the run trust boundary before top-K ranking. Hybrid ranking selects at most one anchor per prior session. The host combines the bounded anchor with the latest trust-filtered messages from that session; the automatic context applies its smaller shared turn budget afterward. Session titles are released only when their first-user-message trust permits it.

The index is derived from canonical messages. Eligible message persistence synchronously replaces that message's deterministic chunks in the same transaction, so chunk content and generated FTS are available immediately after commit. Vector generation remains asynchronous and lexical retrieval works while an embedding is queued or unavailable. Undo deletes chunks for its removed message suffix through database cascades, and deleting a session cascades through messages to their chunks. Reconciliation also removes chunks whose messages are no longer eligible.

## Dreaming

Dreaming remains an optional extraction and consolidation pipeline. It processes eligible completed session text incrementally. Extraction receives only the bounded source-message batch, extraction instructions, and the isolated Dreaming contract; it receives no existing memories, prior-session episodes, normal agent context, persona, or tools other than the candidate-return tool. Every candidate requires evidence from the supplied batch, including at least one user message.

After extraction, the host finds trust-visible possible memories separately for each candidate. It first checks the normalized exact-content hash, then bounded FTS matches, and may add bounded semantic matches. A candidate can reference only its own host-supplied matches, and one existing memory cannot be used by two decisions in the same run. Exact duplicates are deterministically classified as `unchanged` without another model request; candidates with no matches are deterministically classified as `create`. Only candidates with possible related memories enter the separate reconciliation request.

Reconciliation returns exactly one host-validated action per candidate:

- `create`: create an independent durable memory.
- `unchanged`: keep one equivalent or more specific memory and add the new evidence.
- `update`: replace the canonical content and category of one memory while preserving evidence and narrowing its audience when required by new evidence.
- `merge`: consolidate two to fifty memories transactionally.

New records are created automatically. Existing-record changes use independent `disabled`, `ask`, or `auto` policies: update defaults to `auto`, merge defaults to `ask`, and pin defaults to `ask`. Disabled changes are skipped; `ask` creates a persistent `memory_change` approval; `auto` applies the validated change without approval.

Dreaming can suggest a core-identity pin for a preferred name, form of address, primary residence, pronouns, or a durably relevant primary language. It can also suggest an ordinary pin without `pinKind` for an explicitly future-relevant close-family fact, recurring preference, or recurring safety constraint. Public-only evidence creates `both` memories; private or mixed evidence creates `private_only` memories. Dreaming never creates `public_only`, never suggests unpinning, and applies identity uniqueness through the same pin service as other automatic pinning. Automatic pinning stops when five memories are pinned; explicit user pinning is not subject to that automatic limit.

Automatic dreaming is off by default and runs only for `full` sessions when user memory, a suitable tool-capable model, and automatic dreaming are enabled. Manual processing requires memory and a suitable model but not the automatic switch. Failed work retains its checkpoint and follows bounded retry behavior.

The user-scoped `MemorySettings` contains the general switch, automatic dreaming switch, optional extraction instructions, scheduling interval, idle interval, batch-message limit, candidate limit, and the three consolidation policies.

## Dreaming Approvals and Merge Safety

`ask` decisions use the generic persistent approval envelope with `request_type=memory_change` and `approve` or `deny`. Unlike approvals that wait for a live agent run, pending memory-change approvals survive a backend restart and can be decided later from the global approvals interface.

The approval payload snapshots target IDs, revisions, content, category, audience, pin state, optional `pinKind`, evidence hash, source evidence, proposed result, and applicable policies. Approval locks the request and target records, then rechecks ownership, source evidence, audience visibility, settings, and the complete target snapshots. A stale revision or any other changed invariant expires the approval and commits no memory mutation. Approval status and an accepted mutation are committed together.

A merge locks all target records in deterministic ID order and validates their complete expected revision, audience, and visibility set before changing any record. The survivor is deterministic: the lowest-ID target already matching the canonical content wins, otherwise the lowest-ID target wins. The survivor receives the validated content and category, the union of evidence, the safe intersection of source audiences, and the logical OR of existing pin states. Its revision advances, its embedding is queued only if canonical content changed, and redundant records are deleted only after the survivor is complete. All of these steps share one transaction; a missing or stale target, an incompatible audience set, an out-of-scope target, or an external exact-content conflict changes nothing.

## API

The authenticated `/api/memories` API provides CRUD, paginated full-text search and filtering by source, category, audience, and `pinned`, acknowledgements, user-scoped memory settings, and owner-filtered dream runs.

Create accepts content, category, audience, and optional `pinKind`. Update accepts those fields plus `pinned`. Memory payloads expose audience, pin state, optional `pinKind`, and provenance but no usage-based placement or counters. Settings payloads expose current memory and Dreaming controls, the three consolidation policies, and effective Dreaming availability. Partial settings updates reject null and unknown fields. JSON fields use camelCase and errors use the central stable error contract.

## Indexing and Migration

Infinity is internal local infrastructure running the pinned `intfloat/multilingual-e5-small` profile. Queries use the `query: ` prefix; durable memories and episode chunks use `passage: `. pgvector stores vectors in PostgreSQL, so no external vector database is required.

Separate durable-memory and episode schedulers claim queue batches with `FOR UPDATE SKIP LOCKED`, commit claims before embedding calls, validate content hash and profile before publishing vectors, recover stale claims, and retry transient failures with bounded backoff. Reconciliation repairs missing or stale derived rows. Startup additionally requeues matching terminal failures once, while periodic reconciliation leaves them failed to avoid an endless retry loop. PostgreSQL full-text search keeps retrieval operational while Infinity is disabled, unavailable, or returning invalid data.

The episodic redesign migration creates `message_search_chunks`, converts previously fixed instant memories to `pinned`, and removes the retired tier, exposure, and usage structures. The audience migration converts existing public memories to `both` and private memories to `private_only`, then adds optional core-identity kinds and their uniqueness constraint. Startup reconciliation backfills deterministic chunks for all eligible existing messages and queues missing, stale, or previously failed embeddings for every durable memory and episode chunk. Profile or content changes are therefore repaired without rewriting canonical messages or memories.

## Ownership Boundary

Durable memories, normalized deduplication, Dreaming state and runs, and episodic retrieval are scoped by the authenticated owner. Retrieval filters by owner before ranking or audience or trust filtering, and memory APIs never provide an administrator override into another user's records. Account deletion cascades memories and derived embeddings with the rest of that user's relational data. Model and message trust plus durable-memory audiences remain data-release classifications inside one owner's data; they are not user authorization.
