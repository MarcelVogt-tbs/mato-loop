# Freigaben und Datenfreigabe

## Freigabeanfragen

Wenn eine Aktion nicht automatisch erlaubt ist, pausiert der Agent und zeigt eine Freigabeanfrage direkt beim Tool-Aufruf. Die Karte enthält je nach Aktion:

- Titel und Zusammenfassung
- Risikostufe
- betroffene Ressource oder URL
- zu speichernden Memory-Inhalt
- technische Details

Alle offenen Freigabeanfragen finden Sie außerdem im globalen Bereich **Freigaben** des Control Centers.
Die globale Liste zeigt die Ursprungssession und erlaubt dieselben Entscheidungen wie die
Karte im Chat. Sie bündelt auch offene Agent-Rückfragen, die zur Beantwortung ihre Session
öffnen. Das Badge am Control Center und am Benutzermenü zeigt offene Aktionen an.

## Entscheidungen

| Entscheidung | Wirkung |
| --- | --- |
| Ablehnen | Die Aktion wird nicht ausgeführt |
| Einmal erlauben | Gilt nur für diese konkrete Aktion und ist die sichere Vorauswahl |
| Passende Aktionen in dieser Session nicht mehr nachfragen | Der Haken ändert den Hauptbutton zu **Für Session erlauben** und erstellt eine widerrufbare Freigabe für den angezeigten Ressourcenumfang |

Dauerhaft für eine Session erteilte Grants finden Sie weiterhin unter **Dateien** im Control Center. Dort
können Sie diese widerrufen. Sie sind keine offenen Freigabeanfragen und erscheinen daher
nicht in der globalen Pending-Liste.

Der Haken ist bei jeder neuen Anfrage ausgeschaltet und wird nicht im Browser gespeichert. Manche sensible oder nicht sicher eingrenzbare Aktionen bieten bewusst keine Session-Freigabe an.

## Vertrauenslevel und Datenfreigabe

Provider und Modelle besitzen ein **Vertrauenslevel**:

- **Privat** erlaubt private Inhalte und deaktiviert die Pseudonymisierung. Verwenden Sie diese Stufe nur für einen von Ihnen geprüften, vertrauenswürdigen Endpunkt.
- **Öffentlich** behandelt den Zielprovider als externen Empfänger und kann den Schutz erkannter Kennungen und Secrets aktivieren.

Das Vertrauenslevel prüft weder Lokalität noch TLS oder Betreiber des Endpunkts. Diese Eigenschaften müssen Sie selbst beurteilen.

Für öffentliche Modelle kann MatoLoop erkannte Kennungen und Secrets in Text und Tool-Argumenten vor der Übertragung ersetzen. Personen und Orte werden nicht automatisch erkannt. Bilder, Audio und andere Binärdaten werden nicht pseudonymisiert. Die Erkennung reduziert Risiken, ist aber kein Ersatz für eine bewusste Auswahl der gesendeten Daten.

Private Daten aus Integrationen, beispielsweise Kalendertermine oder Aufgaben, benötigen bei öffentlichen Modellen zusätzlich eine ausdrückliche Freigabe für die aktuelle Session. Diese Datenfreigabe ist von der Aktionsrichtlinie getrennt: Eine automatisch erlaubte CalDAV-Abfrage darf ohne Session-Freigabe trotzdem keine Daten an ein öffentliches Modell übertragen. Ein Widerruf verhindert weitere Übertragungen, kann bereits übermittelte Daten aber nicht zurückholen.

## Memory und Persona

Memory-Schreibvorgänge und Persona-Änderungen können abhängig von der Tool-Richtlinie ebenfalls eine Freigabe erfordern. Prüfen Sie den angezeigten Inhalt, weil die Änderung über die aktuelle Antwort hinaus wirken kann.

!!! warning "Keine Secrets in Prompts"
    Senden Sie Passwörter, API-Schlüssel und andere Geheimnisse nicht absichtlich an Modelle. Automatische Schutzmaßnahmen können nicht jede Darstellung zuverlässig erkennen.
