# Mit Git und Worktrees arbeiten

Git läuft auf dem ausgewählten Computer mit dessen Git-Installation und Anmeldung. Es gibt keine eigene Git-Integration oder Backend-Clonefunktion. Die **Git**-App im Control Center zeigt Änderungen und verwaltet den Arbeitsbereich; Agentenaufträge verwenden die normalen Shell-Rechte.

## Ein Repository verwenden

1. Wähle einen verbundenen Computer und einen zugänglichen Arbeitsordner mit Git-Repository.
2. Öffne **Control Center > Git > Repository**. Unter **Aktionen** bietet **Git-Repository anlegen** ohne vorhandenes Repository einen Agentenauftrag zur Initialisierung; er legt weder Commit noch Remote an.
3. Für Projektvorgaben wähle in **Projekt bearbeiten** die Repository-Wurzel und speichere **Standard-Zielbranch** und **Standard-Arbeitsbereich**. Ein später angelegtes Repository wird beim erneuten Öffnen oder Prüfen erkannt.

Git-App und Befehlspalette allein erzeugen keinen Chat. Erst Sessionstart oder Agentenauftrag speichert die Unterhaltung.

## Änderungen ansehen

**Git > Änderungen** vergleicht HEAD mit dem aktuellen Arbeitsstand. Die Liste enthält staged und unstaged Änderungen sowie neue, nicht ignorierte Dateien des Repositorys oder Worktrees. Auch eigene, bereits vorhandene Änderungen zählen mit. Die Zahlen sind kein Änderungsprotokoll einzelner KI-Antworten.

Der Tab zeigt hinzugefügte Zeilen als grünes **+** und gelöschte Zeilen als rotes **−**, jeweils nur bei einer bekannten Anzahl größer null. Ohne solche Zeilenänderungen steht dort nur **Änderungen**; betroffene Dateien bleiben in der Liste sichtbar.

Ein Klick auf eine Datei öffnet den schreibgeschützten Diff im normalen Dateitab. Das Diff-Symbol schaltet zwischen Vergleich und Quelltext um. Ungespeicherte Entwürfe gehören nicht zum Git-Diff und bleiben beim Umschalten erhalten. Auf kleinen Flächen stehen die Vergleichsseiten untereinander.

Die Liste aktualisiert sich beim Öffnen und nach abgeschlossenen Läufen, solange die App sichtbar ist. Für externe Änderungen wähle **Aktualisieren**. Binärdateien, Submodule und Links haben keine Textvorschau; Textvergleiche sind auf 256 KiB je Seite begrenzt. Begrenzte Listen und fehlende Zähler werden gekennzeichnet. Git-Filterprogramme laufen bei dieser Ansicht nicht, daher kann sie von einem gefilterten Git-Diff abweichen.

## Einen getrennten Worktree starten

Worktrees stehen für Projekt-Sessions mit konfiguriertem Computer, Repository und einer Vorlage mit **Worktrees verwenden** zur Verfügung. **Coding** aktiviert die Option standardmäßig.

1. Wähle vor dem Sessionstart unter **Arbeitsbereich** **Neuer Worktree**.
2. Prüfe den lokalen Zielbranch. Er muss einen Commit besitzen.
3. Starte die Session. MatoLoop erstellt daraus eine Arbeitskopie mit eigenem Branch.

Es findet kein Fetch statt und ein Remote ist nicht nötig. Uncommittete Änderungen bleiben im ursprünglichen Verzeichnis. **Lokal** und die Auswahl eines bestehenden Worktrees erstellen keinen Branch.

!!! warning "Worktrees isolieren nicht die ganze Umgebung"
    Mehrere Sessions können denselben Worktree und damit Dateien und Änderungen teilen. Worktrees teilen Git-Metadaten; das ursprüngliche Repository muss erreichbar bleiben. `.env`-Dateien, Abhängigkeiten, Prozesse, Ports und Datenbanken werden nicht automatisch kopiert oder getrennt.

## Arbeitsbereich oder Branch wechseln

Unter **Git > Repository** findest du unterhalb der **Aktionen** die **Einstellungen**. Dort öffnen die Bearbeiten-Symbole die Dialoge für Arbeitsbereich, Branches und Remote. Markiere einen Arbeitsbereich und wähle **⋮ > Aktivieren**; bei Branches übernimmt **Wechseln** die Auswahl. Ein Wechsel verschiebt keine Dateien und kopiert keine Freigaben. Beende laufende Aufträge und speichere Editoränderungen vorher.

**Geändert** kennzeichnet uncommittete Änderungen einschließlich neuer, nicht ignorierter Dateien. Prüfe bei Bedarf mit **Aktualisieren**; nicht prüfbar bedeutet nicht sauber. Sessionlinks zeigen andere Unterhaltungen am selben Arbeitspfad, reservieren aber keinen Worktree.

Ein Branchwechsel betrifft alle Sessions dieses Arbeitsbereichs. Belegte Branches können nicht nochmals ausgecheckt werden. Bei Blockern wird nichts automatisch committet, gestasht oder verworfen. Den Zielbranch änderst du separat; er ist eine Sessionvorgabe, nicht der aktuell ausgecheckte Branch.

## Remote einrichten und veröffentlichen

Unter **Git > Repository > Einstellungen** wählst du einen vorhandenen Remote oder beauftragst über **Einrichten/Bearbeiten** den Agenten, seine Adresse zu ändern. Verwende HTTPS-/SSH-Adressen ohne Zugangsdaten; richte die Anmeldung auf dem Computer ein.

Eine Remoteänderung betrifft alle Worktrees. Bestehende Sessions müssen das geänderte Ziel über **Ziel übernehmen** bestätigen. Ein nachträglicher Remote setzt den Worktree nicht zurück. Veröffentlichung, Pull Requests und Remote-Synchronisierung benötigen ihn, lokale Arbeit und Review nicht.

Unter **Git > Repository > Aktionen** starten die [Git-Befehle](kanaele.md#coding-befehle) direkt einen Agentenauftrag. **Prüfen & sichern** enthält **Code-Review** und **Commit**, **Übernehmen & veröffentlichen** enthält **Merge in „Zielbranch“** und **Pull Request erstellen**, **Remote** enthält **Push** und **Pull (Fast-forward)**. Die Befehlspalette setzt den Auftrag zunächst ein. Toolrechte gelten unabhängig davon. Ein positives Review ist kein Nachweis der Fehlerfreiheit; es prüft auch auf Overengineering.

Aktiviere bei Bedarf **Vor Merge oder Pull Request committen** direkt unterhalb dieser beiden Aktionen. Das Häkchen ist zunächst aus und wird beim Sessionwechsel zurückgesetzt. Es beauftragt den Agenten, zuerst eindeutig zur Aufgabe gehörende Änderungen im Session-Worktree zu committen. Bei unklarer Auswahl fragt er nach; fremde Änderungen und Secrets dürfen nicht mitcommittet werden. Ohne Häkchen sind uncommittete Änderungen ein Blocker. **Push** und **Pull (Fast-forward)** committen auch mit gesetztem Häkchen nichts. In der Palette oder über Telegram entspricht die Auswahl `/git-merge --commit` beziehungsweise `/git-pr --commit`.

**Pull (Fast-forward)** holt den Remote-Zielbranch ins ursprüngliche Projektverzeichnis. Es übernimmt keine Änderungen aus dem Session-Worktree und stoppt bei lokalen Änderungen oder wenn kein Fast-forward möglich ist.

Ein Commit im eigenen Worktree darf parallel zu einem Lauf im Hauptverzeichnis starten. Lokale Übernahme und Projektaktualisierung benötigen dagegen auch das Hauptverzeichnis; Remoteänderungen betreffen alle Worktrees. Bei einer Überschneidung nennt die Meldung die blockierende Session und ihren Status. Beende oder stoppe den dortigen Lauf und starte den Auftrag erneut. Auch auf Freigabe oder Antwort wartende Läufe können blockieren.

## Worktree entfernen

Archivieren oder Löschen einer Session entfernt keinen Worktree. Entferne ihn ausdrücklich im Arbeitsbereich-Dialog der Git-App. Branch, Commits und Gesprächsverläufe bleiben erhalten; betroffene Sessions wechseln nach Prüfung ins lokale Projektverzeichnis. Laufende Aufträge verhindern die Entfernung. Bei Detached HEAD musst du zuerst einen Branch anlegen und auswählen.

!!! danger "Lokale Dateien werden unwiderruflich gelöscht"
    Beim Entfernen gehen uncommittete Änderungen sowie zusätzliche und ignorierte Dateien wie `.env` verloren. Prüfe und sichere diese vor der Bestätigung. Es gibt keine automatische Worktree-Bereinigung.
