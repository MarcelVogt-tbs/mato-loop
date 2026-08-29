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

Manuell eingetragene und automatisch erkannte Modelle können gelöscht werden. Dabei werden ihre Standardmodell-Einstellungen im betroffenen Einstellungsbereich entfernt; betroffene Sessions verwenden anschließend wieder das wirksame Standardmodell. Bietet ein Provider ein gelöschtes Modell weiterhin an, erscheint es bei der nächsten Synchronisierung erneut als neu erkanntes Modell.

## Laufzeiteinstellungen

Je Modell können unter anderem Kontextfenster, Ausgabelimit, Streaming, Tool-Unterstützung, Bildunterstützung, Datenfreigabe, Memory-Policy, Webfetch und Reasoning-Zusammenfassungen konfiguriert werden. MatoLoop bietet den Reasoning-Aufwand im Chat nur für ausdrücklich unterstützte Modelle an und berücksichtigt deren verfügbare Stufen und Providerformat. Qwen3.8-27B unterstützt **Aus**, **Niedrig**, **Mittel** und **Sehr hoch**; MatoLoop verwendet **Mittel** als Standard. Codex übernimmt die unterstützten Stufen einschließlich **Maximum** und **Ultra** sowie den Standardwert aus dem synchronisierten Modellkatalog.

## Standardmodelle

Unter **Einstellungen > Standardmodelle** werden Modelle für verschiedene Aufgaben gewählt:

- Agent
- Bildanalyse
- Kontextkomprimierung und Titelgenerierung
- Dreaming
- Bild-, Sprach- und Audiogenerierung
- Transkription

Nicht jede Aufgabe muss konfiguriert sein. Persönliche Standardmodelle überschreiben die Instanzvorgabe nur, wenn das gewählte Modell effektiv verfügbar ist. Fehlt jedoch das Agent-Standardmodell und ist in der Session kein Modell gewählt, kann kein Chatlauf starten.
