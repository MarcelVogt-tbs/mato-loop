# Tools und Integrationen

## Tool-Richtlinien

Unter **Einstellungen > Tools** legen Sie fest, welche Werkzeuge verfügbar sind und wann eine Freigabe erforderlich ist. Die üblichen Richtlinien sind:

- **Deaktiviert**: Das Tool kann nicht aufgerufen werden. Der Agent kennt die installierte Fähigkeit weiterhin und kann bei einer passenden Anfrage erklären, wie sie aktiviert wird.
- **Nachfragen**: Vor Agent-Tool-Aufrufen ist eine Freigabe erforderlich.
- **Automatisch**: Zulässige Aktionen werden ohne einzelne Nachfrage ausgeführt.

Hostseitige Sicherheitsgrenzen gelten auch bei **Automatisch** weiterhin.

Administratoren wechseln mit **Persönlich** / **Instanz** zwischen den eigenen Tool-Einstellungen und den gemeinsamen Vorgaben. Sie können persönliche Tool-Einstellungen vollständig sperren. Persönliche Richtlinien ersetzen sonst die Instanzvorgaben; User-Agent, Größenlimit und erlaubte private Ursprünge von Webfetch bleiben technische Instanzgrenzen. Mit **Auf Instanzstandard zurücksetzen** werden die persönlichen Tool-Einstellungen gelöscht.

Nur aufrufbare Tools erhalten ihr vollständiges Argumentschema. Deaktivierte oder wegen Agent-Modus, Modellwahl oder fehlender Integration nicht aufrufbare Fähigkeiten erscheinen für den Agenten lediglich als kompakter Hinweis. Dieser Hinweis kann weder eine Aktion noch eine Freigabe auslösen.

Direkte Aktionen, die Sie selbst in der Oberfläche auslösen, verwenden keine Agent-Freigabekarte. Dazu gehören insbesondere Dateiaktionen unter **Control Center > Dateien** und das Diktat über die Mikrofonschaltfläche.

## Toolgruppen

MatoLoop bündelt Werkzeuge für:

- Dateien und Verzeichnisse
- Websuche und Webseitenabruf
- QR-, Bild-, Sprach-, Audio- und Transkriptionsaufgaben
- Lesen und Durchsuchen früherer Sessions
- Memory
- Persona
- Kalender und Aufgaben
- Rezepte und Essensplanung (Mealie)
- Wissensdatenbank (BookStack)
- 3D-Modellierung (OpenSCAD)

Welche Tools tatsächlich erscheinen, hängt zusätzlich von Agent-Modus, Session-Einstellungen, Modellfähigkeiten und konfigurierten Standardmodellen ab.

## Instanz und persönliche Integrationen

Instanz-Integrationen und ihre externen Ressourcen sind bewusst gemeinsam: Alle Benutzer können sie entsprechend den Instanzrichtlinien verwenden. Persönliche Integrationen und Zugangsdaten bleiben dem jeweiligen Benutzer zugeordnet. Administratoren können persönliche Integrationen vollständig sperren und einzeln erlauben, welche fest eingebauten Typen persönlich konfiguriert werden dürfen: SearXNG, CalDAV, BookStack, Mealie, Git und OpenSCAD. Konfigurationen können keine eigenen Tool-Schemas oder beliebigen ausführbaren Tools hinzufügen.

Administratoren sehen den Umschalter **Persönlich** / **Instanz**. Normale Benutzer bearbeiten nur ihren persönlichen Bereich. Git und OpenSCAD werden immer persönlich an einen Computer des angemeldeten Benutzers gebunden.

## Lokale Funktionen

Arbeitsbereiche, Prozesse, Shell-Befehle und statisch dafür vorgesehene lokale Integrationen laufen auf dem für die Session gewählten Computer. Eine lokale Integration erscheint nur, wenn sie diesem Computer zugeordnet ist, der Computer verbunden ist und die fest eingebaute Operation meldet. Ohne passenden Computer bleiben Chat, Websuche und andere Backend-Funktionen verfügbar. Es gibt keinen automatischen Wechsel, Backend-Fallback oder Wiederholungsversuch.

`process.exec` kann nur administrativ konfigurierte Programm-Aliase starten. `shell.run` steht nur mit einer konfigurierten Shell zur Verfügung. Beide Funktionen laufen im gewählten Arbeitsordner, aber ohne allgemeine Betriebssystem-Sandbox. Im Agent-Modus **Lesen** prüft der Execution Agent deshalb den vollständigen Shell-Aufruf und erlaubt ausschließlich die dokumentierte Positivliste einfacher Lesebefehle. Unvollständige Analysen, Umleitungen, Skripte, unbekannte Programme, Symlinks und Pfade außerhalb der freigegebenen Wurzel werden abgelehnt. Eine Freigabe kann diese Grenze nicht umgehen.

Ein nachweislich lesender Shell-Befehl kann exakt für denselben Computer, dieselbe Wurzelversion, denselben Arbeitsordner und denselben Befehl innerhalb der Session freigegeben werden. Andere Shell- und Prozessaufrufe zeigen weiterhin die deutliche Warnung und bieten nur **Einmal erlauben** oder **Ablehnen** an. Für Git sollten die fest eingebauten Git-Tools statt Shell-Befehlen verwendet werden.

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

Im Bereich **Standardressourcen dieses Kontos** wählen Sie unabhängig einen **Standardkalender** und eine **Standard-Aufgabenliste**. Unspezifische Abfragen und neue Einträge verwenden die Auswahl des aktuellen Einstellungsbereichs automatisch. Wenn Sie ausdrücklich eine andere Ressource angeben, hat diese Vorrang. Pro Instanz- oder persönlichem Bereich kann jeweils nur ein Standard aktiv sein.

Lesefreigaben können für die aktuelle Chat-Session gelten. Schreib- und Löschfreigaben gelten nur für den konkreten Vorgang. Wiederkehrende Termine und Termine mit Einladungs- oder unbekannten Eigenschaften können gelesen, aber in der ersten Version nicht verändert werden.

CalDAV-Aufgaben unterstützen Titel, Notiz, eine optionale Fälligkeit als Datum oder Zeitpunkt sowie den Status **offen** oder **erledigt**. Erledigte Aufgaben werden standardmäßig ausgeblendet und können ausdrücklich mit abgefragt werden. Änderungen, Statusänderungen und Löschungen betreffen genau eine Aufgabe. Der Agent kann außerdem bis zu 50 fehlende Aufgaben gesammelt erstellen; gleichnamige offene Aufgaben und Wiederholungen innerhalb des Aufrufs werden übersprungen. Wiederholungen, Unteraufgaben, Erinnerungen, Kategorien und Prioritäten können in der ersten Version nicht bearbeitet werden. Einkaufslisten werden wie normale CalDAV-Aufgabenlisten verwendet; abgehakte Einträge werden als erledigt markiert und nicht automatisch gelöscht.

## Mealie integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere Mealie-Installationen mit Basis-URL und langlebigem API-Token anlegen. Den Token erstellen Sie in Mealie unter Ihrem Benutzerprofil bei **API-Tokens**. MatoLoop speichert ihn verschlüsselt und zeigt ihn nicht wieder an. HTTP ist für lokale Installationen zulässig; verwenden Sie HTTPS, sobald die Verbindung ein nicht vertrauenswürdiges Netz durchquert.

Der Agent kann gespeicherte Rezepte suchen, Zutaten und Zubereitung lesen sowie Rezept- und Freitext-Einträge im Essensplan lesen, erstellen, ändern und löschen. Rezept- und Plan-Kennungen bleiben als verschlüsselte, an die aktuelle Integration gebundene Referenzen verborgen. Ändert sich ein gelesener Planeintrag vor einer Änderung oder Löschung, lehnt MatoLoop den veralteten Zugriff ab.

Für eine Anfrage wie „Erstelle mir eine Einkaufsliste für meinen Wochenessensplan“ benötigen Sie zusätzlich eine CalDAV-Aufgabenliste, zum Beispiel **Einkaufsliste**. Wählen Sie diese unter **Zugriff** als Standard-Aufgabenliste. Der Agent liest den Mealie-Plan und die zugehörigen Rezepte, fasst gleiche Zutaten mit kompatiblen Einheiten zusammen und erstellt die fehlenden CalDAV-Aufgaben mit einer gemeinsamen Freigabe. Vorhandene offene Einträge bleiben erhalten; MatoLoop löscht oder synchronisiert die Liste nicht automatisch. Nicht sicher umrechenbare Einheiten bleiben getrennt.

## BookStack integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere BookStack-Installationen mit Serveradresse, Token-ID und Token-Secret anlegen. Ein API-Token erstellen Sie in BookStack im Benutzerprofil unter **API-Tokens**. Die Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. BookStack erfordert HTTPS.

Der Agent kann damit Seiten suchen und lesen, Seiten anlegen und bearbeiten sowie Bücher, Kapitel und Seiten anlegen, umbenennen und löschen. Zusätzlich kann er Regale auflisten, lesen, anlegen, bearbeiten und löschen. Neue Bücher lassen sich direkt einem oder mehreren Regalen zuordnen, und Buchlisten zeigen die zugehörigen Regale an. Name und Beschreibung eines Buchs oder Kapitels lassen sich nachträglich ändern; welchen Regalen ein Buch angehört, wird dagegen über das Regal selbst gepflegt. Inhalte werden als Markdown ausgetauscht.

An Seiten kann der Agent außerdem Anhänge verwalten. Eine Datei stammt dabei entweder aus dem Internet, aus dem Session- oder Workspace-Speicher, oder es wird nur ein Link hinterlegt. Downloads laufen über dieselbe Webfetch-Strecke wie der normale Webseitenabruf: Ihre Webfetch-Richtlinie gilt weiterhin, und bei Bedarf werden Sie zusätzlich zur Freigabe der Adresse gefragt. Ist Webfetch deaktiviert, bleibt nur der reine Link oder eine lokale Datei. Anhänge sind auf 32 MiB begrenzt.

Manche Anbieter sperren automatisierte Zugriffe. Schlägt ein Download fehl, nennt MatoLoop den tatsächlichen Grund, etwa eine Zeitüberschreitung oder eine Ablehnung durch den Server. Der Agent kann die Adresse dann als Link hinterlegen, statt einen falschen Grund zu vermuten.

Beim Bearbeiten wird eine Seite immer vollständig ersetzt. Der Agent liest sie deshalb unmittelbar vorher. Fehlt der Änderungszeitpunkt oder hat sich die Seite seitdem geändert, lehnt MatoLoop das Überschreiben ab und der Agent muss sie zuerst neu lesen. BookStack unterstützt jedoch kein bedingtes Speichern: Eine Änderung, die genau zwischen dieser Prüfung und dem Speichern erfolgt, kann weiterhin überschrieben werden. Nach einem erfolgreichen Speichern liest der Agent die Seite bei Bedarf erneut; ein reines Umbenennen verlangt keine vorherige Versionsprüfung. Sehr große Seiten meldet MatoLoop als Fehler, statt sie gekürzt zu liefern: Eine gekürzte Fassung könnte sonst versehentlich als vollständige Seite zurückgeschrieben werden.

Über **Zugriff** konfigurieren Sie zunächst den Standard der Installation und anschließend Abweichungen für einzelne Bücher:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen und Schreiben unterstützen alle drei Modi. Löschen unterstützt nur **Nicht erlaubt** und **Nachfragen**.

Suche, Bücherliste und Regale sind keine einzeln freigebbaren Ressourcen. Sperren Sie ein Buch jedoch über **Nicht erlaubt**, verschwindet es trotzdem aus der Suche und aus der Bücherliste, und der Agent kann es auch nicht mehr einem Regal zuordnen oder daraus entfernen. Ersetzt er die Bücher eines Regals vollständig, bleiben gesperrte Bücher unangetastet, statt stillschweigend herauszufallen. Ein gelöschtes Regal löscht die enthaltenen Bücher nicht. Kann das API-Token mehr als 500 Bücher sehen, wird die Ressourcenverwaltung abgelehnt, statt Richtlinien für nicht geladene Bücher zu übersehen. Anhänge folgen der Richtlinie des Buches, zu dem die Seite gehört; das Hochladen einer lokalen Datei verlangt zusätzlich dieselbe Freigabe wie ein direkter Dateizugriff. Berechtigungen des verwendeten API-Tokens in BookStack bleiben zusätzlich wirksam: Was das Token dort nicht darf, kann MatoLoop nicht freigeben.

## OpenSCAD integrieren

Der vorbereitete Docker-Computer enthält OpenSCAD bereits. Legen Sie unter **Einstellungen > Integrationen** eine OpenSCAD-Integration an, wählen Sie den Computer und verwenden Sie **Erkennen**, um den dort geprüften Binary-Pfad zu übernehmen. Ein eigener nativer Execution Agent benötigt OpenSCAD. Der Verbindungstest ist nur erfolgreich, wenn auch ein begrenztes Testrendering funktioniert.

Beschreiben Sie im Chat das gewünschte Modell mit seinen notwendigen Abmessungen und Randbedingungen. Wenn Sie einen Dateinamen nennen, verwendet der Agent diesen und ergänzt bei fehlender Endung `.scad`. Ohne Vorgabe wählt er einen kurzen beschreibenden Namen. Neue Dateien werden standardmäßig unter den Session-Dateien angelegt; einen Workspace verwendet der Agent auf ausdrücklichen Wunsch oder beim Bearbeiten eines dort vorhandenen Projekts.

Der Agent erzeugt den OpenSCAD-Quelltext selbst, rendert ihn und prüft Diagnosen, Abmessungen, Topologiedaten sowie beschriftete Ansichten von vorne, hinten, links, rechts, oben und unten. Nach jeder Vorschau führt ein bildfähiges Chatmodell selbst eine separate visuelle Prüfung durch. Bei einem reinen Textmodell übernimmt das konfigurierte Standard-Vision-Modell diese Prüfung mit bis zu sieben aktuellen Referenzbildern. Das bildfähige Chatmodell erhält das interne Ansichtsblatt anschließend weiterhin direkt im Agentenlauf. Ist kein geeignetes Vision-Modell verfügbar, bleibt der Render nutzbar und wird als visuell ungeprüft gekennzeichnet. Ein erfolgreicher Render, passende Außenmaße und eine unauffällige Sichtprüfung gelten allein nicht als Nachweis für verdeckte Geometrie, Passung, Wandstärke, Festigkeit oder Druckbarkeit. Erkennt der Agent einen bestätigten sichtbaren Fehler, korrigiert er den Quelltext und rendert erneut. Nach erfolgreichem Abschluss stehen die `.scad`-Quelldatei und eine interaktive 3D-Vorschau zur Verfügung. Eine STL-Datei erzeugt der Agent nur auf ausdrücklichen Wunsch. Unter **Control Center > Dateien** können Sie `.scad` zwischen Quelltext und 3D-Ansicht umschalten und STL explizit exportieren. 3D-Modelle lassen sich drehen, verschieben, zoomen und aus festen Perspektiven betrachten.

CAD-Rendering ist standardmäßig auf **Automatisch** gestellt. Unter **Zugriff** können Sie weiterhin **Nachfragen** oder **Nicht erlaubt** wählen. Datei- und Agent-Modus-Grenzen gelten unabhängig davon weiter. OpenSCAD läuft direkt mit den Rechten des Execution Agents. Das private Arbeitsverzeichnis, feste Aufrufparameter, Laufzeit- und Größenlimits sowie die Ausgabeprüfung sind keine Betriebssystem-Sandbox; OpenSCAD kann grundsätzlich auf weitere Dateien und Netzwerkziele zugreifen, die für den Agent erreichbar sind. Verwenden Sie für eine stärkere Trennung einen Container, eine virtuelle Maschine oder einen eigenen eingeschränkten Betriebssystembenutzer. Sehr große Projekte oder STL-Dateien werden abgelehnt beziehungsweise nur als Download angeboten.

## Webfetch konfigurieren

Webfetch besitzt einen eigenen globalen Schalter, eine Freigaberichtlinie, eine Liste erlaubter privater Ursprünge, einen User-Agent und eine maximale Antwortgröße. Modellbezogene Einstellungen können die Richtlinie einschränken oder überschreiben; globale Netzwerkgrenzen bleiben wirksam.
