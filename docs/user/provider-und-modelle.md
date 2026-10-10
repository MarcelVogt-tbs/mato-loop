# Provider und Modelle

## Provider verwalten

**Administration > Provider** öffnet zunächst **Einstellungen** mit dem Schalter für persönliche Provider. Unter **Verwaltung** legen Administratoren gemeinsame Verbindungen an und verwalten deren Modelle. **Einstellungen > Provider** verwaltet ausschließlich Ihre eigenen Provider, auch für Administratoren. Deaktivierte persönliche Konfiguration bleibt gespeichert; gemeinsame Modelle bleiben verfügbar.

### ChatGPT Codex

MatoLoop zeigt einen einmaligen Gerätecode und die ChatGPT-Anmeldeseite. Bestätigen Sie ihn mit einem geeigneten Konto. OAuth-Zugangsdaten werden verschlüsselt gespeichert; ein lokaler Callback-Port ist nicht nötig. Danach können Modelle synchronisiert werden.

### OpenAI-kompatibel

Verwenden Sie Basis-URL, optionalen API-Schlüssel und entweder Chat Completions oder Responses. Modelle werden möglichst über `/models` erkannt; bei fehlender Erkennung ist eine manuelle Modell-ID möglich.

### Lemonade

MatoLoop erkennt installierte Chat- und Medienmodelle eines Lemonade-Servers. Laden, Entladen und Speicherbelegung verwalten Sie in Lemonade; MatoLoop übernimmt dessen Modellresidenz nicht. Nicht jede Modellart ist ein Chatmodell.

### Azure Speech

Verwenden Sie einen HTTPS-Ressourcen- oder regionalen Endpunkt und den API-Schlüssel. Synchronisierung prüft verfügbare Sprachen und bietet `azure-speech-fast-transcription` an. Aktivieren Sie es und wählen Sie es als **Transkription** unter Standardmodelle. Azure Speech verarbeitet aufgezeichnetes Audio, weder Chat noch Live-Transkription oder Sprachgenerierung. Das Originalaudio wird übertragen und kann nicht textlich pseudonymisiert werden. Dienstregion, Tarif, Quoten und Sprachen müssen bei Azure passend verfügbar sein.

## Modelle synchronisieren

Neu erkannte Modelle beginnen normalerweise deaktiviert; bestimmte hostseitig ergänzte native Medienmodelle können aktiv sein. Aktivieren und testen Sie benötigte Modelle. Synchronisierung erhält interne IDs und eigene Laufzeiteinstellungen, kennzeichnet verschwundene Modelle als **Nicht verfügbar** und löscht sie nicht automatisch.

Löschen entfernt passende Standardmodellzuordnungen und explizite sessionweite Verweise. Nicht verfügbare agentenspezifische Sessionwerte werden bei der Auflösung übersprungen. Eine spätere Synchronisierung kann das externe Modell mit einer neuen internen ID wieder anlegen. Ein Fehler bei der Synchronisierung ersetzt nicht den zuletzt gültigen Katalog.

## Laufzeiteinstellungen

Kontextfenster, Auto-/manuelles Ausgabelimit, Streaming, Tool-, Paralleltool- und Bildunterstützung, Memory, Vertrauenslevel und Reasoning werden je Modell konfiguriert. **Auto** lässt ein unbekanntes Ausgabelimit beim Provider; MatoLoop prüft trotzdem seinen lokalen Kontextbudget-Schätzwert. Ein manuelles Limit ist eine Obergrenze, keine reservierte feste Ausgabe.

Reasoning-Stufen erscheinen nur für unterstützte Modelle. Verfügbare Stufen und Standards hängen vom Provider und dessen erkanntem Modellprofil ab; synchronisieren Sie nach Provideränderungen. Die Auswahl eines öffentlichen Modells gibt bereits autorisierte Inhalte an diesen Provider frei. [Datenfreigabe](freigaben-und-datenfreigabe.md) und Memory-Zielgruppen bleiben getrennte Themen.

## Standardmodelle und Sessionvorlagen

Gemeinsame Zuordnungen stehen unter **Administration > Standardmodelle > Verwaltung**, erlaubte persönliche Vorgaben unter **Einstellungen > Standardmodelle**. Die administrative Einstellungsseite steuert die persönliche Freigabe. Zwecke sind:

- Agent und Reasoning-Vorgabe.
- Kontextkomprimierung und Titelgenerierung.
- Dreaming.
- Bild-, Video- und Audioanalyse.
- Bild-, Sprach- und Audiogenerierung sowie Transkription.

Agentenprofile enthalten keine Modellfelder. **Sessionvorlagen** wählen verfügbare Agenten, Startagent, Memory, Nachrichtengröße und optionale Chatmodell-/Reasoning-Werte für die Session oder einzelne Agenten. **Allgemein** bietet Allgemein/Sysadmin; **Coding** bietet Plan/Build und kleine Nachrichten. Beide beginnen mit vollem Memory. Eine Session kopiert die Vorlage einmal; spätere Vorlagenänderungen wirken nicht auf sie zurück.

Ein agentenspezifischer Sessionwert gewinnt vor dem sessionweiten Wert, dann persönlicher und globaler Vorgabe. Nicht verfügbare Werte werden übersprungen. Ohne Agentmodell-Vorgabe dient das erste verfügbare toolfähige Chatmodell in der effektiven Liste. Hilfs-/Medienmodelle verwenden Standardmodelle und ihre bestehenden aktiven Modell-Fallbacks. Plan und Build verlangen außerdem einen verbundenen Computer mit zugänglichem Arbeitsordner.
