# Freigaben und Datenfreigabe

## Freigabeanfragen

Wenn eine Aktion nicht automatisch erlaubt ist, pausiert der Agent und zeigt eine Freigabeanfrage direkt beim Tool-Aufruf. Die Karte enthält je nach Aktion:

- Titel und Zusammenfassung
- Risikostufe
- betroffene Ressource oder URL
- zu speichernden Memory-Inhalt
- technische Details

## Entscheidungen

| Entscheidung | Wirkung |
| --- | --- |
| Ablehnen | Die Aktion wird nicht ausgeführt |
| Einmal erlauben | Gilt nur für diese konkrete Aktion |
| Für Session erlauben | Erstellt eine widerrufbare Freigabe für passende Aktionen dieser Session |

Offene Session-Freigaben finden Sie im Datei-Explorer. Dort können Sie dauerhaft für die Session erteilte Grants widerrufen.

## Vertrauenslevel und Datenfreigabe

Provider und Modelle besitzen ein **Vertrauenslevel**:

- **Privat** erlaubt private Inhalte und deaktiviert die Pseudonymisierung. Verwenden Sie diese Stufe nur für einen von Ihnen geprüften, vertrauenswürdigen Endpunkt.
- **Öffentlich** behandelt den Zielprovider als externen Empfänger und kann den Schutz erkannter Kennungen und Secrets aktivieren.

Das Vertrauenslevel prüft weder Lokalität noch TLS oder Betreiber des Endpunkts. Diese Eigenschaften müssen Sie selbst beurteilen.

Für öffentliche Modelle kann MatoLoop erkannte Kennungen und Secrets in Text und Tool-Argumenten vor der Übertragung ersetzen. Personen und Orte werden nicht automatisch erkannt. Bilder, Audio und andere Binärdaten werden nicht pseudonymisiert. Die Erkennung reduziert Risiken, ist aber kein Ersatz für eine bewusste Auswahl der gesendeten Daten.

Private Daten aus Integrationen, beispielsweise Kalendertermine, benötigen bei öffentlichen Modellen zusätzlich eine ausdrückliche Freigabe für die aktuelle Session. Diese Datenfreigabe ist von der Aktionsrichtlinie getrennt: Eine automatisch erlaubte Kalenderabfrage darf ohne Session-Freigabe trotzdem keine Daten an ein öffentliches Modell übertragen. Ein Widerruf verhindert weitere Übertragungen, kann bereits übermittelte Daten aber nicht zurückholen.

## Memory und Persona

Memory-Schreibvorgänge und Persona-Änderungen können abhängig von der Tool-Richtlinie ebenfalls eine Freigabe erfordern. Prüfen Sie den angezeigten Inhalt, weil die Änderung über die aktuelle Antwort hinaus wirken kann.

!!! warning "Keine Secrets in Prompts"
    Senden Sie Passwörter, API-Schlüssel und andere Geheimnisse nicht absichtlich an Modelle. Automatische Schutzmaßnahmen können nicht jede Darstellung zuverlässig erkennen.
