# Dateien und Arbeitsordner

## Getrennte Bereiche

| Bereich | Zweck |
| --- | --- |
| Arbeitsbereich | Vom gewählten Computer bereitgestellte Wurzel |
| Arbeitsordner | Optionaler relativer Unterordner innerhalb des Arbeitsbereichs |
| Backend-Dateien | Interner Speicher der Session oder ihres Projekts |

Neue Sessions besitzen zunächst keinen Computer und keinen Arbeitsbereich. Jeder Arbeitsbereich ist eine Wurzel, die ein Computer bereitstellt und selbst besitzt; das Backend besitzt keinen eigenen Arbeitsbereich. Ohne einen gewählten Computer und eine seiner Wurzeln funktioniert der Chat weiter und die Session-Dateien bleiben verfügbar. Computer-Arbeitsbereiche und lokale Integrationen stehen dann jedoch nicht zur Verfügung.

MatoLoop unterstützt mehrere Computer. Unter **Sessioneinstellungen** wählen Sie den Computer. Dieser meldet eine oder mehrere Wurzeln und optional ein Home-Verzeichnis innerhalb einer Wurzel. Das Home ist nur die Vorgabe für neue Sessions. Danach speichert jede Session ihre gewählte Wurzel, ihren relativen Arbeitsordner und den dazugehörigen kanonischen aktuellen Ordner getrennt. Arbeitsbereich und aktueller Ordner werden unter **Control Center > Dateien** gewählt. Die Auswahl ist die Vorgabe für den nächsten Lauf; ein bereits laufender Lauf behält unveränderlich seinen bisherigen Computer und Pfad. Wird der Computer getrennt, erfolgt kein automatischer Wechsel und keine automatische Wiederholung.

Computer gehören genau einem Benutzer und können nicht von anderen Konten ausgewählt werden. Diese Anwendungsgrenze ersetzt keine Trennung auf dem Betriebssystem des Computers. Dateien in dessen externem Arbeitsbereich werden beim Löschen des MatoLoop-Kontos nicht automatisch entfernt.

## Backend-Dateien

`root=session` zeigt genau einen Ordner `files`. Bei einer Session ohne Projekt gehören Dateien und Versionsverlauf nur dieser Session. Alle Sessions eines Projekts verwenden dagegen gemeinsam `/data/projects/<projekt-id>/files` und `/data/projects/<projekt-id>/revisions`; die Projekt-ID ist dabei die Wurzelidentität.

Nachrichtenanhänge und Tool-Artefakte bleiben unveränderlich im privaten Speicher ihrer Session und Nachricht. Sie erscheinen nicht im Dateibaum und sind nur über die Anhangsfunktionen erreichbar.

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
- frühere Dateiversionen anzeigen, herunterladen, wiederherstellen oder löschen
- Session-Freigaben anzeigen und widerrufen

Von Tools zurückgegebene Anhänge bleiben an der jeweiligen Nachricht. Reine Status-, JSON- und in Chatantworten eingebettete Ergebnisse erzeugen keine zusätzlichen Dateien.

`root=session` bezeichnet immer die Backend-Dateiwurzel der Session oder ihres Projekts. `root=workspace` bezeichnet immer eine Wurzel des für den Lauf gewählten, dem Benutzer gehörenden Computers. Ohne Computer und Wurzel ist nur dieser Workspace-Zugriff nicht verfügbar. Kopien zwischen beiden Bereichen werden vom Backend vermittelt und anhand von Größe und SHA-256 geprüft. Der Computer darf verwendete Backend-Dateien in einem begrenzten, unvollständigen Cache halten. Dieser Cache ist keine maßgebliche Kopie und kann jederzeit geleert werden.

Benötigt eine angeforderte Computeraktion zwingend einen aktuellen Ordner und wurde keiner gewählt, fragt der Agent nach einem absoluten Pfad. Der anschließende Verzeichniswechsel bleibt eine getrennte, widerrufbare Session-Freigabe.

Bis zu vier Dateien können im Browser gemeinsam ausgewählt und nacheinander hochgeladen werden. Die maximale Uploadgröße und die Quote der jeweiligen Session- oder Projektwurzel werden global von Administratoren unter **Einstellungen > Instanzlimits** festgelegt. Aktuelle Dateibytes und alle gespeicherten Vollversionen zählen gemeinsam gegen diese Quote. Neue Dateien überschreiben keine vorhandenen Pfade.

## Vorschau und Bearbeitung

Die Vorschaugrößen für unterstützte Bilder sowie geeignete UTF-8-Text- und Markdown-Dateien werden global festgelegt; das Textlimit gilt auch für den integrierten Editor. Sichere SVG-Vorschauen bleiben unabhängig davon auf 5 MiB begrenzt. Andere Formate stehen zum Download bereit. Eine neue Datei beginnt mit einer vollständigen Version; Kopieren erzeugt ebenfalls eine neue Identität mit Anfangsversion. Ersetzen hängt nach Prüfung des erwarteten SHA-256-Werts eine Vollversion an. Umbenennen und Verschieben behalten Identität und Verlauf. Wiederherstellen entfernt alle späteren Versionen, die aktuelle Version kann nicht gelöscht werden, und das Löschen einer Datei entfernt ihren gesamten Verlauf.

Beim Senden einer Nachricht übermittelt MatoLoop für die aktuell geöffnete Datei nur Wurzel, Wurzelidentität, Pfad, SHA-256 und den Status ungespeicherter Änderungen. Dateiinhalte werden dadurch nicht automatisch an den Agenten gesendet.

Markdown kann als Quelltext oder visuell bearbeitet werden. In der schreibgeschützten Vorschau lassen sich Codeblöcke herunterladen oder kopieren. Mermaid-Diagramme erscheinen direkt im übrigen Markdown und lassen sich als SVG herunterladen, als Quelltext kopieren oder im gemeinsamen Bildviewer vergrößern. Dokumente mit Mermaid-Codeblöcken können nur im Quelltextmodus bearbeitet werden. Enthält das Dokument Bildreferenzen, bleibt es aus Sicherheitsgründen ebenfalls im Quelltextmodus.

Gespeicherte Markdown-Dateien können über **Als PDF speichern** in einer hellen Druckansicht geöffnet werden. Nach dem Rendern von Formeln und Mermaid-Diagrammen erscheint der Druckdialog des Browsers; dort kann das Dokument als PDF gespeichert werden. Ungespeicherte Änderungen werden nicht exportiert.

!!! warning "Löschen"
    Das Löschen eines Ordners wirkt rekursiv. Prüfen Sie den angezeigten Pfad vor der Bestätigung.
