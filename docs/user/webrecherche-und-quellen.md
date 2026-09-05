# Webrecherche und Quellen

## Websuchmodus der Session

| Modus | Verhalten |
| --- | --- |
| Erzwingen | Vor der normalen Antwort muss eine erfolgreiche Websuche stattfinden |
| Verfügbar | Der Agent kann bei Bedarf suchen |
| Deaktiviert | Das Suchwerkzeug wird nicht angeboten |

Die Auswahl des tatsächlichen Suchproviders erfolgt durch MatoLoop und kann vom Modell nicht überschrieben werden.

## Suchprovider

Ohne weitere Einrichtung verwendet MatoLoop eine eingebaute Suche ohne API-Schlüssel. Zusätzliche SearXNG-Instanzen sowie Primär- und optionaler Fallback-Provider werden unter **Einstellungen > Integrationen** verwaltet.

Der Fallback wird nur bei vorübergehenden Fehlern des primären Providers verwendet.

## Webseiten abrufen

`webfetch` ruft eine konkrete HTTP- oder HTTPS-Adresse ab und ist unabhängig von der Websuche konfiguriert. Unter **Einstellungen > Tools > Richtlinien** gilt dafür eine einheitliche Tool-Richtlinie:

- **Deaktiviert**
- **Nachfragen**
- **Automatisch**

Die Richtlinie wird getrennt für private und öffentliche Modelle gespeichert. Administratoren legen unter **Verfügbare Tools** zusätzlich fest, ob Webfetch verfügbar ist und ob nur öffentliche Adressen zulässig sind. Diese Adressbeschränkung ist standardmäßig aktiv. Wird sie deaktiviert, darf Webfetch jedes vom Backend erreichbare HTTP(S)-Ziel aufrufen, einschließlich privater, lokaler und Metadaten-Adressen. Weiterleitungen werden erneut autorisiert und ihre Zieladressen erneut aufgelöst und gebunden.

## Veröffentlichte Untertitel

`fetch_transcript` ruft vorhandene veröffentlichte Untertitel unterstützter öffentlicher YouTube-Videos ab und verwendet dieselbe Webzugriffsrichtlinie. Das Werkzeug lädt kein Video herunter, übersetzt keine Untertitel und startet keine automatische Transkription. Ist eine gewünschte Sprache nicht vorhanden, wird die ursprüngliche verfügbare Untertitelsprache gekennzeichnet zurückgegeben. Bildinhalt, Kameraführung und sichtbare Bedienoberflächen können damit nicht ausgewertet werden.

## Quellen in Antworten

Aktuelle Recherchequellen erscheinen als hervorgehobene Links in der Antwort. Prüfen Sie wichtige Aussagen weiterhin anhand der verlinkten Originalquelle; Suchergebnisse und Webseiten können unvollständig oder fehlerhaft sein.
