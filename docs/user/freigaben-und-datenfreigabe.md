# Freigaben und Datenfreigabe

## Freigabeanfragen

Wenn eine Aktion nicht automatisch erlaubt ist, pausiert der Agent und zeigt eine Freigabeanfrage direkt beim Tool-Aufruf. Die Karte enthält je nach Aktion:

- Titel und Zusammenfassung
- Risikostufe
- betroffene Ressource oder URL
- zu speichernden Memory-Inhalt
- technische Details

The global **Benachrichtigungen** view in the Control Center shows approvals, agent questions, and informational notices in one chronological list. Clicking an approval or question opens its source session, where you can decide or answer with the conversation context visible. Like normal response notices, session approval and question notices disappear when read, including when opening their session or marking all notices read. They are not created while their session is visible and focused. Dreaming Memory proposals open under **Memory > Dreaming > Offene Vorschläge**, where they remain available until resolved. Marking a notice read individually or in bulk, or deleting it, never resolves the underlying request. The center badge counts only unread notices; clicking the floating Control Center button with a badge opens notifications directly. Shared-terminal shell requests use ordinary session grants. Review the command and terminal cwd in the contextual approval, or edit the real terminal line and press Enter to allow the rechecked draft once. Pending **Strg+C** expires that same request without cancelling the parent run.

## Entscheidungen

| Entscheidung | Wirkung |
| --- | --- |
| Ablehnen | Die Aktion wird nicht ausgeführt |
| Für diesen Lauf erlauben | Default: permits matching actions until the current run finishes or is cancelled |
| Passende Aktionen in dieser Session nicht mehr nachfragen | Der Haken ändert den Hauptbutton zu **Für Session erlauben** und erstellt eine widerrufbare Freigabe für den angezeigten Ressourcenumfang |

Granted permissions for the current session can be viewed and revoked individually or together under **Session > Session-Freigaben** in the Control Center, not in the global notification center. They are not pending requests and do not increase its badge count.

**Für Session erlauben** also covers later runs in the same chat session, until revocation or session deletion. Reloading the browser does not end a grant. Both choices retain the displayed resource and read/write scope. Sensitive actions and edited arguments remain bound to the approved values and content revisions. Run-only grants apply to the executing run, including when that run belongs to a subagent; they do not transfer to other runs.

Der Haken ist bei jeder neuen Anfrage ausgeschaltet und wird nicht im Browser gespeichert. Manche sensible oder nicht sicher eingrenzbare Aktionen bieten bewusst keine Session-Freigabe an.

## Vertrauenslevel und Datenfreigabe

Provider und Modelle besitzen ein **Vertrauenslevel**:

- **Privat** erlaubt private Inhalte und deaktiviert die Pseudonymisierung. Verwenden Sie diese Stufe nur für einen von Ihnen geprüften, vertrauenswürdigen Endpunkt.
- **Öffentlich** behandelt den Zielprovider als externen Empfänger und kann den Schutz erkannter Kennungen und Secrets aktivieren.

Das Vertrauenslevel prüft weder Lokalität noch TLS oder Betreiber des Endpunkts. Diese Eigenschaften müssen Sie selbst beurteilen.

Für öffentliche Modelle kann MatoLoop erkannte Kennungen und Secrets in Text und Tool-Argumenten vor der Übertragung ersetzen. Personen und Orte werden nicht automatisch erkannt. Bilder, Audio und andere Binärdaten werden nicht pseudonymisiert. Die Erkennung reduziert Risiken, ist aber kein Ersatz für eine bewusste Auswahl der gesendeten Daten.

Private Daten aus Integrationen, beispielsweise Kalendertermine, Aufgaben, Rezepte oder Essenspläne, benötigen bei öffentlichen Modellen zusätzlich eine ausdrückliche Freigabe für die aktuelle Session. Diese Datenfreigabe ist von der Aktionsrichtlinie getrennt: Eine automatisch erlaubte CalDAV- oder Mealie-Abfrage darf ohne Session-Freigabe trotzdem keine Daten an ein öffentliches Modell übertragen. Ein Widerruf verhindert weitere Übertragungen, kann bereits übermittelte Daten aber nicht zurückholen.

## Memory und Personalisierung

Memory-Schreibvorgänge sowie Änderungen an Persona oder persönlichem Profil können abhängig von ihrer jeweiligen Tool-Richtlinie eine Freigabe erfordern. Prüfen Sie den angezeigten Inhalt und den Modellbereich, weil die Änderung über die aktuelle Antwort hinaus wirken kann.

!!! warning "Keine Secrets in Prompts"
    Senden Sie Passwörter, API-Schlüssel und andere Geheimnisse nicht absichtlich an Modelle. Automatische Schutzmaßnahmen können nicht jede Darstellung zuverlässig erkennen.
