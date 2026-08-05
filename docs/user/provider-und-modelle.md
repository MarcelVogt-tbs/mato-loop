# Provider und Modelle

## Provider verwalten

Unter **Einstellungen > Provider** können Sie Provider hinzufügen, bearbeiten, aktivieren, testen und löschen. Der globale LLM-Schalter muss ebenfalls aktiviert sein.

### ChatGPT Codex

MatoLoop zeigt einen einmaligen Gerätecode und öffnet die ChatGPT-Anmeldeseite. Nach erfolgreicher Bestätigung wird die Verbindung verschlüsselt gespeichert. Es ist derzeit eine Codex-Konfiguration vorgesehen.

### OpenAI-kompatibel

Benötigt eine Basis-URL und optional einen API-Schlüssel. Als API-Variante stehen Chat Completions und Responses zur Verfügung. Verfügbare Modelle werden nach Möglichkeit über den `/models`-Endpunkt erkannt.

### Lemonade

Verbindet einen lokalen Lemonade-Server. MatoLoop erkennt verfügbare Chat- und Medienmodelle sowie deren Fähigkeiten.

## Modelle synchronisieren

Neu erkannte Modelle beginnen in der Regel deaktiviert; vom Provider als natives Chatmodell gekennzeichnete Modelle können bereits aktiviert sein. Aktivieren und testen Sie nur die benötigten Modelle. Verschwindet ein Modell beim späteren Synchronisieren, bleibt seine Konfiguration als **Nicht verfügbar** erhalten.

Bei Providern ohne Modellerkennung kann eine Modell-ID manuell eingetragen werden.

## Laufzeiteinstellungen

Je Modell können unter anderem Kontextfenster, Ausgabelimit, Streaming, Tool-Unterstützung, Bildunterstützung, Datenfreigabe, Memory-Policy, Webfetch und Reasoning-Zusammenfassungen konfiguriert werden. Verwenden Sie manuelle Overrides nur, wenn die tatsächlichen Fähigkeiten des Zielservers bekannt sind.

## Standardmodelle

Unter **Einstellungen > Standardmodelle** werden Modelle für verschiedene Aufgaben gewählt:

- Agent
- Bildanalyse
- Kontextkomprimierung und Titelgenerierung
- Dreaming
- Bild-, Sprach- und Audiogenerierung
- Transkription

Nicht jede Aufgabe muss konfiguriert sein. Fehlt jedoch das Agent-Standardmodell und ist in der Session kein Modell gewählt, kann kein Chatlauf starten.
