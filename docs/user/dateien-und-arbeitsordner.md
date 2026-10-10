# Dateien und Arbeitsordner

## Getrennte Bereiche

| Bereich | Zweck |
| --- | --- |
| Computer-Dateien | Gewöhnliche absolute Pfade auf dem gewählten Computer |
| Arbeitsordner | Aktueller absoluter Ordner auf dem gewählten Computer |
| Ablage | Verwaltete Dateien und Versionen einer Session |

Der interne Session-Speicher ist kein Workspace. Ein Workspace ist ein gewöhnlicher absoluter Pfad auf dem gewählten MatoLoop Agent. Neue Sessions übernehmen ihn nur durch ausdrückliche Auswahl oder Projektvorgaben. Ohne Computer und aktuellen Ordner bleiben normaler Chat und Ablage verfügbar; Plan und Build benötigen dagegen einen verbundenen Computer und zugänglichen Arbeitsordner.

Unter **Control Center > Session** wählen Sie den Computer; **Computer-Dateien** öffnet dessen Home oder einen absoluten Pfad und übernimmt einen ausgewählten Ordner als Arbeitsordner. Der Agent meldet Home und verfügbare Operationen, keine Wurzel-IDs. Ein Modell-Computerwechsel folgt dem Tool-Recht **Computer übernehmen** und gilt ab dem nächsten Modellschritt: **Automatisch** wechselt ohne Nachfrage, **Nachfragen** verwendet passende Lauf-/Session-Freigaben oder fragt nach, **Deaktiviert** blockiert den Wechsel. Verbindungsverlust löst weder automatischen Wechsel noch Wiederholung aus. Linux verwendet POSIX-Pfade, Windows Pfade wie `C:/Users/Ada/workspace`.

Computer gehören genau einem Benutzer und können nicht von anderen Konten ausgewählt werden. Diese Anwendungsgrenze ersetzt keine Trennung auf dem Betriebssystem. MatoLoop Agent arbeitet mit den Rechten seines Betriebssystembenutzers und ist keine Sandbox. Dateien auf dem Computer werden beim Löschen des MatoLoop-Kontos nicht automatisch entfernt.

## Backend-Dateien

`root=files` zeigt den revisionierten Dateibereich einer Session; Pfade enthalten keinen vorangestellten Wurzelnamen. Eine Session ohne Projekt verwendet `/data/sessions/<session-id>/files` und `/data/sessions/<session-id>/revisions`. Eine Projekt-Session verwendet `/data/projects/<projekt-id>/sessions/<session-id>/files` und `/data/projects/<projekt-id>/sessions/<session-id>/revisions`. Die Session-ID bleibt immer die Wurzelidentität.

Uploads, daraus erzeugte Begleitdateien und dauerhafte Binärausgaben von Backend-Tools sind normale verwaltete Dateien mit gespeicherten Vollversionen. Nachrichten speichern dafür Wurzel, Datei-ID, Version und Pfad.

Beim Verschieben einer Session in ein anderes Projekt oder aus einem Projekt verschiebt MatoLoop ihren vollständigen Dateibaum samt Versionsverlauf. Datei-IDs, Versionen und Nachrichtenverweise bleiben erhalten. Solange eine andere Session auf ihre Dateien verweist, kann die besitzende Session weder verschoben noch gelöscht werden.

Im Dateibrowser ist zunächst die aktuelle Session ausgewählt. Innerhalb eines Projekts können Sie unter **Dateien aus Session** ausdrücklich die Dateien einer anderen eigenen Session desselben Projekts öffnen und bearbeiten. Andere Projekte und Benutzer bleiben ausgeschlossen. Eine dort geöffnete Datei wird nicht als aktive Datei an eine Nachricht der aktuellen Session angehängt.

## Dateien im Control Center

**Ablage** verwaltet Session-Dateien; **Computer-Dateien** zeigt den Dateibaum des gewählten Computers. In beiden Apps können Sie je nach verfügbarem Zugriff:

- Ordner öffnen, filtern und weitere Einträge nachladen
- den Home- oder einen anderen absoluten Ordner des Computers öffnen
- einen markierten Ordner als aktuellen Ordner der Session verwenden
- den gewählten Computerpfad durchsuchen
- Dateien hochladen und herunterladen
- leere Text-, Markdown- oder OpenSCAD-Dateien anlegen
- Ordner anlegen
- Dateien und Ordner umbenennen oder löschen
- Dateiversionen anzeigen, herunterladen oder wiederherstellen

Versionen gelten nur für verwaltete Dateien. **Ordner als ZIP herunterladen** lädt einen Ordner samt Inhalt; große oder unsichere Dateibäume können die Transfergrenzen überschreiten. Home-, absolute Pfad- und Arbeitsordneraktionen gehören ausschließlich zu **Computer-Dateien**.

Session-Freigaben zeigen und widerrufen Sie unter **Session > Session-Freigaben**, nicht in der Datei-App oder im Benachrichtigungsbereich.

Nachrichten verweisen bei Uploads und dauerhaften Binärausgaben auf die exakte Dateiversion. Normales Bearbeiten, Umbenennen und Verschieben erhalten diese Verweise. Löschen entfernt Datei und vollständigen Versionsverlauf; die Nachricht zeigt die Datei danach als nicht mehr verfügbar. Reine Text-, JSON-, Status- und Fehlerergebnisse bleiben Teil der Nachricht.

Bestätigte Dateiaktionen erscheinen als **Dateiänderungen** an der Antwort; ausdrücklich bereitgestellte Dateien haben Öffnen-/Downloadaktionen. Öffentliche Ausgabeverweise und endgültige Versionen erscheinen erst am Laufende. Bereits geöffnete aktuelle Dateien werden aktualisiert, ohne Ihre zwischenzeitliche Navigation zu überschreiben. **Kopieren** erzeugt eine eigenständige Zieldatei mit eigenem Verlauf. Während eines Laufs sind geänderte verwaltete Dateien sichtbar, aber für konkurrierende Änderungen gesperrt; am Ende entsteht je tatsächlich geänderter Datei eine endgültige Vollversion.

`root=files` bezeichnet den verwalteten Dateibereich. `root=computer` bezeichnet den aktuellen absoluten Ordner des für den Lauf gewählten, dem Benutzer gehörenden MatoLoop Agent. Ohne Computer ist nur dieser Zugriff nicht verfügbar. Änderungen dort bleiben Eigentum des MatoLoop Agent; importierte Bytes werden zu verwalteten Dateien. Zwischen beiden Bereichen wird nur kopiert, nicht verschoben; MatoLoop prüft Größe und SHA-256.

Direkte Aktionen in **Computer-Dateien** sind an Eigentümer und Computer gebunden. Sie umgehen absichtlich Modell-Richtlinien und können auf alles zugreifen, was dessen Betriebssystembenutzer erlaubt. Linux-No-Follow- bzw. Windows-No-Reparse-Prüfungen gelten weiter. Direkte Aktionen in **Ablage** bleiben auf den ausgewählten eigenen Sessionbereich begrenzt.

Benötigt eine vom Modell angeforderte Computeraktion zwingend einen aktuellen Ordner und wurde keiner gewählt, fragt der Agent nach einem absoluten Pfad. Der anschließende Verzeichniswechsel folgt der Dateizugriffsrichtlinie für das Home-Verzeichnis oder externe Pfade und kann eine widerrufbare Session-Freigabe erzeugen. Für spätere Modellaktionen außerhalb dieses Arbeitsverzeichnisses gilt zusätzlich die Profilberechtigung **Zugriff außerhalb des Arbeitsverzeichnisses**; Aktionsrechte für Lesen, Schreiben und Löschen bleiben davon getrennt.

Bis zu vier Dateien können gemeinsam ausgewählt und nacheinander hochgeladen werden. Uploadgröße und Sessionquote legen Administratoren unter **Administration > Instanzlimits** fest. Aktuelle Bytes und gespeicherte Vollversionen teilen die Quote. Neue Dateien überschreiben keine vorhandenen Pfade.

## Vorschau und Bearbeitung

Die Vorschaugrößen für unterstützte Bilder sowie geeignete UTF-8-Text- und Markdown-Dateien werden global festgelegt; das Textlimit gilt auch für den integrierten Editor. Sichere SVG-Vorschauen bleiben unabhängig davon auf 5 MiB begrenzt. Andere Formate stehen zum Download bereit. Eine neue Datei beginnt mit einer vollständigen Version. Normales Ersetzen hängt nach Prüfung des erwarteten SHA-256-Werts eine Vollversion an; Umbenennen und Verschieben behalten Identität und Verlauf.

Das Löschen einer Datei entfernt ihren gesamten Versionsverlauf. Beim Aktivieren wird die gewählte ältere Version zur aktuellen Version; alle gespeicherten Versionen bleiben erhalten.

Beim Senden einer Nachricht übermittelt MatoLoop für die aktuell geöffnete Datei nur Wurzel, Wurzelidentität, Pfad, SHA-256 und den Status ungespeicherter Änderungen. Dateiinhalte werden dadurch nicht automatisch an den Agenten gesendet.

Text und Markdown öffnen im Quelltexteditor; Markdown bietet zusätzlich eine sanitierte Vorschau, keine visuelle WYSIWYG-Bearbeitung. Externe Bilder bleiben in Dateivorschauen deaktiviert. Code und Mermaid lassen sich kopieren oder herunterladen. Dateitabs bewahren Auswahl, Undo-Verlauf und Entwurf im Browser-Arbeitsspeicher; ein Reload stellt diese Entwürfe nicht wieder her. Bei einem Speicherkonflikt können Sie den Serverstand vergleichen und bewusst übernehmen oder Ihren Entwurf gegen den geprüften Stand behalten; keine Auswahl speichert automatisch.

Gespeicherte Markdown-Dateien können über **Als PDF speichern** in einer hellen Druckansicht geöffnet werden. Nach dem Rendern von Formeln und Mermaid-Diagrammen erscheint der Druckdialog des Browsers; dort kann das Dokument als PDF gespeichert werden. Ungespeicherte Änderungen werden nicht exportiert.

!!! warning "Löschen"
    Das Löschen eines Ordners wirkt rekursiv. Das Löschen einer Datei entfernt ihren vollständigen Versionsverlauf. Prüfen Sie den Pfad vor der Bestätigung.
