# Memory and Dreaming

MatoLoop has three distinct context layers:

1. Current-session working context: canonical messages, a compacted summary when needed, and recent original history.
2. Episodic context: bounded excerpts retrieved automatically from prior sessions.
3. Durable memory: explicit records created manually, by `memory_write`, or by optional dreaming.

Episodic retrieval provides session awareness without first converting a conversation into durable memory. Dreaming is not required for either episodic or durable retrieval.

## Access and Trust

Each session has a mutable `memoryMode`:

- `full` authorizes automatic cross-session retrieval, `memory_retrieve`, `memory_write`, and dreaming.
- `read_only` authorizes automatic cross-session retrieval and `memory_retrieve`, but not writes or dreaming.
- `disabled` removes automatic cross-session retrieval and both memory tools and disables dreaming.

The user-scoped memory switch and the existing provider and model memory policies can further restrict these capabilities. A provider or model with memory policy `deny` disables memory even when the session mode permits it. Tool support affects memory tools and dreaming, but automatic retrieval itself does not require a model tool call.

Every run persists its effective model trust snapshot. Durable memories and prior-session messages retain `public` or `private` provenance. For these cross-session sources, public models receive only public content; private models may receive both trust levels. Ownership, current-session exclusion, and trust restrictions are applied before retrieval limits. Local embeddings do not weaken these SQL release filters.

Durable memories and prior-session episodes are explicitly untrusted data, never instructions. Conflict priority is:

1. Current user message
2. Pinned durable memory
3. Retrieved durable memory
4. Historical episode

An automatic episode is a bounded context reference, not permission to read its complete source session. `session_read` remains separately controlled: the current session is host-allowed, while another session requires the normal exact `session.read` grant or approval. Public session tools also retain their public-only release boundary.

## Automatic Context

On a normal user turn, MatoLoop builds one query from the latest user message. A short follow-up of at most 120 characters also includes up to 1,000 characters from the preceding user turn. The resulting query is limited to 2,000 characters.

When local semantic search is available, the host computes the query embedding once and reuses it for durable-memory and episodic search. Both searches combine PostgreSQL full-text ranking with local E5 cosine ranking through reciprocal rank fusion. If query embedding or vector search fails, lexical retrieval remains available.

Automatic retrieval is bounded as follows:

- At most three unpinned durable results, with at most 2,500 total memory-content characters.
- At most two prior-session episodes, with one anchor per source session.
- Semantic cosine-distance thresholds of `0.21` for durable memory and `0.25` for episodes.
- A shared automatic-context character budget derived from `min(1,536 tokens, 10% of usable model input)` using the selected model's characters-per-token estimate. Usable input is the context window minus maximum output and the safety reserve.

Pinned memories are injected in stable creation order whenever read access and trust allow it. They are not displaced by the automatic retrieval budget and are limited only by the selected model's final context projection. Retrieved unpinned memories consume the memory-content cap and automatic budget first; episode excerpts use the remaining automatic budget. The final context manager still guarantees that the complete request fits the selected model.

Generic intent about the latest or previous session uses recency retrieval instead of semantic retrieval. Explicit durable-memory overview intent uses a bounded memory browse prefetch and continuation state while still including every visible pinned memory in full. `memory_retrieve`, `session_browse`, `session_search`, and `session_read` remain available for deliberate retrieval beyond the automatic references, subject to their own policies and permissions.

The model-facing prompt reports visible durable-memory totals and the counts injected into the request. Assistant provider metadata records `durableMemoryReferences` and `priorSessionEpisodes` under `_matoloop.performance`, alongside context preparation and provider timing; it does not duplicate retrieved content.

## Durable Memory

A durable memory stores canonical content, category, trust, source provenance, revision, evidence links, seen state, and `pinned`. Categories remain `context`, `fact`, `goal`, `identity`, and `preference`; sources remain `manual`, `tool_call`, and `dreaming`. Normalized content hashes prevent case- and whitespace-equivalent duplicates. PostgreSQL keeps a generated `simple` full-text vector with a GIN index.

`pinned` is the only injection distinction. Pinned records are always supplied automatically when visible; unpinned records are selected by hybrid retrieval. All durable memories, including pinned records, remain in the semantic index. There are no active/retrieve tiers, capacity lifecycle, exposure attribution, usage counts, promotion, or demotion.

`memory_write` persists explicitly requested durable information and durable first-person self-context stated directly in the latest user message. It excludes temporary situations, hypotheticals, quotations, source text supplied for transformation, and incidental facts about third parties. Writes use the existing `memory.write` permission flow. Updates require an injected or retrieved memory ID and revision; the host enforces trust, provenance, stale-write protection, evidence, and deduplication.

`memory_retrieve` supports hybrid `search` and chronological `browse`, with `context` or `write` purpose. Search and browse cover all trust-visible durable records regardless of pinning. References include ID, revision, category, pin state, content, source session, and source, creation, and update timestamps. Timestamp metadata describes observation and record lifecycle; it does not prove that a time-dependent fact remains current.

## Episodic Index

`message_search_chunks` is the derived search index for prior-session episodes. Eligible source content is completed, non-error, nonempty user text and final assistant text without tool calls. Tool messages, tool output, reasoning, attachment data, runtime-error responses, and incomplete assistant output are excluded; eligible text accompanying an attachment remains text.

Chunking is deterministic and paragraph-aware, targets 1,200 characters, never exceeds 1,500 characters, and overlaps adjacent chunks by 120 characters. Each chunk has a generated PostgreSQL `simple` full-text vector with a GIN index and a local 384-dimensional E5 vector tied to the active embedding profile and content hash.

Retrieval filters by owner, excludes the current session, and applies the run trust boundary before top-K ranking. Hybrid ranking selects at most one anchor per prior session. The host then creates a chronological, trust-filtered excerpt around each anchor, bounded by message count and characters; the automatic context applies its smaller shared turn budget afterward. Session titles are released only when their first-user-message trust permits it.

The index is derived from canonical messages. Undo deletes chunks for its removed message suffix through database cascades, and deleting a session cascades through messages to their chunks. Reconciliation also removes chunks whose messages are no longer eligible.

## Dreaming

Dreaming remains an optional extraction and reconciliation pipeline. It processes eligible completed session text incrementally, extracts evidence-backed durable candidates, and reconciles them with trust-visible existing memories before creating, updating, or keeping records. It does not provide session awareness, perform retrieval for normal turns, attribute memory exposure, count usage, or manage pinning or lifecycle tiers.

Automatic dreaming is off by default and runs only for `full` sessions when user memory, a suitable tool-capable model, and automatic dreaming are enabled. Manual processing requires memory and a suitable model but not the automatic switch. Failed work retains its checkpoint and follows bounded retry behavior.

The user-scoped `MemorySettings` contains only the general switch, automatic dreaming switch, optional extraction instructions, scheduling interval, idle interval, batch-message limit, and candidate limit. Lifecycle budgets and thresholds are no longer settings.

## API

The authenticated `/api/memories` API provides CRUD, paginated full-text search and filtering by source, category, trust, and `pinned`, acknowledgements, user-scoped memory settings, and owner-filtered dream runs.

Create accepts content, category, and trust. Update accepts those fields plus `pinned`. Memory payloads expose `pinned` and provenance but no retrieval tier, tier lock, tier timestamps, last-used timestamp, or usage count. Settings payloads expose only current memory and dreaming controls plus effective dreaming availability. Partial settings updates reject null and unknown fields. JSON fields use camelCase and errors use the central stable error contract.

## Indexing and Migration

Infinity is internal local infrastructure running the pinned `intfloat/multilingual-e5-small` profile. Queries use the `query: ` prefix; durable memories and episode chunks use `passage: `. pgvector stores vectors in PostgreSQL, so no external vector database is required.

Separate durable-memory and episode schedulers claim queue batches with `FOR UPDATE SKIP LOCKED`, commit claims before embedding calls, validate content hash and profile before publishing vectors, recover stale claims, and retry transient failures with bounded backoff. They periodically reconcile canonical data with derived indexes. PostgreSQL full-text search keeps retrieval operational while Infinity is disabled, unavailable, or returning invalid data.

The redesign migration creates `message_search_chunks`, converts previously fixed instant memories to `pinned`, and removes lifecycle and exposure columns and tables. Startup reconciliation backfills deterministic chunks for all eligible existing messages and queues missing or stale embeddings for every durable memory and episode chunk. Profile or content changes are therefore repaired without rewriting canonical messages or memories.

## Ownership Boundary

The current product remains single-user, but sessions, settings, dream runs, and episodic retrieval already use persistent user ownership. Durable-memory storage and deduplication remain installation-wide and need an owner key before multiple accounts are supported. Trust levels are model data-release classifications, not user authorization.
