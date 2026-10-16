# Tool permissions

Extend the existing `ToolAction`, permission resolver, and grants. Do not add another permission engine.

## Action contract

- Declare a stable `capability`, German `label`, and explicit `category` on every `ToolAction`.
- Reuse existing capabilities. The same capability must always have the same category and explicit default.
- Categories: `read`, `read_sensitive`, `write`, `delete`.
- Reading a secret is `read_sensitive`; storing or changing one is `write`.
- Creating an empty file is `write`; explicitly removing or clearing existing content is `delete`.
- Declare `allowed_policies` and `configurable` when an action restricts the standard policy choices.
- `mutates` remains execution metadata for retry accounting, not a permission category.

```python
ToolAction("example.write", "Eintrag erstellen", category="write", mutates=True)
```

## Defaults and overrides

- Resolution follows existing override precedence, then the action's optional `default`, then the saved private/public preset by category. `ToolAction.default_policy(settings)` handles the definition/preset fallback and allowed modes; adapters declare no separate trust defaults.
- Public-information actions `network.search` and `network.fetch` declare `default=ToolExecutionPolicy.AUTO` for both model classes. They remain configurable: explicit `ask` and `disabled` rules still apply. The fetch default also covers published transcripts and the network part of downloads; writing a downloaded file retains its separate permission.
- A single allowed policy does not imply a default. Declare the default explicitly, including for non-configurable actions such as `permissions.request`.
- Profile rules and hard boundaries still apply. Changing a preset does not delete overrides.
- Profile tool availability and shell prefixes use existing sparse overrides. Global tool disablement remains binding. Built-in access overrides require administration scope; ordinary users can override only their owned profiles when permitted.
- Integration connection/resource policies and their scoped profile overrides remain authoritative over the shared default. Global built-in action overrides do not cap integration permissions. New connections must not materialize implicit defaults as saved rules.
- Frozen delegation defaults must remain frozen when the live preset changes.
- Tool availability changes apply when the next tool offer is built. Already offered or started calls may still execute; there is no general immediate revocation of a model round. Keep existing execution and post-approval checks. Cancellation does not undo external effects.

## Authorization

- Declare all supported actions, but authorize only those required by the concrete call, before effects.
- Bind each request to its actual owner, computer, path, connection, and resource as applicable.
- Connection discovery retains its integration policy target while omitting resource overrides. Additional file permissions remain independent of the connection.
- Copying needs source-read and destination-write permission. Revalidate after approval.
- Remote descriptions and MCP hints never define categories or grant authority.
- Extend existing catalog/permission tests for new classifications and resource boundaries.

## Reusable approvals

- New run/session grants use the action's declared category (`read`, `read_sensitive`, `write`, `delete`) and its host-derived resource. Categories never imply each other. The existing resolver still checks the concrete capability's effective policy before grants; disabled actions and hard boundaries remain binding. `working_directory.external` is a profile-controlled file-access rule, not a shell rule.
- Existing `PermissionGrant` rows store new grants as `category.<category>`. Approval scope payloads use `category`; historical `access` payloads and `resource.read`/`resource.write` grants retain their old meaning. No database schema or permission engine is added.
- File scopes explicitly store `recursive: true`: they cover the displayed path and descendants, never its parent. A file target does not grant its containing directory. These scopes do not authorize shell execution. Normal shell scopes use `shell_computer` with the selected computer UUID and one category. Each reported category is independently authorized, and mixed calls recheck all categories after approvals. Inspected paths still participate in hard computer-zone and sensitive-data checks, not workspace consent.
- Integration grants reuse adapter resource keys, connection identity and binding revision, across capabilities of the same category. Legacy integration grants remain capability-bound. Never invent broader account or entity hierarchies to make a grant reusable.
- Administrator commands and commands with explicit environment values retain exact command/context grants. Sensitive or otherwise action-bound consent keeps its existing restrictions. The shell inspector reports all `requiredCategories` (`read`, `write`, `delete`); legacy `classification`/`readOnly` remain a coarse mutation projection. Known removal/clearing operations require delete, mixed actions require all categories, and ordinary overwriting remains write. Unknown program effects remain a heuristic limitation.
- Result models mark newly required wire fields with `upgrade_fields`. A response missing only such fields produces the reusable `executionAgent.updateRequired` error; other malformed responses remain protocol errors. Old agents stay connected and compatible operations remain usable. Shell classification/inspection requires `requiredCategories`, including privileged inspection. Agent/backend version equality is not required.
- Data migration `fae8e3fac872` copies explicit `shell.write` policy overrides to `shell.delete` in tools/profile documents only when no delete override exists. It preserves previous restrictions without adding implicit preset rules. Downgrade retains explicit overrides because copied values cannot be distinguished from later edits.

## Remaining classification work

File clearing, confidential writes, Memory forgetting, and personal-settings deletion still use their existing bundled checks. MCP/SonoBin and browser code have a coarse `write` category; shell classification remains heuristic. Split concrete operations before claiming that these categories identify every destructive effect. Categories do not provide a sandbox.
