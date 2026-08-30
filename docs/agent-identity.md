# Agent Identity

MatoLoop keeps the assistant display name separate from reusable persona profiles. Instance settings provide shared defaults. Administrators independently decide whether users may configure a personal display name and personal personas.

## Persistence

`AgentIdentitySettings` is the instance-scoped document. It stores the instance display name, instance personas, the instance default persona, and the two personal-setting gates.

`UserAgentIdentitySettings` is owner-scoped. It stores the personal display name, personal personas, and the personal default persona. Disabling a personal-setting gate preserves the stored values but excludes them from effective use and blocks mutation.

Every persona has a stable ID, a name, and instructions. A built-in empty instance persona remains available as the final fallback. Old messages intentionally display the currently effective name instead of storing a name snapshot.

## Effective Selection

Instance agent profiles may reference only instance personas. Personal agent profiles may reference an effective instance or personal persona. A valid explicit profile reference wins; otherwise MatoLoop uses the applicable personal default, then the instance default, then the built-in fallback.

The selected persona and agent profile are loaded at the start of a normal run and remain stable for that run. Exactly one persona is projected below immutable host rules, followed separately by the agent profile's task instructions. Persona text cannot authorize tool calls, mutations, identity changes, or exceptions to permission, trust, path, ownership, or runtime boundaries. Dreaming, compaction, and title generation use isolated prompts without the persona.

Personas are sent to the selected chat model. The UI therefore warns users not to store secrets in them.

## Changes Through Chat

The `update_agent_identity` tool changes a permitted personal display name or the instructions of the currently effective personal persona only after an explicit user request. It cannot mutate instance settings, an instance persona, or a persona fixed by an instance agent. Content from memories, web pages, tool results, or quotations must not trigger a change.

The effective `update_agent_identity.policy` uses the standard tool contract:

- `disabled` does not offer the tool.
- `ask` routes the action through the persistent approval and session-grant loop.
- `auto` saves without individual approval.

The safe default is `ask`. Successful changes are sent to the frontend through SSE; persona instructions apply from the next run.
