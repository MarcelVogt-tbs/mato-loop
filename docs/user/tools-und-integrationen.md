# Tools und Integrationen

## Tool-Richtlinien

Unter **Einstellungen > Tools** legen Sie fest, welche Werkzeuge verfügbar sind und wann eine Freigabe erforderlich ist. Die üblichen Richtlinien sind:

- **Deaktiviert**: Das Tool kann nicht aufgerufen werden. Der Agent kennt die installierte Fähigkeit weiterhin und kann bei einer passenden Anfrage erklären, wie sie aktiviert wird.
- **Nachfragen**: Vor Agent-Tool-Aufrufen ist eine Freigabe erforderlich.
- **Automatisch**: Zulässige Aktionen werden ohne einzelne Nachfrage ausgeführt.

Hostseitige Sicherheitsgrenzen gelten auch bei **Automatisch** weiterhin.

Nur aufrufbare Tools erhalten ihr vollständiges Argumentschema. Deaktivierte oder wegen Agent-Modus, Modellwahl oder fehlender Integration nicht aufrufbare Fähigkeiten erscheinen für den Agenten lediglich als kompakter Hinweis. Dieser Hinweis kann weder eine Aktion noch eine Freigabe auslösen.

Direkte Aktionen, die Sie selbst in der Oberfläche auslösen, verwenden keine Agent-Freigabekarte. Dazu gehören insbesondere Dateiaktionen im Explorer und das Diktat über die Mikrofonschaltfläche.

## Toolgruppen

MatoLoop bündelt Werkzeuge für:

- Dateien und Verzeichnisse
- Websuche und Webseitenabruf
- Bild-, Sprach-, Audio- und Transkriptionsaufgaben
- Lesen und Durchsuchen früherer Sessions
- Memory
- Persona
- Kalender
- Wissensdatenbank (BookStack)

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

Auf derselben Seite können Sie einen Kalender dieses Kontos als installationsweiten **Standardkalender** auswählen. Unspezifische Kalenderabfragen und neue Termine verwenden ihn automatisch. Wenn Sie ausdrücklich einen anderen Kalender angeben, hat diese Auswahl Vorrang. Pro Installation kann nur ein Standardkalender aktiv sein.

Lesefreigaben können für die aktuelle Chat-Session gelten. Schreib- und Löschfreigaben gelten nur für den konkreten Vorgang. Wiederkehrende Termine und Termine mit Einladungs- oder unbekannten Eigenschaften können gelesen, aber in der ersten Version nicht verändert werden.

## BookStack integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere BookStack-Installationen mit Serveradresse, Token-ID und Token-Secret anlegen. Ein API-Token erstellen Sie in BookStack im Benutzerprofil unter **API-Tokens**. Die Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. BookStack erfordert HTTPS.

Der Agent kann damit Seiten suchen und lesen, Seiten anlegen und bearbeiten sowie Bücher, Kapitel und Seiten anlegen, umbenennen und löschen. Zusätzlich kann er Regale auflisten, lesen, anlegen, bearbeiten und löschen. Neue Bücher lassen sich direkt einem oder mehreren Regalen zuordnen, und Buchlisten zeigen die zugehörigen Regale an. Name und Beschreibung eines Buchs oder Kapitels lassen sich nachträglich ändern; welchen Regalen ein Buch angehört, wird dagegen über das Regal selbst gepflegt. Inhalte werden als Markdown ausgetauscht.

An Seiten kann der Agent außerdem Anhänge verwalten. Eine Datei stammt dabei entweder aus dem Internet, aus dem Session- oder Workspace-Speicher, oder es wird nur ein Link hinterlegt. Downloads laufen über dieselbe Webfetch-Strecke wie der normale Webseitenabruf: Ihre Webfetch-Richtlinie gilt weiterhin, und bei Bedarf werden Sie zusätzlich zur Freigabe der Adresse gefragt. Ist Webfetch deaktiviert, bleibt nur der reine Link oder eine lokale Datei. Anhänge sind auf 32 MiB begrenzt.

Manche Anbieter sperren automatisierte Zugriffe. Schlägt ein Download fehl, nennt MatoLoop den tatsächlichen Grund, etwa eine Zeitüberschreitung oder eine Ablehnung durch den Server. Der Agent kann die Adresse dann als Link hinterlegen, statt einen falschen Grund zu vermuten.

Beim Bearbeiten wird eine Seite immer vollständig ersetzt. Der Agent liest sie deshalb unmittelbar vorher und bekommt nach dem Speichern den in BookStack tatsächlich abgelegten Inhalt zurück, damit er das Ergebnis prüfen kann.

Über **Zugriff** konfigurieren Sie zunächst den Standard der Installation und anschließend Abweichungen für einzelne Bücher:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen und Schreiben unterstützen alle drei Modi. Löschen unterstützt nur **Nicht erlaubt** und **Nachfragen**.

Suche und Bücherliste gelten für die gesamte Installation und lassen sich nicht auf einzelne Bücher einschränken. Das Gleiche gilt für Regale: Sie sind Ordnungsstrukturen und keine einzeln freigebbaren Ressourcen, weshalb das Zuordnen von Büchern zu einem Regal nur über die Richtlinie der gesamten Installation abgesichert ist. Ein gelöschtes Regal löscht die enthaltenen Bücher nicht. Anhänge folgen dagegen der Richtlinie des Buches, zu dem die Seite gehört. Berechtigungen des verwendeten API-Tokens in BookStack bleiben zusätzlich wirksam: Was das Token dort nicht darf, kann MatoLoop nicht freigeben.

## Webfetch konfigurieren

Webfetch besitzt einen eigenen globalen Schalter, eine Freigaberichtlinie, eine Liste erlaubter privater Ursprünge, einen User-Agent und eine maximale Antwortgröße. Modellbezogene Einstellungen können die Richtlinie einschränken oder überschreiben; globale Netzwerkgrenzen bleiben wirksam.
