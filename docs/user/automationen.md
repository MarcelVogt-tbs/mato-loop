# Automationen

An automation is a saved agent task in a normal session. Open **Automationen** in the side drawer to find configured tasks. Each entry opens the same conversation that is available in the project or session list.

## Create and edit

In an existing session, open **Control Center > App hinzufügen > Automationen**. To start in a new session, use the plus button next to **Automationen** in the drawer.

The app lists all automations in the session. Select **Neu** to create one or open a row to edit it. Enter a name and describe what the agent should do. Add a schedule or select an event under **Auslöser**. Without either, the task runs only on demand. Select **Speichern**. Each session supports multiple automations. The app remains attached after saving. Closing its tab does not stop the automations; add the app again to edit them.

Schedule rows show readable descriptions such as **Täglich um 08:00 Uhr**. Open a row to edit it; other rows stay compact. Long tasks show a preview with **Vollständigen Auftrag anzeigen** and **Bearbeiten**. Save and run controls remain below the scrolling content. The advanced **Zeitregel bearbeiten** section preserves unusual existing rules without changing their meaning.

Model, agent profile, computer, working directory, and permissions come from the session's existing settings. To use a skill, explicitly ask the agent to load it by name in the task. Skills use the normal session mechanism; there is no separate skill binding or forced activation.

## Run and interact

- **Jetzt ausführen** queues the saved task. Save or discard edits first.
- **Deaktivieren** stops automatic starts from schedules and events; **Aktivieren** enables them again. Manual starts remain available. Running work is unaffected.
- Use the normal chat stop button to stop the current agent.
- Tools, results, errors, approval requests, and questions appear directly in the conversation. You can answer and continue chatting there.
- Chat messages do not change the saved task automatically. Their conversation context can influence later runs.
- If the agent is already working in the session, a queued automation waits. Further due occurrences of an already queued or running automation are recorded as skipped.

The app shows **Letzter Lauf** with the start time and status of the most recent actual execution. Skipped occurrences do not replace it. Select the row to jump to its result or error message in the conversation. An active execution may link to its input until a result exists. **Abgeschlossen** means the execution finished normally, not that a monitored condition was met. Definitions without an execution show **Noch nicht ausgeführt**.

The conversation is the execution log: automation input messages identify the originating task by name, and intermediate findings, results, tools, and failures remain there. The app contains configuration, controls, and the compact last-run overview, without a separate execution transcript or history detail view.

Normal answers use the same unread indicators and response notifications as interactive sessions. The agent decides whether to use `notify` for a targeted alert or `silence` for a check without a reportable change. A successful `notify` replaces the run's automatic completion notice; `silence` suppresses that notice while keeping the final response and unread indicator. Neither suppresses runtime errors, approvals, questions, or explicit notices already created. Both tools are also available in ordinary chats; `silence` is appropriate when the user requests a quiet response, not merely because an answer is short. A focused, visible session does not create an automatic response notice; configured Web Push is sent only while the app is inactive.

Within the available context budget, the agent receives current automation definitions for its own session before each model request, including disabled tasks, revisions, schedules, and the next effective scheduled start. It can reuse a matching task without first listing automations. When full definitions do not fit, the context contains a compact catalog with task excerpts and schedule counts; full details remain available through `get_automation`. If the catalog also needs shortening, it explicitly reports how many definitions were omitted. Automation context never makes an otherwise fitting chat request exceed the model's input budget. These definitions are configuration data, not instructions to start every task. Reading other sessions' automations and changing definitions still use the normal tool permission paths.

## Schedules

Recurring presets cover daily, weekdays, weekly, and monthly schedules. **Eigener Rhythmus > Zeitregel bearbeiten** accepts five-field cron expressions, for example `0 8 * * 1-5` for weekdays at 08:00 in the saved timezone. New schedules use the browser's timezone; changing devices does not change saved schedules.

One-time timestamps and optional end times are entered in the device's local timezone. A recurring end time is inclusive. Missed occurrences during server downtime are not replayed. Simultaneous schedules are combined into one execution. Failed or interrupted agent work is not automatically repeated.

## Remove and migrate

Use **Archivieren** in a session's menu or **Projekt archivieren** in a project's menu to put conversations aside. Open **Archiv** above the user menu to read or restore them. Project archival includes its sessions without changing their individual archive flags. Restoring a project therefore leaves independently archived sessions archived.

Archived sessions remain readable, but new agent work and automation starts require restoration. Running work may finish. Automation activation settings are retained; queued automation work is cancelled and missed dates or events are not replayed. Automations themselves have no archive option.

**Automation entfernen** removes the task, schedules, and scheduling history. The conversation, files, permissions, and any running agent remain. Deleting the session or project also deletes its automation after confirmation.

Existing hidden automation sessions become visible with their original history and files. Former output chats retain previously delivered messages, but new work appears in the execution session. Old configuration links open that session and its Automation app; run links jump to the corresponding conversation message.

Previously archived automations become disabled. Existing skill bindings become explicit instructions in the saved task, including the original skill ID. Historical run snapshots remain available.

## Event triggers and permissions

**Agent hat geantwortet** and **Agent-Auftrag fehlgeschlagen** react to completed normal agent runs across your own sessions. Streaming fragments and other users' activity do not trigger work. Automation-originated runs, retries and delegated work do not trigger further automations.

Put optional conditions in the task. The triggered agent evaluates them and may decide to do nothing. Event context contains message and session references; reading their content still requires normal session access. A busy automation records a further occurrence as skipped.

Ask the agent to prepare session permissions when setting up a task. `request_session_permissions` uses the existing approval card without executing the proposed operation. Existing permissions are reused and disabled actions remain blocked. File access, supported shell inspection, session search/read, and integrations with declared permissions are supported. One-time-only or unsupported permissions are requested during actual execution. Grants retain their normal profile, trust, resource and session boundaries.
