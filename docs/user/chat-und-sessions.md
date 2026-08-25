# Chat und Sessions

## Neue und vorhandene Sessions

Eine neue Session entsteht serverseitig erst mit der ersten Nachricht. Nicht gesendeter Text wird im Browser getrennt je Session und Projekt gespeichert. Vorhandene Sessions können geöffnet, angepinnt, umbenannt, zwischen Projekten verschoben und gelöscht werden.

Projekte gruppieren zusammengehörige Sessions in der Seitenleiste. Ein Projektordner kann unabhängig von anderen Projekten aufgeklappt werden. Das Session-Symbol am Projekt startet eine neue Session in diesem Projekt; Sessions ohne Projekt stehen darunter im Bereich **Sessions**.

## Session-Einstellungen

Für jede Session können Sie festlegen:

| Einstellung | Bedeutung |
| --- | --- |
| Modell | Verwendetes aktives Chatmodell |
| Memory | Voll, nur lesen oder deaktiviert |
| Websuche | Erzwingen, verfügbar oder deaktiviert |
| Computer | Optional verbundener Computer für lokale Funktionen |

Vom Modell ausgelöste Datei- und Shell-Aktionen werden durch die vier Richtlinien des gewählten Computers beschränkt; `process.execute` bleibt eine getrennte Tool-Richtlinie. Einen getrennten Agent-Modus gibt es nicht. Als rein lesend geprüfte Shell-Befehle können exakt für denselben Befehl, Computer und absoluten Arbeitsordner freigegeben werden. Andere Shell-Freigaben gelten nur einmal.

Eine neue Session besitzt nur internen Session-Speicher und noch keinen Workspace. Die Computerauswahl und der absolute aktuelle Ordner unter **Control Center > Dateien** sind veränderbare Session-Vorgaben. Beim Start eines Laufs speichert MatoLoop davon eine unveränderliche Momentaufnahme. Änderungen gelten erst für den nächsten Lauf. Ist kein Computer gewählt oder der gewählte Computer offline, bleiben Chat und Backend-Tools verfügbar; Computer-, Prozess- und passende lokale Integrationstools fehlen. MatoLoop wechselt nicht automatisch zu einem anderen Computer und wiederholt keine fehlgeschlagene lokale Operation automatisch.

## Laufende Antworten

Antworten werden während der Erzeugung angezeigt. MatoLoop stellt Arbeitsphasen, Laufzeit, Tool-Aufrufe und deren Status dar. Ein aktiver Lauf kann gestoppt werden. Bei einer kurzzeitigen Verbindungsunterbrechung versucht die Oberfläche, den Ereignisstrom wieder aufzunehmen.

Reasoning-Zusammenfassungen werden nur angezeigt, wenn das Modell sie liefert und die Modellkonfiguration sie erlaubt. Sie sind nicht mit vollständigen internen Gedankengängen gleichzusetzen.

Bei unterstützten Modellen kann der Reasoning-Aufwand direkt neben der Modellauswahl geändert oder wieder auf den Modellstandard zurückgesetzt werden. Qwen3.8-27B verwendet standardmäßig **Mittel**. Codex übernimmt verfügbare Stufen und Standardwert aus dem aktuellen Modellkatalog.

## Nachrichtenaktionen

- **Erneut versuchen** startet nach einer fehlgeschlagenen letzten Antwort einen neuen Versuch.
- **Rückgängig** entfernt eine Benutzernachricht und den nachfolgenden Verlauf und lädt Text sowie Anhänge wieder als Entwurf.
- **Kopieren** übernimmt geeignete Nachrichteninhalte in die Zwischenablage.

!!! warning "Externe Wirkungen bleiben bestehen"
    Rückgängig und erneutes Versuchen machen bereits ausgeführte Dateiänderungen, Webzugriffe, Freigaben oder Memory-Änderungen nicht rückgängig.

## Darstellung

Assistant-Antworten unterstützen Markdown, Tabellen, Aufgabenlisten, Codeblöcke mit Syntaxhervorhebung, mathematische Ausdrücke und Mermaid-Diagramme. Codeblöcke lassen sich über ihre Aktionen herunterladen oder kopieren. Mermaid-Diagramme erscheinen ohne eigenen Rahmen direkt zwischen dem übrigen Markdown; fertige Diagramme lassen sich als SVG herunterladen, als Quelltext kopieren oder unabhängig von ihrer Breite im gemeinsamen Bildviewer vergrößern. Während ein Diagramm entsteht, erscheint ein Erstellhinweis. Die Mermaid-Quelle bleibt Bestandteil der Chatnachricht und erzeugt keine zusätzliche Session-Datei. Externe Links öffnen sicher in einem neuen Tab. Externe Bilder werden erst nach Ihrer Zustimmung für den jeweiligen Ursprung geladen.
