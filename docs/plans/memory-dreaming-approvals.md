# Memory, Project Context, Dreaming, and Approvals

Status: implemented in August 2026. This file is retained as an implementation record.

## Implemented Contract

- Every visible pin is injected in full. Automatic unpinned-memory snippets treat `manual`, `tool_call`, and `dreaming` sources equally.
- Automatic project context includes a compact index of up to ten recent `full` sessions and prioritizes relevant snippets from distinct `full` sessions in the same project. The index points to each session's latest eligible message; a semantic reference contains only its strongest topical anchor. Project instructions remain an optional authoritative layer for deliberately maintained rules.
- Dreaming extraction receives only its bounded chat source-message batch and returns evidence-backed broad durable-memory candidates outside the maintained user profile, not ordinary project or task facts.
- The host matches every candidate independently. Exact normalized duplicates become `unchanged`, unmatched normal candidates become `create`, and only non-removal candidates with possible related memories enter model reconciliation.
- Removal requires direct user evidence and one normalized exact target. Unmatched or merely related removal candidates are skipped before reconciliation.
- Reconciliation supports `create`, `unchanged`, `update`, `merge`, and exact `remove`.
- Every new Dreaming memory is unpinned. Updates and merges preserve existing pins but never promote records.
- Update and remove policies are `disabled`, `ask`, or `auto`, with defaults of `auto` and `ask`. Merge requires both policies.
- `ask` uses the generic persistent approval envelope with `request_type=memory_change` and `approve` or `deny`. These approvals do not depend on a live agent run and survive backend restart.
- Approval payloads snapshot target IDs, revisions, content, category, trust, pin state, evidence, source context, proposed result, and policies. Decision handling locks and revalidates the complete snapshot; a stale target or policy expires the request without partial mutation.
- Merge locks two to fifty records in deterministic ID order. It chooses an exact-content target with the lowest ID as survivor, otherwise the lowest-ID target. The survivor receives canonical content and category, united evidence, private trust if any input is private, and the logical OR of existing pinning before redundant records are deleted in the same transaction.
- Approved updates, merges, and removals update the originating Dreaming run counters in the approval transaction.
- Derived embeddings are refreshed only when canonical content changes.
- Preferred name, form of address, primary residence, pronouns, and primary language live in the separate maintained user profile. Dreaming neither reads nor changes it.
- The global approvals page and notification center show memory updates, merges, and removals with their before and proposed states.

## Remaining Exclusions

- No full scan or model-driven consolidation of all existing memories; reconciliation occurs only when a new candidate has local matches.
- No separate memory proposal, maintenance, or workflow-engine tables.
- No automatically generated project summary or project-memory table.
- No approval history beyond the shared request status and payload.
- No model-driven user-profile mutation.
- No Dreaming pin or unpin action.
- No merge undo, tombstones, or redirects.
- No model-driven mutation of project instructions; users maintain them explicitly.
