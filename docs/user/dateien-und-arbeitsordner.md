# Dateien und Arbeitsordner

## Getrennte Bereiche

| Bereich | Zweck |
| --- | --- |
| Computer-Dateien | Gewöhnliche absolute Pfade auf dem gewählten Computer |
| Arbeitsordner | Aktueller absoluter Ordner auf dem gewählten Computer |
| Backend-Dateien | Interner Speicher der Session oder ihres Projekts |

Neue Sessions besitzen zunächst nur ihren benutzergebundenen internen Session-Speicher. Dieser ist kein Workspace. Ein Workspace ist ein gewöhnlicher absoluter Pfad auf dem gewählten MatoLoop Agent; das Backend besitzt keinen eigenen Workspace. Ohne einen gewählten Computer und aktuellen Ordner funktioniert der Chat weiter und die Session-Dateien bleiben verfügbar. Computer-Dateien und lokale Integrationen stehen dann jedoch nicht zur Verfügung.

MatoLoop unterstützt mehrere Computer. Unter **Sessioneinstellungen** wählen Sie den Computer. Der MatoLoop Agent meldet genau ein erforderliches Home-Verzeichnis als Startpunkt und seine verfügbaren Operationen, aber keine Wurzeln, Wurzel-IDs oder Wurzelversionen. Den aktuellen absoluten Ordner wählen Sie unter **Control Center > Dateien**. Die Session speichert Computer und Pfad als Vorgabe für den nächsten Lauf; ein bereits laufender Lauf behält seine unveränderliche Momentaufnahme. Wird der Computer getrennt, erfolgt kein automatischer Wechsel und keine automatische Wiederholung.

Computer gehören genau einem Benutzer und können nicht von anderen Konten ausgewählt werden. Diese Anwendungsgrenze ersetzt keine Trennung auf dem Betriebssystem. MatoLoop Agent arbeitet mit den Rechten seines Betriebssystembenutzers und ist keine Sandbox. Dateien auf dem Computer werden beim Löschen des MatoLoop-Kontos nicht automatisch entfernt.

## Backend-Dateien

`root=files` zeigt den revisionierten Dateibereich der Session oder ihres Projekts; Pfade enthalten keinen vorangestellten Wurzelnamen. Eine Session ohne Projekt verwendet `/data/sessions/<session-id>/files` und `/data/sessions/<session-id>/revisions`. Alle Sessions eines Projekts verwenden gemeinsam `/data/projects/<projekt-id>/files` und `/data/projects/<projekt-id>/revisions`; die Projekt-ID ist dabei die Wurzelidentität.

Uploads, daraus erzeugte Begleitdateien und dauerhafte Binärausgaben von Backend-Tools sind normale verwaltete Dateien mit gespeicherten Vollversionen. Nachrichten speichern dafür Wurzel, Datei-ID, Version und Pfad.

Wird eine eigenständige Session in ein Projekt verschoben, übernimmt MatoLoop ihre Dateien samt Versionsverlauf in den gemeinsamen Projektbereich. Vorhandene Projektordner werden zusammengeführt, bestehende gleichnamige Pfade aber niemals überschrieben; bei einem Konflikt bleibt die Session unverändert. Die übernommenen Dateien sind anschließend für alle Sessions des Projekts sichtbar und bleiben im Projekt, wenn die ursprüngliche Session später wieder herausgeschoben wird. Beim Verlassen eines Projekts erhält die Session einen neuen leeren Dateibereich.

## Dateien im Control Center

In der App **Dateien** können Sie:

- Ordner öffnen, filtern und weitere Einträge nachladen
- den Home- oder einen anderen absoluten Ordner des Computers öffnen
- einen markierten Ordner als aktuellen Ordner der Session verwenden
- den gewählten Computerpfad durchsuchen
- Dateien hochladen und herunterladen
- leere Text- oder Markdown-Dateien anlegen
- Ordner anlegen
- Dateien und Ordner umbenennen oder löschen
- Dateiversionen anzeigen, herunterladen oder wiederherstellen

View and revoke granted session permissions under **Session > Session-Freigaben** in the Control Center, not in the file app or the global notification center.

Nachrichten verweisen bei Uploads und dauerhaften Binärausgaben auf die exakte Dateiversion. Normales Bearbeiten, Umbenennen und Verschieben erhalten diese Verweise. Löschen entfernt Datei und vollständigen Versionsverlauf; die Nachricht zeigt die Datei danach als nicht mehr verfügbar. Reine Text-, JSON-, Status- und Fehlerergebnisse bleiben Teil der Nachricht.

Nach erfolgreichen Schreib-, Kopier- und Verschiebeaktionen erscheint die vorhandene Dateikarte zum Ansehen oder Herunterladen; für erzeugte Session-Dateien gibt es keine zusätzliche Entwurfskarte. Eine neu erzeugte Session-Datei öffnet MatoLoop automatisch im vorhandenen Dateibrowser, solange Sie dessen Navigation während des Agentenlaufs nicht gewechselt haben und kein ungespeicherter Entwurf verdrängt würde. Ist dieselbe aktuelle Datei bereits geöffnet, bleibt sie ausgewählt und wird aktualisiert, statt eine weitere Ansicht zu öffnen. Textvorschauen lassen sich direkt kopieren. **Kopieren** erzeugt am Ziel eine eigenständige verwaltete Datei mit eigenem Versionsverlauf.

`root=files` bezeichnet den verwalteten Dateibereich. `root=computer` bezeichnet den aktuellen absoluten Ordner des für den Lauf gewählten, dem Benutzer gehörenden MatoLoop Agent. Ohne Computer ist nur dieser Zugriff nicht verfügbar. Änderungen dort bleiben Eigentum des MatoLoop Agent; importierte Bytes werden zu verwalteten Dateien. Zwischen beiden Bereichen wird nur kopiert, nicht verschoben; MatoLoop prüft Größe und SHA-256.

Direkte Dateiaktionen unter **Control Center > Dateien** sind an den angemeldeten Eigentümer und den gewählten Computer gebunden. Sie dürfen absichtlich auf alles zugreifen, was dessen Betriebssystembenutzer lesen oder ändern darf, und umgehen die Modell-Richtlinien und Modell-Freigaben. Betriebssystemrechte sowie Descriptor- und No-Follow-Prüfungen des MatoLoop Agent gelten weiterhin.

Benötigt eine vom Modell angeforderte Computeraktion zwingend einen aktuellen Ordner und wurde keiner gewählt, fragt der Agent nach einem absoluten Pfad. Der anschließende Verzeichniswechsel folgt der Dateizugriffsrichtlinie für das Home-Verzeichnis oder externe Pfade und kann eine widerrufbare Session-Freigabe erzeugen.

Bis zu vier Dateien können im Browser gemeinsam ausgewählt und nacheinander hochgeladen werden. Die maximale Uploadgröße und die Quote der jeweiligen Session- oder Projektwurzel werden global von Administratoren unter **Einstellungen > Instanzlimits** festgelegt. Aktuelle Dateibytes und alle gespeicherten Vollversionen zählen gemeinsam gegen diese Quote. Neue Dateien überschreiben keine vorhandenen Pfade.

## Vorschau und Bearbeitung

Die Vorschaugrößen für unterstützte Bilder sowie geeignete UTF-8-Text- und Markdown-Dateien werden global festgelegt; das Textlimit gilt auch für den integrierten Editor. Sichere SVG-Vorschauen bleiben unabhängig davon auf 5 MiB begrenzt. Andere Formate stehen zum Download bereit. Eine neue Datei beginnt mit einer vollständigen Version. Normales Ersetzen hängt nach Prüfung des erwarteten SHA-256-Werts eine Vollversion an; Umbenennen und Verschieben behalten Identität und Verlauf.

Das Löschen einer Datei entfernt ihren gesamten Versionsverlauf. Beim Aktivieren wird die gewählte ältere Version zur aktuellen Version; alle gespeicherten Versionen bleiben erhalten.

Beim Senden einer Nachricht übermittelt MatoLoop für die aktuell geöffnete Datei nur Wurzel, Wurzelidentität, Pfad, SHA-256 und den Status ungespeicherter Änderungen. Dateiinhalte werden dadurch nicht automatisch an den Agenten gesendet.

Markdown kann als Quelltext oder visuell bearbeitet werden. In der schreibgeschützten Vorschau lassen sich Codeblöcke herunterladen oder kopieren. Mermaid-Diagramme erscheinen direkt im übrigen Markdown und lassen sich als SVG herunterladen, als Quelltext kopieren oder im gemeinsamen Bildviewer vergrößern. Dokumente mit Mermaid-Codeblöcken können nur im Quelltextmodus bearbeitet werden. Enthält das Dokument Bildreferenzen, bleibt es aus Sicherheitsgründen ebenfalls im Quelltextmodus.

Gespeicherte Markdown-Dateien können über **Als PDF speichern** in einer hellen Druckansicht geöffnet werden. Nach dem Rendern von Formeln und Mermaid-Diagrammen erscheint der Druckdialog des Browsers; dort kann das Dokument als PDF gespeichert werden. Ungespeicherte Änderungen werden nicht exportiert.

!!! warning "Löschen"
    Das Löschen eines Ordners wirkt rekursiv. Das Löschen einer Datei entfernt ihren vollständigen Versionsverlauf. Prüfen Sie den Pfad vor der Bestätigung.
