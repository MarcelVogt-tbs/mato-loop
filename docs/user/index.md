# MatoLoop Dokumentation

MatoLoop ist eine selbst gehostete Arbeitsumgebung für KI-gestützte Chats und Agenten. Sessions, Nachrichten, Dateien, Freigaben und Erinnerungen bleiben in MatoLoop gespeichert, während unterschiedliche Modellanbieter für die eigentliche Modellverarbeitung verwendet werden können.

## Erste Schritte

- [Schnellstart](schnellstart.md): erstes Administratorkonto einrichten, Provider verbinden und die erste Session starten
- [Chat und Sessions](chat-und-sessions.md): Nachrichten, Modelle und Session-Verwaltung
- [Dateien und Arbeitsordner](dateien-und-arbeitsordner.md): Computer, Arbeitsbereiche und Session-Dateien
- [Freigaben und Datenfreigabe](freigaben-und-datenfreigabe.md): Entscheidungen über sensible Aktionen und externe Modelle
- [Skills](skills.md): wiederverwendbare Anweisungen verwenden, im Chat erstellen und verwalten
- [Automationen](automationen.md): Agent-Aufträge manuell sowie mit einmaligen oder wiederkehrenden Zeitplänen ausführen
- [Kanäle](kanaele.md): persönliche Telegram-Bots für Chats und Automationen verbinden

## Administration

Open **Einstellungen** to manage personal providers, profiles, skills, integrations, channels, personalization, and memory. **Personalisierung > Personas** manages your named personas, account-wide assistant name, and personal profile assignments when allowed. Global or owned personas can be assigned to accessible global or owned agents. Administrators manage the global persona catalog, its default, and shared assignments under **Administration > Personas**. Configure models and workflow on profiles, personal profile permissions under **Tools**, and integration permissions on the relevant connection; **Weitere Einstellungen** in the agent editor links to these related settings. Administrators manage global providers, profiles, skills, tools, integrations, and MCP servers in **Administration**. Sections using **Einstellungen / Verwaltung** open on **Einstellungen** first; **Verwaltung** contains their entries. Channel-provider gates and instance Web Push configuration also belong to Administration; device push subscriptions remain in the notification Control Center app. Personal tool overrides require both personal profiles and tool-right customization to be allowed; administrators may always use their own personal settings. Open your account through **Konto**. Automations are configured in the session's **Automation** Control Center app.

Persönliche Sessions, Dateien, Arbeitsbereiche, Erinnerungen, Kanäle und Zugangsdaten sind nach Benutzer getrennt. Gemeinsame Provider, Modelle und Integrationen der Instanz stehen dagegen absichtlich allen Benutzern nach den festgelegten Richtlinien zur Verfügung.

Personal providers and personal tool-right customization start disabled for ordinary users; personal personas, skills, and agent profiles start enabled. Administrators retain their personal-setting exception. Communication channels start globally disabled for everyone, including administrators: activate them under **Administration > Kanäle > Einstellungen**, then manage individual provider allowances under **Verwaltung**. Telegram is allowed by default but requires global activation. Existing configuration without an explicit value uses these defaults; explicitly saved values are retained.

## Grundprinzip

Provider transportieren Modellnachrichten und Antworten. Dateizugriffe, Webzugriffe, Tool-Aufrufe, Freigaben und Memory werden weiterhin durch MatoLoop kontrolliert. Das Modell führt keine Werkzeuge direkt aus.
