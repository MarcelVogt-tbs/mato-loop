# Tools und Integrationen

## Tool-Rechte

Das globale Freigabeverhalten und eingebaute Profil-Presets konfigurieren Administratoren unter **Administration > Tools**. Erlaubte eigene Profilrechte stehen unter **Einstellungen > Tools**. Arbeitsanweisungen bleiben am Profil; Modellwahl gehört zu **Standardmodelle** und **Sessionvorlagen**, Persona-Zuordnungen zu **Personas**. Integrations- und Ressourcenrechte bearbeiten Sie an der Verbindung. Rechte haben getrennte Werte für private und öffentliche Modelle:

- **Deaktiviert**: Das Tool kann nicht aufgerufen werden. Der Agent kennt die installierte Fähigkeit weiterhin und kann bei einer passenden Anfrage erklären, wie sie aktiviert wird.
- **Nachfragen**: Vor Agent-Tool-Aufrufen ist eine Freigabe erforderlich.
- **Automatisch**: Zulässige Aktionen werden ohne einzelne Nachfrage ausgeführt.

Hostseitige Sicherheitsgrenzen gelten auch bei **Automatisch** weiterhin.

Computerwechsel, das Löschen von Session-Dateien und E-Mail-Senden/-Löschen folgen den konfigurierten Aktionsrechten. Unter **Dateisystem** steuert **Vertrauliche Pfade und mögliche Secret-Ausgabe** zusätzlich Zugriffe auf erkannte geschützte Computerpfade sowie Shell-Befehle, die Zugangsdaten ausgeben könnten. Das Verhalten folgt dem Freigabeverhalten beziehungsweise einer eigenen Aktionsregel. **Nachfragen** verlangt Zustimmung für die konkrete Aktion. **Deaktiviert** blockiert solche Zugriffe; **Automatisch** hebt nur diese zusätzliche Nachfrage auf. Aktionsrechte, Außenordnerrechte und technische Grenzen gelten weiter.

**Allgemein**, **Plan**, **Build** und **Sysadmin** sind mitgelieferte Profile mit geschützten Namen und Arbeitsabläufen. Administratoren können deren Tool-Einstellungen, Aktionsrechte und Integrationsvorgabe instanzweit durch eigene Werte anpassen. Entfernen eines eigenen Wertes stellt die mitgelieferte Vorgabe wieder her. Normale Benutzer können Built-in-Profile nicht direkt anpassen; sie erstellen bei Bedarf ein persönliches Profil auf dieser Basis. Dafür müssen persönliche Profile und Tool-Anpassungen erlaubt sein; Administratoren behalten ihre Ausnahme. Bei abgeschalteter Erlaubnis bleiben persönliche Werte gespeichert, werden aber ignoriert. Mindestens ein globales Profil muss aktiv bleiben.

Neue Chats verwenden den Startagenten ihrer Sessionvorlage. Plan blockiert erkannte Computer-Schreib- und Löschaktionen, Build erlaubt sie automatisch, Sysadmin fragt. **Zugriff außerhalb des Arbeitsverzeichnisses** betrifft Datei-Tools: Plan/Build fragen außerhalb nach, Sysadmin erlaubt den Bereich und prüft weiterhin die jeweilige Aktion. Shell folgt ihren eigenen Aktionsrechten ohne Workspace-Begrenzung. Bei eigenen Profilen entfernt **Alle eigenen Regeln entfernen** nach Bestätigung die Aktionsregeln einschließlich öffentlicher Werte aus dem Formular. Tool-Einstellungen bleiben erhalten. Erst **Speichern** übernimmt die Änderung dauerhaft. Historische Sessions bleiben bei deaktiviertem Profil lesbar, benötigen aber für neue Läufe ein aktives Profil. Abschaltungen, Remote-ACLs, harte Computer-, Eigentümer- und Delegationsgrenzen bleiben bindend.

**Administration > Tools** hat drei Tabs: **Einstellungen**, **Tool-Einstellungen** und **Berechtigungen**. Unter **Einstellungen** steht zuerst das Freigabeverhalten, danach die Freigabe persönlicher Tool-Anpassungen. Die beiden anderen Tabs verwenden dieselbe Profilauswahl; Auswahl und Formularentwurf bleiben beim Wechsel erhalten. **Berechtigungen** zeigt nur eigene Aktionsregeln, auch bei administrativen Anpassungen der mitgelieferten Profile, gegliedert nach **Toolgruppe > Tool > Aktion**. Gruppenweite Zusatzrechte stehen direkt unter der Gruppe. Gemeinsame Aktionen erscheinen bei allen zugehörigen Tools; Bearbeiten oder Entfernen ändert dieselbe Regel überall.

**Eigene Regel hinzufügen** öffnet zuerst die suchbare Auswahl **Tool oder Bereich**, mit der Toolgruppe als Zusatzangabe. Danach wählen Sie eine Aktion dieses Tools; eine einzige verfügbare Aktion wird vorausgewählt. Gruppenweite Rechte finden Sie unter **Zusatzrechte** der jeweiligen Gruppe. Der Dialog weist auf gemeinsam verwendete Aktionen hin. Bei aktiver Modelltrennung wählen Sie den Modellbereich und anschließend einen konkreten Wert: **Automatisch**, **Nachfragen** oder **Deaktiviert**. Bearbeiten und Entfernen erfolgen je Modellbereich an der jeweiligen Zeile. Entfernen stellt die Vererbung vom Freigabeverhalten beziehungsweise Basisprofil wieder her. **Keine eigene Regel** kennzeichnet einen erbenden Modellbereich. Technische Sperren werden gesondert angezeigt. Ausgeblendete öffentliche Werte bleiben bei ausgeschalteter Modelltrennung erhalten. **Übernehmen** aktualisiert das Seitenformular; erst **Speichern** speichert dauerhaft. Persönliche Tools zeigen nur eigene Profile.

**Tool-Einstellungen > Globaler Standard** zeigt alle Tools nach Toolgruppen mit Ein-/Aus-Schalter. Bei eingeschalteten Tools stehen darunter ihre technischen Einstellungen: Webfetch-Parameter, Medienzeitlimits und Shell-Präfixe. Ausschalten blendet die Einstellungen aus, erhält aber deren Werte und die Aktionsregeln.

Bei einem **Agentenprofil** zeigt dieser Tab nur ausdrücklich konfigurierte Einstellungen. **Eigene Einstellung hinzufügen** bietet Tool-Verfügbarkeit und beim Shell-Tool eigene Shell-Präfixe. Ein Profil kann ein global verfügbares Tool ein- oder ausschalten, auch abweichend von seiner Vorgabe. **Global deaktiviert** bleibt bindend und kann nicht durch das Profil aufgehoben werden. Bearbeiten ändert den eigenen Wert; Entfernen stellt die Vererbung wieder her. Zeitlimits und Webfetch-Parameter bleiben global. Einschalten eines Tools erteilt keine zusätzlichen Aktionsrechte: Ein Plan-Profil mit gesperrten Schreibaktionen erhält dadurch keine Schreibrechte.

Nur aufrufbare Tools erhalten ihr vollständiges Argumentschema. Deaktivierte oder wegen Profilrechten, Modellwahl oder fehlender Integration nicht aufrufbare Fähigkeiten erscheinen für den Agenten lediglich als kompakter Hinweis. Dieser Hinweis kann weder eine Aktion noch eine Freigabe auslösen.

Änderungen der Tool-Verfügbarkeit gelten beim nächsten Aktualisieren des Toolangebots. Bereits angebotene oder gestartete Aufrufe einer laufenden Modellrunde können noch ausgeführt werden. Ein Laufabbruch macht bereits erfolgte externe Änderungen nicht rückgängig.

### Freigabeverhalten {#globale-tool-vorgaben-anwenden}

**Administration > Tools > Einstellungen > Freigabeverhalten** legt fest, welche Aktionen der Agent standardmäßig automatisch ausführt und wann er um Erlaubnis fragt. Die Auswahl wird als Grundstandard gespeichert; eigene Aktionsregeln haben Vorrang. **Grundeinstellungen** enthält einen gleich benannten direkten Link zu diesem Tab.

- **Privat / Öffentlich:** getrennte Dropdowns bei aktiver Modelltrennung.
- **Alle Modelle:** bearbeitet bei ausgeschalteter Modelltrennung die privaten Werte. Öffentliche Werte bleiben gespeichert.
- **Info-Symbol:** öffnet diesen Abschnitt in einem neuen Tab; der Formularentwurf bleibt erhalten.
- Der gewählte Grundstandard bleibt auch mit eigenen Regeln sichtbar.

#### Welche Auswahl passt?

| Grundstandard | Normales Lesen | Sensibles Lesen | Schreiben | Löschen |
| --- | --- | --- | --- | --- |
| Alles bestätigen | Nachfragen | Nachfragen | Nachfragen | Nachfragen |
| Änderungen und sensible Zugriffe bestätigen | Automatisch | Nachfragen | Nachfragen | Nachfragen |
| Änderungen bestätigen | Automatisch | Automatisch | Nachfragen | Nachfragen |
| Nur Löschen bestätigen | Automatisch | Automatisch | Automatisch | Nachfragen |
| Alles automatisch ausführen | Automatisch | Automatisch | Automatisch | Automatisch |

Die Tabelle gilt für Aktionen ohne eigene Tool-Vorgabe. Websuche, Webseitenabruf und veröffentlichte Untertitel bringen **Automatisch** als Vorgabe mit, auch für öffentliche Modelle. **Alles bestätigen** bedeutet daher: Alles außer lesenden Zugriffen auf öffentlich verfügbare Ressourcen bestätigen. Eigene Berechtigungsregeln haben Vorrang. Beim Herunterladen bleibt das Speichern eine getrennte Schreibaktion. Die Einstellung **Nur öffentliche Zieladressen zulassen** begrenzt weiterhin die erreichbaren Ziele; ausdrücklich zugelassene interne Adressen werden ebenfalls ohne zusätzliche Nachfrage abgerufen, sofern keine eigene Regel dies anders festlegt.

- **Vorgabe privat:** Änderungen bestätigen.
- **Vorgabe öffentlich:** Alles bestätigen.
- **Änderungen bestätigen** erlaubt auch sensibles Lesen automatisch, fragt aber vor Schreiben und Löschen. Speziellere Kombinationen lassen sich über eigene Aktionsregeln festlegen.
- Beim Update bleiben ausdrücklich gespeicherte globale Aktionsregeln erhalten. Frühere implizite Tool-Defaults werden nicht als eigene Regeln nachgebaut; ohne eigene Regel gilt das neue Freigabeverhalten.

#### Was bedeuten die Kategorien?

- **Lesen:** beispielsweise Dateien lesen oder Informationen suchen.
- **Ändern:** beispielsweise Dateien schreiben, Automationen bearbeiten oder Secrets speichern.
- **Löschen:** ausdrücklich ausgewiesene Löschrechte für Dateien und Automationen.
- **Sensibles Lesen:** erkannte vertrauliche Dateien wie `.env`, mögliche Secret-Ausgaben sowie Variablen/Secrets abrufen oder verwenden. Das Speichern von Secrets zählt dagegen zu **Ändern**.

#### Grundstandard ändern

1. Den gewünschten Grundstandard im jeweiligen Dropdown auswählen. **Vorgabe** kennzeichnet die Ausgangseinstellung.
2. **Speichern** anklicken.

- Vorhandene eigene Aktionsregeln bleiben erhalten. Ohne eigene Regel gilt zuerst eine ausdrückliche Vorgabe der Toolaktion, andernfalls der gewählte Grundstandard. Das gilt auch für Integrationen und neu hinzukommende Aktionen.
- Zum Wiederherstellen der Vererbung entfernen Sie die jeweilige eigene Regel unter **Berechtigungen**.
- Profil- und Integrationsregeln, Shell-Präfixe und technische Tool-Verfügbarkeit bleiben erhalten.
- Beim Tabwechsel mit ungespeicherten Änderungen können Sie den Entwurf verwerfen oder abbrechen und zuerst speichern.

#### Geltungsbereich und Grenzen

- Die Tabelle beschreibt die gemeinsamen Vorgaben für Tools und Integrationen. Eigene Profil-, Verbindungs- und Ressourcenregeln können abweichen, beispielsweise automatische Schreibaktionen bei Build.
- Technische Sperren und die für eine Aktion zulässigen Berechtigungswerte gelten weiterhin.
- Ein automatisch erlaubter Shell-Befehl kann löschen. Die Kategorie **Löschen** erfasst keine beliebigen Löschwirkungen innerhalb anderer Aktionen.
- Die Erkennung sensibler Dateien und Ausgaben ist nicht vollständig; sie erkennt nicht jede vertrauliche Information.
- Einzelne bestehende Aktionen bündeln noch mehrere Vorgänge: etwa Erinnerungen speichern/vergessen oder persönliche Einstellungen ändern/löschen. Das Leeren einer Datei gilt derzeit ebenfalls noch als Schreiben. Die Kategorien ersetzen diese aufrufabhängigen Prüfungen nicht; der Schutz vertraulicher Pfade greift weiterhin auch bei Dateiänderungen.

Eigene direkte Oberflächeaktionen verwenden keine Modell-Freigabekarte, etwa in **Ablage**, **Computer-Dateien** oder bei einer Mikrofonaufnahme. Ihre Eigentümer- und technischen Grenzen gelten weiter.

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
- Browsersteuerung, E-Mail, Support (tbs.Desk), Docker-Verwaltung (Arcane) und Musik (SonoBin)
- Aufgabenlisten, Subagenten und geschützte Variablen-/Secret-Verwendung

Verfügbare Tools hängen von Profil, Session, Modellfähigkeiten, Standardmodellen, Verbindungen und Computeroperationen ab. Eine Katalogerwähnung einer deaktivierten Fähigkeit ist kein aufrufbares Tool.

## Globale und persönliche Integrationen

Gemeinsame Verbindungen und Rechte stehen unter **Administration > Integrationen**, persönliche unter **Einstellungen > Integrationen**. **Einstellungen > Profilzuordnungen** auf diesen Seiten steuert die Profil-Verfügbarkeit einzelner Verbindungen. Ohne eigene Regel gilt das globale **Freigabeverhalten** anhand der Aktionskategorie. Jede Verbindung kann eigene private/öffentliche Regeln festlegen; globale Aktionsregeln für eingebaute Tools sind dabei keine zusätzliche Obergrenze. Persönliche Verbindungen können zusätzlich Rechte und Verfügbarkeit je zugänglichem Profil überschreiben, auch für globale Profile und bei gesperrten persönlichen Agenten. CalDAV bietet Standardressourcen. Adapterfreigaben bestimmen erlaubte Verbindungstypen; persönliche Zugangsdaten bleiben eigentümergebunden.

Der aktuelle Einstellungsbereich bestimmt den Eigentümer neuer Verbindungen. Globale und persönliche Listen sind getrennt. Eigentümer können Verbindungen aktivieren/deaktivieren; technische Nichtverfügbarkeit wird gesondert gezeigt. Computergebundene Browser-/OpenSCAD-Verbindungen sind immer persönlich. MCP verwendet eigene Seiten mit demselben Eigentümer- und Berechtigungsmodell.

## OpenCode integrieren

Richten Sie OpenCode unter **Einstellungen > Integrationen** mit Basis-URL, Benutzername und optionalem Passwort ein. HTTP ist für ein vertrauenswürdiges lokales Netz zulässig; außerhalb davon sollten Sie HTTPS verwenden. Das Passwort wird verschlüsselt gespeichert und nicht wieder angezeigt. OpenCode kann nur persönlich konfiguriert werden und benötigt keinen MatoLoop Agent.

Erstellen oder öffnen Sie ein Projekt und wählen Sie anschließend im Control Center **OpenCode > OpenCode verbinden**. Wählen Sie dort den Server sowie das erkannte Projekt und Arbeitsverzeichnis. Die Zuordnung kann geändert oder getrennt werden, bis der erste Plan gespeichert wurde. Wählen Sie danach unter **OpenCode > Modelle** getrennte Analyse- und Ausführungsmodelle. Wenn ein Modell Varianten für den Reasoning-Aufwand anbietet, erscheint darunter **Denken (Analyse)** oder **Denken (Ausführung)**. **OpenCode-Standard** übernimmt die Vorgabe des Modells; andere Einträge gelten nur für das jeweilige Modell. Die Dateiansicht ist schreibgeschützt und zeigt den entfernten Baum, Vorschauen und Änderungen.

Bitten Sie den Agenten im Projektchat um Analyse und Planung. OpenCode untersucht das Repository schreibgeschützt; der MatoLoop-Agent erstellt daraus den gespeicherten Plan. Im Control Center können Sie Pläne auswählen und in die FIFO-Warteschlange einreihen. Es läuft höchstens eine Ausführung pro Projekt. Externe OpenCode-Aktivität oder ein unklarer Ausgang pausiert die Warteschlange. Prüfen Sie dann den tatsächlichen Arbeitsbaum, bevor Sie fortfahren. OpenCode-Freigaben erscheinen bei der Ausführung mit **Einmal erlauben**, **Immer erlauben** oder **Ablehnen**.

## Lokale Funktionen

Arbeitsbereiche, Prozesse, Shell-Befehle und statisch dafür vorgesehene lokale Integrationen laufen auf dem für die Session gewählten Computer. Eine lokale Integration erscheint nur, wenn sie diesem Computer zugeordnet ist, der Computer verbunden ist und die fest eingebaute Operation meldet. Ohne passenden Computer bleiben Chat, Websuche und andere Backend-Funktionen verfügbar. Es gibt keinen automatischen Wechsel, Backend-Fallback oder Wiederholungsversuch.

Jeder Computer besitzt zwei Dateirichtlinien mit **Automatisch**, **Nachfragen** oder **Nicht erlaubt**. Standardmäßig gilt im Home-Verzeichnis **Automatisch** und außerhalb **Nachfragen**. Diese Richtlinien steuern die ausdrückliche Ordnerwahl; **Nicht erlaubt** bleibt zusätzlich eine harte Grenze für Modellaktionen. Ob Datei-Tools außerhalb des aktuellen Arbeitsverzeichnisses automatisch zugreifen dürfen, bestimmt das Agentenprofil. Shell hat eigene Aktionsrechte ohne Workspace-Begrenzung. Zwei feste Schalter aktivieren oder blockieren Shell-Aktionen in der Home- und externen Computerzone; beide sind standardmäßig aktiviert. Alle erkannten Zielzonen müssen erlaubt sein. Eine unvollständige Analyse erfordert zusätzlich die externe Shell-Zone.

`shell_run` startet immer einen frischen Prozess im gewählten absoluten Sessionordner, unter Linux mit der konfigurierten Shell und unter Windows mit PowerShell. Persönlicher Terminalzustand wird nicht übernommen. Shell-Rechte gelten je Kategorie für den Computer, unabhängig von der Workspace-Begrenzung für Datei-Tools. Die Analyse unterscheidet erkannte Lese-, Schreib- und Löschaktionen; gemischte Befehle benötigen alle betroffenen Kategorien. Die Betriebssystemrechte des Agents gelten, ohne Sandbox. Unbekannte Programme, Tests und Builds können weiterhin als lesend eingestuft werden und trotzdem Nebenwirkungen haben. Das alte Feld `readOnly` verspricht keine nebenwirkungsfreie Ausführung.

Plan, Build und Sysadmin erlauben Shell-Lesen standardmäßig für beide Modellklassen automatisch; Ergebnisse können daher auch öffentliche Provider ohne Einzelfreigabe erreichen. Plan verbietet erkannte Schreib- und Löschaktionen, garantiert aber keine nebenwirkungsfreie Shell. Build erlaubt alle drei Shell-Kategorien; Sysadmin fragt bei Schreiben und Löschen nach. Presets bearbeiten oder zurücksetzen Sie unter **Administration > Tools**.

Ungültige Syntax beendet die Ausführung mit `process.invalidSyntax`; technische Grenzen für Befehlstext, Aufzählungen oder Ziele liefern `process.inspectionLimitExceeded`. POSIX `<>` wird unterstützt und auch für `/dev/null` als Schreiben eingestuft. Fehlende erforderliche Ziele machen die Analyse unvollständig, ihre Pfade bleiben jedoch erhalten; Symlink-, No-Follow- und Zugriffsfehler bleiben harte Fehler. Eine unvollständige Analyse benötigt weiterhin den Schalter für externe Shell-Aktionen und kann **Nicht erlaubt** für externe Dateizugriffe nicht umgehen. Gekürzte Befehlsmetadaten können spätere bekannte Schreibaktionen oder harte Pfade nicht verbergen.

Geprüfte Ausgaben auf das echte `/dev/null` und eng begrenzte installierte `ls`-Metadaten behalten ihre besonderen Klassifizierungs- und Revisionsprüfungen. Sie umgehen keine harten Computergrenzen. Eine normale Shell-Ausführung braucht keine zusätzliche Workspace-Freigabe für erkannte externe Werkzeug- oder Cache-Pfade. Die technische Entscheidung steht in der [Architektur im Repository](https://github.com/devmato/mato-loop/blob/develop/docs/architecture.md#matoloop-agent-boundary).

Aktualisieren Sie Backend und MatoLoop Agent gemeinsam, damit die getrennten Shell-Kategorien unterstützt werden. Fehlt eine benötigte Protokollfunktion, erscheint der allgemeine Agent-Aktualisierungshinweis; kompatible Dateiaktionen bleiben nutzbar. Bestehende explizite Shell-Schreibregeln werden einmalig auch als Löschregeln übernommen, sofern noch keine gesetzt sind. Historische Freigaben werden nicht erweitert. Neue Freigaben verwenden [Kategorien und Ressourcen](freigaben-und-datenfreigabe.md).

Globale Shell-Präfixe konfigurieren Sie unter **Tools > Tool-Einstellungen > Globaler Standard** beim eingeschalteten Shell-Tool über **Shell-Präfixe konfigurieren**, getrennt für Lesen/Schreiben mit einem Tokenpräfix pro Zeile. Bei eigenen Agentenprofilen legen Sie sie im selben Tab über **Eigene Einstellung hinzufügen** an. Ohne eigenen Eintrag gelten die geerbten Werte. Eine ausdrücklich leere eigene Liste bedeutet hingegen, keine Präfixe zu verwenden. **Abbrechen** oder Schließen verwirft den Dialogentwurf; **Übernehmen** aktualisiert das Formular. Erst **Speichern** auf der Seite speichert die Einstellungen dauerhaft. Jeder Teilbefehl muss passen. Präfixe können eine Aktionsrückfrage ersetzen, keine Abschaltung, Außenordner-, Shellzonen-, Deny- oder konfigurierte Rechte für vertrauliche Daten.

Git-Aktionen verwenden Shell-Befehle auf dem gewählten, verbundenen Computer mit installiertem Git. Es gibt keine eigene Git-Integration, Git-Konfigurationsseite oder Clone-Funktion im Backend. Git-Befehle des Modells folgen den Profilrechten, Präfixlisten, Session-Freigaben und harten Grenzen der Shell; automatisch erlaubte Befehle benötigen keine einzelne Freigabe. Konfigurieren Sie Git und die Anmeldung auf diesem Computer. Bestehende Installationen müssen die [Hinweise zur Bereinigung nach dem Upgrade](betrieb-und-datensicherung.md#removed-git-integration) beachten.

## Terminal im Control Center

**Terminal** ist eine Control-Center-App für gespeicherte Chats mit verbundenem Computer. **Agent-Ausgabe** zeigt Befehle und gefilterte Ausgabe, einschließlich gespeicherter jüngster Ergebnisse. **Terminal** öffnet auf unterstützten Linux-Agents eine persönliche Shell im Home. Eigene Befehle laufen außerhalb von Modellrechten mit den Betriebssystemrechten des Agents.

Agentenaufrufe bleiben frische Prozesse im Sessionordner. Appöffnung und Ansichtswechsel ändern das nicht. Persönliche Variablen, Aliase, Funktionen und `cd` werden nicht übernommen; `change_directory` ändert den Agentenordner dauerhaft. Abhängige Shellschritte gehören in denselben Aufruf oder erhalten explizite Umgebung.

Modellaufrufe verwenden Shellrichtlinien, Präfixe und Lauf-/Sessionrechte. Automatisch erlaubte Befehle brauchen keine zusätzliche Bestätigung. Rückfragen erscheinen im Chat, Hinweise verlinken dorthin. Unterstützte Vorab-Freigaben können ohne Befehlsausführung vorbereitet werden; vor echter Ausführung werden Befehl und Ziele erneut geprüft.

**Ablehnen** verwirft die angefragte Aktion. Der Chat-Stoppknopf beendet den Agentenlauf; **Strg+C** im persönlichen Terminal dessen Vordergrundprozess. Das Schließen der Ansicht stoppt keinen Agentenbefehl.

Reload, Panelzuklappen und Sessionwechsel erhalten die persönliche Shell. Der neueste berechtigte Viewer ersetzt den vorherigen. Tabentfernung, Shellende, Bindungsverlust und Backend-/Agentneustart beenden sie. Persönliche Ausgabe ist nur ein Arbeitsspeicherausschnitt. Agentenhistorie verwendet bis zu 50 passende gespeicherte Ergebnisse plus Liveausgabe, begrenzt auf 256 KiB; Replay führt nichts erneut aus.

## Integrationen anlegen und bearbeiten

Unter **Integrationen > Verwaltung** öffnet **Integration hinzufügen** einen kurzen Dialog für Integrationstyp, Anbieter und Name. **Weiter** öffnet die Konfigurationsseite, ohne bereits eine Verbindung anzulegen. Dort vervollständigen Sie Serveradresse, Zugangsdaten und die jeweils benötigten Einstellungen. Erst **Speichern** legt die Integration an und öffnet ihre Bearbeitungsseite.

Der Stift in der Integrationsliste öffnet dieselbe Seite für bestehende Verbindungen. Administration und persönliche Einstellungen verwenden denselben Aufbau mit den Tabs **Einstellungen** und **Berechtigungen**; der gewählte Bereich bestimmt weiterhin, wem die Verbindung gehört. **Speichern** im gemeinsamen Seitenkopf speichert den aktiven Tab. Tabwechsel erhalten ungespeicherte Eingaben in beiden Bereichen. Beim Verlassen können diese ausdrücklich verworfen werden. Leere Credential-Felder beim Bearbeiten erhalten die gespeicherten Zugangsdaten. Bei einer neuen Integration wird **Berechtigungen** erst nach dem ersten Speichern verfügbar.

**Verbindung testen** im Tab **Einstellungen** prüft die gespeicherte Konfiguration und zeigt das Ergebnis auf der Seite. Speichern Sie Änderungen deshalb vor dem Test. OpenSCAD-Erkennung, Browser-Einrichtung und **Browserprofil zurücksetzen** stehen in der jeweiligen Konfiguration. Die Liste bietet Aktivierung, Bearbeiten und Löschen mit Bestätigung. Bisherige Berechtigungslinks führen direkt zum Tab **Berechtigungen**.

### Integrationsrechte und eigene Profilregeln

Im Tab **Berechtigungen** wählen Sie **Standard für diese Integration** oder ein Agentenprofil. Beide Ansichten zeigen nur eigene Berechtigungsregeln. **Eigene Regel hinzufügen** öffnet die Auswahl **Bereich** (Integration oder Ressource), danach **Aktion**, bei aktiver Modelltrennung **Modellbereich** und schließlich **Berechtigung**. Der Wert ist immer **Automatisch**, **Nachfragen** oder **Deaktiviert**, soweit die Aktion ihn unterstützt. Bearbeiten und Entfernen erfolgen je Modellbereich; Entfernen stellt die Vererbung wieder her und erhält den anderen Modellbereich. Globale Verbindungen erlauben Administratoren eigene Regeln für globale Profile; persönliche Verbindungen erlauben ihrem Eigentümer Regeln für zugängliche globale und eigene Profile. Die Regeln betreffen nur diese Verbindung. **Übernehmen** aktualisiert das Formular; erst **Speichern** speichert dauerhaft.

Ressourcen erscheinen als aufklappbare Zeilen mit Verfügbarkeitsschalter. Beim Standard sind alle erkannten Ressourcen sichtbar, bei einem Profil nur Ressourcen mit eigenen Abweichungen. Im aufgeklappten Bereich stehen nur eigene Berechtigungsregeln. Ausschalten sperrt die Ressource im gewählten Bereich, ohne Regeln zu löschen. Beim Einschalten gelten die gespeicherten Regeln wieder. Profilabweichungen haben gegenüber dem gleichen Verbindungswert Vorrang; eine Ressourcenregel ist spezieller als ein allgemeines Aktionsrecht. Entfernte oder serverseitig gesperrte Ressourcen werden dadurch nicht zugänglich. Der Pfeil zum Zurücksetzen der Verfügbarkeit stellt die Vererbung wieder her. **Verfügbarkeit anpassen** legt beim Profil einen eigenen Wert für die ganze Verbindung fest. Der Instanzschalter für Public/Private gilt auch hier; ausgeblendete öffentliche Regeln bleiben erhalten.

Um beim Profil eine bisher nur geerbte Ressource ein- oder auszuschalten, wählen Sie **Eigene Regel hinzufügen**, die Ressource unter **Bereich** und **Verfügbarkeit** unter **Aktion**. Dadurch entsteht nur eine eigene Verfügbarkeitseinstellung; die Aktionsrechte bleiben geerbt.

Die allgemeine Vorgabe **Integrationen standardmäßig verfügbar** steht im Agentenprofil unter **Weitere Einstellungen**. Einzelne Verbindungen werden ausschließlich an der Verbindung konfiguriert.

Beim Update werden frühere Profilzuordnungen einmalig bereinigt:

- Unterstützte Zuordnungen werden an die Verbindung übertragen. Dort bereits konfigurierte Verfügbarkeits-Overrides haben Vorrang.
- Persönliche Zuordnungen zu globalen Verbindungen und Verweise auf entfernte Verbindungen werden gelöscht.
- Danach gelten für diese Profile wieder die Profilvorgabe und gegebenenfalls der Verbindungs-Override des Basisprofils. Eine Verbindung kann dadurch verfügbar werden oder wegfallen.
- Es gibt keine zusätzliche Altzuordnungsverwaltung. Die entfernten Werte werden bei einem Downgrade nicht wiederhergestellt.

## SearXNG integrieren

Erstellen Sie SearXNG-Verbindungen unter **Integrationen > Verwaltung** und wählen Sie Primär-/Fallback-Suche unter **Einstellungen** auf derselben Seite. Der persönliche oder administrative Bereich bestimmt den Geltungsbereich. Aktivieren Sie am SearXNG-Server die benötigte JSON-Schnittstelle und testen Sie die Verbindung.

Die eingebaute Suche ohne API-Schlüssel ist keine Integration und steht unabhängig von SearXNG zur Verfügung.

## CalDAV integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere CalDAV-Konten mit Serveradresse, Benutzername und Passwort oder App-Passwort anlegen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. CalDAV erfordert HTTPS.

Die **Berechtigungen** der Verbindung verwalten Konto-/Ressourcenrechte für **Private Modelle** und **Öffentliche Modelle**, bei ausgeschalteter Trennung für **Alle Modelle**. Die Auswahl bietet **Standard für diese Integration** oder ein zugängliches Profil. Je Ressource erscheinen nur passende Aktionen:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen, Schreiben und Löschen folgen dem Freigabeverhalten oder den eigenen Regeln. Serverseitig schreibgeschützte Kalender und Aufgabenlisten bleiben unabhängig von der MatoLoop-Richtlinie schreibgeschützt.

Im Bereich **Standardressourcen dieses Kontos** wählen Sie unabhängig von der Modellklasse einen **Standardkalender** und eine **Standard-Aufgabenliste**. Unspezifische Abfragen und neue Einträge verwenden die Auswahl des aktuellen Einstellungsbereichs automatisch. Wenn Sie ausdrücklich eine andere Ressource angeben, hat diese Vorrang. Pro Instanz- oder persönlichem Bereich kann jeweils nur ein Standard aktiv sein.

Lesefreigaben können für die aktuelle Chat-Session gelten. Schreib- und Löschfreigaben gelten nur für den konkreten Vorgang. Termine können bis zu zehn Erinnerungen erhalten, die in Minuten vor dem Terminbeginn angegeben werden. Wiederkehrende Termine und Termine mit Einladungs- oder unbekannten Eigenschaften können gelesen, aber in der ersten Version nicht verändert werden.

CalDAV-Aufgaben unterstützen Titel, Notiz, eine optionale Fälligkeit als Datum oder Zeitpunkt sowie den Status **offen** oder **erledigt**. Erledigte Aufgaben werden standardmäßig ausgeblendet und können ausdrücklich mit abgefragt werden. Änderungen, Statusänderungen und Löschungen betreffen genau eine Aufgabe. Der Agent kann außerdem bis zu 50 fehlende Aufgaben gesammelt erstellen; gleichnamige offene Aufgaben und Wiederholungen innerhalb des Aufrufs werden übersprungen. Wiederholungen, Unteraufgaben, Erinnerungen, Kategorien und Prioritäten können in der ersten Version nicht bearbeitet werden. Einkaufslisten werden wie normale CalDAV-Aufgabenlisten verwendet; abgehakte Einträge werden als erledigt markiert und nicht automatisch gelöscht.

## IMAP/SMTP-Mail integrieren

Unter **Einstellungen > Integrationen** können Sie Mail-Konten mit IMAP- und SMTP-Server, TLS-Modus, Benutzername, festem Absender und Passwort oder App-Passwort anlegen. Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. Unverschlüsselte Verbindungen werden nicht unterstützt.

An der Verbindung legen Sie private/öffentliche Kontorechte zum Lesen, Verwalten von Entwürfen, Senden und Löschen fest. Alle Aktionen unterstützen **Nicht erlaubt**, **Nachfragen** und **Automatisch**. Ohne eigene Regel gilt das Freigabeverhalten anhand der Aktionskategorie. IMAP-Ordner haben keine eigenen Rechte.

Sobald ein aktives Konto verfügbar ist, erscheint **E-Mail** im Control-Center-Launcher. Dort lesen Sie live Ordner/Nachrichten, laden Anhänge herunter, speichern Entwürfe und senden. Es gibt keine lokale Mail-Synchronisationsdatenbank; externe HTML-Inhalte werden nicht nachgeladen. Unklarer Versand wird nicht automatisch wiederholt; prüfen Sie zuerst den Mailserver.

## Mealie integrieren

Unter **Einstellungen > Integrationen** legen Sie eine Mealie-Verbindung mit Basis-URL ohne `/api` und langlebigem API-Token an. Den Token erstellen Sie im Mealie-Benutzerprofil unter **API-Tokens** oder direkt unter `/user/profile/api-tokens`. MatoLoop speichert ihn verschlüsselt und zeigt ihn nicht wieder an. HTTP ist für lokale Installationen zulässig; verwenden Sie HTTPS außerhalb eines vertrauenswürdigen Netzes.

Der Agent kann alle Rezeptseiten durchsuchen, nach exakten Tags, Kategorien oder strukturierten Lebensmitteln filtern, Zutaten und Zubereitung lesen sowie Rezepte erstellen, bearbeiten, per URL importieren und löschen. Essensplan-Einträge mit Rezept oder Freitext lassen sich ebenfalls lesen, erstellen, ändern und löschen. Rezeptänderungen erhalten nicht angegebene Felder; ausdrücklich übergebene Zutaten- oder Zubereitungslisten ersetzen die jeweilige Liste vollständig. Schreibaktionen brauchen standardmäßig eine Freigabe. Vor Rezeptänderung oder -löschung liest der Agent die Details; geänderte Rezept- oder Planeinträge machen alte Referenzen ungültig. Nach unklarem Schreibergebnis prüfen Sie zuerst Mealie: Bei einer manuellen Erstellung kann ein teilweise befülltes Rezept entstanden sein.

Für eine Einkaufsliste zum Wochenplan richten Sie eine CalDAV-Aufgabenliste wie **Einkaufsliste** ein und wählen sie unter **Standards** als Vorgabe. Der Agent liest Plan und Rezepte, fasst kompatible Mengen zusammen und erstellt fehlende Aufgaben mit einer gemeinsamen Freigabe. Bestehende offene Aufgaben bleiben erhalten; die Liste wird nicht automatisch gelöscht oder synchronisiert. Nicht sicher umrechenbare Einheiten bleiben getrennt. Native Mealie-Einkaufslisten werden nicht verwendet.

## BookStack integrieren

Unter **Einstellungen > Integrationen** können Sie mehrere BookStack-Installationen mit Serveradresse, Token-ID und Token-Secret anlegen. Ein API-Token erstellen Sie in BookStack im Benutzerprofil unter **API-Tokens**. Die Zugangsdaten werden verschlüsselt gespeichert und nie wieder angezeigt. BookStack erfordert HTTPS.

Der Agent kann damit Seiten suchen und lesen, Seiten anlegen und bearbeiten sowie Bücher, Kapitel und Seiten anlegen, umbenennen und löschen. Zusätzlich kann er Regale auflisten, lesen, anlegen, bearbeiten und löschen. Neue Bücher lassen sich direkt einem oder mehreren Regalen zuordnen, und Buchlisten zeigen die zugehörigen Regale an. Name und Beschreibung eines Buchs oder Kapitels lassen sich nachträglich ändern; welchen Regalen ein Buch angehört, wird dagegen über das Regal selbst gepflegt. Inhalte werden als Markdown ausgetauscht.

An Seiten kann der Agent außerdem Anhänge verwalten. Eine Datei stammt dabei entweder aus dem Internet, aus den verwalteten Session-Dateien oder von einem absoluten Pfad auf dem gewählten Computer; alternativ wird nur ein Link hinterlegt. Downloads laufen über dieselbe Webfetch-Strecke wie der normale Webseitenabruf: Das Webfetch-Recht des Profils gilt weiterhin, und bei Bedarf werden Sie zusätzlich zur Freigabe der Adresse gefragt. Ist Webfetch deaktiviert, bleibt nur der reine Link oder eine lokale Datei. Anhänge sind auf 32 MiB begrenzt.

Manche Anbieter sperren automatisierte Zugriffe. Schlägt ein Download fehl, nennt MatoLoop den tatsächlichen Grund, etwa eine Zeitüberschreitung oder eine Ablehnung durch den Server. Der Agent kann die Adresse dann als Link hinterlegen, statt einen falschen Grund zu vermuten.

Beim Bearbeiten wird eine Seite immer vollständig ersetzt. Der Agent liest sie deshalb unmittelbar vorher. Fehlt der Änderungszeitpunkt oder hat sich die Seite seitdem geändert, lehnt MatoLoop das Überschreiben ab und der Agent muss sie zuerst neu lesen. BookStack unterstützt jedoch kein bedingtes Speichern: Eine Änderung, die genau zwischen dieser Prüfung und dem Speichern erfolgt, kann weiterhin überschrieben werden. Nach einem erfolgreichen Speichern liest der Agent die Seite bei Bedarf erneut; ein reines Umbenennen verlangt keine vorherige Versionsprüfung. Sehr große Seiten meldet MatoLoop als Fehler, statt sie gekürzt zu liefern: Eine gekürzte Fassung könnte sonst versehentlich als vollständige Seite zurückgeschrieben werden.

An der BookStack-Verbindung konfigurieren Sie private/öffentliche Installationsrechte und anschließend Rechte einzelner Bücher:

- **Nicht erlaubt** blockiert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt zulässige Aktionen ohne einzelne Nachfrage aus.

Lesen, Schreiben und Löschen folgen dem Freigabeverhalten oder den eigenen Regeln.

Suche, Bücherliste und Regale sind keine einzeln freigebbaren Ressourcen. Sperren Sie ein Buch jedoch über **Nicht erlaubt**, verschwindet es trotzdem aus der Suche und aus der Bücherliste, und der Agent kann es auch nicht mehr einem Regal zuordnen oder daraus entfernen. Ersetzt er die Bücher eines Regals vollständig, bleiben gesperrte Bücher unangetastet, statt stillschweigend herauszufallen. Ein gelöschtes Regal löscht die enthaltenen Bücher nicht. Kann das API-Token mehr als 500 Bücher sehen, wird die Ressourcenverwaltung abgelehnt, statt Richtlinien für nicht geladene Bücher zu übersehen. Anhänge folgen der Richtlinie des Buches, zu dem die Seite gehört; das Hochladen einer lokalen Datei verlangt zusätzlich dieselbe Freigabe wie ein direkter Dateizugriff. Berechtigungen des verwendeten API-Tokens in BookStack bleiben zusätzlich wirksam: Was das Token dort nicht darf, kann MatoLoop nicht freigeben.

## OpenSCAD integrieren

Der vorbereitete Docker-Computer enthält OpenSCAD bereits. Legen Sie unter **Einstellungen > Integrationen** eine OpenSCAD-Integration an, wählen Sie den Computer und verwenden Sie **Erkennen**, um den dort geprüften Binary-Pfad zu übernehmen. Ein eigener nativer MatoLoop Agent benötigt OpenSCAD. Der Verbindungstest ist nur erfolgreich, wenn auch ein begrenztes Testrendering funktioniert.

Beschreiben Sie im Chat das gewünschte Modell mit seinen notwendigen Abmessungen und Randbedingungen. Wenn Sie einen Dateinamen nennen, verwendet der Agent diesen und ergänzt bei fehlender Endung `.scad`. Ohne Vorgabe wählt er einen kurzen beschreibenden Namen. Neue Dateien werden standardmäßig unter den Session-Dateien angelegt; einen absoluten Computerpfad verwendet der Agent auf ausdrücklichen Wunsch oder beim Bearbeiten eines dort vorhandenen Projekts.

Der Agent erzeugt Quelltext, rendert und prüft Diagnosen, Abmessungen, Topologie und sechs beschriftete Ansichten. Visuelle Prüfung verwendet das aktive bildfähige Modell oder **Standardmodelle > Bildanalyse**, mit bis zu sieben Referenzbildern. Ohne passenden Helfer bleibt der Render nutzbar, aber visuell ungeprüft. Vorschau-OFF und Ansichts-PNGs sind temporär; nur ausdrücklich exportierte STL-Dateien werden dauerhaft versioniert. Unter **Ablage** oder **Computer-Dateien** schalten Sie `.scad` zwischen Quelle und 3D-Ansicht um und exportieren explizit STL. Erfolgreicher Render und Sichtprüfung beweisen keine verdeckte Geometrie, Passung, Wandstärke, Festigkeit oder Druckbarkeit.

CAD-Rendering zählt als **Schreiben** und benötigt unter den ausgelieferten Grundstandards eine Freigabe. Das gewählte Freigabeverhalten oder eine eigene Verbindungs-/Profilregel kann es automatisch erlauben. Dateirichtlinien gelten unabhängig. OpenSCAD arbeitet mit Agent-Betriebssystemrechten, nicht in einer OS-Sandbox. Größere Projekte oder STL-Dateien können abgelehnt bzw. nur heruntergeladen werden; stärkere Isolation gehört zur Agent-Deploymentumgebung.

## Webfetch konfigurieren

Die gemeinsamen Einstellungen **Nur öffentliche Zieladressen zulassen**, **User-Agent** und **Maximale Antwortgröße** stehen unter **Administration > Tools > Einstellungen > Webabrufe und Downloads**. Sie gelten für Webseitenabrufe, Dateidownloads und Untertitelabrufe sowie URL-Imports über denselben Abrufweg, beispielsweise bei BookStack-Anhängen. Shell, Computer-Browser, Websuchdienste und konfigurierte Integrationsverbindungen haben eigene Zugriffsregeln. Beim Update werden diese drei Werte bewusst auf die Vorgaben zurückgesetzt: öffentliche Zieladressen, User-Agent `MatoLoop/1.0` und 64 MiB. JavaScript-Einstellungen, Tool-Aktivierungen und Berechtigungen bleiben erhalten.

Unter **Administration > Tools > Tool-Einstellungen > Globaler Standard** konfigurieren Administratoren die JavaScript-Unterstützung des eingeschalteten Webabrufs, ohne Engineauswahl. **Websites mit automatischem JavaScript** enthält einen exakten DNS-Hostnamen pro Zeile, ohne URL, IP-Adresse, Port oder Platzhalter; Vorgabe sind `reddit.com` und `www.reddit.com`. Es sind höchstens 100 Einträge erlaubt. Großschreibung, abschließende Punkte und doppelte Einträge werden beim Speichern normalisiert. Subdomains müssen separat eingetragen werden. Eine leere Liste verwendet standardmäßig HTTP; bei aktiviertem JavaScript kann das Modell die Auswahl weiterhin ausdrücklich ändern. Die Liste wird nicht automatisch gelernt. Kein Modus braucht einen Computer. Der Standard-User-Agent ist `MatoLoop/1.0`; JavaScript-HTML lehnt Werte mit `Mozilla` ab. Die aktive Adressbeschränkung gilt auch für JavaScript-Unteranfragen; Abschalten erlaubt jedes vom Backend erreichbare HTTP(S)-Ziel. Große Ergebnisse bleiben hinter einem abrufbaren Ergebnisverweis vollständig gespeichert.

**JavaScript-Konfiguration** beim Tool **Webseite abrufen** öffnet den Dialog. **JavaScript aktivieren** ist standardmäßig eingeschaltet. Ausschalten sperrt JavaScript für Webfetch vollständig, auch bei einer ausdrücklichen Modellanforderung. Die Hostliste wird ausgeblendet, bleibt aber gespeichert. Einschalten zeigt das Formular wieder. Die Liste steuert die automatische Auswahl und beschränkt nicht die erlaubten Websites. **Abbrechen** oder Schließen verwirft den Dialogentwurf; **Übernehmen** aktualisiert das Formular. Erst **Speichern** auf der Seite speichert die Einstellungen dauerhaft.

Das Recht **Webseiten abrufen** wird im Agentenprofil für private und öffentliche Modelle getrennt gespeichert. Technische Verfügbarkeit und die aktivierte Beschränkung auf öffentliche Adressen können durch kein Profil und keine Freigabe überstimmt werden.

## Browser integrieren

Legen Sie unter **Einstellungen > Integrationen** eine persönliche Browserverbindung für einen eigenen Linux-Computer an. Docker-Agents enthalten die Laufzeit; native Agents benötigen bei Bedarf **Browser einrichten** mit ausdrücklichem Setupstart. Abgelehnte Hostpakete können die Funktion unverfügbar lassen. Wählen Sie denselben Computer in der Session und aktivieren Sie die Verbindung in den Profilzuordnungen, wenn das Profil Integrationen standardmäßig ausschließt.

Container-Agents bieten **Control Center > Browser** mit Tabs, Adressfeld und manueller Steuerung. Native Agents automatisieren im Hintergrund ohne grafischen Viewer. **Agent verbinden** oder eine genehmigte Modell-Steuerungsanfrage gibt die Seite an den Agenten frei. **Manuell übernehmen** entzieht diese Steuerung; warten Sie auf das Ende einer schon laufenden Aktion. Ein Sessionwechsel lässt den ursprünglichen Browser bestehen und gibt der neuen Session keine Kontrolle. **Browser hier übernehmen** wechselt sie ausdrücklich.

Manueller Upload: erst das Website-Dateifeld anklicken, dann **Datei hochladen** aus Ablage/Computer-Dateien wählen. Bei fehlendem offenen Websitefeld bleibt **Datei einsetzen** für den erneuten Versuch. Downloads landen unter `homeDirectory/browser/downloads` ohne Überschreiben und sind über Computer-Dateien erreichbar. **Profil zurücksetzen** entfernt Website-Anmeldungen und Browserzustand, nicht veröffentlichte Downloads. Prüfen Sie dafür, dass Browser und Setup untätig sind.

Browserrechte unterscheiden Lesen, Schreiben, Code und Steuerung. Code kann ausdrücklich auch Dateien, Prozesse, Netzwerke und Browserprofildaten mit den Rechten des Browserprozesses erreichen; die normalen Shell-/Dateitool-Prüfungen untersuchen diesen Code nicht. Konfigurieren Sie Rechte und äußere Isolation entsprechend. Websiteprofile sind sensible Daten im Agent-Datenverzeichnis und gehören ins [Backup](betrieb-und-datensicherung.md#zu-sichernde-daten).

## tbs.Desk integrieren

Eine persönliche tbs.Desk-Verbindung benötigt Basis-URL, Benutzername und Passwort; verwenden Sie HTTPS außerhalb eines vertrauenswürdigen Netzes. Testen und aktivieren Sie die Verbindung. Sie stellt den **Support-Assistent** bereit, solange sie verfügbar ist. Der Agent sucht anhand eines Kundenhinweises oder aktiven Anrufs passende Kunden; wählen Sie den Kunden aus dem Ergebnis. Ticketzugriff wird auf diesen Kunden begrenzt. **tbs.Desk** im Control Center ergänzt die Sessionansicht. Bei fehlender Verbindung prüfen Sie Zugang und Adapterfreigabe; neu eingegebene Passwörter bleiben verschlüsselt.

## Arcane integrieren

Geben Sie Arcane-Manager-URL und API-Schlüssel in einer erlaubten gemeinsamen oder persönlichen Verbindung an. HTTPS ist bei nicht vertrauenswürdigem Netz erforderlich. Nach Verbindungstest wählen Sie unter Berechtigungen erlaubte Umgebungen und Lese-/Verwaltungs-/Löschrechte. Die Arcane-Rechte des Schlüssels bleiben bindend.

Der Agent kann Umgebungen, Container, Projekte, Images, Volumes und Netzwerke untersuchen und die angebotenen Lebenszyklusaktionen ausführen. Container-exec, beliebige Compose-/Secret-Inhalte und Massenbereinigung gehören nicht dazu. Bei unklarem Ergebnis einer Änderung prüfen Sie Arcane vor einer Wiederholung; MatoLoop wiederholt sie nicht automatisch.

## SonoBin integrieren

Die Musikverbindung benötigt SonoBins HTTPS-Basis-URL und einen persönlichen Bearer-Token aus dessen Kontoeinstellungen; `/mcp/` wird abgeleitet. Testen/Speichern erkennt den Tool-Katalog, ohne gespeicherte Rechte pauschal zurückzusetzen. Alle SonoBin-Tools teilen derzeit die Kategorie **Schreiben** und erben das Freigabeverhalten. Auch reine Leseaufrufe benötigen unter den ausgelieferten Grundstandards eine Freigabe, sofern keine eigene Regel sie automatisch erlaubt.

Suchen, Playlists, Likes und entfernte Imports folgen SonoBins Kontorechten. Administration, Diagnostik und Assetdownloads brauchen entsprechende SonoBin-Berechtigungen und Serverkonfiguration. Eine Playliständerung lädt keine Musik herunter. Bei einem Import verfolgen Sie den Auftragsstatus bis zum bestätigten Bibliotheksimport; fertiger Download allein beweist ihn nicht. Benötigte Rechte zeigt SonoBins Verbindungskontext.

## MCP-Server integrieren

Generische Server stehen unter **Einstellungen > MCP-Server** bzw. **Administration > MCP-Server**, nicht im Produktkatalog. Persönliche Server brauchen ihre administrative Freigabe. Verwenden Sie Streamable HTTP, mit keiner Anmeldung, verschlüsseltem Bearer-Token oder OAuth. HTTP überträgt Daten/Zugang unverschlüsselt; verwenden Sie HTTPS außerhalb eines vertrauenswürdigen Netzes.

Erstellen oder aktualisieren Sie die Verbindung und prüfen Sie die erkannten Tools. Server- und Toolrechte haben private/öffentliche Werte; persönliche Verbindungen können sie je Profil überschreiben. Externe Beschreibungen gewähren keine Rechte, und ein fehlgeschlagener Refresh erhält den vorherigen Katalog. Produktadapter wie SonoBin richten Sie in ihrem eigenen Bereich ein.

Die erkannten Tools stehen direkt in der Berechtigungsübersicht mit Anzeigename und gegebenenfalls abweichendem technischen Namen. Aufgeklappte Toolzeilen und der Dialog **Eigene Regel hinzufügen** zeigen die Beschreibung und Serverhinweise. Die Bereichsauswahl im Dialog durchsucht beide Namen. In der Standardansicht sehen Sie alle Tools, bei einem Agentenprofil nur eigene Abweichungen. Die Berechtigungszeilen zeigen jeweils nur eigene Regeln. MCP-Aufrufe teilen derzeit die Kategorie **Schreiben** und erben das Freigabeverhalten. Tool-Metadaten sind nicht verifizierte Serverangaben und bestimmen keine Berechtigungen.

Für OAuth muss der Server MCP-Authentifizierungsmetadaten und Bearer-Zugriff unterstützen; ein Browser-SSO allein reicht nicht. Prüfen Sie **Administration > Grundeinstellungen** auf den richtigen öffentlichen Ursprung ohne Pfad, öffnen Sie **Anmeldung öffnen**, bestätigen Sie beim Anbieter und beenden Sie die Anmeldung in MatoLoop. Ein vorregistrierter Client benötigt den Callback `<ursprung>/api/mcp/oauth/callback`. Bei abgelaufenem Versuch oder Backend-Neustart starten Sie erneut; gespeicherte Verbindungen bleiben erhalten. Nach abgelehnter Token-Erneuerung ist eine neue Anmeldung nötig, kein automatischer Aufruf-Replay.
