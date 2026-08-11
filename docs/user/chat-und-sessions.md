# Chat und Sessions

## Neue und vorhandene Sessions

Eine neue Session entsteht serverseitig erst mit der ersten Nachricht. Nicht gesendeter Text wird im Browser getrennt je Session gespeichert. Vorhandene Sessions können geöffnet, angepinnt, umbenannt und gelöscht werden.

## Session-Einstellungen

Für jede Session können Sie festlegen:

| Einstellung | Bedeutung |
| --- | --- |
| Modell | Verwendetes aktives Chatmodell |
| Agent-Modus **Lesen** | Bietet nur lesende Dateitools an |
| Agent-Modus **Schreiben** | Bietet zusätzlich Dateiänderungen an |
| Memory | Voll, nur lesen oder deaktiviert |
| Websuche | Erzwingen, verfügbar oder deaktiviert |
| Computer | Optional verbundener Computer für lokale Funktionen |
| Arbeitsbereich | Vom Computer gemeldete Wurzel |
| Arbeitsordner | Optionaler relativer Unterordner im Arbeitsbereich |

Der Agent-Modus beschränkt den Agenten. Direkte Dateiaktionen, die Sie selbst im Explorer ausführen, richten sich nach den dort geltenden Grenzen.

Die Auswahl von Computer, Arbeitsbereich und Arbeitsordner ist eine veränderbare Session-Vorgabe. Beim Start eines Laufs speichert MatoLoop davon eine unveränderliche Momentaufnahme. Änderungen gelten erst für den nächsten Lauf. Ist kein Computer gewählt oder der gewählte Computer offline, bleiben Chat und Backend-Tools verfügbar; Workspace-, Prozess- und passende lokale Integrationstools fehlen. MatoLoop wechselt nicht automatisch zu einem anderen Computer und wiederholt keine fehlgeschlagene lokale Operation automatisch.

## Laufende Antworten

Antworten werden während der Erzeugung angezeigt. MatoLoop stellt Arbeitsphasen, Laufzeit, Tool-Aufrufe und deren Status dar. Ein aktiver Lauf kann gestoppt werden. Bei einer kurzzeitigen Verbindungsunterbrechung versucht die Oberfläche, den Ereignisstrom wieder aufzunehmen.

Reasoning-Zusammenfassungen werden nur angezeigt, wenn das Modell sie liefert und die Modellkonfiguration sie erlaubt. Sie sind nicht mit vollständigen internen Gedankengängen gleichzusetzen.

## Nachrichtenaktionen

- **Erneut versuchen** startet nach einer fehlgeschlagenen letzten Antwort einen neuen Versuch.
- **Rückgängig** entfernt eine Benutzernachricht und den nachfolgenden Verlauf und lädt Text sowie Anhänge wieder als Entwurf.
- **Kopieren** übernimmt geeignete Nachrichteninhalte in die Zwischenablage.

!!! warning "Externe Wirkungen bleiben bestehen"
    Rückgängig und erneutes Versuchen machen bereits ausgeführte Dateiänderungen, Webzugriffe, Freigaben oder Memory-Änderungen nicht rückgängig.

## Darstellung

Assistant-Antworten unterstützen Markdown, Tabellen, Aufgabenlisten, Codeblöcke mit Syntaxhervorhebung, mathematische Ausdrücke und Mermaid-Diagramme. Codeblöcke lassen sich über ihre Aktionen herunterladen oder kopieren. Mermaid-Diagramme erscheinen ohne eigenen Rahmen direkt zwischen dem übrigen Markdown; fertige Diagramme lassen sich als SVG herunterladen, als Quelltext kopieren oder unabhängig von ihrer Breite im gemeinsamen Bildviewer vergrößern. Während ein Diagramm entsteht, erscheint ein Erstellhinweis. Die Mermaid-Quelle bleibt Bestandteil der Chatnachricht und erzeugt keine zusätzliche Session-Datei. Externe Links öffnen sicher in einem neuen Tab. Externe Bilder werden erst nach Ihrer Zustimmung für den jeweiligen Ursprung geladen.
