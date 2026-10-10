# Chat und Sessions

## Neue und vorhandene Sessions

Die Startseite und eine leere neue Unterhaltung sind zunächst Browserentwürfe. Eine erste Nachricht oder das Öffnen einer sessiongebundenen Control-Center-App legt die Session samt internem Speicher an. Die feste Session-Einstellungsansicht und globale Benachrichtigungen allein erzeugen keine Session.

Sessions können angepinnt, umbenannt, zwischen Projekten verschoben, archiviert und gelöscht werden. Projekte gruppieren Sessions, teilen aber nicht deren Dateiablage. Ein Projekt kann Sessionvorlage, Computer und Arbeitsordner als Vorgaben für neue Sessions speichern; spätere Projektänderungen schreiben bestehende Sessions nicht um. Beim Projektwechsel wird ein Entwurf nur in einen leeren Zielentwurf übernommen, nie über vorhandene Inhalte oder eine Warteschlange geschrieben.

## Session-Einstellungen

| Einstellung | Bedeutung |
| --- | --- |
| Sessionvorlage | Verfügbare Agenten, Startagent, Memory, Nachrichtengröße und optionale Modell-/Reasoning-Vorgaben |
| Agent | Arbeitsweise, Tool-Rechte und Integrationsauswahl |
| Modell und Reasoning | Effektive Modellwahl und unterstützte Reasoning-Stufe |
| Memory | Voll, nur lesen oder deaktiviert |
| Websuche | Erzwingen, verfügbar oder deaktiviert |
| Computer und Arbeitsordner | Optionaler lokaler Ausführungsort |

Unter **Control Center > Session** ändern Sie die Sessionwerte. Die Vorlagen **Allgemein** und **Coding** bieten Allgemein/Sysadmin bzw. Plan/Build. Eine Vorlagenauswahl ersetzt Agentenauswahl, Memory, Nachrichtengröße und Modell-/Reasoning-Vorgaben; Projekt, Computer, Arbeitsordner und Websuche bleiben unabhängig. Spätere Änderungen einer gespeicherten Vorlage wirken nicht rückwirkend.

Modell und Reasoning gehören nicht zum Agentenprofil. Ein agentenspezifischer Sessionwert hat Vorrang vor dem sessionweiten Wert, dann persönlichen und globalen **Standardmodellen**; beim Reasoning folgt zuletzt der Modellstandard. Nicht verfügbare Modellreferenzen werden bei der Auflösung übersprungen. Ohne ausdrückliches Agentmodell kann das erste verfügbare toolfähige Chatmodell dienen. Hilfsmodelle verwenden Standardmodelle und ihre jeweiligen Fallbacks.

Plan und Build brauchen einen verbundenen Computer und zugänglichen Arbeitsordner. Allgemeiner Chat und Backend-Tools funktionieren ohne Computer. Fehlende Laufvoraussetzungen erscheinen als gespeicherte Host-Antwort, die Sie nach einer Korrektur erneut versuchen können.

## Laufende Antworten

MatoLoop zeigt Antworttext, aktuelle Aktivität, Tool-Ergebnisse, Laufzeit und gegebenenfalls Reasoning-Zusammenfassungen. Diese Zusammenfassungen sind keine vollständigen internen Gedankengänge. Bei Verbindungsunterbrechung lädt die Oberfläche den gespeicherten Zustand nach; ein Browserdisconnect stoppt den Agenten nicht. Der Stoppknopf beendet den Lauf, ohne abgeschlossene Wirkungen rückgängig zu machen.

### Während eines Laufs senden

**Jetzt senden** ergänzt den aktuellen Lauf um Ihre neue Nachricht. Eine laufende Modellanfrage wird neu aufgebaut; aktive Tool-Arbeit darf zuerst enden. Eine überholte Freigabe wird geschlossen und der Agent plant mit Ihrer neuen Anweisung weiter. Das erlaubt keine zuvor gesperrte Aktion.

**Nach dem Lauf senden** reiht die Nachricht für einen anschließenden Lauf ein. Der ownergebundene Browser-Postausgang bewahrt eingereihte Nachrichten und Anhänge in Reihenfolge. Fehlgeschlagene Sendungen bleiben für einen erneuten Versuch oder die Übernahme in den Entwurf erhalten. Abmelden oder ein Kontowechsel entfernt den bisherigen Postausgang dieses Browsers.

Der laufende Auftrag behält sein Startprofil und seinen Modell-/Vertrauenskontext. Neue Profilauswahlen gelten für spätere Läufe. Für Details zu wirksamen Rechten siehe [Freigaben](freigaben-und-datenfreigabe.md).

### Aufgaben und Subagenten

**Agent-Coding** im Control Center bündelt Einstiege zu Ablage, Computer-Dateien, Terminal, Browser und OpenCode, soweit verfügbar. Die App öffnet bestehende Werkzeuge; sie gewährt keine zusätzlichen Rechte und erzeugt keine automatische Workspace-Isolation.

Geeignete Profile können eine Aufgabenliste oberhalb des Eingabefelds anzeigen. Sie lässt sich zuklappen; der Status dokumentiert gemeldeten Fortschritt und ist kein unabhängiger Erfolgsnachweis.

Delegierte Aufgaben erscheinen mit anklickbarem Titel und Status in der Aktivität. Der Subagenteninspektor zeigt deren eigenen Verlauf schreibgeschützt. Fragen und Freigaben beantworten Sie in der Hauptsession. Kinder teilen den Computer-Arbeitsbereich; unterschiedliche Sessions bieten dort keine Dateisolation. Ein Stopp des Elternlaufs stoppt auch offene delegierte Arbeit.

## Rückfragen

Eine strukturierte Rückfrage ersetzt vorübergehend das Eingabefeld. **Weiter** navigiert; **Fertig** sendet den bisherigen Antwortstand auch vor der letzten Frage. Fragen dürfen unbeantwortet bleiben. **Ablehnen** verwirft den Entwurf. Geschützte Felder behandeln ausgelassene Werte ausdrücklich als fehlend; siehe [Variablen und Secrets](variablen-und-secrets.md).

## Nachrichtenaktionen

- **Erneut versuchen** startet für die letzte fehlgeschlagene Host-Antwort einen neuen Versuch. Vor-Ausführungsfehler werden mit aktuellen Sessionvorgaben erneut geprüft; bereits gestartete Läufe behalten ihren Retry-Kontext.
- **Rückgängig** entfernt eine Benutzernachricht und den folgenden Verlauf, nachdem Text und Anhänge wieder in den Entwurf übernommen wurden.
- **Kopieren** übernimmt Nachrichteninhalte in die Zwischenablage.

!!! warning "Externe Wirkungen bleiben bestehen"
    Rückgängig, Retry und Stopp machen bereits ausgeführte Datei-, Integrations-, Freigabe- oder Memory-Änderungen nicht rückgängig.

## Darstellung

Antworten unterstützen Markdown, Tabellen, Code, Mathematik und Mermaid. Codeblöcke lassen sich kopieren oder herunterladen; fertige Mermaid-Diagramme als SVG herunterladen, als Quelle kopieren oder vergrößern. Die Quelle bleibt in der Nachricht, ohne zusätzliche Datei. Externe Bilder benötigen eine Zustimmung je Ursprung und Session. **Kleine Nachrichten** aus der Sessionvorlage bzw. Sessionansicht verdichtet die Darstellung, ohne den Inhalt zu kürzen.
