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

`webfetch` ruft eine konkrete HTTP- oder HTTPS-Adresse ab und ist unabhängig von der Websuche konfiguriert. Unter **Einstellungen > Tools > Richtlinien** gelten für öffentliche und private Adressen jeweils die normalen Tool-Richtlinien:

- **Deaktiviert**
- **Nachfragen**
- **Automatisch**

Die Richtlinien werden getrennt für private und öffentliche Modelle gespeichert. Administratoren legen unter **Verfügbare Tools** zusätzlich fest, ob öffentliche beziehungsweise private Adressen technisch verfügbar sind. Weiterleitungen und Zieladressen werden erneut geprüft. Metadaten- und andere gefährliche Netzwerkziele bleiben unabhängig von der Richtlinie blockiert.

## Veröffentlichte Untertitel

`fetch_transcript` ruft vorhandene veröffentlichte Untertitel unterstützter öffentlicher YouTube-Videos ab und verwendet dieselbe Webzugriffsrichtlinie. Das Werkzeug lädt kein Video herunter, übersetzt keine Untertitel und startet keine automatische Transkription. Ist eine gewünschte Sprache nicht vorhanden, wird die ursprüngliche verfügbare Untertitelsprache gekennzeichnet zurückgegeben. Bildinhalt, Kameraführung und sichtbare Bedienoberflächen können damit nicht ausgewertet werden.

## Quellen in Antworten

Aktuelle Recherchequellen erscheinen als hervorgehobene Links in der Antwort. Prüfen Sie wichtige Aussagen weiterhin anhand der verlinkten Originalquelle; Suchergebnisse und Webseiten können unvollständig oder fehlerhaft sein.
