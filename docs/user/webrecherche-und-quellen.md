# Webrecherche und Quellen

## Websuchmodus der Session

| Modus | Verhalten |
| --- | --- |
| Erzwingen | Vor der normalen Antwort muss eine erfolgreiche Websuche stattfinden |
| Verfügbar | Der Agent kann bei Bedarf suchen |
| Deaktiviert | Das Suchwerkzeug wird nicht angeboten |

Die Auswahl des tatsächlichen Suchproviders erfolgt durch MatoLoop und kann vom Modell nicht überschrieben werden.

## Suchprovider

Ohne weitere Einrichtung verwendet MatoLoop eine eingebaute Suche ohne API-Schlüssel. Zusätzliche SearXNG-Instanzen werden unter **Einstellungen > Integrationen** verwaltet. Primär- und optionaler Fallback-Provider werden unter **Einstellungen > Tools** ausgewählt.

Der Fallback wird nur bei vorübergehenden Fehlern des primären Providers verwendet.

## Webseiten abrufen

`webfetch` ruft eine konkrete HTTP- oder HTTPS-Adresse ab und ist unabhängig von der Websuche konfiguriert. Verfügbare Richtlinien sind:

- jedes Mal nachfragen
- öffentliche Ziele automatisch erlauben
- öffentliche und ausdrücklich erlaubte private Ursprünge automatisch erlauben

Weiterleitungen und Zieladressen werden erneut geprüft. Link-lokale, Metadaten- und andere gefährliche Netzwerkziele werden blockiert. Private Ursprünge benötigen eine explizite Freigabe in den Einstellungen.

## Veröffentlichte Untertitel

`fetch_transcript` ruft vorhandene veröffentlichte Untertitel unterstützter öffentlicher YouTube-Videos ab und verwendet dieselbe Webzugriffsrichtlinie. Das Werkzeug lädt kein Video herunter, übersetzt keine Untertitel und startet keine automatische Transkription. Ist eine gewünschte Sprache nicht vorhanden, wird die ursprüngliche verfügbare Untertitelsprache gekennzeichnet zurückgegeben. Bildinhalt, Kameraführung und sichtbare Bedienoberflächen können damit nicht ausgewertet werden.

## Quellen in Antworten

Aktuelle Recherchequellen erscheinen als hervorgehobene Links in der Antwort. Prüfen Sie wichtige Aussagen weiterhin anhand der verlinkten Originalquelle; Suchergebnisse und Webseiten können unvollständig oder fehlerhaft sein.
