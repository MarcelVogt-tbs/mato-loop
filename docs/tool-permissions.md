# Tool permissions

Extend the existing `ToolAction`, permission resolver, and grants. Do not add another permission engine.

## Action contract

- Declare a stable `capability`, German `label`, and explicit `category` on every `ToolAction`.
- Reuse existing capabilities. The same capability must always have the same category.
- Categories: `read`, `read_sensitive`, `write`, `delete`.
- Reading a secret is `read_sensitive`; storing or changing one is `write`.
- Creating an empty file is `write`; explicitly removing or clearing existing content is `delete`.
- Declare `allowed_policies` and `configurable` when an action restricts the standard policy choices.
- `mutates` remains execution metadata for retry accounting, not a permission category.

```python
ToolAction("example.write", "Eintrag erstellen", category="write", mutates=True)
```

## Defaults and overrides

- Built-in and integration actions inherit the saved private/public permission preset by category unless an action override exists. `ToolAction.default_policy(settings)` applies the preset and allowed modes; adapters declare no separate trust defaults.
- Profile rules and hard boundaries still apply. Changing a preset does not delete overrides.
- Profile tool availability and shell prefixes use existing sparse overrides. Global tool disablement remains binding. Built-in access overrides require administration scope; ordinary users can override only their owned profiles when permitted.
- Integration connection/resource policies and their scoped profile overrides remain authoritative over the shared default. Global built-in action overrides do not cap integration permissions. New connections must not materialize implicit defaults as saved rules.
- Frozen delegation defaults must remain frozen when the live preset changes.

## Authorization

- Declare all supported actions, but authorize only those required by the concrete call, before effects.
- Bind each request to its actual owner, computer, path, connection, and resource as applicable.
- Copying needs source-read and destination-write permission. Revalidate after approval.
- Remote descriptions and MCP hints never define categories or grant authority.
- Extend existing catalog/permission tests for new classifications and resource boundaries.

## Remaining classification work

File clearing, confidential writes, Memory forgetting, and personal-settings deletion still use their existing bundled checks. MCP/SonoBin and browser code have a coarse `write` category; shell classification remains heuristic. Split concrete operations before claiming that these categories identify every destructive effect. Categories do not provide a sandbox.
