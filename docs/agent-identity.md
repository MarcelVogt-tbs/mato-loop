# Agent identity

Personas are reusable named instruction records with stable IDs. Global personas are available to every user and editable only by administrators; personal personas are owner-bound. The assistant display name remains a separate scalar value, not a persona or agent-profile field.

## Storage and resolution

`AgentIdentitySettings` stores the global display name, the global persona catalog, one `default_persona_id`, administrative `profile_personas` assignments, and the single `personal_personas_enabled` gate. `UserAgentIdentitySettings` stores the owner's optional display name, personal persona catalog, and personal `profile_personas` assignments.

An assignment maps an exact agent-profile ID to a persona ID. It does not copy or merge instructions. Resolution uses:

1. The owner's permitted assignment for that agent, referencing a global or owned persona.
2. An administrative assignment, applicable only to global agent profiles.
3. The global default persona, also used by personal agents without an assignment and by profile-independent helpers.

There is no personal default persona or persona inheritance from a base profile. An empty persona text adds no instructions. Deleting an assignment restores the inherited value. Deleting a persona removes its local assignments; references from other owners fall back when the persona is no longer available. The selected global default cannot be deleted until another default is chosen.

The personal-persona gate controls personal catalog mutations, personal assignments, and the account-wide display-name override together. Disabled personal values remain stored but inactive and read-only for ordinary users. Administrators retain their personal exception. Personal assignments can target accessible global and owned profiles independently of the personal-profile gate for global targets. They never alter other users' behavior.

Migration `7494cba04f61` directly preserves the existing catalogs, IDs, names, and texts while moving profile selections into scoped assignments. Former personal defaults and base-profile selections become explicit assignments for existing personal profiles; future profiles use the global default unless assigned otherwise. The obsolete separate personal-name gate is removed.

## Management and runtime

**Administration > Personas > Einstellungen** holds the joint personal-persona gate. **Verwaltung** contains the one global agent name, persona CRUD, default selection, and administrative assignments. **Einstellungen > Personalisierung > Personas** contains the personal name, owned persona CRUD, and personal assignments; assignment selectors also offer global personas.

The editor's **Weitere Einstellungen** tab groups persona, tool-right, and integration links. Direct links retain the agent context. The normal settings/management tab order is **Einstellungen**, then **Verwaltung**, with settings selected initially. Context links open the corresponding management content.

A run snapshots one resolved persona below immutable host rules, separately from task instructions. Persona changes cannot authorize tools or bypass permission, trust, path, ownership, or runtime boundaries. Dreaming, compaction, and title generation use isolated prompts without the persona.

The identity tool changes a permitted personal display name or the active personal persona only after an explicit request. Editing a reusable personal persona affects all its assignments. If a global persona is active, the tool creates a personal persona and assigns it to the current agent instead of mutating global configuration. The gate, policy, and approved persona snapshot are rechecked before persistence. Changes apply from the next run.
