# Kanäle

Kanäle verbinden persönliche Messenger mit MatoLoop. Sie sind keine Integrationen und stellen dem Agenten keine zusätzlichen Tools oder Zugriffsrechte bereit. Jeder Benutzer verwaltet ausschließlich die eigenen Kanalinstanzen und Zugangsdaten unter **Einstellungen > Kanäle**.

## Telegram verbinden

Erstelle mit BotFather einen persönlichen Telegram-Bot. Nach globaler Kanalfreigabe öffne **Einstellungen > Kanäle > Kanal hinzufügen > Telegram**. MatoLoop speichert den Bot-Token verschlüsselt und zeigt ihn nicht erneut. Folge dem Telegram-Link oder sende `/start PAIRING_CODE` im privaten Botchat. Gruppen und Telegram-Kanäle werden nicht verarbeitet.

Nach der Kopplung sendest du Text, Bilder, Dateien, Audio, Video oder Sprachnachrichten. Aufnahmen verwenden dieselbe Verarbeitung wie Webaufnahmen: Originalaudio bleibt gespeichert und abspielbar, ein verfügbares Standard-Transkriptionsmodell ergänzt Text. Ein Fehler entfernt die Aufnahme nicht. Gewöhnliche Audiodateien werden ohne automatische Transkription gespeichert.

Während der Agent arbeitet, zeigt Telegram parallel den Schreibstatus und die aktuelle Aktivität, Reasoning-Zusammenfassung oder entstehende, formatierte Antwort als temporären Entwurf. Die Aktivität verwendet dieselben Bezeichnungen wie die MatoLoop-Oberfläche und ein passendes Symbol, zum Beispiel **📁 Datei lesen**. Vor dem ersten Reasoning erscheint **⚙️ Verarbeite Anfrage**. Reasoning erscheint als **🧠 Nachdenken**, ergänzt um eine tatsächlich veröffentlichte Zusammenfassung. Die letzte Tool-Aktivität bleibt bis zur nächsten sichtbaren Aktivität oder Antwort stehen. Tool-Argumente und -Ergebnisse erscheinen dort nicht. Wartet der Agent auf eine Freigabe oder Antwort, pausiert der Schreibstatus.

Freigaben einer Telegram-Anfrage erscheinen mit verfügbaren Entscheidungsknöpfen im privaten Chat. MatoLoop prüft Eigentümer, Ausgangsnachricht, Session, Lauf und offene Anfrage erneut. Shell-Befehle verwenden normale Freigaben für isolierte Agentenprozesse. Skillentwürfe brauchen weiterhin genaue Dateirevisionsprüfung in MatoLoop; Telegram kann sie nur ablehnen. Geschützte Credential-Rückfragen beantwortest du in MatoLoop, nicht als gewöhnliche Messenger-Antwort.

Strukturierte Agent-Rückfragen erscheinen ebenfalls direkt im privaten Chat. Wähle Antworten
über die Schaltflächen aus und wechsle mit **Zurück** oder **Weiter** zwischen mehreren Fragen.
Eigene Antworten und Freitext gibst du in das dafür eingeblendete Antwortfeld ein. Alternativ
kannst du Telegrams Funktion **Antworten** an der Fragen-Nachricht verwenden. Das Absenden einer
Texteingabe wechselt direkt zur nächsten Frage. Mit
**Antworten senden** schließt du die Rückfrage ab; **Ablehnen** beendet sie ohne Antworten.

Die endgültige Antwort wird als normale Nachricht zugestellt und zusammen mit der Anfrage vollständig in der zugehörigen MatoLoop-Session gespeichert. Telegram stellt Überschriften, Hervorhebungen, Listen, Links, Zitate und Code passend dar; komplexere Inhalte wie Tabellen, Mathematik oder Mermaid-Diagramme erscheinen in einer lesbaren Text- oder Codeform. Mit `/new` beginnst du eine neue Session, `/sessions` zeigt zuletzt verwendete Sessions und `/use <ID>` wechselt die Session. `/current`, `/stop`, `/unlink` und `/help` stehen ebenfalls zur Verfügung.

## Coding-Befehle

Das Telegram-Befehlsmenü und `/help` verwenden denselben allgemeinen Befehlskatalog
wie die Palette am MatoLoop-Eingabefeld. Die **Git**-App zeigt dessen Git-Aktionen:

In MatoLoop heißen die Befehle beispielsweise `/git-review`. Telegram benötigt
Unterstriche und zeigt deshalb `/git_review` im Menü und in `/help`; MatoLoop
ordnet diese Schreibweise automatisch demselben Befehl zu. In Telegram stehen
folgende Befehle zur Verfügung:

- `/git_init`: Git im gewählten Workspace anlegen, ohne Commit oder Veröffentlichung.
- `/git_review`: Änderungen einschließlich noch nicht übernommener Commits prüfen.
- `/git_commit`: Ausgewählte Arbeitsänderungen ausdrücklich committen, ohne Push.
- `/git_merge`: Aufgabenänderungen bei Bedarf committen, eigene unveröffentlichte Commits auf den lokalen Zielbranch
  rebasen, reviewen, prüfen und das ursprüngliche Checkout per Fast-forward
  aktualisieren. Kein Fetch oder Push; ein Remote ist nicht erforderlich.
- `/git_push`: Vorhandene Commits veröffentlichen, ohne Commit oder Merge; bei bereits integriertem Worktree den aktuellen Zielbranch.
- `/git_pr`: Aufgabenänderungen bei Bedarf committen, den Remote-Zielstand integrieren, prüfen und ohne lokalen Branchwechsel
  veröffentlichen und einen Pull Request erstellen
  beziehungsweise den vorhandenen aktualisieren.
- `/git_sync`: Den ursprünglichen Projekt-Checkout aktualisieren, wenn er sauber,
  auf dem Zielbranch und ohne zusätzliche Zusammenführung aktualisierbar ist.
- `/git_remote [Name] [Adresse]`: Einen Remote über den Agenten einrichten oder
  seine Adresse ändern. Fehlende Angaben fragt der Agent nach. Zugangsdaten gehören
  in die Anmeldung auf dem Computer, nicht in die Adresse. Ein geändertes Ziel muss
  in bestehenden Sessions über die Git-App ausdrücklich übernommen werden.

Wähle vorher mit `/sessions` und `/use <ID>` die gewünschte Coding-Session.
Die Befehle starten normale Agentenläufe auf dem Computer dieser Session; die
vorhandenen Tool-Rechte, Rückfragen und Freigaben gelten weiter. Merge und PR
benötigen einen ausgewählten Projekt-Worktree. Beide dürfen eindeutig zugehörige Aufgabenänderungen
vorher committen und beachten Repository-Konventionen sowie ausdrückliche Einschränkungen wie
„nur vorhandene Commits übernehmen“. Bei unklarer Auswahl fragt der Agent nach. Merge kann klare
Integrationskonflikte und -fehler beheben; PR stoppt bei Konflikten, notwendigen Review-Befunden
oder fehlgeschlagenen Checks, statt sie zu reparieren. Für PRs müssen ein
geeignetes Werkzeug und die Anmeldung beim Git-Hoster auf dem Computer vorhanden
sein. Ein laufender Auftrag wird durch diese Befehle nicht unterbrochen.

## Administrative Freigabe

Kommunikationskanäle sind global zunächst deaktiviert, auch für Administratoren. Aktiviere **Kommunikationskanäle aktivieren** unter **Administration > Kanäle > Einstellungen**. **Verwaltung** enthält die Anbieterfreigaben; Telegram ist standardmäßig erlaubt, braucht aber weiterhin die globale Aktivierung.

Deaktivierung erhält Verbindungen, verschlüsselte Zugangsdaten und Anbieterfreigaben. Empfang, Zustellung, Kopplung und Tests sind dann gesperrt. Offene externe Zustellungen werden beendet und beim Reaktivieren nicht nachgeholt. Automationsziele fallen auf das gespeicherte App-Ergebnis zurück; Aufträge und Ergebnisse bleiben in ihrer normalen Session verfügbar.
