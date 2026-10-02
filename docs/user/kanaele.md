# Kanäle

Kanäle verbinden persönliche Messenger mit MatoLoop. Sie sind keine Integrationen und stellen dem Agenten keine zusätzlichen Tools oder Zugriffsrechte bereit. Jeder Benutzer verwaltet ausschließlich die eigenen Kanalinstanzen und Zugangsdaten unter **Einstellungen > Kanäle**.

## Telegram verbinden

Erstellen Sie mit BotFather einen persönlichen Telegram-Bot. Öffnen Sie unter **Einstellungen > Kanäle** den Tab **Kanalinstanzen**, wählen Sie **Kanal hinzufügen** und anschließend den Anbieter **Telegram**. MatoLoop speichert den eingegebenen Bot-Token verschlüsselt und zeigt ihn später nicht wieder an. Öffnen Sie anschließend den angezeigten Telegram-Link oder senden Sie `/start KOPPLUNGSCODE` in einem privaten Chat an den Bot. Gruppen- und Kanalnachrichten werden nicht verarbeitet.

After pairing, you can send text, images, files, audio, video, and voice messages to the bot. Voice messages follow the same processing as web recordings: the original audio is stored as a message attachment and displayed with a player in MatoLoop. Automatic transcription adds recognized text to your request without replacing the recording. If transcription fails or is unavailable, the audio remains accessible. The model can access the stored attachment later through the normal attachment tools. Ordinary audio files are stored without automatic transcription.

Während der Agent arbeitet, zeigt Telegram parallel den Schreibstatus und die aktuelle Aktivität, Reasoning-Zusammenfassung oder entstehende, formatierte Antwort als temporären Entwurf. Die Aktivität verwendet dieselben Bezeichnungen wie die MatoLoop-Oberfläche und ein passendes Symbol, zum Beispiel **📁 Datei lesen**. Vor dem ersten Reasoning erscheint **⚙️ Verarbeite Anfrage**. Reasoning erscheint als **🧠 Nachdenken**, ergänzt um eine tatsächlich veröffentlichte Zusammenfassung. Die letzte Tool-Aktivität bleibt bis zur nächsten sichtbaren Aktivität oder Antwort stehen. Tool-Argumente und -Ergebnisse erscheinen dort nicht. Wartet der Agent auf eine Freigabe oder Antwort, pausiert der Schreibstatus.

Agent approvals originating from a Telegram request appear with their available decision buttons directly in the private chat, including shared-terminal shell approvals. MatoLoop revalidates the paired owner, original message, session, run, and pending approval before accepting a decision. If the terminal draft has changed since the delivered review, review it again in MatoLoop; an older channel decision cannot authorize edited text. Skill build or edit requests still require exact file-revision review and approval in MatoLoop; Telegram can deny them.

Strukturierte Agent-Rückfragen erscheinen ebenfalls direkt im privaten Chat. Wählen Sie Antworten
über die Schaltflächen aus und wechseln Sie mit **Zurück** oder **Weiter** zwischen mehreren Fragen.
Eigene Antworten und Freitext geben Sie in das dafür eingeblendete Antwortfeld ein. Alternativ
können Sie Telegrams Funktion **Antworten** an der Fragen-Nachricht verwenden. Das Absenden einer
Texteingabe wechselt direkt zur nächsten Frage. Mit
**Antworten senden** schließen Sie die Rückfrage ab; **Ablehnen** beendet sie ohne Antworten.

Die endgültige Antwort wird als normale Nachricht zugestellt und zusammen mit der Anfrage vollständig in der zugehörigen MatoLoop-Session gespeichert. Telegram stellt Überschriften, Hervorhebungen, Listen, Links, Zitate und Code passend dar; komplexere Inhalte wie Tabellen, Mathematik oder Mermaid-Diagramme erscheinen in einer lesbaren Text- oder Codeform. Mit `/new` beginnen Sie eine neue Session, `/sessions` zeigt zuletzt verwendete Sessions und `/use <ID>` wechselt die Session. `/current`, `/stop`, `/unlink` und `/help` stehen ebenfalls zur Verfügung.

## Administrative Freigabe

Telegram is enabled for new installations. Administrators can disable or enable the provider under **Einstellungen > Kanäle > Verfügbare Kanäle**. While disabled, personal Telegram instances remain stored but are unavailable. Queued Telegram deliveries are not replayed after re-enabling the provider. Scheduled automation turns and their results are available in their normal MatoLoop session; there is no separate automation output-channel configuration.
