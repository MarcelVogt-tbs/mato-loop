# Kanäle

Kanäle verbinden persönliche Messenger mit MatoLoop. Sie sind keine Integrationen und stellen dem Agenten keine zusätzlichen Tools oder Zugriffsrechte bereit. Jeder Benutzer verwaltet ausschließlich die eigenen Kanalinstanzen und Zugangsdaten unter **Einstellungen > Kanäle**.

## Telegram verbinden

Erstellen Sie mit BotFather einen persönlichen Telegram-Bot. Nach globaler Kanalfreigabe öffnen Sie **Einstellungen > Kanäle > Kanal hinzufügen > Telegram**. MatoLoop speichert den Bot-Token verschlüsselt und zeigt ihn nicht erneut. Folgen Sie dem Telegram-Link oder senden Sie `/start PAIRING_CODE` im privaten Botchat. Gruppen und Telegram-Kanäle werden nicht verarbeitet.

Nach der Kopplung senden Sie Text, Bilder, Dateien, Audio, Video oder Sprachnachrichten. Aufnahmen verwenden dieselbe Verarbeitung wie Webaufnahmen: Originalaudio bleibt gespeichert und abspielbar, ein verfügbares Standard-Transkriptionsmodell ergänzt Text. Ein Fehler entfernt die Aufnahme nicht. Gewöhnliche Audiodateien werden ohne automatische Transkription gespeichert.

Während der Agent arbeitet, zeigt Telegram parallel den Schreibstatus und die aktuelle Aktivität, Reasoning-Zusammenfassung oder entstehende, formatierte Antwort als temporären Entwurf. Die Aktivität verwendet dieselben Bezeichnungen wie die MatoLoop-Oberfläche und ein passendes Symbol, zum Beispiel **📁 Datei lesen**. Vor dem ersten Reasoning erscheint **⚙️ Verarbeite Anfrage**. Reasoning erscheint als **🧠 Nachdenken**, ergänzt um eine tatsächlich veröffentlichte Zusammenfassung. Die letzte Tool-Aktivität bleibt bis zur nächsten sichtbaren Aktivität oder Antwort stehen. Tool-Argumente und -Ergebnisse erscheinen dort nicht. Wartet der Agent auf eine Freigabe oder Antwort, pausiert der Schreibstatus.

Freigaben einer Telegram-Anfrage erscheinen mit verfügbaren Entscheidungsknöpfen im privaten Chat. MatoLoop prüft Eigentümer, Ausgangsnachricht, Session, Lauf und offene Anfrage erneut. Shell-Befehle verwenden normale Freigaben für isolierte Agentenprozesse. Skillentwürfe brauchen weiterhin genaue Dateirevisionsprüfung in MatoLoop; Telegram kann sie nur ablehnen. Geschützte Credential-Rückfragen beantworten Sie in MatoLoop, nicht als gewöhnliche Messenger-Antwort.

Strukturierte Agent-Rückfragen erscheinen ebenfalls direkt im privaten Chat. Wählen Sie Antworten
über die Schaltflächen aus und wechseln Sie mit **Zurück** oder **Weiter** zwischen mehreren Fragen.
Eigene Antworten und Freitext geben Sie in das dafür eingeblendete Antwortfeld ein. Alternativ
können Sie Telegrams Funktion **Antworten** an der Fragen-Nachricht verwenden. Das Absenden einer
Texteingabe wechselt direkt zur nächsten Frage. Mit
**Antworten senden** schließen Sie die Rückfrage ab; **Ablehnen** beendet sie ohne Antworten.

Die endgültige Antwort wird als normale Nachricht zugestellt und zusammen mit der Anfrage vollständig in der zugehörigen MatoLoop-Session gespeichert. Telegram stellt Überschriften, Hervorhebungen, Listen, Links, Zitate und Code passend dar; komplexere Inhalte wie Tabellen, Mathematik oder Mermaid-Diagramme erscheinen in einer lesbaren Text- oder Codeform. Mit `/new` beginnen Sie eine neue Session, `/sessions` zeigt zuletzt verwendete Sessions und `/use <ID>` wechselt die Session. `/current`, `/stop`, `/unlink` und `/help` stehen ebenfalls zur Verfügung.

## Administrative Freigabe

Kommunikationskanäle sind global zunächst deaktiviert, auch für Administratoren. Aktivieren Sie **Kommunikationskanäle aktivieren** unter **Administration > Kanäle > Einstellungen**. **Verwaltung** enthält die Anbieterfreigaben; Telegram ist standardmäßig erlaubt, braucht aber weiterhin die globale Aktivierung.

Deaktivierung erhält Verbindungen, verschlüsselte Zugangsdaten und Anbieterfreigaben. Empfang, Zustellung, Kopplung und Tests sind dann gesperrt. Offene externe Zustellungen werden beendet und beim Reaktivieren nicht nachgeholt. Automationsziele fallen auf das gespeicherte App-Ergebnis zurück; Aufträge und Ergebnisse bleiben in ihrer normalen Session verfügbar.
