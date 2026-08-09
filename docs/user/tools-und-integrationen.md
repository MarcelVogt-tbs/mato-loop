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
- QR-, Bild-, Sprach-, Audio- und Transkriptionsaufgaben
- Lesen und Durchsuchen früherer Sessions
- Memory
- Persona
- Kalender und Aufgaben

Welche Tools tatsächlich erscheinen, hängt zusätzlich von Agent-Modus, Session-Einstellungen, Modellfähigkeiten und konfigurierten Standardmodellen ab.

## SearXNG integrieren

Unter **Einstellungen > Integrationen** können Sie zusätzliche SearXNG-Instanzen anlegen, aktivieren, bearbeiten, testen und löschen. Wählen Sie anschließend unter **Einstellungen > Tools** den primären Suchprovider und optional einen Fallback.

Die eingebaute Suche ohne API-Schlüssel ist keine Integration und steht unabhängig von SearXNG zur Verfügung.

## CalDAV integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere CalDAV-Konten mit Serveradresse, Benutzername und Passwort oder App-Passwort anlegen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. CalDAV erfordert HTTPS.

Über **Zugriff** konfigurieren Sie zunächst den Standard des Kontos und anschließend Abweichungen für einzelne Kalender und Aufgabenlisten. Gehören ein Kalender und eine Aufgabenliste zur selben CalDAV-Sammlung, erscheinen sie gemeinsam in einer Karte mit getrennten Richtlinienabschnitten **Kalender** und **Aufgaben**. An jeder Ressource erscheinen nur die dafür passenden Rechte:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen und Schreiben unterstützen alle drei Modi. Löschen unterstützt nur **Nicht erlaubt** und **Nachfragen**. Serverseitig schreibgeschützte Kalender und Aufgabenlisten bleiben unabhängig von der MatoLoop-Richtlinie schreibgeschützt.

Im gemeinsamen Bereich **Standardressourcen dieses Kontos** wählen Sie unabhängig einen **Standardkalender** und eine **Standard-Aufgabenliste**. Unspezifische Abfragen und neue Einträge verwenden die jeweilige installationsweite Auswahl automatisch. Wenn Sie ausdrücklich eine andere Ressource angeben, hat diese Vorrang. Pro Installation kann jeweils nur ein Standard aktiv sein.

Lesefreigaben können für die aktuelle Chat-Session gelten. Schreib- und Löschfreigaben gelten nur für den konkreten Vorgang. Wiederkehrende Termine und Termine mit Einladungs- oder unbekannten Eigenschaften können gelesen, aber in der ersten Version nicht verändert werden.

CalDAV-Aufgaben unterstützen Titel, Notiz, eine optionale Fälligkeit als Datum oder Zeitpunkt sowie den Status **offen** oder **erledigt**. Erledigte Aufgaben werden standardmäßig ausgeblendet und können ausdrücklich mit abgefragt werden. Jede Erstellung, Änderung, Statusänderung oder Löschung betrifft genau eine Aufgabe. Wiederholungen, Unteraufgaben, Erinnerungen, Kategorien und Prioritäten können in der ersten Version nicht bearbeitet werden. Einkaufslisten werden wie normale CalDAV-Aufgabenlisten verwendet; abgehakte Einträge werden als erledigt markiert und nicht automatisch gelöscht.

## Webfetch konfigurieren

Webfetch besitzt einen eigenen globalen Schalter, eine Freigaberichtlinie, eine Liste erlaubter privater Ursprünge, einen User-Agent und eine maximale Antwortgröße. Modellbezogene Einstellungen können die Richtlinie einschränken oder überschreiben; globale Netzwerkgrenzen bleiben wirksam.
