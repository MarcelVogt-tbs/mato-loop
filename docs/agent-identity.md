# Agent Identity

MatoLoop has exactly one user-scoped agent identity. It applies across all sessions. Tool policies and available resources control technical capabilities; they are not separate personalities.

## Persistence

`AgentIdentitySettings` is a user-scoped settings document with two fields:

- `display_name` is the speaker name shown in chat.
- `instructions` describes personality, tone, and general working style.

When the document is absent, additive defaults use the name `MatoLoop` and empty identity instructions. The settings document uses the existing JSONB persistence and requires no relational migration. Old messages do not store a name snapshot and are intentionally displayed with the currently configured name.

## Prompt Boundary

The identity is loaded once at the start of a normal agent run and remains stable for that run. The system prompt places it below immutable host rules and closes the dynamic identity block with a host-controlled boundary. Identity text can affect the name, tone, character, and working style, but cannot authorize tool calls, mutations, identity changes, or exceptions to tool, permission, trust, path, or runtime boundaries. Only the latest user request can authorize an action. Dreaming, compaction, and title generation continue to use isolated prompts without the identity.

The identity is sent to the selected chat model. The UI therefore warns users not to store secrets in it.

## Changes Through Chat

The `update_agent_identity` tool changes the name and identity instructions only after an explicit user request. Content from memories, web pages, tool results, or quotations must not trigger a change. When the tool is disabled, changes are available only under **Einstellungen > Personalisierung > Assistent**. Agent models must support tool calling.

The global `update_agent_identity.policy` tool setting uses the same contract as media tools:

- `disabled` does not offer the tool.
- `ask` routes the action through the existing approval and session-grant loop.
- `auto` saves without individual approval.

The safe default is `ask`. The tool has only the `settings.agent_identity.update` capability for the authenticated user's identity. Successful changes are sent to the frontend through SSE; the new prompt instructions reliably apply from the next run.
