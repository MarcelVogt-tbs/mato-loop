# Integrationen und Toolbeiträge

Integrationen erweitern die MatoLoop-Harness, ohne den providerneutralen Agent-Loop oder die Permission-Grenze zu umgehen. Eine persistierte Instanz benennt einen Integrationstyp und einen hostseitig bekannten Adapter. Der Adapter validiert seine Konfiguration und erzeugt ausschließlich statisch implementierte Beiträge.

## Beitragsarten

Ein Adapter kann:

- ein neues Tool mit stabilem Namen, Pydantic-Eingabemodell und Hosthandler beitragen,
- Werte zu einem ausdrücklich erweiterbaren hostseitigen Konfigurationsfeld eines bestehenden Tools ergänzen,
- ein statisches, hostkontrolliertes Promptfragment für ein tatsächlich angebotenes Tool bereitstellen.

Persistierte Konfiguration und externe Antworten dürfen keine Handler, beliebigen JSON-Schemas oder Promptanweisungen liefern. Doppelte Toolnamen und Beiträge zu nicht deklarierten Konfigurationsfeldern sind Programmierfehler. Hostseitige Konfigurationswerte werden niemals allein durch ihren Beitrag zu Modellargumenten.

## Effektives Toolset

Vor jedem Modellrequest verbindet die Registry Built-ins, Sessionmodus, Modellfähigkeiten, Tool-Policies und aktive Integrationsbeiträge zu einem unveränderlichen Toolset. Dieses Objekt exportiert die Providerschemas und validiert die zurückgegebenen Calls. Damit kann ein Provider kein Tool ausführen, das ihm für diesen Request nicht angeboten wurde.

Ein Binding merkt sich außerdem die erlaubten Optionswerte und die zugehörige Integrations-ID. Direkt vor einem externen Zugriff lädt der Handler den aktuellen Integrationszustand erneut. Gelöschte oder deaktivierte Instanzen sind deshalb auch dann unwirksam, wenn sie im vorherigen Modellrequest noch angeboten wurden.

## Websuche

`web_search` besitzt für das Modell ausschließlich das Feld `query`. Der Host wählt Primär- und Fallbackprovider aus `ToolSettings.web_search`; Modellargumente können diese Auswahl nicht überschreiben. Die No-Key-Metasuche ist als `no_key` immer im Toolproviderkatalog vorhanden, aber keine Integration. Jede aktive SearXNG-Instanz ergänzt diesen Katalog um `searxng:<uuid>`. Anzeigenamen sind nur Beschriftungen; Umbenennungen verändern die stabile Toolkennung nicht.

Jeder normalisierte Treffer enthält `sourceId`, `title`, `url`, `snippet` und `publishedDate`. Die `sourceId` wird ausschließlich vom Host aus der kanonischen URL erzeugt; Providerwerte können sie nicht vorgeben. Treffer mit derselben kanonischen URL werden dedupliziert. Snippets sind untrusted Discovery-Daten und kein verifizierter Seiteninhalt. Ungültiges Provider-JSON wird als stabiler Integrationsfehler behandelt, statt als technisch erfolgreiche leere Evidenz zu gelten.

Primär- und Fallbackprovider werden unter **Einstellungen > Tools** konfiguriert. Der Fallback ist optional und darf nicht mit dem Primärprovider übereinstimmen. Meldet SearXNG bei leerem Ergebnis ausgefallene Standard-Engines, wiederholt MatoLoop die Anfrage einmal über dieselbe Instanz mit `bing`, `mojeek` und `presearch`. Der konfigurierte Fallback wird ausschließlich nach einem verbleibenden temporären Fehler verwendet: Netzwerkfehler, Timeout, HTTP 408, HTTP 429, HTTP 5xx oder weiterhin ausgefallene SearXNG-Engines. Authentifizierungs-, Konfigurations- und Protokollfehler sowie echte leere Trefferlisten lösen keinen Fallback aus. Wird eine ausgewählte Integration deaktiviert oder gelöscht, normalisiert die Service-Transaktion den Primärprovider auf die No-Key-Metasuche und entfernt einen ungültigen Fallback.

Die eingebaute No-Key-Metasuche verwendet den Python-Client `ddgs` mit automatischer Backendauswahl und führt den synchronen Aufruf mit begrenztem Timeout außerhalb des Async-Loops aus. Abhängig von der aktuellen Verfügbarkeit können unterschiedliche öffentliche Suchdienste antworten. Der persistierte Legacy-Key `duckduckgo` wird beim Laden auf `no_key` normalisiert. Suchbegriffe verlassen die Installation und werden an den jeweils verwendeten Dienst übertragen.
