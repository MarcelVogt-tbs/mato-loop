# Automations

An automation runs a saved agent task manually or on a schedule. Open **Einstellungen > Automationen** to see its status and next start. Select an automation to manage its **Einstellungen** and **Verlauf** on the same page.

## Create an automation

Choose **Automation erstellen** and answer three questions:

1. **Auftrag**: Give it a name and describe the task and expected result.
2. **Ausführung**: Choose manual, one-time, or recurring execution. Manual automations need no schedule. Recurring schedules offer daily, weekday, weekly, and monthly presets.
3. **Ergebnis**: Results and errors always appear in the automation's history. Optionally select a chat to receive successful results as well.

Choose **Speichern**. Changes apply only after saving. **Jetzt ausführen** starts the saved task; save or discard pending changes before starting it manually. A schedule continues using the saved definition while you edit.

## Understand the status

- **Nur manuell**: No automatic starts are configured.
- **Geplant**: At least one future scheduled start is enabled.
- **Zeitpläne pausiert**: Automatic starts are paused; manual execution remains possible.
- **Keine weiteren Termine**: No enabled schedule has another occurrence. Manual execution remains possible.
- **Läuft**, **Eingereiht**, **Freigabe erforderlich**, or **Eingabe erforderlich**: A run is active. The scheduling state is shown separately, because pausing future starts does not cancel current work.
- **Archiviert**: History remains accessible, but the automation cannot be edited or started.

There can be at most one active run per automation. Handle pending permissions or questions in the notification center. A failed or interrupted run is not automatically retried, because earlier tools may already have caused external effects.

## Scheduling options

New recurring schedules use the browser's timezone, shown under **Zeitplanoptionen**. Changing devices does not change the saved timezone. One-time timestamps and optional end dates are entered in the current device's local time.

**Benutzerdefiniert** accepts a five-field Cron expression: minute, hour, day, month, weekday. For example, `0 8 * * 1-5` means Monday through Friday at 08:00 in the selected timezone. Existing custom expressions are not rewritten by the preset selector. A monthly day that does not exist in a particular month has no occurrence in that month.

Use **Zeitplanoptionen** for timezone overrides, an inclusive end date, or disabling one schedule. Use **Weitere Ausführungseinstellungen** to add another schedule. The primary **Zeitpläne aktiv** switch pauses all automatic starts without removing their configuration. Switching to manual mode saves an empty schedule list; the old schedule drafts are retained only until saving or leaving the page.

With exactly one one-time schedule, **Nach erfolgreichem geplanten Termin archivieren** archives the automation after that scheduled run succeeds. Manual, failed, cancelled, or skipped runs do not trigger archival. Archived automations can be permanently deleted from **Archiv**, including their history.

During daylight-saving changes, skipped recurring local times execute once after the spring jump, and duplicated autumn times execute once. Occurrences missed during server downtime are not replayed. Simultaneous schedules are combined into one run; a due occurrence while a previous run is active produces a skipped history entry.

## Advanced task settings

Each automation has its own persistent execution context. Previous results, files, and permissions can affect subsequent runs; it does not begin as a fresh chat each time.

**Weitere Auftragseinstellungen** allows a one-time copy of settings from a chat, including model, memory, web search, computer, and working directory. Later changes to the source chat are not synchronized. Without an explicit model selection, the effective default model is used.

A skill is optional. The agent can select an appropriate or explicitly named skill from the task using the normal skill-loading tool. **Skill verbindlich verwenden (optional)** instead binds a specific skill by ID and requires it to be loaded. Skills add instructions, never tools or permissions. Their normal approval policies still apply.

## Results and history

**Verlauf** shows run results or controlled errors, execution status, and timing. **Konfiguration dieses Laufs** contains the historical task and configuration used by that run; later edits do not change it. Completion and failure notifications open the exact run directly.

An output chat receives the final successful assistant response, not internal reasoning or tool records. If the chat is busy, delivery waits. Delivery failure is separate from execution failure: a successful result remains available even if delivery fails. After three technical delivery attempts, **Zustellung erneut versuchen** retries delivery without rerunning the task.

After choosing an output chat, **Weitere Ergebniseinstellungen** offers the chat's default channel, the MatoLoop app, or an available personal channel. An unavailable saved channel remains visible; disabled providers fall back to the app according to the channel policy.

**Automation an diesen Chat binden** changes deletion behavior: deleting that chat also requires deleting the dependent automation. With an optional link, the chat can be unlinked while retaining the automation.

## Agent tools and permissions

The agent can list, create, edit, start, and delete automations, including manual-only tasks. These operations use the effective policies under **Einstellungen > Tools** and the selected agent profile. Selecting a skill or creating an automation does not grant additional tool access or bypass approvals.
