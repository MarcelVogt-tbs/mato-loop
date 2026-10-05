# Personalization

Under **Einstellungen > Personalisierung**, **Über mich** holds your maintained personal information; **Personas** manages your assistant name, reusable personas, and agent-specific assignments.

## Personas and profile overrides

Administrators manage global personas under **Administration > Personas > Verwaltung** and choose one **Globale Standard-Persona**. Each persona has a name and instructions describing tone and working style. You can create, read, edit, and delete your own personas when personal customization is allowed.

Choose **Override hinzufügen**, select an agent profile, and choose a persona. You may select a global or owned persona for an accessible global or personal agent. Your assignment affects only your account. Administrators set shared assignments for global profiles.

The priority is your personal assignment, the administrative assignment if present, then the global default. The assignment dialog shows the inherited persona and previews the selected instructions. Assignments reference the same persona: editing its text affects all agents using it. Deleting an assignment restores the default. Deleting a persona makes its assignments fall back; the global default must be replaced before it can be deleted.

The agent editor's **Weitere Einstellungen** tab opens persona assignments, tool rights, and integration assignments directly for the relevant profile. A new agent must be saved first.

## One assistant name

**Globaler Agentenname** is a single setting under **Administration > Personas > Verwaltung**. **Mein Agentenname** optionally overrides it for all agents in your account. **Globalen Namen verwenden** removes that personal override. This name is separate from persona names and individual agent-profile names.

## Personal customization

**Administration > Personas > Einstellungen > Persönliche Personas erlauben** controls your own personas, personal assignments, and personal assistant name together. Disabled values remain stored but inactive and read-only for ordinary users. Administrators retain their personal exception.

On an explicit request, the agent can update your permitted name or personal persona through the settings tool. Editing an existing personal persona affects its other assignments too. If a global persona is active, the tool creates a personal persona for the current agent. Tool policy determines whether approval is needed; changes apply from the next run.

!!! warning "No secrets in persona text"

    Persona instructions are sent to the selected model. Do not store passwords, tokens, or other secrets in them.
