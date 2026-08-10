# Dateien und Arbeitsordner

## Getrennte Bereiche

| Bereich | Zweck |
| --- | --- |
| Arbeitsbereich | Vom gewählten Ausführungsagenten bereitgestellte Wurzel |
| Arbeitsordner | Optionaler relativer Unterordner innerhalb des Arbeitsbereichs |
| Session-Dateien | Interner, dauerhaft zur Session gehörender Speicher |

Neue Sessions besitzen zunächst keinen Ausführungsagenten und keinen Arbeitsbereich. Ohne einen gewählten und verbundenen Agenten funktioniert der Chat weiter, lokale Arbeitsbereiche und lokale Integrationen stehen jedoch nicht zur Verfügung. Der getrennte Session-Speicher existiert immer im MatoLoop-Backend.

MatoLoop unterstützt mehrere Ausführungsagenten. Unter **Sessioneinstellungen** wählen Sie einen Agenten, einen von ihm gemeldeten Arbeitsbereich und optional einen relativen Arbeitsordner. Die Auswahl ist die Vorgabe für den nächsten Lauf; ein bereits laufender Agentenlauf behält unveränderlich seinen bisherigen Agenten und Pfad. Wird der Agent getrennt, erfolgt kein automatischer Wechsel und keine automatische Wiederholung.

## Session-Speicher

Der Session-Speicher enthält fünf Bereiche:

- `files`: direkt veränderbare Dateien
- `attachments`: ursprüngliche Nachrichtenanhänge
- `generated`: erzeugte Medien und Dateien
- `downloads`: durch Tools heruntergeladene Inhalte
- `derived`: serverseitig abgeleitete Inhalte

Nur `files` ist direkt veränderbar. Die übrigen Bereiche werden von MatoLoop verwaltet, können aber gelesen oder als Kopierquelle verwendet werden.

## Datei-Explorer

Im Explorer können Sie:

- Ordner öffnen, filtern und weitere Einträge nachladen
- den gewählten Arbeitsbereich durchsuchen
- Dateien hochladen und herunterladen
- leere Text- oder Markdown-Dateien anlegen
- Ordner anlegen
- Dateien und Ordner umbenennen oder löschen
- Session-Freigaben anzeigen und widerrufen

Von Tools zurückgegebene Dateien erscheinen als schreibgeschützte Session-Artefakte unter `generated`, heruntergeladene Originale unter `downloads` und erforderliche Medienableitungen unter `derived`. Reine Status-, JSON- und in Chatantworten eingebettete Ergebnisse erzeugen keine zusätzlichen Dateien.

`root=session` bezeichnet immer den Backend-Speicher der Session, `root=workspace` den Arbeitsbereich des für den Lauf gewählten Ausführungsagenten. Kopien zwischen beiden Bereichen werden vom Backend vermittelt und anhand von Größe und SHA-256 geprüft. Der Agent darf verwendete Session-Dateien in einem begrenzten, unvollständigen Cache halten. Dieser Cache ist keine maßgebliche Kopie und kann jederzeit geleert werden.

Bis zu vier Dateien mit jeweils höchstens 64 MiB können gemeinsam hochgeladen werden. Neue Dateien überschreiben keine vorhandenen Pfade. Der gesamte Session-Dateibaum und alle Nachrichtenanhänge einer Session sind jeweils auf 500 MiB begrenzt.

## Vorschau und Bearbeitung

Unterstützte Bilder bis 5 MiB sowie geeignete UTF-8-Text- und Markdown-Dateien bis 1 MiB besitzen eine Vorschau. Das Limit von 1 MiB gilt auch für den integrierten Texteditor. Andere Formate stehen zum Download bereit. Textdateien werden mit einer Revisionsprüfung gespeichert, damit parallele Änderungen nicht unbemerkt überschrieben werden.

Markdown kann als Quelltext oder visuell bearbeitet werden. Die gespeicherte Datei lässt sich vollständig zwischen **Vorschau** und **Quelltext** umschalten; Mermaid-Diagramme erscheinen direkt im übrigen Markdown und lassen sich im gemeinsamen Bildviewer vergrößern. Dokumente mit Mermaid-Codeblöcken können nur im Quelltextmodus bearbeitet werden. Enthält das Dokument Bildreferenzen, bleibt es aus Sicherheitsgründen ebenfalls im Quelltextmodus.

Gespeicherte Markdown-Dateien können über **Als PDF speichern** in einer hellen Druckansicht geöffnet werden. Nach dem Rendern von Formeln und Mermaid-Diagrammen erscheint der Druckdialog des Browsers; dort kann das Dokument als PDF gespeichert werden. Ungespeicherte Änderungen werden nicht exportiert.

!!! warning "Löschen"
    Das Löschen eines Ordners wirkt rekursiv. Prüfen Sie den angezeigten Pfad vor der Bestätigung.
