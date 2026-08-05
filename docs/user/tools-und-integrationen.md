# Tools und Integrationen

## Tool-Richtlinien

Unter **Einstellungen > Tools** legen Sie fest, welche Werkzeuge verfügbar sind und wann eine Freigabe erforderlich ist. Die üblichen Richtlinien sind:

- **Deaktiviert**: Das Tool wird dem Agenten nicht angeboten.
- **Nachfragen**: Vor Agent-Tool-Aufrufen ist eine Freigabe erforderlich.
- **Automatisch**: Zulässige Aktionen werden ohne einzelne Nachfrage ausgeführt.

Hostseitige Sicherheitsgrenzen gelten auch bei **Automatisch** weiterhin.

Direkte Aktionen, die Sie selbst in der Oberfläche auslösen, verwenden keine Agent-Freigabekarte. Dazu gehören insbesondere Dateiaktionen im Explorer und das Diktat über die Mikrofonschaltfläche.

## Toolgruppen

MatoLoop bündelt Werkzeuge für:

- Dateien und Verzeichnisse
- Websuche und Webseitenabruf
- Bild-, Sprach-, Audio- und Transkriptionsaufgaben
- Lesen und Durchsuchen früherer Sessions
- Memory
- Persona

Welche Tools tatsächlich erscheinen, hängt zusätzlich von Agent-Modus, Session-Einstellungen, Modellfähigkeiten und konfigurierten Standardmodellen ab.

## SearXNG integrieren

Unter **Einstellungen > Integrationen** können Sie zusätzliche SearXNG-Instanzen anlegen, aktivieren, bearbeiten, testen und löschen. Wählen Sie anschließend unter **Einstellungen > Tools** den primären Suchprovider und optional einen Fallback.

Die eingebaute Suche ohne API-Schlüssel ist keine Integration und steht unabhängig von SearXNG zur Verfügung.

## Webfetch konfigurieren

Webfetch besitzt einen eigenen globalen Schalter, eine Freigaberichtlinie, eine Liste erlaubter privater Ursprünge, einen User-Agent und eine maximale Antwortgröße. Modellbezogene Einstellungen können die Richtlinie einschränken oder überschreiben; globale Netzwerkgrenzen bleiben wirksam.
