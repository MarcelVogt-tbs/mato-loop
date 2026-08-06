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

## CalDAV integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere CalDAV-Konten mit Serveradresse, Benutzername und Passwort oder App-Passwort anlegen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. CalDAV erfordert HTTPS.

Über **Zugriff** konfigurieren Sie zunächst den Standard des Kontos und anschließend Abweichungen für einzelne Kalender:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen und Schreiben unterstützen alle drei Modi. Löschen unterstützt nur **Nicht erlaubt** und **Nachfragen**. Serverseitig schreibgeschützte Kalender bleiben unabhängig von der MatoLoop-Richtlinie schreibgeschützt.

Lesefreigaben können für die aktuelle Chat-Session gelten. Schreib- und Löschfreigaben gelten nur für den konkreten Vorgang. Wiederkehrende Termine und Termine mit Einladungs- oder unbekannten Eigenschaften können gelesen, aber in der ersten Version nicht verändert werden.

## Webfetch konfigurieren

Webfetch besitzt einen eigenen globalen Schalter, eine Freigaberichtlinie, eine Liste erlaubter privater Ursprünge, einen User-Agent und eine maximale Antwortgröße. Modellbezogene Einstellungen können die Richtlinie einschränken oder überschreiben; globale Netzwerkgrenzen bleiben wirksam.
