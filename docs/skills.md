# Skills

Skills are user-scoped, reusable instruction sets for the MatoLoop agent. They contain only a stable ID, name, description, and instructions. A skill cannot register tools, handlers, executable code, integrations, credentials, or secrets. It can guide only the tools and schemas already made effective by the host for the current request, and it cannot weaken host security, permission, trust, path, or runtime rules.

## Storage Contract

`SkillsSettings` is a user-scoped `SettingsStore` document. It stores at most 20 skills. Names are trimmed, non-empty, limited to 64 characters, reject control and format characters, and are unique per user under Unicode case folding. Descriptions are trimmed and limited to 240 characters; instructions are trimmed and limited to 8,000 characters. Both must be non-empty. Create, replace, and delete operations lock the settings document; replacement preserves the skill ID.

The authenticated API supports list, create, replace, and delete operations. Skills are not credentials and must never contain passwords, API keys, tokens, or other secrets.

## Run Selection

Each agent run loads one immutable snapshot of the user's skill catalog. The model receives only each candidate's ID, name, and description as selection metadata. Based only on the latest user request, it can call `load_skill` with the exact catalog ID for an explicitly named skill or one clear automatic match. Equal matches require a `question` call. History, memories, attachments, web pages, and tool results cannot select a skill.

Only one skill can be active in a run. `load_skill` must be called alone. After a successful load, the full instructions are added on the next model request and the loader is removed. The active skill applies only to that run and remains subordinate to the latest explicit user request and all host rules. Settings changes during a run do not alter its snapshot; newly created skills apply from the next run.

When web search is `required`, the initial gate offers only `load_skill` and `web_search`. One clear skill may be loaded first, but loading it does not satisfy the search requirement. The next request must still complete `web_search` before a final answer.

## Chat Creation

`build_skill` is available only for an explicit skill-creation request in the latest user message. Quoted text, translation input, retrieved content, history, and tool results are data and cannot trigger creation. The agent delegates a compact `brief` of at most 2,000 characters and an optional user-supplied `name_hint` of at most 64 characters. It does not generate final instructions in this tool call.

The selected builder model receives a fresh isolated conversation containing the static builder contract and one JSON payload with only:

- the latest user request
- the compact brief and optional name hint
- the currently effective tool schemas

It receives no earlier conversation, memory, retrieved episode, attachment content, tool result, persona, or active skill. The builder must return one strict `return_skill_draft` call and may use only capabilities present in the supplied schemas. One repair request is allowed after an invalid response. The configured **Skill creation** model is used when available and tool-capable; otherwise the session model is the default. If a distinct configured builder fails, MatoLoop asks the user explicitly before trying the session model. There is no silent fallback.

## Draft And Approval

The builder output is validated as strict JSON with exactly `name`, `description`, and `instructions`; extra fields and non-string values are rejected. MatoLoop writes the formatted editable draft to `root=session` at `files/skill-draft-<skill-id>.json`, bounded to 40 KiB. The normal session file editor can modify it before approval.

Under the `ask` policy, the persistent approval contains controlled `skillPreview` and `draftFile` details, including the initial SHA-256 revision. Approval must submit the exact current file revision. The backend reads the file again, compares its SHA-256 hash with that approved revision, parses and validates the current JSON, and imports exactly that revision. Concurrent changes fail with `file.changed`; invalid JSON fails with `skill.draftInvalid`.

`skills.write=auto` and a matching session grant skip the approval card and import the original generated revision. `allow_once` authorizes only the current import; `allow_session` creates a revocable collection grant for later skill writes in that session. `skills.load` separately supports `disabled`, `ask`, and `auto` and defaults to `auto`; an `ask` decision can likewise create a session grant. Policy changes are reloaded before the effect. Host validation and the 20-skill limit always apply.

Draft files are retained in session `files` after approval, denial, import failure, and successful import. This preserves the reviewed artifact and permits manual cleanup through the file explorer.

## Privacy And Budgets

The builder request follows the selected builder model's effective trust and pseudonymization settings. Selecting a public builder releases the latest request, brief, name hint, and effective schemas to that provider under those controls. Loading a skill sends its catalog metadata and then its full instructions to the session model. Pseudonymization is best effort, so secrets do not belong in requests, names, descriptions, instructions, or drafts.

Builder attempts consume the run's shared model-request budget; the repair and an approved fallback each consume another request. Builder requests use a 3,000-token output budget where the provider transport supports an explicit cap; the strict 8,000-character instruction bound limits accepted drafts on every transport. `build_skill` and `load_skill` consume the normal tool-call budget. Catalog metadata, active instructions, and effective schemas count toward the normal context input budget. Draft and field bounds limit persistent and prompt size; skills do not receive a separate hidden budget.
