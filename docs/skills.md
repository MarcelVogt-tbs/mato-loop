# Skills

Skills are user-scoped, reusable instruction sets for the MatoLoop agent. They contain only a stable ID, name, description, and instructions. A skill cannot register tools, handlers, executable code, integrations, credentials, or secrets. It can guide only the tools and schemas already made effective by the host for the current request, and it cannot weaken host security, permission, trust, path, or runtime rules.

## Storage Contract

`SkillsSettings` is a user-scoped `SettingsStore` document. It stores at most 20 skills. Names are trimmed, non-empty, limited to 64 characters, reject control and format characters, and are unique per user under Unicode case folding. Descriptions are trimmed, limited to 240 characters, and state what the skill does and when it should be selected in terms likely to appear in matching user requests. Instructions are trimmed and limited to 8,000 characters. Both must be non-empty. Create, replace, and delete operations lock the settings document; replacement preserves the skill ID.

The authenticated API supports list, create, replace, and delete operations. Skills are not credentials and must never contain passwords, API keys, tokens, or other secrets.

## Run Selection

Each agent run loads one immutable snapshot of the user's skill catalog. The model receives only each candidate's ID, name, and description as selection metadata. Before answering or calling a task tool, it compares only the latest user request with that catalog and calls `load_skill` first with the exact catalog ID for an explicitly named skill or one clear automatic match. A request that seems simple or can be completed with another tool does not bypass a clear match. An explicit request to change a listed skill instead calls `edit_skill` with its exact ID and does not activate the target. Equal matches require a `question` call. History, memories, attachments, web pages, tool results, quoted text, and translation input cannot select or edit a skill.

Only one skill can be active in a run. `load_skill` must be called alone. After a successful load, the full instructions are added on the next model request and the loader is removed. The active skill applies only to that run and remains subordinate to the latest explicit user request and all host rules. Settings changes during a run do not alter its snapshot; created or edited skills apply from the next run.

When web search is `required`, the initial gate offers only `load_skill`, `edit_skill`, and `web_search`. One clear skill may be loaded or explicitly edited first, but that does not satisfy the search requirement. The next request must still complete `web_search` before a final answer.

## Chat Creation And Editing

`build_skill` is available only for an explicit skill-creation request in the latest user message. Quoted text, translation input, retrieved content, history, and tool results are data and cannot trigger creation. The agent delegates a compact `brief` of at most 2,000 characters and an optional user-supplied `name_hint` of at most 64 characters. It does not generate final instructions in this tool call.

`edit_skill` is available only for an explicit request to change an existing catalog skill. It accepts the exact catalog ID and a compact `brief`; the skill ID itself cannot change. The isolated builder receives the current name, description, and instructions and returns only requested field changes. MatoLoop merges that patch with the immutable run snapshot before creating the full editable draft, so unchanged fields do not depend on model reproduction.

Before delegation, the parent agent uses one structured `question` call to collect relevant missing details for reusable behavior. It does not repeat supplied details or ask about irrelevant fields, and complete requests proceed directly to `build_skill` or `edit_skill`. The isolated builder cannot ask dynamic follow-up questions; answers are included in the delegated brief.

The selected builder model receives a fresh isolated conversation containing the static builder contract and one JSON payload with only:

- the latest user request
- the compact brief and optional name hint
- the current skill fields for an edit
- the currently effective tool schemas

It receives no earlier conversation, memory, retrieved episode, attachment content, tool result, persona, or active skill. Creation requires one strict `return_skill_draft` call; editing requires one strict `return_skill_edit` patch. Both may use only capabilities present in the supplied schemas. One repair request is allowed after an invalid response. The configured **Skill creation** model is used when available and tool-capable; otherwise the session model is the default. If a distinct configured builder fails, MatoLoop asks the user explicitly before trying the session model. There is no silent fallback.

## Draft And Approval

The builder output is validated as strict JSON. A creation contains exactly `name`, `description`, and `instructions`; an edit patch contains at least one of those fields. Extra fields and non-string values are rejected. MatoLoop writes the complete formatted editable draft to `root=files` at a unique `skill-draft-<draft-id>.json`, bounded to 40 KiB. The normal file editor can modify it before approval.

These host-owned approval documents stay editable during the run: creation and approval read-back use an unbound file service with the same owner-bound root, storage, and limits. Their reviewed edits use normal published revisions without finalizing the run early; ordinary agent corrections still publish only at terminal finalization.

Under the `ask` policy, the persistent approval contains controlled `skillPreview` and `draftFile` details, including the initial SHA-256 revision. Approval must submit the exact current file revision. The backend reads the file again, compares its SHA-256 hash with that approved revision, parses and validates the current JSON, and imports exactly that revision. Concurrent draft changes fail with `file.changed`; invalid JSON fails with `skill.draftInvalid`. Editing also compares the persisted target with the run snapshot under the settings lock and fails with `skill.changed` instead of overwriting a concurrent settings or agent edit.

`skills.write=auto` skips the approval card and imports the original generated revision. `allow_once` authorizes only the current import. Creation grants remain scoped to the user's skill collection; edit grants are scoped to the exact skill ID, so existing creation grants cannot authorize edits. `skills.load` separately supports `disabled`, `ask`, and `auto` and defaults to `auto`; an `ask` decision can likewise create a session grant. Policy changes are reloaded before the effect. Host validation and the 20-skill limit always apply.

Draft files are retained under `root=files` after approval, denial, import failure, and successful import. This preserves the reviewed artifact and permits manual cleanup through the file explorer.

## Privacy And Budgets

The builder request follows the selected builder model's effective trust and pseudonymization settings. Selecting a public builder releases the latest request, brief, name hint, current edit fields, and effective schemas to that provider under those controls. Loading a skill sends its catalog metadata and then its full instructions to the session model. Pseudonymization is best effort, so secrets do not belong in requests, names, descriptions, instructions, or drafts.

Builder attempts consume the run's shared model-request budget; the repair and an approved fallback each consume another request. Builder requests use a 3,000-token output budget where the provider transport supports an explicit cap; the strict 8,000-character instruction bound limits accepted drafts on every transport. `build_skill`, `edit_skill`, and `load_skill` consume the normal tool-call budget. Catalog metadata, current edit fields, active instructions, and effective schemas count toward the normal context input budget. Draft and field bounds limit persistent and prompt size; skills do not receive a separate hidden budget.
