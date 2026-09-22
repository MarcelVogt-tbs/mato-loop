# Provider und Modelle

## Provider verwalten

Unter **Einstellungen > Provider** können Sie Provider hinzufügen, bearbeiten, aktivieren, testen und löschen.

Administratoren wechseln mit **Persönlich** / **Instanz** zwischen ihren eigenen und den gemeinsamen Einstellungen. Nur Administratoren dürfen die Instanz ändern und festlegen, ob persönliche Provider erlaubt sind. Normale Benutzer verwalten ausschließlich ihre persönliche Seite. Instanz-Provider und ihre Modelle stehen allen Benutzern zur Verfügung; persönliche Provider und ihre verschlüsselten Zugangsdaten bleiben dem jeweiligen Benutzer zugeordnet.

### ChatGPT Codex

MatoLoop zeigt einen einmaligen Gerätecode und öffnet die ChatGPT-Anmeldeseite. Nach erfolgreicher Bestätigung wird die Verbindung verschlüsselt gespeichert. Es ist derzeit eine Codex-Konfiguration vorgesehen.

### OpenAI-kompatibel

Benötigt eine Basis-URL und optional einen API-Schlüssel. Als API-Variante stehen Chat Completions und Responses zur Verfügung. Verfügbare Modelle werden nach Möglichkeit über den `/models`-Endpunkt erkannt.

### Lemonade

Verbindet einen lokalen Lemonade-Server. MatoLoop erkennt verfügbare Chat- und Medienmodelle sowie deren Fähigkeiten.

## Modelle synchronisieren

Neu erkannte Modelle beginnen in der Regel deaktiviert; vom Provider als natives Chatmodell gekennzeichnete Modelle können bereits aktiviert sein. Aktivieren und testen Sie nur die benötigten Modelle. Verschwindet ein Modell beim späteren Synchronisieren, bleibt seine Konfiguration als **Nicht verfügbar** erhalten.

Bei Providern ohne Modellerkennung kann eine Modell-ID manuell eingetragen werden.

Manuell eingetragene und automatisch erkannte Modelle können gelöscht werden. Dabei entfernt MatoLoop Verweise auf dieses Modell aus betroffenen Agentenprofilen und die ausdrückliche Modellwahl betroffener Sessions. Diese Sessions verwenden anschließend das Modell ihres Profils, sofern dort eines gewählt ist. Bietet ein Provider ein gelöschtes Modell weiterhin an, erscheint es bei der nächsten Synchronisierung erneut als neu erkanntes Modell. Auch Synchronisierung, Deaktivierung, geänderte Fähigkeiten und eine getrennte Codex-Verbindung bereinigen ungültig gewordene Profilverweise.

## Laufzeiteinstellungen

Je Modell können unter anderem Kontextfenster, Ausgabelimit, Streaming, Tool-Unterstützung, Bildunterstützung, Datenfreigabe, Memory-Policy und Reasoning-Zusammenfassungen konfiguriert werden. Webfetch folgt stattdessen den Rechten des gewählten Agentenprofils für die effektive private oder öffentliche Modellklasse. MatoLoop bietet den Reasoning-Aufwand im Chat nur für ausdrücklich unterstützte Modelle an und berücksichtigt deren verfügbare Stufen und Providerformat. Qwen3.8-27B unterstützt **Aus**, **Niedrig**, **Mittel** und **Sehr hoch**; MatoLoop verwendet **Mittel** als Modellstandard. Codex übernimmt die unterstützten Stufen einschließlich **Maximum** und **Ultra** sowie den Modellstandard aus dem synchronisierten Katalog.

## Profilmodelle

Unter **Einstellungen > Agenten > Modelle** wählen Sie je Agentenprofil Modelle für verschiedene Aufgaben:

- Agent
- Bildanalyse
- Kontextkomprimierung und Titelgenerierung
- Dreaming
- Bild-, Sprach- und Audiogenerierung
- Transkription

**Allgemein**, **Plan**, **Build** und **Sysadmin** sind unabhängige geschützte Instanz-Basisprofile. Ein eigenes Instanz- oder persönliches Profil wählt genau eines davon als Basis und übernimmt jedes nicht angepasste Feld fortlaufend. Es gibt keine globalen oder persönlichen Standardmodelle außerhalb der Profile. Eine ausdrückliche Modellwahl der Session hat Vorrang vor dem Agentmodell des Profils. Sessiongebundene Hilfsaufrufe verwenden die Modellfelder dieses Profils; profilunabhängiges Dreaming verwendet **Allgemein**. Nicht jede Hilfsaufgabe muss konfiguriert sein. Fehlt jedoch sowohl in der Session als auch im Profil ein Agentmodell, kann kein Chatlauf starten.
