# Schnellstart

## 1. Benutzer einrichten

Beim ersten Aufruf zeigt MatoLoop die Ersteinrichtung an. Legen Sie einen Benutzernamen und ein Passwort mit mindestens zehn Zeichen fest. Nach dem Speichern werden Sie automatisch angemeldet.

## 2. Provider verbinden

Öffnen Sie **Einstellungen > Provider** und wählen Sie einen Provider:

- **ChatGPT Codex** verbindet ein geeignetes ChatGPT-Konto über einen Gerätecode.
- **OpenAI-kompatibel** verbindet einen kompatiblen Server über Basis-URL und optionalen API-Schlüssel.
- **Lemonade** verbindet einen lokalen Lemonade-Server und erkennt dessen Chat- und Medienmodelle.

Beim Anlegen synchronisiert MatoLoop die verfügbaren Modelle. Eine erneute Synchronisierung kann später manuell gestartet werden.

## 3. Modell aktivieren

Öffnen Sie beim Provider das gewünschte Modell und aktivieren Sie es, falls es nicht bereits als natives Providermodell aktiv ist. Danach können Sie die Verbindung mit einem aktiven Chatmodell testen. Wählen Sie anschließend unter **Einstellungen > Standardmodelle** mindestens ein Standardmodell für **Agent** aus.

## 4. Computer optional verbinden

Für reinen Chat ist kein Computer erforderlich. Für lokale Arbeitsbereiche, Prozesse oder lokale Integrationen öffnen Sie **Einstellungen > Computer**, fügen einen Computer hinzu und übernehmen Computer-ID sowie das nur einmal angezeigte Kopplungstoken. Der optionale Compose-Computer verwendet `MATO_EXECUTION_AGENT_ID`, `MATO_EXECUTION_AGENT_PAIRING_TOKEN`, `MATO_EXECUTION_AGENT_WORKSPACE` und `MATO_EXECUTION_AGENT_CONFIG` im Profil `execution-agent`. Nach erfolgreicher Kopplung kann das Kopplungstoken entfernt werden. Ausführliche Hinweise stehen unter [Betrieb und Datensicherung](betrieb-und-datensicherung.md#optionalen-computer-einrichten).

## 5. Erste Session starten

Kehren Sie zum Chat zurück, geben Sie eine Nachricht ein und senden Sie sie ab. Eine neue Session wird erst mit dieser ersten Nachricht dauerhaft angelegt.

Vor dem Senden können Sie unter anderem auswählen:

- Modell
- Agent-Modus **Lesen** oder **Schreiben**
- Memory-Modus
- Websuchmodus
- Computer
- Arbeitsbereich
- Arbeitsordner

Ohne gewählten und verbundenen Computer bleibt der Chat nutzbar; lokale Arbeitsbereiche und lokale Integrationen fehlen.

## 6. Freigaben prüfen

Benötigt eine Aktion Ihre Zustimmung, erscheint die Anfrage direkt beim zugehörigen Tool-Aufruf. Prüfen Sie Ressource, Risikostufe und Details, bevor Sie **Einmal erlauben**, **Für Session erlauben** oder **Ablehnen** wählen.

Als Nächstes: [Chat und Sessions](chat-und-sessions.md)
