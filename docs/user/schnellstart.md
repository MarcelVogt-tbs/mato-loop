# Schnellstart

## 1. Benutzer einrichten

On first launch, MatoLoop opens initial setup. Choose a username and a password with at least ten characters. This first local account becomes an administrator and is signed in automatically after saving. Administrators create additional accounts under **Administration > Benutzer**.

## 2. Provider verbinden

Open **Administration > Provider** to configure a global provider. If personal providers are allowed, use **Einstellungen > Provider** for your own connection:

- **ChatGPT Codex** verbindet ein geeignetes ChatGPT-Konto über einen Gerätecode.
- **OpenAI-kompatibel** verbindet einen kompatiblen Server über Basis-URL und optionalen API-Schlüssel.
- **Lemonade** verbindet einen lokalen Lemonade-Server und erkennt dessen Chat- und Medienmodelle.

Beim Anlegen synchronisiert MatoLoop die verfügbaren Modelle. Eine erneute Synchronisierung kann später manuell gestartet werden.

## 3. Modell aktivieren

Öffnen Sie beim Provider das gewünschte Modell und aktivieren Sie es, falls es nicht bereits als natives Providermodell aktiv ist. Danach können Sie die Verbindung mit einem aktiven Chatmodell testen. Öffnen Sie anschließend **Einstellungen > Agenten**, wählen Sie mindestens für **Allgemein** ein Agentmodell und konfigurieren Sie **Plan**, **Build** und **Sysadmin** bei Bedarf unabhängig davon.

## 4. Computer optional verbinden

Für reinen Chat ist kein Computer erforderlich. Für lokale Arbeitsbereiche, Prozesse oder lokale Integrationen öffnen Sie **Einstellungen > Computer** und fügen einen Computer hinzu. Der normale Compose-Stack startet den MatoLoop Agent ungekoppelt. Führen Sie anschließend `matoloop-agent pair` aus und geben Sie Backend-Adresse, Computer-ID sowie das nur einmal angezeigte Kopplungstoken ein. Für automatisierte Starts müssen alle drei `MATOLOOP_AGENT_*`-Verbindungswerte vorliegen. Ausführliche Hinweise stehen unter [Betrieb und Datensicherung](betrieb-und-datensicherung.md#optionalen-computer-einrichten).

## 5. Erste Session starten

Kehren Sie zum Chat zurück, geben Sie eine Nachricht ein und senden Sie sie ab. Eine neue Session wird erst mit dieser ersten Nachricht dauerhaft angelegt.

Vor dem Senden können Sie unter anderem auswählen:

- Modell
- Agentenprofil
- Memory-Modus
- Websuchmodus
- Computer

Den aktuellen absoluten Ordner wählen Sie bei Bedarf unter **Control Center > Dateien**. Ohne gewählten und verbundenen Computer bleibt der Chat nutzbar; Computer-Dateien und lokale Integrationen fehlen.

## 6. Freigaben prüfen

Benötigt eine Aktion Ihre Zustimmung, erscheint die Anfrage direkt beim zugehörigen Tool-Aufruf. Prüfen Sie Ressource, Risikostufe und Details, bevor Sie **Einmal erlauben**, **Für Session erlauben** oder **Ablehnen** wählen.

Under **Control Center > Benachrichtigungen**, normal pending approvals and agent questions from all sessions appear above informational notices and can both be answered directly, including shared-terminal shell approvals. Marking notices read does not resolve these actions. For a pending terminal command, you can alternatively edit its real shell line and press Enter to allow the rechecked draft once.

Als Nächstes: [Chat und Sessions](chat-und-sessions.md)
