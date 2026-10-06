# Session templates and model defaults

Agent profiles define workflow and permissions, not model selection. `model-defaults` settings hold global model assignments and optional owner-bound overrides for the agent model, reasoning, compaction, titles, skill creation, dreaming, vision, video, audio analysis, image generation, speech generation, audio generation, and transcription. Null means inherit; a false title-vision switch remains an explicit override. Provider/model availability and capabilities are still validated.

Persisted `ai` documents use `AiProviderSettings` and `UserAiSettings` for provider inventory only. `load_effective_ai` combines that inventory with model defaults into the runtime `AiSettings` projection. Obsolete default fields in old inventory documents are ignored and disappear when the document is saved. Provider mutations clean only the authoritative model-default documents; auxiliary requests no longer load an unused agent profile.

`session-templates` settings contain global or personal named templates. Each template selects available agents, a start agent, the existing `full`, `read_only`, or `disabled` memory mode, an optional session-wide chat model and reasoning effort, and optional chat-model/reasoning overrides by agent ID. Auxiliary model overrides do not belong to templates.

- **Allgemein** (internally Default) selects Allgemein and Sysadmin, starting with Allgemein, with standard-size messages.
- **Coding** selects Plan and Build, starting with Plan, with small messages.
- Both start with full memory and inherit model defaults unless configured otherwise.
- Global and personal management use the existing administrative scope and owner boundary. Each feature has its own personal-setting gate, initially enabled. Administrators retain the established personal exception.

## Session behavior

Templates and the Session Control Center expose `messageSize` (`standard` or `small`). The size is copied into `chat_sessions.session_profile` on creation and template selection and can then be changed per session. Small messages use smaller text, Markdown headings, and code, with tighter line, paragraph, message, and user-bubble spacing. Switching sizes recalculates the virtual transcript heights. Existing sessions without a size remain standard; template edits do not rewrite sessions. Agent profiles have no presentation setting.

Home and the blank-session composer show four equal-size fields in this order: template, project, memory, web search. At less than 600px they use a centered two-column grid. Project defaults continue to supply the computer and working directory independently.

The read-only `POST /sessions/defaults` preview accepts the same typed session options as creation, including per-agent overrides. Initial loading, template selection, and manual draft changes share this preview. The frontend applies its effective memory state rather than maintaining a second memory-policy calculation. Late responses cannot replace a newer draft or template choice.

Creation copies the selected template into the session: agent selection, per-agent model overrides, and the source template ID live in `chat_sessions.session_profile`; the existing model, reasoning, and memory columns store session-wide values. The optional source ID is display metadata, not a live dependency; older sessions without it remain valid. Editing or deleting the original template does not rewrite sessions.

The Session Control Center offers template selection and direct agent, computer, memory, web-search, model, and reasoning controls. Selecting a template replaces the available agents, active agent, memory mode, and all session/agent model and reasoning overrides with its current values, including inherited values. Project, computer, working directory, and web-search settings are independent. Users then adjust the selected agent's model and reasoning locally; the agent list itself is edited in template management.

Model and reasoning values resolve independently: session override for the selected agent, session-wide override, allowed personal defaults, global defaults, then the provider's documented reasoning default. Composer model/reasoning selections edit the current agent's session override. Agent switching uses that agent's effective model. Auxiliary requests use model defaults and their existing active-model fallback rules. Native child sessions retain the parent's selected model and resolved reasoning and keep their existing memory/permission restrictions.

When no agent-model default is configured, the runtime selects the first available chat model with tool support in inventory order (global models, then permitted personal models). This effective fallback is not persisted and never replaces an explicit model reference. Global administration resolves only global inventory and defaults. Model-default settings display the automatic candidate; session previews and runtime use the same central resolution. Auxiliary and media model assignments keep their existing fallback rules.

The runtime checks the session's available-agent list as well as current owner and instance availability. This list is session configuration, not an additional authority grant. Changes follow the existing session-settings lock and active-run restrictions. A removed or disabled explicitly selected model is unavailable until changed; it is not silently substituted by another model.

## Migration

Revision `ee5db377dca5` removes obsolete answer-display values from agent-profile documents and initializes template message sizes, using small for the shipped Coding template and standard for other templates. Existing sessions are untouched. Downgrade removes template/session sizes; removed answer-display values are not restored.

Revision `870fad9fb7e7` adds the JSONB session configuration, initializes both templates, moves the former global default agent's model settings into `model-defaults`, and removes model fields from agent documents. It transfers built-in chat-model/reasoning overrides to the matching templates and preserves effective chat-model choices for existing sessions without constraining their formerly unrestricted agent selection. Explicit session choices retain precedence during migration.

The former model fields of each agent document are retained in inactive `agent-models-archive` settings. Conflicting auxiliary choices and custom-profile choices not represented by the two shipped templates remain there for reference rather than introducing a hidden legacy runtime layer. Downgrade restores these archived fields on surviving profiles and removes the new template/default documents and session configuration column; it cannot retain edits made only in the new configuration model.
