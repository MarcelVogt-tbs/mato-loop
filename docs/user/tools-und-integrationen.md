# Tools und Integrationen

## Tool-Rechte

Unter **Einstellungen > Agenten** konfigurieren Sie je Profil die Rechte für eingebaute Tools, Integrationen und einzelne Ressourcen getrennt für private und öffentliche Modelle. Dort liegen außerdem Modelle, Reasoning, Shell-Präfixe, Persona und Aufgabenanweisungen. Die üblichen Rechte sind:

- **Deaktiviert**: Das Tool kann nicht aufgerufen werden. Der Agent kennt die installierte Fähigkeit weiterhin und kann bei einer passenden Anfrage erklären, wie sie aktiviert wird.
- **Nachfragen**: Vor Agent-Tool-Aufrufen ist eine Freigabe erforderlich.
- **Automatisch**: Zulässige Aktionen werden ohne einzelne Nachfrage ausgeführt.

Hostseitige Sicherheitsgrenzen gelten auch bei **Automatisch** weiterhin.

**Allgemein**, **Plan**, **Build** und **Sysadmin** sind voneinander unabhängige geschützte Instanz-Basisprofile. Eigene Instanz- und persönliche Profile wählen genau eines davon als Basis. Jedes nicht angepasste Feld und jeder nicht angepasste Berechtigungseintrag übernimmt weiterhin den aktuellen Wert dieser Basis; es gibt keine globale oder persönliche Tool-Baseline außerhalb der Profile. Administratoren können persönliche Profile vollständig sperren.

**Allgemein** gilt für neue Sessions. **Plan** verhindert erkannte Schreibaktionen, **Build** lässt Schreiben innerhalb der wirksamen Grenzen automatisch zu und **Sysadmin** führt Leseaktionen automatisch aus, fragt aber vor Datei- und Shell-Änderungen nach. Die Profilberechtigung **Zugriff außerhalb des Arbeitsverzeichnisses** gilt gemeinsam für Datei-Tools und erkannte Shell-Pfade. Plan und Build fragen dort nach, Sysadmin liest dort automatisch; die jeweilige Aktionsberechtigung gilt zusätzlich. Ein technisch deaktiviertes Tool, eine technisch deaktivierte Integration und fehlende Rechte des externen Dienstes sind harte Grenzen, die kein Profil und keine Freigabe überstimmen kann. Dasselbe gilt für Vertrauens-, Pfad-, Computer- und Delegationsgrenzen. Der Webfetch-User-Agent, das Größenlimit und die Beschränkung auf öffentliche Adressen bleiben technische Instanzgrenzen.

Unter **Einstellungen > Tools** legen Administratoren die technische Verfügbarkeit eingebauter Tools, Webfetch-Netzwerkparameter und Medien-Zeitlimits fest. Diese Seite enthält keine Modelle, Rechte oder Shell-Präfixe.

Nur aufrufbare Tools erhalten ihr vollständiges Argumentschema. Deaktivierte oder wegen Profilrechten, Modellwahl oder fehlender Integration nicht aufrufbare Fähigkeiten erscheinen für den Agenten lediglich als kompakter Hinweis. Dieser Hinweis kann weder eine Aktion noch eine Freigabe auslösen.

Direkte Aktionen, die Sie selbst in der Oberfläche auslösen, verwenden keine Agent-Freigabekarte. Dazu gehören insbesondere Dateiaktionen unter **Control Center > Dateien** und das Diktat über die Mikrofonschaltfläche.

Der direkte Dateizugriff im Control Center ist an Ihr Konto und den ausgewählten MatoLoop Agent gebunden. Er umgeht absichtlich die Profilrechte für Modellaktionen und kann auf alle Pfade zugreifen, die dessen Betriebssystembenutzer erreichen kann. MatoLoop Agent verwendet Descriptor- und No-Follow-Prüfungen, ist aber keine Betriebssystem-Sandbox.

## Toolgruppen

MatoLoop bündelt Werkzeuge für:

- Dateien und Verzeichnisse
- Websuche und Webseitenabruf
- QR-, Bild-, Sprach-, Audio- und Transkriptionsaufgaben
- Lesen und Durchsuchen früherer Sessions
- Memory
- Persona und persönliches Profil
- Kalender und Aufgaben
- Rezepte und Essensplanung (Mealie)
- Wissensdatenbank (BookStack)
- 3D-Modellierung (OpenSCAD)
- Projektanalyse und Planausführung (OpenCode)

Welche Tools tatsächlich erscheinen, hängt zusätzlich vom gewählten Agentenprofil, den Session-Einstellungen, Modellfähigkeiten und den Profilmodellen ab.

## Instanz und persönliche Integrationen

Unter **Einstellungen > Integrationen** liegen die technische Verbindungskonfiguration und die Suchprovider-Auswahl. Die Seite **Standardressourcen** ist ausschließlich für CalDAV verfügbar und wählt Standardkalender und Standard-Aufgabenliste; Integrations- und Ressourcenrechte werden dagegen in Agentenprofilen gepflegt. Instanz-Integrationen und ihre externen Ressourcen sind bewusst gemeinsam: Alle Benutzer können sie entsprechend ihrem gewählten Profil verwenden. Persönliche Integrationen und Zugangsdaten bleiben dem jeweiligen Benutzer zugeordnet. Administratoren können persönliche Integrationen vollständig sperren und einzeln erlauben, welche fest eingebauten Typen persönlich konfiguriert werden dürfen: SearXNG, CalDAV, BookStack, Mealie, OpenSCAD und OpenCode. Konfigurationen können keine eigenen Tool-Schemas oder beliebigen ausführbaren Tools hinzufügen. Verbindungsänderungen und Löschungen entfernen ungültige Integrations- und Ressourcenrechte aus betroffenen Profilen.

Administratoren sehen den Umschalter **Persönlich** / **Instanz**. Andere Benutzer bearbeiten nur ihren persönlichen Bereich. OpenSCAD ist immer persönlich an einen Computer gebunden, der dem angemeldeten Benutzer gehört.

## OpenCode integrieren

Richten Sie OpenCode unter **Einstellungen > Integrationen > Persönlich** mit Basis-URL, Benutzername und optionalem Passwort ein. HTTP ist für ein vertrauenswürdiges lokales Netz zulässig; außerhalb davon sollten Sie HTTPS verwenden. Das Passwort wird verschlüsselt gespeichert und nicht wieder angezeigt. OpenCode kann nur persönlich konfiguriert werden und benötigt keinen MatoLoop Agent.

Erstellen oder öffnen Sie ein Projekt und wählen Sie anschließend im Control Center **OpenCode > OpenCode verbinden**. Wählen Sie dort den Server sowie das erkannte Projekt und Arbeitsverzeichnis. Die Zuordnung kann geändert oder getrennt werden, bis der erste Plan gespeichert wurde. Wählen Sie danach unter **OpenCode > Modelle** getrennte Analyse- und Ausführungsmodelle. Wenn ein Modell Varianten für den Reasoning-Aufwand anbietet, erscheint darunter **Denken (Analyse)** oder **Denken (Ausführung)**. **OpenCode-Standard** übernimmt die Vorgabe des Modells; andere Einträge gelten nur für das jeweilige Modell. Die Dateiansicht ist schreibgeschützt und zeigt den entfernten Baum, Vorschauen und Änderungen.

Bitten Sie den Agenten im Projektchat um Analyse und Planung. OpenCode untersucht das Repository schreibgeschützt; der MatoLoop-Agent erstellt daraus den gespeicherten Plan. Im Control Center können Sie Pläne auswählen und in die FIFO-Warteschlange einreihen. Es läuft höchstens eine Ausführung pro Projekt. Externe OpenCode-Aktivität oder ein unklarer Ausgang pausiert die Warteschlange. Prüfen Sie dann den tatsächlichen Arbeitsbaum, bevor Sie fortfahren. OpenCode-Freigaben erscheinen bei der Ausführung mit **Einmal erlauben**, **Immer erlauben** oder **Ablehnen**.

## Lokale Funktionen

Arbeitsbereiche, Prozesse, Shell-Befehle und statisch dafür vorgesehene lokale Integrationen laufen auf dem für die Session gewählten Computer. Eine lokale Integration erscheint nur, wenn sie diesem Computer zugeordnet ist, der Computer verbunden ist und die fest eingebaute Operation meldet. Ohne passenden Computer bleiben Chat, Websuche und andere Backend-Funktionen verfügbar. Es gibt keinen automatischen Wechsel, Backend-Fallback oder Wiederholungsversuch.

Jeder Computer besitzt zwei Dateirichtlinien mit **Automatisch**, **Nachfragen** oder **Nicht erlaubt**. Standardmäßig gilt im Home-Verzeichnis **Automatisch** und außerhalb **Nachfragen**. Diese Richtlinien steuern die ausdrückliche Ordnerwahl; **Nicht erlaubt** bleibt zusätzlich eine harte Grenze für Modellaktionen. Ob das Modell außerhalb des aktuellen Arbeitsverzeichnisses automatisch arbeiten darf, bestimmt dagegen sein Agentenprofil. Zwei feste Schalter aktivieren oder blockieren Shell-Aktionen im Home-Verzeichnis und außerhalb davon; beide sind standardmäßig aktiviert. Beim Prüfen eines Shell-Befehls müssen die Schalter für den aktuellen Ordner und alle erkannten Zielpfade aktiviert sein. Eine unvollständige Analyse erfordert zusätzlich den Schalter für externe Shell-Aktionen.

Ohne Terminal-Verbindung verwendet `shell_run` die konfigurierte Shell in einem isolierten Aufruf im gewählten absoluten Arbeitsordner. Mit Verbindung verwendet der Aufruf die laufende Bash und deren tatsächlichen Arbeitsordner. Beide Varianten laufen mit den Rechten des Betriebssystembenutzers des MatoLoop Agent und ohne Betriebssystem-Sandbox. `shell.write` bedeutet, dass eine bekannte Änderung erkannt wurde; andernfalls gilt der Befehl auch bei unvollständiger Analyse als `shell.read`. Unbekannte Programme, Skripte, Tests und Builds können trotzdem Nebenwirkungen haben. Das alte Protokollfeld `readOnly` und Bezeichnungen der Leseklasse versprechen keine nebenwirkungsfreie Ausführung. Diese Unsicherheit ist eine bewusst akzeptierte Abwägung für den Arbeitsablauf.

Die Shell-Rechte gehören zum Agentenprofil. **Plan**, **Build** und **Sysadmin** erlauben `shell.read` für beide Vertrauensklassen automatisch, sodass gelesene Ergebnisse ohne einzelne Freigabe an öffentliche Provider gelangen können. Plan gibt Pläne im Chat zurück und deaktiviert direkte Computeränderungen, Schreibzugriffe auf verwaltete Session-Dateien und bekannte `shell.write`-Aktionen. Seine Anweisungen verbieten absichtliche Änderungen und Shell-Umgehungen, garantieren aber keine nebenwirkungsfreie Planung. Build erlaubt Computer- und Shell-Schreibaktionen innerhalb der wirksamen Grenzen automatisch. Sysadmin untersucht Systeme selbstständig, benötigt aber für Dateiänderungen, Löschungen und bekannte `shell.write`-Aktionen eine Freigabe.

Ungültige Syntax beendet die Ausführung mit `process.invalidSyntax`; technische Grenzen für Befehlstext, Aufzählungen oder Ziele liefern `process.inspectionLimitExceeded`. POSIX `<>` wird unterstützt und auch für `/dev/null` als Schreiben eingestuft. Fehlende erforderliche Ziele machen die Analyse unvollständig, ihre Pfade bleiben jedoch erhalten; Symlink-, No-Follow- und Zugriffsfehler bleiben harte Fehler. Eine unvollständige Analyse benötigt weiterhin den Schalter für externe Shell-Aktionen und kann **Nicht erlaubt** für externe Dateizugriffe nicht umgehen. Gekürzte Befehlsmetadaten können spätere bekannte Schreibaktionen oder harte Pfade nicht verbergen.

Nur eine geprüfte statische Ausgabe auf das echte Gerät `/dev/null` und die eng begrenzte Suche nach installierten `ls`-Metadaten in den sechs festen `PATH`-Verzeichnissen können die normale Freigabe außerhalb des Arbeitsordners überspringen. Diese Ziele bleiben an die Prüfrevision und harte Grenzen gebunden. Die Suche ist flach und begrenzt, weder rekursiv noch ein Inhaltszugriff; dereferenzierende Optionen, Wrapper, beliebige Verzeichnis-Symlinks und `/usr` selbst sind nicht ausgenommen. Ein normaler Inhaltszugriff auf denselben Pfad behält immer die üblichen Prüfungen. Lesen, Schreiben, Bearbeiten, Patchen und Löschen über Datei-Tools bleiben unverändert. Den genauen Umfang einschließlich geprüfter Standardaliase für usr-merge und Verzeichnisfingerabdrücke definiert `docs/architecture.md`.

Aktualisieren Sie Backend und MatoLoop Agent gemeinsam, damit diese Shell-Semantik übereinstimmt. Protokollversion 1 erhält die zusätzlichen Zielarten und Fehler ohne Shell-bezogene Datenbankmigration. Ältere Agents bleiben konservativ; das Backend deutet deren Schreibeinstufung nicht als Lesen um. Bestehende Freigaben behalten ihre Klassenbindung und ihr JSON-Format, historische Freigabetexte werden nicht umgeschrieben.

Im Agentenprofil können für beide Klassen erlaubte Befehlspräfixe hinterlegt werden, jeweils ein Präfix pro Zeile. Die Prüfung erfolgt tokenbasiert und alle Teilbefehle einer Pipeline oder Liste müssen passen. Eine Session-Freigabe gilt für die angezeigten Präfixe auf demselben Computer, in demselben absoluten Arbeitsordner und nur in derselben Klasse. Deaktivierte Profilrechte, ausgeschaltete Shell-Zonen und nicht erlaubte Pfade können dadurch nicht überstimmt werden.

Git-Aktionen verwenden Shell-Befehle auf dem gewählten, verbundenen Computer mit installiertem Git. Es gibt keine eigene Git-Integration, Git-Konfigurationsseite oder Clone-Funktion im Backend. Git-Befehle des Modells folgen den Profilrechten, Präfixlisten, Session-Freigaben und harten Grenzen der Shell; automatisch erlaubte Befehle benötigen keine einzelne Freigabe. Konfigurieren Sie Git und die Anmeldung auf diesem Computer. Bestehende Installationen müssen die [Hinweise zur Bereinigung nach dem Upgrade](betrieb-und-datensicherung.md#removed-git-integration) beachten.

## Terminal im Control Center

**Terminal** ist eine integrierte Control-Center-App für eine gespeicherte Chat-Session mit einem ausgewählten, verbundenen Computer. Unter Linux startet sie eine interaktive Bash im Home-Verzeichnis des Agenten. Persönliche Befehle laufen direkt mit den Rechten des Betriebssystembenutzers des MatoLoop Agenten, außerhalb der Modellrichtlinien und Freigaben. Das Terminal ist keine Sandbox.

**Agent verbinden** übergibt dem Chat-Agenten exklusiv dieselbe Bash mit ihrem Arbeitsordner, ihrer Umgebung, ihren Variablen, Aliasen, Funktionen und ihrer Historie. Die Übergabe erfolgt erst an einer leeren Eingabeaufforderung und überschreibt keine persönliche Eingabe. Solange der Agent verbunden ist, ist die Tastatureingabe gesperrt; Live-Ausgaben bleiben sichtbar. Nach dem Trennen ist die vollständige interaktive Bash mit Tab-Vervollständigung und Historie wieder verfügbar. Nicht verbundene `shell_run`-Aufrufe bleiben isoliert. Der Shell-Arbeitsordner ist unabhängig vom Session-Arbeitsbereich der Datei-Tools.

Es gibt keinen separaten Terminal-Schalter für **Automatisch** oder **Bestätigen** und keinen bearbeitbaren Befehlsentwurf. Agentenaufrufe verwenden weiterhin die Shell-Rechte des Profils, Präfixlisten und Session-Freigaben. Automatisch erlaubte Befehle benötigen keine zusätzliche Bestätigung. Wenn eine Freigabe nötig ist, zeigen Chat und **Benachrichtigungen** den unveränderlichen Befehl und den Terminal-Arbeitsordner mit **Einmal erlauben**, **Für Session erlauben** und **Ablehnen**, soweit verfügbar. Der Befehl und seine geprüften Ziele werden unmittelbar vor der Ausführung erneut validiert.

**Ablehnen** verwirft einen wartenden Befehl. Während der Ausführung unterbricht **Strg+C** den Vordergrundprozess. Mit der Stopp-Schaltfläche im Chat wird der gesamte Lauf beendet. Das Trennen ist während eines wartenden oder laufenden Agentenbefehls gesperrt; ein beschäftigter Terminal-Tab kann nicht entfernt werden.

Das Einklappen des Control Centers, eine getrennte Browserverbindung, das Neuladen der Seite und ein Sessionwechsel lassen Shell und Verbindung ohne Leerlaufzeitlimit bestehen. Bricht eine zuvor bestehende Ansicht unerwartet ab, versucht sie automatisch, sich erneut mit derselben Shell zu verbinden. Ist diese Shell bereits beendet, startet der automatische Versuch keine neue; dafür bleibt **Neu verbinden** verfügbar. Die neueste berechtigte Ansicht übernimmt die Kontrolle; eine ältere Ansicht zeigt ihre Ablösung an und übernimmt nicht automatisch erneut. Das Entfernen des Terminal-Tabs, das Beenden der Shell, der Verlust der Anmelde-, Session- oder Computerbindung sowie ein Neustart von Backend oder Agent beenden die Shell; dasselbe gilt für einen Verbindungsabbruch des Agents. Eine bewusste neue Verbindung kann eine neue Shell starten, stellt aber weder den alten Arbeitsordner noch die Umgebung wieder her. Beim Wiederverbinden wird nur das begrenzte Ende der Rohausgabe wiederholt, ohne den Bildschirm zu löschen oder die Eingabeaufforderung neu zu zeichnen; dies ist weder ein exaktes Vollbild noch ein vollständiges Transkript. Die Oberfläche weist auf fehlende frühere Ausgaben hin. Kanonische Tool-Ergebnisse und Freigabedatensätze bleiben getrennt gespeichert.

## SearXNG integrieren

Unter **Einstellungen > Integrationen** können Sie zusätzliche SearXNG-Instanzen anlegen, aktivieren, bearbeiten, testen und löschen. Dort wählen Sie für den aktuellen persönlichen oder Instanzbereich auch den primären Suchprovider und optional einen Fallback.

Die eingebaute Suche ohne API-Schlüssel ist keine Integration und steht unabhängig von SearXNG zur Verfügung.

## CalDAV integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere CalDAV-Konten mit Serveradresse, Benutzername und Passwort oder App-Passwort anlegen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. CalDAV erfordert HTTPS.

Im Agentenprofil konfigurieren Sie für **Private Modelle** oder **Öffentliche Modelle** zunächst die Rechte des Kontos und anschließend die Rechte einzelner Kalender und Aufgabenlisten. An jeder Ressource erscheinen nur die dafür passenden Rechte:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen und Schreiben unterstützen alle drei Modi. Löschen unterstützt nur **Nicht erlaubt** und **Nachfragen**. Serverseitig schreibgeschützte Kalender und Aufgabenlisten bleiben unabhängig von der MatoLoop-Richtlinie schreibgeschützt.

Im Bereich **Standardressourcen dieses Kontos** wählen Sie unabhängig von der Modellklasse einen **Standardkalender** und eine **Standard-Aufgabenliste**. Unspezifische Abfragen und neue Einträge verwenden die Auswahl des aktuellen Einstellungsbereichs automatisch. Wenn Sie ausdrücklich eine andere Ressource angeben, hat diese Vorrang. Pro Instanz- oder persönlichem Bereich kann jeweils nur ein Standard aktiv sein.

Lesefreigaben können für die aktuelle Chat-Session gelten. Schreib- und Löschfreigaben gelten nur für den konkreten Vorgang. Termine können bis zu zehn Erinnerungen erhalten, die in Minuten vor dem Terminbeginn angegeben werden. Wiederkehrende Termine und Termine mit Einladungs- oder unbekannten Eigenschaften können gelesen, aber in der ersten Version nicht verändert werden.

CalDAV-Aufgaben unterstützen Titel, Notiz, eine optionale Fälligkeit als Datum oder Zeitpunkt sowie den Status **offen** oder **erledigt**. Erledigte Aufgaben werden standardmäßig ausgeblendet und können ausdrücklich mit abgefragt werden. Änderungen, Statusänderungen und Löschungen betreffen genau eine Aufgabe. Der Agent kann außerdem bis zu 50 fehlende Aufgaben gesammelt erstellen; gleichnamige offene Aufgaben und Wiederholungen innerhalb des Aufrufs werden übersprungen. Wiederholungen, Unteraufgaben, Erinnerungen, Kategorien und Prioritäten können in der ersten Version nicht bearbeitet werden. Einkaufslisten werden wie normale CalDAV-Aufgabenlisten verwendet; abgehakte Einträge werden als erledigt markiert und nicht automatisch gelöscht.

## IMAP/SMTP-Mail integrieren

Unter **Einstellungen > Integrationen** können Sie Mail-Konten mit IMAP- und SMTP-Server, TLS-Modus, Benutzername, festem Absender und Passwort oder App-Passwort anlegen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. Unverschlüsselte Verbindungen werden nicht unterstützt.

Im Agentenprofil legen Sie für jedes Konto getrennte Rechte für private und öffentliche Modelle zum Lesen, Verwalten von Entwürfen, Senden und Löschen fest. Lesen und Entwürfe unterstützen **Nicht erlaubt**, **Nachfragen** und **Automatisch**. Senden und Löschen erfordern immer eine einzelne Freigabe oder bleiben deaktiviert. Die Einstellung gilt für das gesamte Konto; IMAP-Ordner besitzen keine eigenen Rechte.

Sobald mindestens ein aktives Mail-Konto verfügbar ist, erscheint **Mail** im Control-Center-Launcher. Dort können Sie Ordner und Nachrichten öffnen, Anhänge herunterladen, Textentwürfe mit Anhängen speichern und E-Mails senden. Nachrichten werden live vom Mailserver geladen und nicht in einer lokalen Mail-Datenbank synchronisiert. Externe Bilder und andere Inhalte aus HTML-Mails werden nicht nachgeladen. Ein unklarer Versandstatus wird nicht automatisch wiederholt; prüfen Sie in diesem Fall zuerst den Mailserver.

## Mealie integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere Mealie-Installationen mit Basis-URL und langlebigem API-Token anlegen. Den Token erstellen Sie in Mealie unter Ihrem Benutzerprofil bei **API-Tokens**. MatoLoop speichert ihn verschlüsselt und zeigt ihn nicht wieder an. HTTP ist für lokale Installationen zulässig; verwenden Sie HTTPS, sobald die Verbindung ein nicht vertrauenswürdiges Netz durchquert.

Der Agent kann gespeicherte Rezepte suchen, Zutaten und Zubereitung lesen sowie Rezept- und Freitext-Einträge im Essensplan lesen, erstellen, ändern und löschen. Rezept- und Plan-Kennungen bleiben als verschlüsselte, an die aktuelle Integration gebundene Referenzen verborgen. Ändert sich ein gelesener Planeintrag vor einer Änderung oder Löschung, lehnt MatoLoop den veralteten Zugriff ab.

Für eine Anfrage wie „Erstelle mir eine Einkaufsliste für meinen Wochenessensplan“ benötigen Sie zusätzlich eine CalDAV-Aufgabenliste, zum Beispiel **Einkaufsliste**. Wählen Sie diese unter **Standards** als Standard-Aufgabenliste. Der Agent liest den Mealie-Plan und die zugehörigen Rezepte, fasst gleiche Zutaten mit kompatiblen Einheiten zusammen und erstellt die fehlenden CalDAV-Aufgaben mit einer gemeinsamen Freigabe. Vorhandene offene Einträge bleiben erhalten; MatoLoop löscht oder synchronisiert die Liste nicht automatisch. Nicht sicher umrechenbare Einheiten bleiben getrennt.

## BookStack integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere BookStack-Installationen mit Serveradresse, Token-ID und Token-Secret anlegen. Ein API-Token erstellen Sie in BookStack im Benutzerprofil unter **API-Tokens**. Die Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. BookStack erfordert HTTPS.

Der Agent kann damit Seiten suchen und lesen, Seiten anlegen und bearbeiten sowie Bücher, Kapitel und Seiten anlegen, umbenennen und löschen. Zusätzlich kann er Regale auflisten, lesen, anlegen, bearbeiten und löschen. Neue Bücher lassen sich direkt einem oder mehreren Regalen zuordnen, und Buchlisten zeigen die zugehörigen Regale an. Name und Beschreibung eines Buchs oder Kapitels lassen sich nachträglich ändern; welchen Regalen ein Buch angehört, wird dagegen über das Regal selbst gepflegt. Inhalte werden als Markdown ausgetauscht.

An Seiten kann der Agent außerdem Anhänge verwalten. Eine Datei stammt dabei entweder aus dem Internet, aus den verwalteten Session-Dateien oder von einem absoluten Pfad auf dem gewählten Computer; alternativ wird nur ein Link hinterlegt. Downloads laufen über dieselbe Webfetch-Strecke wie der normale Webseitenabruf: Das Webfetch-Recht des Profils gilt weiterhin, und bei Bedarf werden Sie zusätzlich zur Freigabe der Adresse gefragt. Ist Webfetch deaktiviert, bleibt nur der reine Link oder eine lokale Datei. Anhänge sind auf 32 MiB begrenzt.

Manche Anbieter sperren automatisierte Zugriffe. Schlägt ein Download fehl, nennt MatoLoop den tatsächlichen Grund, etwa eine Zeitüberschreitung oder eine Ablehnung durch den Server. Der Agent kann die Adresse dann als Link hinterlegen, statt einen falschen Grund zu vermuten.

Beim Bearbeiten wird eine Seite immer vollständig ersetzt. Der Agent liest sie deshalb unmittelbar vorher. Fehlt der Änderungszeitpunkt oder hat sich die Seite seitdem geändert, lehnt MatoLoop das Überschreiben ab und der Agent muss sie zuerst neu lesen. BookStack unterstützt jedoch kein bedingtes Speichern: Eine Änderung, die genau zwischen dieser Prüfung und dem Speichern erfolgt, kann weiterhin überschrieben werden. Nach einem erfolgreichen Speichern liest der Agent die Seite bei Bedarf erneut; ein reines Umbenennen verlangt keine vorherige Versionsprüfung. Sehr große Seiten meldet MatoLoop als Fehler, statt sie gekürzt zu liefern: Eine gekürzte Fassung könnte sonst versehentlich als vollständige Seite zurückgeschrieben werden.

Im Agentenprofil konfigurieren Sie getrennt für private und öffentliche Modelle zunächst die Rechte der BookStack-Installation und anschließend Rechte für einzelne Bücher:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen und Schreiben unterstützen alle drei Modi. Löschen unterstützt nur **Nicht erlaubt** und **Nachfragen**.

Suche, Bücherliste und Regale sind keine einzeln freigebbaren Ressourcen. Sperren Sie ein Buch jedoch über **Nicht erlaubt**, verschwindet es trotzdem aus der Suche und aus der Bücherliste, und der Agent kann es auch nicht mehr einem Regal zuordnen oder daraus entfernen. Ersetzt er die Bücher eines Regals vollständig, bleiben gesperrte Bücher unangetastet, statt stillschweigend herauszufallen. Ein gelöschtes Regal löscht die enthaltenen Bücher nicht. Kann das API-Token mehr als 500 Bücher sehen, wird die Ressourcenverwaltung abgelehnt, statt Richtlinien für nicht geladene Bücher zu übersehen. Anhänge folgen der Richtlinie des Buches, zu dem die Seite gehört; das Hochladen einer lokalen Datei verlangt zusätzlich dieselbe Freigabe wie ein direkter Dateizugriff. Berechtigungen des verwendeten API-Tokens in BookStack bleiben zusätzlich wirksam: Was das Token dort nicht darf, kann MatoLoop nicht freigeben.

## OpenSCAD integrieren

Der vorbereitete Docker-Computer enthält OpenSCAD bereits. Legen Sie unter **Einstellungen > Integrationen** eine OpenSCAD-Integration an, wählen Sie den Computer und verwenden Sie **Erkennen**, um den dort geprüften Binary-Pfad zu übernehmen. Ein eigener nativer MatoLoop Agent benötigt OpenSCAD. Der Verbindungstest ist nur erfolgreich, wenn auch ein begrenztes Testrendering funktioniert.

Beschreiben Sie im Chat das gewünschte Modell mit seinen notwendigen Abmessungen und Randbedingungen. Wenn Sie einen Dateinamen nennen, verwendet der Agent diesen und ergänzt bei fehlender Endung `.scad`. Ohne Vorgabe wählt er einen kurzen beschreibenden Namen. Neue Dateien werden standardmäßig unter den Session-Dateien angelegt; einen absoluten Computerpfad verwendet der Agent auf ausdrücklichen Wunsch oder beim Bearbeiten eines dort vorhandenen Projekts.

Der Agent erzeugt den OpenSCAD-Quelltext selbst, rendert ihn und prüft Diagnosen, Abmessungen, Topologiedaten sowie beschriftete Ansichten von vorne, hinten, links, rechts, oben und unten. Nach jeder Vorschau führt ein bildfähiges Chatmodell selbst eine separate visuelle Prüfung durch. Bei einem reinen Textmodell übernimmt das Vision-Modell des gewählten Agentenprofils diese Prüfung mit bis zu sieben aktuellen Referenzbildern. Das bildfähige Chatmodell erhält das interne Ansichtsblatt anschließend weiterhin direkt im Agentenlauf. Ist kein geeignetes Vision-Modell verfügbar, bleibt der Render nutzbar und wird als visuell ungeprüft gekennzeichnet. Ein erfolgreicher Render, passende Außenmaße und eine unauffällige Sichtprüfung gelten allein nicht als Nachweis für verdeckte Geometrie, Passung, Wandstärke, Festigkeit oder Druckbarkeit. Erkennt der Agent einen bestätigten sichtbaren Fehler, korrigiert er den Quelltext und rendert erneut. Nach erfolgreichem Abschluss stehen die `.scad`-Quelldatei und eine interaktive 3D-Vorschau zur Verfügung. Eine STL-Datei erzeugt der Agent nur auf ausdrücklichen Wunsch. Exportierte OpenSCAD-Dateien werden als verwaltete Dateien versioniert und von der Nachricht in ihrer exakten Version referenziert. Unter **Control Center > Dateien** können Sie `.scad` zwischen Quelltext und 3D-Ansicht umschalten und STL explizit exportieren. 3D-Modelle lassen sich drehen, verschieben, zoomen und aus festen Perspektiven betrachten.

CAD-Rendering steht in der statischen Ausgangsrichtlinie auf **Nachfragen**. Im Agentenprofil kann jede OpenSCAD-Integration stattdessen **Automatisch** oder **Nicht erlaubt** erhalten. Datei-Richtlinien gelten unabhängig davon weiter. OpenSCAD läuft direkt mit den Rechten des MatoLoop Agent. Das private Arbeitsverzeichnis, feste Aufrufparameter, Laufzeit- und Größenlimits sowie die Ausgabeprüfung sind keine Betriebssystem-Sandbox; OpenSCAD kann grundsätzlich auf weitere Dateien und Netzwerkziele zugreifen, die für den Agent erreichbar sind. Verwenden Sie für eine stärkere Trennung einen Container, eine virtuelle Maschine oder einen eigenen eingeschränkten Betriebssystembenutzer. Sehr große Projekte oder STL-Dateien werden abgelehnt beziehungsweise nur als Download angeboten.

## Webfetch konfigurieren

Administratoren schalten Webfetch unter **Einstellungen > Tools** ein oder aus. Dort konfigurieren sie außerdem den technischen User-Agent, die maximale Antwortgröße und **Nur öffentliche Adressen zulassen**. Diese Option ist standardmäßig aktiv und blockiert alle nicht öffentlichen Zieladressen. Wird sie deaktiviert, darf Webfetch jedes vom Backend erreichbare HTTP(S)-Ziel aufrufen, einschließlich privater, lokaler und Metadaten-Adressen.

Das Recht **Webseiten abrufen** wird im Agentenprofil für private und öffentliche Modelle getrennt gespeichert. Technische Verfügbarkeit und die aktivierte Beschränkung auf öffentliche Adressen können durch kein Profil und keine Freigabe überstimmt werden.
