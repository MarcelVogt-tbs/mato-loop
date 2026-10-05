# Provider und Modelle

## Provider verwalten

**Administration > Provider** opens on **Einstellungen**, where administrators control **Persönliche Provider**. The second tab, **Verwaltung**, adds, edits, enables, tests, and deletes global providers and their models.

**Einstellungen > Provider** manages only your own providers, including when signed in as an administrator. This navigation entry is hidden when personal providers are disabled; existing personal configuration remains stored. New providers inherit the global or personal scope from the page where they are created. Global providers and their models remain available to all users in chat and agent profiles. Personal providers and encrypted credentials belong only to their owner.

### ChatGPT Codex

MatoLoop zeigt einen einmaligen Gerätecode und öffnet die ChatGPT-Anmeldeseite. Nach erfolgreicher Bestätigung wird die Verbindung verschlüsselt gespeichert. Es ist derzeit eine Codex-Konfiguration vorgesehen.

### OpenAI-kompatibel

Benötigt eine Basis-URL und optional einen API-Schlüssel. Als API-Variante stehen Chat Completions und Responses zur Verfügung. Verfügbare Modelle werden nach Möglichkeit über den `/models`-Endpunkt erkannt.

### Lemonade

Verbindet einen lokalen Lemonade-Server. MatoLoop erkennt verfügbare Chat- und Medienmodelle sowie deren Fähigkeiten.

## Modelle synchronisieren

Neu erkannte Modelle beginnen in der Regel deaktiviert; vom Provider als natives Chatmodell gekennzeichnete Modelle können bereits aktiviert sein. Aktivieren und testen Sie nur die benötigten Modelle. Verschwindet ein Modell beim späteren Synchronisieren, bleibt seine Konfiguration als **Nicht verfügbar** erhalten.

Bei Providern ohne Modellerkennung kann eine Modell-ID manuell eingetragen werden.

Deleting a model clears matching model defaults and explicit session-wide selections. Explicit per-agent session or template overrides remain visible as unavailable until changed. Synchronization, deactivation, capability changes, and Codex disconnection also clear invalid default assignments. A later synchronization may discover the same external model again with a new internal ID.

## Laufzeiteinstellungen

Je Modell können unter anderem Kontextfenster, Ausgabelimit, Streaming, Tool-Unterstützung, Bildunterstützung, Datenfreigabe, Memory-Policy und Reasoning-Zusammenfassungen konfiguriert werden. Webfetch folgt stattdessen den Rechten des gewählten Agentenprofils für die effektive private oder öffentliche Modellklasse. MatoLoop bietet den Reasoning-Aufwand im Chat nur für ausdrücklich unterstützte Modelle an und berücksichtigt deren verfügbare Stufen und Providerformat. Qwen3.8-27B unterstützt **Aus**, **Niedrig**, **Mittel** und **Sehr hoch**; MatoLoop verwendet **Mittel** als Modellstandard. Codex übernimmt die unterstützten Stufen einschließlich **Maximum** und **Ultra** sowie den Modellstandard aus dem synchronisierten Katalog.

## Model defaults and session templates

Configure global assignments under **Administration > Standardmodelle > Verwaltung** and permitted personal overrides under **Einstellungen > Standardmodelle**. The administrative **Einstellungen** tab controls the personal-setting gate. Available purposes include:

- Agent
- Bildanalyse
- Kontextkomprimierung und Titelgenerierung
- Dreaming
- Bild-, Sprach- und Audiogenerierung
- Transkription

Agent profiles contain no model settings. **Sessionvorlagen** configures available agents, the start agent, memory, and optional chat-model/reasoning overrides for the whole session or individual agents. Select a template before the project on Home or when starting a new session. **Allgemein** offers Allgemein and Sysadmin; **Coding** offers Plan and Build. Both start with full memory. Under **Session**, changing the template replaces the session's agent selection, memory, model, and reasoning settings. You can then change the active agent's model and reasoning, memory, computer, and web search directly. Template changes in administration do not affect existing sessions.

An agent-specific session override wins over a session-wide value, followed by personal and global model defaults. Empty fields inherit their displayed defaults. Helper and media models always use **Standardmodelle** and the existing fallbacks.
