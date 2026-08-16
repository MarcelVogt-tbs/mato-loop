# Memory Dreaming Consolidation and Approvals

Status: implemented in August 2026. This file is retained as an implementation record.

## Implemented Contract

- Dreaming extraction receives only its bounded source-message batch and returns evidence-backed candidates.
- The host performs trust-filtered matching independently for each candidate. Exact normalized duplicates become `unchanged`, unmatched candidates become `create`, and only candidates with possible related memories enter model reconciliation.
- Reconciliation supports `create`, `unchanged`, `update`, and `merge`, with one-way pin suggestions.
- New Dreaming memories remain automatic. Update, merge, and pin policies are `disabled`, `ask`, or `auto`, with defaults of `auto`, `ask`, and `ask` respectively.
- `ask` uses the generic persistent approval envelope with `request_type=memory_change` and `approve` or `deny`. These approvals do not depend on a live agent run and survive backend restart.
- Approval payloads snapshot target IDs, revisions, content, category, trust, pin state, evidence, source context, proposed result, and policies. Decision handling locks and revalidates the complete snapshot; a stale target or policy expires the request without partial mutation.
- Merge locks two to fifty records in deterministic ID order. It chooses an exact-content target with the lowest ID as survivor, otherwise the lowest-ID target. The survivor receives canonical content and category, united evidence, private trust if any input is private, and the logical OR of existing pinning before redundant records are deleted in the same transaction.
- Derived embeddings are refreshed only when canonical content changes.
- Pin suggestions are limited to identity memories for preferred name, form of address, primary residence, pronouns, or primary language. Dreaming never unpins, and automatic pinning stops at five pinned memories.
- The global approvals page and notification center show memory updates, merges, and pin suggestions with their before and proposed states.

## Remaining Exclusions

- No full scan or model-driven consolidation of all existing memories; reconciliation occurs only when a new candidate has local matches.
- No separate memory proposal, maintenance, or workflow-engine tables.
- No approval history beyond the shared request status and payload.
- No structured contact or user-profile fields.
- No automatic pinning of contact details or categories outside the fixed identity allowlist.
- No Dreaming unpin action.
- No merge undo, tombstones, or redirects.
- No configurable pin categories or automatic pin limit.
- No multi-user durable-memory ownership. Durable records and normalized deduplication remain installation-wide until an owner key is added.
