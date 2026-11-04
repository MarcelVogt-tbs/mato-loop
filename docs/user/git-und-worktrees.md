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

Die Git-Befehle bleiben auch ohne Remote, passenden Worktree oder verbundenen Computer
auswählbar. Der Agent prüft die Git-Voraussetzungen bei der Bearbeitung und erklärt fehlende
Voraussetzungen oder fragt nach. Für Abschluss, Merge und Pull Request brauchst du einen
separaten Worktree mit einem anderen Arbeitsbranch als dem Zielbranch. Fehlt er, fordert dich
der Agent zur Auswahl in der Git-App auf; er wechselt den Arbeitsbereich nicht eigenmächtig.
Ist ein ausgewählter Computer nicht erreichbar, kann die normale Nachrichtenqueue den Versand
anhalten. Stelle die Verbindung wieder her und setze den Versand fort. Schreibgeschützte
Sessions und die Prüfungen für Zugriff, Arbeitsbereich und geänderte Publikationsziele bleiben geschützt.

Aktiviere bei Bedarf **Vor Merge oder Pull Request committen** direkt unterhalb dieser beiden Aktionen. Das Häkchen ist zunächst aus und wird beim Sessionwechsel zurückgesetzt. Es beauftragt den Agenten, zuerst eindeutig zur Aufgabe gehörende Änderungen im Session-Worktree zu committen. Bei unklarer Auswahl fragt er nach; fremde Änderungen und Secrets dürfen nicht mitcommittet werden. Ohne Häkchen sind uncommittete Änderungen ein Blocker. **Push** und **Pull (Fast-forward)** committen auch mit gesetztem Häkchen nichts. In der Palette oder über Telegram entspricht die Auswahl `/git-merge --commit` beziehungsweise `/git-pr --commit`.

Die Rückmeldung beginnt mit Aktion und Ergebnis. Darunter nennt der Agent nur relevante
Details wie Commit, Zielbranch, PR-Link, ausgeführte Prüfungen oder offene Punkte.
Zu jedem als Ergebnis genannten Commit zeigt er neben dem Hash die vollständige gespeicherte
Commit-Nachricht aus Git: Titel, gegebenenfalls Beschreibung und Trailer wie `Release-Note`.
Der Text bleibt unverändert, auch wenn eine spätere Integration blockiert ist.
Reviews trennen notwendige Korrekturen von optionalen Verbesserungen und nummerieren
Befunde, damit du gezielt darauf verweisen kannst. Die Antwort folgt deiner
[Sprachvorgabe](webrecherche-und-quellen.md#webseiten-abrufen); Git-Bezeichner und
zitierte Commit-Titel bleiben unverändert. Bei Fehlern muss die Antwort zwischen bereits
abgeschlossenen Schritten und dem noch offenen Teil unterscheiden.

Code-Review, Autoabschluss und die Prüfungen bei Merge oder Pull Request folgen denselben
Kriterien: Fehler, Sicherheit und Datenerhalt, parallele Nutzung und erwartete Last,
fehlende Tests für riskante Logik, relevante Geschwindigkeitsprobleme und unnötige Komplexität.
Ein Befund braucht einen konkreten Nachteil und die kleinste vollständige Korrektur.
Vorhandene Lösungen wiederverwenden und unnötigen Code entfernen geht vor neuen Abstraktionen;
notwendige Schutzprüfungen bleiben erhalten. Optionale Vereinfachungen blockieren keinen Merge
und erlauben keine zusätzlichen Umbauten. Das einzelne **Code-Review** ändert weiterhin nichts.

### Nach einer Umsetzung automatisch abschließen

1. Sende deinen Umsetzungsauftrag in **Build**.
2. Aktiviere in deiner Worktree-Session unter **Git > Repository > Aktionen** **Nach Umsetzung abschließen**. Der Haken plant sofort die normale Nachricht `/git-finish` ein, genau wie eine manuelle Eingabe. Ohne laufende Antwort kann der Auftrag sofort starten.
3. Entferne den Haken, wenn du die noch nicht gesendete Nachricht wieder entfernen möchtest. Dafür muss der Computer nicht verbunden sein. Auch das normale Entfernen der Nachricht im Chat entfernt den Haken. Nach dem Senden zeigt der Chat den tatsächlich übermittelten Prompt statt des Kurzbefehls.

Der Auftrag prüft die Änderungen, behebt notwendige Befunde innerhalb der Aufgabe und prüft erneut. Seine Anweisungen erlauben Commit und lokalen Merge in den angezeigten Zielbranch erst ohne verbleibende notwendige Korrekturen, fehlgeschlagene Checks oder fehlende Entscheidungen. Fragen und reine Analysen berechtigen nicht dazu. Die Einzelaktion **Code-Review** bleibt schreibgeschützt.

Du kannst `/git-finish` auch selbst über die Befehlsauswahl oder **Review, Korrektur & Merge** starten. Die Git-Aktionsbuttons reihen normale Befehlsnachrichten ein, auch während einer laufenden Antwort. Dein Entwurf und seine Anhänge bleiben dabei erhalten. Fehlende Voraussetzungen werden bei der jeweiligen Aktion erklärt; Berechtigungen und Prüfungen des Git-Zustands gelten weiterhin.

Der Haken verwendet die [gespeicherte Nachrichtenqueue](chat-und-sessions.md#wahrend-einer-antwort-eingreifen), keinen besonderen Git-Schrittautomaten und keine Vormerkung für eine spätere Build-Nachricht. Auch nach einem abgebrochenen oder fehlgeschlagenen Auftrag kann die normale Queue den Abschluss starten. Entferne die vorgemerkte Nachricht, wenn du das nicht möchtest. Änderungen an Computer, Arbeitsbereich, Branch oder Ziel werden vor dem Start erneut geprüft.

Klare Codekonflikte und Integrationsfehler behebt der Agent im eigenen Worktree und prüft erneut. Sind die beabsichtigten Änderungen nicht eindeutig vereinbar, erklärt er kurz das Problem und fragt mit Optionen und Empfehlung nach. Relevante Sessions desselben Projekts darf er dafür gezielt lesen; gespeicherte Memories und projektfremde Unterhaltungen bleiben an einen ausdrücklichen Abrufauftrag gebunden. Ungespeicherte Editoränderungen musst du vorher sichern. Es gibt keinen automatischen Push, Pull Request oder Worktree-Abbau. Die Prüfungen sind Agentenarbeit, kein unabhängiger Nachweis der Fehlerfreiheit.

Der Agent kann den committeten Zielbranch in seinen sauberen Worktree einarbeiten und dort prüfen,
während im ursprünglichen Verzeichnis noch uncommittete Änderungen liegen oder ein anderer Branch
ausgecheckt ist. Dieses Verzeichnis bleibt dabei unverändert. Erst direkt vor der Rückintegration
müssen beide Arbeitsbäume sauber und das ursprüngliche Verzeichnis auf dem Zielbranch sein.
Ist der Zielbranch inzwischen weitergelaufen, übernimmt und prüft der Agent die neuen Commits erneut.

Vor einer Rückfrage wegen des ursprünglichen Verzeichnisses prüft der Agent aktuelle Tool-Hinweise
auf laufende Arbeit oder vorübergehende Git-Sperren. Sind diese bestätigt, soll er warten, später
erneut prüfen und selbstständig fortfahren; unabhängige erlaubte Schritte erledigt er inzwischen.
Geänderte Dateien allein belegen keine laufende Arbeit. Fremde Änderungen werden nicht automatisch
committet, gestasht, zurückgesetzt oder verworfen. Nur notwendige Entscheidungen oder Eingriffe
führen zu einer Rückfrage; mit dem normalen Stoppknopf brichst du ab. Es gibt keine zusätzliche
Merge-Warteschlange, automatische Erkennung externer Agenten oder garantierte Sperre gegen externe
Schreibzugriffe. Nach einem Backend-Neustart werden unterbrochene Läufe als fehlgeschlagen gemeldet;
prüfe vor einem neuen Versuch den tatsächlichen Git-Zustand.

**Pull (Fast-forward)** holt den Remote-Zielbranch ins ursprüngliche Projektverzeichnis. Es übernimmt keine Änderungen aus dem Session-Worktree und stoppt bei lokalen Änderungen oder wenn kein Fast-forward möglich ist.

Sessions reservieren weder Arbeitsbereiche noch gemeinsame Git-Metadaten. Eine andere laufende oder wartende Session blockiert deshalb keine neuen Chats oder Git-Aufträge. Der Agent muss konkrete Konflikte anhand des aktuellen Zustands erkennen und behandeln. Parallele Änderungen im selben Arbeitsbereich können sich trotzdem beeinflussen; nutze für unabhängige Aufgaben getrennte Worktrees.

## Worktree entfernen

Entferne einen Worktree im Arbeitsbereich-Dialog der Git-App oder aktiviere beim Löschen einer Projekt-Session **Worktree ebenfalls entfernen**. Die Option ist zunächst aus und erscheint nur, wenn der Worktree auf dem verbundenen Computer innerhalb von drei Sekunden geprüft werden kann. Ohne diese Auswahl und beim Archivieren bleibt er erhalten. Scheitert die Entfernung, wird die Session nicht gelöscht.

Branch und Commits bleiben erhalten; andere betroffene Sessions wechseln nach Prüfung ins lokale Projektverzeichnis. Der Löschdialog zeigt diese Sessions an. Hat sich die Zuordnung vor dem Löschen geändert, bricht die Entfernung ab; öffne den Dialog dann erneut. Aktive Aufträge mit überlappendem Arbeitspfad und gesperrte Worktrees verhindern die Entfernung; Aufträge in anderen Worktrees desselben Repositorys nicht. Auch eingereihte oder auf Freigabe beziehungsweise Antwort wartende Aufträge gelten als aktiv. Bei Detached HEAD musst du zuerst einen Branch anlegen und auswählen.

!!! danger "Lokale Dateien werden unwiderruflich gelöscht"
    Beim Entfernen gehen uncommittete Änderungen sowie zusätzliche und ignorierte Dateien wie `.env` verloren. Prüfe und sichere diese vor der Bestätigung. Es gibt keinen automatischen Commit oder Stash. Auch ein späterer Fehler beim Löschen der Session stellt den entfernten Worktree nicht wieder her. Es gibt keine automatische Worktree-Bereinigung.
