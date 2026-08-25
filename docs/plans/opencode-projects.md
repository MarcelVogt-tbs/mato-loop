# OpenCode-Backed Projects

Status: implemented in August 2026. This file is retained as an implementation record.

## Implemented Contract

- Projects remain optional owner-bound session groups; standalone sessions are supported.
- A project can receive one immutable personal OpenCode binding at creation.
- The binding stores an integration ID plus opaque remote project and relative workspace identities. Remote absolute roots stay backend-only.
- Analysis and execution use separately selected live-discovered OpenCode models without fallback.
- OpenCode performs read-only repository analysis. The MatoLoop agent authors the canonical persistent Markdown plan.
- Enqueueing snapshots exact plan text, revision, model, and working directory.
- Persistent execution records run in per-project FIFO order with one active execution per project.
- Existing external OpenCode activity pauses the queue. Restart and uncertain post-dispatch failures become `outcome_unknown` and are not replayed automatically.
- Remote permission requests are shown with `once`, `always`, and `reject`; cancellation is best effort.
- The Control Center provides model selection, read-only files and status, plans, queue state, cancellation, and permission decisions.
- Chat tools for analysis, plan creation, plan listing, and enqueueing are available only in a bound project and use the normal MatoLoop approval flow.

## Remaining Exclusions

- No repository copy, Git worktree, commit, merge, push, terminal, remote editor, or remote download.
- No queue reorder, skip, retry, or automatic replay after an unknown outcome.
- No OpenCode provider transport, MatoLoop Agent binding, or generic remote-agent runtime.
