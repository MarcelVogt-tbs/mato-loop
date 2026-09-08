# Kanäle

Kanäle verbinden persönliche Messenger mit MatoLoop. Sie sind keine Integrationen und stellen dem Agenten keine zusätzlichen Tools oder Zugriffsrechte bereit. Jeder Benutzer verwaltet ausschließlich die eigenen Kanalinstanzen und Zugangsdaten unter **Einstellungen > Kanäle**.

## Telegram verbinden

Erstellen Sie mit BotFather einen persönlichen Telegram-Bot. Öffnen Sie unter **Einstellungen > Kanäle** den Tab **Kanalinstanzen**, wählen Sie **Kanal hinzufügen** und anschließend den Anbieter **Telegram**. MatoLoop speichert den eingegebenen Bot-Token verschlüsselt und zeigt ihn später nicht wieder an. Öffnen Sie anschließend den angezeigten Telegram-Link oder senden Sie `/start KOPPLUNGSCODE` in einem privaten Chat an den Bot. Gruppen- und Kanalnachrichten werden nicht verarbeitet.

After pairing, you can send text, images, files, audio, video, and voice messages to the bot. Voice messages follow the same processing as web recordings: the original audio is stored as a message attachment and displayed with a player in MatoLoop. Automatic transcription adds recognized text to your request without replacing the recording. If transcription fails or is unavailable, the audio remains accessible. The model can access the stored attachment later through the normal attachment tools. Ordinary audio files are stored without automatic transcription.

Während der Agent arbeitet, zeigt Telegram zunächst den Schreibstatus und anschließend die aktuelle Reasoning-Zusammenfassung, kurze Tool-Aktivität und entstehende, formatierte Antwort als temporären Entwurf. Telegram animiert die Punkte am Ende laufender Statusmeldungen nativ. Die Aktivität verwendet dieselben Bezeichnungen wie die MatoLoop-Oberfläche, zum Beispiel **Datei lesen**. Vor dem ersten Reasoning erscheint **Verarbeite Anfrage**, nach Tool-Aufrufen **Verarbeite Tool-Ergebnisse**. Reasoning ohne veröffentlichte Zusammenfassung erscheint als **Nachdenken**; sobald eine Zusammenfassung vorliegt, hat deren Überschrift Vorrang. Tool-Argumente und -Ergebnisse erscheinen dort nicht. Wartet der Agent auf eine Freigabe oder Antwort, bleibt dieser Zustand ohne Animation sichtbar.

Agent approvals originating from a Telegram request appear with their available decision buttons directly in the private chat, including shared-terminal shell approvals. MatoLoop revalidates the paired owner, original message, session, run, and pending approval before accepting a decision. If the terminal draft has changed since the delivered review, review it again in MatoLoop; an older channel decision cannot authorize edited text. Skill build or edit requests still require exact file-revision review and approval in MatoLoop; Telegram can deny them.

Strukturierte Agent-Rückfragen erscheinen ebenfalls direkt im privaten Chat. Wählen Sie Antworten
über die Schaltflächen aus und wechseln Sie mit **Zurück** oder **Weiter** zwischen mehreren Fragen.
Eigene Antworten und Freitext geben Sie in das dafür eingeblendete Antwortfeld ein. Alternativ
können Sie Telegrams Funktion **Antworten** an der Fragen-Nachricht verwenden. Das Absenden einer
Texteingabe wechselt direkt zur nächsten Frage. Mit
**Antworten senden** schließen Sie die Rückfrage ab; **Ablehnen** beendet sie ohne Antworten.

Die endgültige Antwort wird als normale Nachricht zugestellt und zusammen mit der Anfrage vollständig in der zugehörigen MatoLoop-Session gespeichert. Telegram stellt Überschriften, Hervorhebungen, Listen, Links, Zitate und Code passend dar; komplexere Inhalte wie Tabellen, Mathematik oder Mermaid-Diagramme erscheinen in einer lesbaren Text- oder Codeform. Mit `/new` beginnen Sie eine neue Session, `/sessions` zeigt zuletzt verwendete Sessions und `/use <ID>` wechselt die Session. `/current`, `/stop`, `/unlink` und `/help` stehen ebenfalls zur Verfügung.

## Administrative Freigabe

Telegram ist bei einer neuen Installation zunächst erlaubt. Administratoren können den Anbieter unter **Einstellungen > Kanäle > Verfügbare Kanäle** global sperren oder erneut erlauben. Während einer Sperrung bleiben persönliche Telegram-Instanzen und ihre Automationseinstellungen gespeichert, werden aber ausgeblendet und nicht verwendet. Automationsläufe fallen in dieser Zeit auf die MatoLoop-App zurück; bereits wartende Telegram-Zustellungen werden nicht später nachgesendet.
