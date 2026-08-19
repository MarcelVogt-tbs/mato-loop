# Dateien und Arbeitsordner

## Getrennte Bereiche

| Bereich | Zweck |
| --- | --- |
| Arbeitsbereich | Vom gewählten Computer bereitgestellte Wurzel |
| Arbeitsordner | Optionaler relativer Unterordner innerhalb des Arbeitsbereichs |
| Session-Dateien | Interner, dauerhaft zur Session gehörender Speicher |

Neue Sessions besitzen zunächst keinen Computer und keinen Arbeitsbereich. Jeder Arbeitsbereich ist eine Wurzel, die ein Computer bereitstellt und selbst besitzt; das Backend besitzt keinen eigenen Arbeitsbereich. Ohne einen gewählten Computer und eine seiner Wurzeln funktioniert der Chat weiter und die Session-Dateien bleiben verfügbar. Computer-Arbeitsbereiche und lokale Integrationen stehen dann jedoch nicht zur Verfügung.

MatoLoop unterstützt mehrere Computer. Unter **Sessioneinstellungen** wählen Sie den Computer. Dieser meldet eine oder mehrere Wurzeln und optional ein Home-Verzeichnis innerhalb einer Wurzel. Das Home ist nur die Vorgabe für neue Sessions. Danach speichert jede Session ihre gewählte Wurzel, ihren relativen Arbeitsordner und den dazugehörigen kanonischen aktuellen Ordner getrennt. Arbeitsbereich und aktueller Ordner werden unter **Control Center > Dateien** gewählt. Die Auswahl ist die Vorgabe für den nächsten Lauf; ein bereits laufender Lauf behält unveränderlich seinen bisherigen Computer und Pfad. Wird der Computer getrennt, erfolgt kein automatischer Wechsel und keine automatische Wiederholung.

Computer gehören genau einem Benutzer und können nicht von anderen Konten ausgewählt werden. Diese Anwendungsgrenze ersetzt keine Trennung auf dem Betriebssystem des Computers. Dateien in dessen externem Arbeitsbereich werden beim Löschen des MatoLoop-Kontos nicht automatisch entfernt.

## Session-Speicher

Der Session-Speicher enthält fünf Bereiche:

- `files`: direkt veränderbare Dateien
- `attachments`: ursprüngliche Nachrichtenanhänge
- `generated`: erzeugte Medien und Dateien
- `downloads`: durch Tools heruntergeladene Inhalte
- `derived`: serverseitig abgeleitete Inhalte

Nur `files` ist direkt veränderbar. Die übrigen Bereiche werden von MatoLoop verwaltet, können aber gelesen oder als Kopierquelle verwendet werden.

## Dateien im Control Center

In der App **Dateien** können Sie:

- Ordner öffnen, filtern und weitere Einträge nachladen
- einen vom Computer gemeldeten Arbeitsbereich wählen
- einen markierten Ordner als aktuellen Ordner der Session verwenden
- den gewählten Arbeitsbereich durchsuchen
- Dateien hochladen und herunterladen
- leere Text- oder Markdown-Dateien anlegen
- Ordner anlegen
- Dateien und Ordner umbenennen oder löschen
- Session-Freigaben anzeigen und widerrufen

Von Tools zurückgegebene Dateien erscheinen als schreibgeschützte Session-Artefakte unter `generated`, heruntergeladene Originale unter `downloads` und erforderliche Medienableitungen unter `derived`. Reine Status-, JSON- und in Chatantworten eingebettete Ergebnisse erzeugen keine zusätzlichen Dateien.

`root=session` bezeichnet immer den Backend-Speicher der Session. `root=workspace` bezeichnet immer eine Wurzel des für den Lauf gewählten, dem Benutzer gehörenden Computers. Ohne Computer und Wurzel ist nur dieser Workspace-Zugriff nicht verfügbar. Kopien zwischen beiden Bereichen werden vom Backend vermittelt und anhand von Größe und SHA-256 geprüft. Der Computer darf verwendete Session-Dateien in einem begrenzten, unvollständigen Cache halten. Dieser Cache ist keine maßgebliche Kopie und kann jederzeit geleert werden.

Benötigt eine angeforderte Computeraktion zwingend einen aktuellen Ordner und wurde keiner gewählt, fragt der Agent nach einem absoluten Pfad. Der anschließende Verzeichniswechsel bleibt eine getrennte, widerrufbare Session-Freigabe.

Bis zu vier Dateien können im Browser gemeinsam ausgewählt und nacheinander hochgeladen werden. Die maximale Uploadgröße und die gemeinsame Quote des vollständigen Session-Dateibaums werden global von Administratoren unter **Einstellungen > Instanzlimits** festgelegt. Neue Dateien überschreiben keine vorhandenen Pfade.

## Vorschau und Bearbeitung

Die Vorschaugrößen für unterstützte Bilder sowie geeignete UTF-8-Text- und Markdown-Dateien werden global festgelegt; das Textlimit gilt auch für den integrierten Editor. Sichere SVG-Vorschauen bleiben unabhängig davon auf 5 MiB begrenzt. Andere Formate stehen zum Download bereit. Textdateien werden mit einer Revisionsprüfung gespeichert, damit parallele Änderungen nicht unbemerkt überschrieben werden.

Markdown kann als Quelltext oder visuell bearbeitet werden. In der schreibgeschützten Vorschau lassen sich Codeblöcke herunterladen oder kopieren. Mermaid-Diagramme erscheinen direkt im übrigen Markdown und lassen sich als SVG herunterladen, als Quelltext kopieren oder im gemeinsamen Bildviewer vergrößern. Dokumente mit Mermaid-Codeblöcken können nur im Quelltextmodus bearbeitet werden. Enthält das Dokument Bildreferenzen, bleibt es aus Sicherheitsgründen ebenfalls im Quelltextmodus.

Gespeicherte Markdown-Dateien können über **Als PDF speichern** in einer hellen Druckansicht geöffnet werden. Nach dem Rendern von Formeln und Mermaid-Diagrammen erscheint der Druckdialog des Browsers; dort kann das Dokument als PDF gespeichert werden. Ungespeicherte Änderungen werden nicht exportiert.

!!! warning "Löschen"
    Das Löschen eines Ordners wirkt rekursiv. Prüfen Sie den angezeigten Pfad vor der Bestätigung.
