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

- Resolution follows existing override precedence, then the action's optional `default`, then the saved private/public preset's category baseline and named automatic capabilities. `ToolAction.default_policy(settings)` handles this fallback and allowed modes; adapters declare no separate trust defaults.
- `ask_all`, `balanced`, `ask_changes`, `ask_delete`, and `auto_all` are fixed, trust-independent presets. Private models default to `ask_changes` (Komfortabel), public models to `balanced` (Ausgewogen). All except `ask_all` automatically permit public research, session-file reads/writes, file provision, media processing/generation, skill loading, and current-session tool-result reads. Other capabilities inherit the category baseline. Search/fetch have no unconditional action default; `ask_all` also asks for these actions. Downloads retain their independent destination-write check. The configuration endpoint derives the displayed category baseline and additional permissions from the same definitions.
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

- New run/session grants use the action's declared category (`read`, `read_sensitive`, `write`, `delete`) and its host-derived resource. The central matcher lets `write` also satisfy `read` on the same scope and lifetime. Delete and sensitive read remain independent; a write grant stays one revocable row. The existing resolver still checks the concrete capability's effective policy before grants; disabled actions and hard boundaries remain binding. `working_directory.external` is a profile-controlled file-access rule, not a shell rule.
- Existing `PermissionGrant` rows store grants as `category.<category>`. Approval scope payloads use `category`; the matcher no longer supports historical `resource.read`/`resource.write` grants or capability-bound resource grants. Navigation and exact run-action consent keep their existing formats. No database schema or permission engine is added.
- File scopes explicitly store `recursive: true`: they cover the selected path and descendants. The approval defaults to the requested path and can offer its direct parent, never a path outside the same computer or session store. These scopes do not authorize shell execution. Normal shell scopes default to `shell_action`, binding the exact command, computer, directory and environment hash; an explicit broader option uses `shell_computer`. Each reported category is checked, and mixed calls recheck all categories after approvals. Inspected paths still participate in hard computer-zone and sensitive-data checks, not workspace consent.
- File and normal-shell approvals share host-generated `grantOptions` (ID, display and scope fingerprint). Decisions accept only an offered option rederived from the validated original scopes. Omitted selection retains the original scope, including messaging decisions. `grantedScopes` records the selected scopes for permission listings. Sensitive, administrator and action-bound restrictions cannot be widened through this selection.
- Integration grants reuse adapter resource keys, connection identity and binding revision, across capabilities of the same category, including `write` covering `read`. Never invent broader account or entity hierarchies to make a grant reusable.
- Administrator commands and commands with explicit environment values retain exact command/context grants. Sensitive or otherwise action-bound consent keeps its existing restrictions. The shell inspector reports all `requiredCategories` (`read`, `write`, `delete`); legacy `classification`/`readOnly` remain a coarse mutation projection. Known removal/clearing operations require delete, mixed actions require all categories, and ordinary overwriting remains write. Unknown program effects remain a heuristic limitation.
- Result models mark newly required wire fields with `upgrade_fields`. A response missing only such fields produces the reusable `executionAgent.updateRequired` error; other malformed responses remain protocol errors. Old agents stay connected and compatible operations remain usable. Shell classification/inspection requires `requiredCategories`, including privileged inspection. Agent/backend version equality is not required.
- Data migration `fae8e3fac872` copies explicit `shell.write` policy overrides to `shell.delete` in tools/profile documents only when no delete override exists. It preserves previous restrictions without adding implicit preset rules. Downgrade retains explicit overrides because copied values cannot be distinguished from later edits.
- Session-file deletion uses `session.files.delete`, independently of workspace `filesystem.delete`. Existing owner/root/path checks, category grants and Plan write-path restrictions apply. Migration `39856f53cf7e` resets stored tool presets to private `ask_changes` and public `balanced` and copies explicit filesystem-delete overrides to session-delete overrides only when absent. Other overrides remain unchanged; downgrade retains copied rules and maps `balanced` to `ask_all`.
- Data migration `ab5df5b19220` resets all existing grants and expires pending agent-action approvals once. Users explicitly reconfirm access under the unified rules; navigation can be restored through explicit directory selection. Policies, presets and memory proposals remain unchanged; downgrade cannot restore revoked consent.

## Remaining classification work

File clearing, confidential writes, Memory forgetting, and personal-settings deletion still use their existing bundled checks. MCP/SonoBin and browser code have a coarse `write` category; shell classification remains heuristic. Split concrete operations before claiming that these categories identify every destructive effect. Categories do not provide a sandbox.
