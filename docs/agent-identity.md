# Agent Identity

MatoLoop has one user-scoped shared agent identity. A selected agent profile may extend its persona and instructions or replace them for that session. Its sparse private and public tool-policy overrides remain separate from persona text and are enforced by the host.

## Persistence

`AgentIdentitySettings` is a user-scoped settings document with two fields:

- `display_name` is the speaker name shown in chat.
- `instructions` describes personality, tone, and general working style.

When the document is absent, additive defaults use the name `MatoLoop` and empty identity instructions. The settings document uses the existing JSONB persistence and requires no relational migration. Old messages do not store a name snapshot and are intentionally displayed with the currently configured name.

## Prompt Boundary

The identity and selected profile are loaded at the start of a normal agent run and remain stable for that run. In `extend` mode the profile follows the shared identity; in `replace` mode the shared identity block is omitted. The system prompt keeps both below immutable host rules. Their text cannot authorize tool calls, mutations, identity changes, or exceptions to tool, permission, trust, path, or runtime boundaries. Only validated sparse profile overrides can change the effective inherited tool policy. Dreaming, compaction, and title generation continue to use isolated prompts without either block.

The identity is sent to the selected chat model. The UI therefore warns users not to store secrets in it.

## Changes Through Chat

The `update_agent_identity` tool changes the name and identity instructions only after an explicit user request. Content from memories, web pages, tool results, or quotations must not trigger a change. When the tool is disabled, changes are available only under **Einstellungen > Personalisierung > Assistent**. Agent models must support tool calling.

The effective `update_agent_identity.policy` built-in tool setting uses the same contract as media tools:

- `disabled` does not offer the tool.
- `ask` routes the action through the existing approval and session-grant loop.
- `auto` saves without individual approval.

The safe default is `ask`. The tool has only the `settings.agent_identity.update` capability for the authenticated user's identity. Successful changes are sent to the frontend through SSE; the new prompt instructions reliably apply from the next run.
