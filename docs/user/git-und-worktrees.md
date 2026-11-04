# Mit Git und Worktrees arbeiten

Git läuft auf dem ausgewählten Computer mit dessen Git-Installation und Anmeldung. Es gibt keine eigene Git-Integration oder Backend-Clonefunktion. Die **Git**-App im Control Center zeigt Änderungen und verwaltet den Arbeitsbereich; Agentenaufträge verwenden die normalen Shell-Rechte.

## Ein Repository verwenden

1. Wähle einen verbundenen Computer und einen zugänglichen Arbeitsordner mit Git-Repository.
2. Bei neuen Projekt-Sessions wird **Git** automatisch zum Control Center hinzugefügt, sobald im gewählten Projekt-Arbeitsordner ein Repository erkannt wird. Die App öffnet sich dabei nicht. Öffne **Control Center > Git > Repository**, um das Repository anzusehen. Unter **Aktionen** bietet **Git-Repository anlegen** ohne vorhandenes Repository einen Agentenauftrag zur Initialisierung; er legt weder Commit noch Remote an.
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

Es findet kein Fetch statt und ein Remote ist nicht nötig. Uncommittete Änderungen bleiben im ursprünglichen Verzeichnis. Laufende Sessions desselben Projekts verhindern das Anlegen nicht. **Lokal** und die Auswahl eines bestehenden Worktrees erstellen keinen Branch.

!!! warning "Worktrees isolieren nicht die ganze Umgebung"
    Mehrere Sessions können denselben Worktree und damit Dateien und Änderungen teilen. Worktrees teilen Git-Metadaten; das ursprüngliche Repository muss erreichbar bleiben. `.env`-Dateien, Abhängigkeiten, Prozesse, Ports und Datenbanken werden nicht automatisch kopiert oder getrennt.

## Arbeitsbereich oder Branch wechseln

Unter **Git > Repository** findest du unterhalb der **Aktionen** die **Einstellungen**. Dort öffnen die Bearbeiten-Symbole die Dialoge für Arbeitsbereich, Branches und Remote. Die Arbeitsbereich- und Branchlisten kannst du auch während eines Auftrags durchsuchen und schließen. Markiere einen Arbeitsbereich und wähle **⋮ > Aktivieren**; bei Branches übernimmt **Wechseln** die Auswahl. Ein Wechsel verschiebt keine Dateien und kopiert keine Freigaben. Beende laufende Aufträge vor dem Wechsel. Speichere vor einem Branchwechsel die Editoränderungen im betroffenen Arbeitsbereich; ein neuer Branch ohne Checkout benötigt das nicht. Vor dem Wechsel des Session-Arbeitsbereichs müssen weiterhin alle Editorentwürfe gesichert sein.

**Geändert** kennzeichnet uncommittete Änderungen einschließlich neuer, nicht ignorierter Dateien. Prüfe bei Bedarf mit **Aktualisieren**; nicht prüfbar bedeutet nicht sauber. Sessionlinks zeigen andere Unterhaltungen am selben Arbeitspfad, reservieren aber keinen Worktree.

Ein Branchwechsel betrifft alle Sessions dieses Arbeitsbereichs. Belegte Branches können nicht nochmals ausgecheckt werden. Bei Blockern wird nichts automatisch committet, gestasht oder verworfen. Den Zielbranch änderst du separat; er ist eine Sessionvorgabe, nicht der aktuell ausgecheckte Branch.

## Remote einrichten und veröffentlichen

Unter **Git > Repository > Einstellungen** wählst du einen vorhandenen Remote oder beauftragst über **Einrichten/Bearbeiten** den Agenten, seine Adresse zu ändern. Verwende HTTPS-/SSH-Adressen ohne Zugangsdaten; richte die Anmeldung auf dem Computer ein.

Eine Remoteänderung betrifft alle Worktrees. Bestehende Sessions müssen das geänderte Ziel über **Ziel übernehmen** bestätigen. Ein nachträglicher Remote setzt den Worktree nicht zurück. Veröffentlichung, Pull Requests und Remote-Synchronisierung benötigen ihn, lokale Arbeit und Review nicht.

Unter **Git > Repository > Aktionen** starten die [Git-Befehle](kanaele.md#coding-befehle) direkt einen Agentenauftrag. **Prüfen & sichern** enthält **Code-Review** und **Commit**, **Übernehmen & veröffentlichen** enthält **Merge in „Zielbranch“** und **Pull Request erstellen**, **Remote** enthält **Push** und **Pull (Fast-forward)**. Die Befehlspalette setzt den Auftrag zunächst ein. Toolrechte gelten unabhängig davon. Ein positives Review ist kein Nachweis der Fehlerfreiheit; es prüft auch auf Overengineering.

Aktiviere bei Bedarf **Vor Merge oder Pull Request committen** direkt unterhalb dieser beiden Aktionen. Das Häkchen ist zunächst aus und wird beim Sessionwechsel zurückgesetzt. Es beauftragt den Agenten, zuerst eindeutig zur Aufgabe gehörende Änderungen im Session-Worktree zu committen. Bei unklarer Auswahl fragt er nach; fremde Änderungen und Secrets dürfen nicht mitcommittet werden. Ohne Häkchen sind uncommittete Änderungen ein Blocker. **Push** und **Pull (Fast-forward)** committen auch mit gesetztem Häkchen nichts. In der Palette oder über Telegram entspricht die Auswahl `/git-merge --commit` beziehungsweise `/git-pr --commit`.

Die Rückmeldung beginnt mit Aktion und Ergebnis. Darunter nennt der Agent nur relevante
Details wie Commit, Zielbranch, PR-Link, ausgeführte Prüfungen oder offene Punkte.
Reviews trennen notwendige Korrekturen von optionalen Verbesserungen und nummerieren
Befunde, damit du gezielt darauf verweisen kannst. Die Antwort folgt deiner
[Sprachvorgabe](webrecherche-und-quellen.md#webseiten-abrufen); Git-Bezeichner und
zitierte Commit-Titel bleiben unverändert. Bei Fehlern muss die Antwort zwischen bereits
abgeschlossenen Schritten und dem noch offenen Teil unterscheiden.

### Nach einer Umsetzung automatisch abschließen

1. Aktiviere in deiner Worktree-Session unter **Git > Repository > Aktionen** **Nach Umsetzung abschließen**.
2. Wähle **Build** und sende deinen Umsetzungsauftrag. Der Schalter gilt einmalig für die nächste normale Build-Nachricht in diesem Arbeitsbereich; Plan-Nachrichten und einzelne Git-Aktionen verbrauchen ihn nicht. Bei ungespeicherten Editoränderungen kannst du im Dialog **Ohne Autoabschluss senden** wählen; für diese Nachricht wird kein Abschluss vorgemerkt und der Schalter wird ausgeschaltet.
3. MatoLoop merkt direkt dahinter genau einen **Automatischer Folgeauftrag** vor: `/git-finish` (**Review, Korrektur & Merge**). Nach dem Umsetzungsauftrag prüft dieser normale Agentenlauf die Änderungen, behebt konkrete Befunde und prüft erneut. Seine Anweisungen erlauben Commit und lokalen Merge in den angezeigten Zielbranch erst ohne verbleibende Befunde, fehlgeschlagene Checks oder fehlende Entscheidungen. Fragen und reine Analysen berechtigen nicht dazu. Die Einzelaktion **Code-Review** bleibt schreibgeschützt.

Du kannst `/git-finish` auch selbst über die Befehlsauswahl oder **Review, Korrektur & Merge** starten. Die Git-Aktionsbuttons reihen normale Befehlsnachrichten ein, auch während einer laufenden Antwort. Dein Entwurf und seine Anhänge bleiben dabei erhalten. Fehlende Voraussetzungen werden bei der jeweiligen Aktion erklärt; Berechtigungen und Prüfungen des Git-Zustands gelten weiterhin.

Der Folgeauftrag verwendet die [gespeicherte Nachrichtenqueue](chat-und-sessions.md#wahrend-einer-antwort-eingreifen), keinen besonderen Git-Schrittautomaten. Scheitert der Umsetzungsauftrag oder brichst du ihn ab, bleibt der Abschluss angehalten. Prüfe den tatsächlichen Arbeitsstand, bevor du ihn entfernst oder neu beauftragst; bloßes Fortsetzen umgeht diese Prüfung nicht. Änderungen an Computer, Arbeitsbereich, Branch oder Ziel werden vor dem Start erneut geprüft. Entfernst du einen noch vorgemerkten Umsetzungsauftrag, entfernt MatoLoop auch seinen vorgemerkten Abschluss.

Klare Codekonflikte und Integrationsfehler behebt der Agent im eigenen Worktree und prüft erneut. Sind die beabsichtigten Änderungen nicht eindeutig vereinbar, erklärt er kurz das Problem und fragt mit Optionen und Empfehlung nach. Relevante Sessions desselben Projekts darf er dafür gezielt lesen; gespeicherte Memories und projektfremde Unterhaltungen bleiben an einen ausdrücklichen Abrufauftrag gebunden. Ungespeicherte Editoränderungen musst du vorher sichern. Es gibt keinen automatischen Push, Pull Request oder Worktree-Abbau. Die Prüfungen sind Agentenarbeit, kein unabhängiger Nachweis der Fehlerfreiheit.

Zeigen die Tools laufende Arbeit oder eine vorübergehende Git-Sperre, soll der Agent warten, später erneut prüfen und selbstständig fortfahren. Geänderte Dateien allein belegen keine laufende Arbeit. Ist der Zielbranch inzwischen weitergelaufen, arbeitet der Agent den neuen Stand im eigenen Worktree ein und prüft erneut, bevor er das ursprüngliche Projektverzeichnis per Fast-forward aktualisiert. Nur notwendige Entscheidungen oder Eingriffe führen zu einer Rückfrage; mit dem normalen Stoppknopf brichst du ab. Es gibt keine zusätzliche Merge-Warteschlange oder garantierte Sperre gegen externe Schreibzugriffe. Nach einem Backend-Neustart werden unterbrochene Läufe als fehlgeschlagen gemeldet; prüfe vor einem neuen Versuch den tatsächlichen Git-Zustand.

**Pull (Fast-forward)** holt den Remote-Zielbranch ins ursprüngliche Projektverzeichnis. Es übernimmt keine Änderungen aus dem Session-Worktree und stoppt bei lokalen Änderungen oder wenn kein Fast-forward möglich ist.

Sessions reservieren weder Arbeitsbereiche noch gemeinsame Git-Metadaten. Eine andere laufende oder wartende Session blockiert deshalb keine neuen Chats oder Git-Aufträge. Der Agent muss konkrete Konflikte anhand des aktuellen Zustands erkennen und behandeln. Parallele Änderungen im selben Arbeitsbereich können sich trotzdem beeinflussen; nutze für unabhängige Aufgaben getrennte Worktrees.

## Worktree entfernen

Entferne einen Worktree im Arbeitsbereich-Dialog der Git-App oder aktiviere beim Löschen einer Projekt-Session **Worktree ebenfalls entfernen**. Die Option ist zunächst aus und erscheint nur, wenn der Worktree auf dem verbundenen Computer innerhalb von drei Sekunden geprüft werden kann. Ohne diese Auswahl und beim Archivieren bleibt er erhalten. Scheitert die Entfernung, wird die Session nicht gelöscht.

Branch und Commits bleiben erhalten; andere betroffene Sessions wechseln nach Prüfung ins lokale Projektverzeichnis. Der Löschdialog zeigt diese Sessions an. Hat sich die Zuordnung vor dem Löschen geändert, bricht die Entfernung ab; öffne den Dialog dann erneut. Aktive Aufträge mit überlappendem Arbeitspfad und gesperrte Worktrees verhindern die Entfernung; Aufträge in anderen Worktrees desselben Repositorys nicht. Auch eingereihte oder auf Freigabe beziehungsweise Antwort wartende Aufträge gelten als aktiv. Bei Detached HEAD musst du zuerst einen Branch anlegen und auswählen.

!!! danger "Lokale Dateien werden unwiderruflich gelöscht"
    Beim Entfernen gehen uncommittete Änderungen sowie zusätzliche und ignorierte Dateien wie `.env` verloren. Prüfe und sichere diese vor der Bestätigung. Es gibt keinen automatischen Commit oder Stash. Auch ein späterer Fehler beim Löschen der Session stellt den entfernten Worktree nicht wieder her. Es gibt keine automatische Worktree-Bereinigung.
