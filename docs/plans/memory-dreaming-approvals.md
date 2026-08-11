# Memory Dreaming Consolidation and Approvals

Status: deferred. Implement the global pending approvals interface before starting this plan.

## Goal

Keep the current durable-memory and retrieval design while allowing Dreaming to update,
merge, and selectively pin related memories when new conversation evidence touches them.
Changes that require approval use the shared approval-request model rather than a
memory-specific proposal system.

## Decisions

- Do not add a separate memory-maintenance scheduler or full historical scan.
- Reconcile existing memories only when a new Dreaming candidate touches the same facts.
- Keep automatic creation of new Dreaming memories unchanged.
- Make Dreaming updates configurable with `disabled`, `ask`, and `auto`; default to `auto`.
- Make Dreaming merges configurable with `disabled`, `ask`, and `auto`; default to `ask`.
- Make Dreaming pinning configurable with `disabled`, `ask`, and `auto`; default to `ask`.
- Use the generic persistent approval-request envelope for `ask` decisions.
- Keep approval execution type-specific: agent actions resume a waiting run, while memory
  changes lock and mutate memory records transactionally.
- Do not add structured user-profile fields. Conversational identity remains durable memory.

## Reconciliation Contract

Extend Dreaming reconciliation with these host-validated actions:

```text
create      no existing memory ID
unchanged   exactly one existing memory ID
update      exactly one existing memory ID plus canonical content and category
merge       at least two existing memory IDs plus canonical content and category
pin         one-way suggestion for the resulting memory
```

The host validates supplied IDs, expected revisions, trust visibility, action shape, and
pinning eligibility. The model never selects or bypasses the configured policy.

## Update and Merge

- A single-memory update preserves evidence and never downgrades private trust.
- A merge locks all records in deterministic order and fails atomically on stale revisions.
- The survivor receives canonical content and category from the validated decision.
- Evidence links are united.
- Private trust dominates public trust.
- Existing pinning is preserved with logical OR.
- Derived indexes are refreshed only when canonical content changes.
- Redundant records are deleted only after the survivor is complete.
- Facts that can change independently remain separate.
- Uncertain or ambiguous candidates remain unchanged.

Hard deletion follows the current manual-memory behavior. Merge tombstones, undo history,
and redirects are outside this plan unless an audit or recovery requirement is introduced.

## Pinning

Dreaming may only suggest pinning an `identity` memory that is explicit, stable, and useful
in nearly every future conversation. Eligible examples are:

- preferred or full name
- preferred form of address
- current primary residence at city, region, or country level
- explicitly stated pronouns
- explicitly stated primary language when durably relevant

Dreaming must not suggest pinning contact details, full street addresses, employers,
relationships, birthdays, health information, goals, projects, interests, preferences, or
temporary circumstances. Those records remain retrievable and can still be pinned manually.

Dreaming never unpins a memory. Automatic Dreaming pinning stops once five memories are
pinned; explicit user pinning remains unrestricted.

## Generic Approvals

Evolve the existing approval request instead of creating a memory-specific table:

```text
request_type         agent_action | memory_change | future types
action_kind          existing action name | memory_update | memory_merge | memory_pin
user_id              required owner
session_id           optional context
run_id               optional AgentRun reference
available_decisions  type-specific decisions
payload              bounded, validated action data
status               pending | approved | denied | expired
```

Agent actions retain `allow_once`, `allow_session`, and `deny`. Memory changes use `approve`
and `deny`. A single API lists pending requests and dispatches decisions by `request_type`.
Do not add a workflow registry, plugin resolver, or generic execution engine; use explicit
type branches for implemented actions.

Memory approval payloads contain target IDs, expected revisions, canonical content,
category, evidence IDs, trust input, and any validated pin request. Approval rechecks current
settings and revisions. A changed target expires the request without partial mutation.

## User Interface

Reuse the global approvals settings page and badge. Add `Memory` to its existing type filter
when the first memory approval type ships. Show before and after content, merge sources,
category changes, and requested pinning. Keep ordinary session approvals inline in chat as
well as in the global pending list.

## Out of Scope

- full existing-memory scan
- separate memory proposal or maintenance tables
- approval history beyond the shared request lifecycle
- structured user contact profile
- automatic contact-detail pinning
- merge undo or tombstones
- configurable pin categories or limits
