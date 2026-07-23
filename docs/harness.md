# Schlanker Agent-Harness

Dieses Dokument legt die Zielarchitektur für den schlanken, OpenCode-nahen Agent-Harness fest. Es ergänzt `docs/architecture.md`; bei der Implementierung gelten die hier beschriebenen Laufzeit-, Tool-, Permission- und Kontextgrenzen als verbindlich.

## Laufzeit und Coordinator

MatoLoop betreibt Runs und Freigaben über genau einen prozesslokalen Run-/Approval-Coordinator. Der Backend-Prozess läuft deshalb mit genau einem Worker. Der Coordinator besitzt die aktiven Run-Tasks und die Waiter für offene Freigaben. Er ist weder ein verteilter Scheduler noch eine Queue und verwendet keine prozessübergreifenden Locks.

- Ein Run wird nur von dem Coordinator fortgesetzt, der ihn gestartet hat.
- Eine Freigabeentscheidung darf nur einen noch lebenden Waiter dieses Coordinators auflösen.
- Mehrere Backend-Worker, parallele Coordinator-Instanzen oder die Übernahme eines Runs durch einen anderen Prozess werden nicht unterstützt.
- Persistente Run-Zustände dienen API, UI und Audit. Sie sind kein Checkpoint des Python-Stacks und keine ausführbare Queue.
- Geordneter Shutdown und Prozessabbruch dürfen keine zweite Tool-Ausführung auslösen.

Approvals und Session-Grants sind persistent. Daraus folgt ausdrücklich keine Restart-Resume-Garantie: Nach einem Backend-Neustart werden zuvor aktive oder wartende Runs terminal als unterbrochen beziehungsweise fehlgeschlagen markiert und ihre offenen Approvals verfallen. Eine Entscheidung für ein persistiertes Approval ohne lebenden Waiter liefert `approval.runInactive`; sie setzt den alten Run nicht fort und erzeugt keinen Grant. Der Benutzer startet bei Bedarf einen neuen Run. Bereits vor dem Neustart vollständig persistierte Session-Grants bleiben gültig.

Diese Einschränkung ist eine bewusste Grenze der ersten Harness-Version. Restart-Resume würde persistente Tool-Checkpoints, idempotente Ausführung, Leases und eine Wiederanlaufstrategie erfordern und darf nicht durch das bloße erneute Laden eines `AgentRun` simuliert werden.

Ein Benutzerabbruch ist eine explizite Run-Operation und wird nicht aus einem SSE-Disconnect abgeleitet. Der Cancel-Endpunkt fordert die Cancellation des prozesslokalen Tasks an und wartet auf dessen persistierten Terminalzustand. Wiederholte Cancel-Aufrufe dürfen den bereits laufenden Cleanup nicht erneut canceln. Bereits abgeschlossene Runs werden idempotent zurückgegeben. Der bereits erfolgreich als Text oder öffentliches Reasoning gestreamte Stand wird als unterbrochene Assistant-Nachricht persistiert; ohne sichtbaren Stand entsteht ein leerer Abbruchmarker. Nicht veröffentlichte Deltas, insbesondere verworfene Entwürfe einer verpflichtenden Websuche, bleiben ausgeschlossen. Bereits ausgeführte Tool-Wirkungen werden nicht rückgängig gemacht. Offene Approvals verfallen und unvollständige Tool-Calls erhalten ein terminales Fehlerergebnis, bevor `message.interrupted` und anschließend `run.cancelled` veröffentlicht werden. Ein geordneter Backend-Shutdown gilt nicht als expliziter Benutzerabbruch und erzeugt keinen solchen Nachrichtenmarker.

## Tool-Vertrag

Ein Tool ist eine Host-Fähigkeit mit einem stabilen Namen, einer Beschreibung, einem typisierten Eingabeschema und einer asynchronen Ausführung. Provider erhalten ausschließlich die von der Registry exportierten Schemas und melden normalisierte Tool-Calls zurück. Sie führen keine Tools aus.

Die Tool-Registry ist die einzige Namensauflösung für Tool-Calls. Sie muss doppelte Namen beim Aufbau ablehnen, Eingaben vor der Ausführung validieren und nur Tools exportieren, die für Session, Modus und effektive Integrationen verfügbar sind. Unbekannte Tools, ungültige Argumente und Ausführungsfehler werden in stabile Tool-Fehler übersetzt. Exception-Texte oder Secrets gelangen weder in den Modellkontext noch in API-Antworten.

Jede Ausführung erhält einen Host-Context mit mindestens Run-ID, aktueller Session-ID, Registry-unabhängigen Services und `Context.ask`. Tools greifen nicht direkt auf Coordinator-Waiter, Approval-Tabellen oder Providerobjekte zu. `Context.ask` nimmt eine typisierte Permission-Anforderung entgegen und führt zentral dieselbe Reihenfolge aus:

1. Unveränderliche Host-Policy prüfen.
2. Einen passenden persistenten Session-Grant prüfen.
3. Falls weiterhin nötig, ein persistentes Approval erzeugen und auf den prozesslokalen Coordinator warten.
4. `allow_once`, `allow_session` oder `deny` als typisiertes Ergebnis liefern.
5. Nur bei `allow_session` und einem lebenden Run den Grant in der Approval-Transaktion persistieren und anschließend den Waiter fortsetzen.

Ein Modellargument darf weder die Registry-Auswahl noch `Context.ask`, Capability-Grenzen oder Ressourcen-Matcher umgehen. Das Tool führt die angefragte Wirkung erst nach einem erlaubenden Ergebnis aus.

## Typisierte Ressourcen-Matcher

Eine Permission besteht aus Capability und typisiertem Ressourcen-Matcher. Sicherheitsentscheidungen dürfen nicht auf frei zusammengesetzten Anzeige-Strings, Präfixvergleichen oder Toolnamen beruhen. Ein Matcher besitzt einen eindeutigen Typ und dafür validierte Felder, beispielsweise einen kanonischen Pfad, eine Session-ID oder einen festgelegten Session-Suchbereich.

- Pfad-Matcher enthalten den expliziten Root `session` oder `workspace` und vergleichen relative Pfade komponentenweise innerhalb der hostseitig gebundenen Rootgrenze; Stringpräfixe sind unzulässig.
- Session-Matcher vergleichen UUIDs und unterscheiden die aktuelle Session von einer explizit benannten anderen Session.
- Session-Such-Matcher repräsentieren die Suche als eigene Ressource. Eine Suchanfrage ist nicht implizit durch einen Grant zum Lesen einer einzelnen Session erlaubt.
- URL-Matcher verwenden den SHA-256-Hash der vollständigen kanonischen URL. Anzeigeinformationen werden getrennt und ohne Querywerte persistiert.
- Persistierte Matcher müssen verlustfrei wieder in denselben typisierten Matcher deserialisiert werden.
- Approval-Anzeigeinformationen wie Titel, Zusammenfassung und Risikostufe sind Metadaten und nicht Teil der Match-Entscheidung.

Für `read_file` und `list_directory` bestimmt der Host die Rootgrenze vor der Permission-Prüfung. `root=session` ist immer an die aktuelle Session gebunden; `root=workspace` ausschließlich an das ausgewählte Arbeitsverzeichnis und ohne Auswahl unverfügbar. Agent-Dateitools folgen keinen Symlinks. Im Session-Root sind `attachments`, `generated`, `downloads` und `derived` lesbar und als Copy-Quelle erlaubt, aber unveränderlich; Mutationen sind nur unter `files` möglich.

Für `webfetch` wird jede initiale URL und jedes Redirectziel vor DNS-Auflösung und HTTP-Zugriff durch die effektive Modellpolicy bewertet. `ask` fordert jede URL an, `auto_public` erlaubt öffentliche Ziele automatisch und fragt für explizit allowlistete private Origins, `auto_allowed` erlaubt beide Zielklassen automatisch. Die Netzwerkklassifikation nach der Permission bleibt eine unveränderliche Hostgrenze. Kein Grant kann eine nicht allowlistete private, Link-Local- oder Metadatenadresse öffnen.

Tool-Attachments sind persistente Kerndaten der zugehörigen Tool-Message. Binärdaten werden atomar in den flachen, hostverwalteten Session-Verzeichnissen `attachments`, `generated`, `downloads` und `derived` gespeichert, gegen MIME-Typ, Magic Bytes, Decoder und Quota validiert und bei einer fehlgeschlagenen Transaktion bereinigt. API, SSE und kanonische Messageinhalte enthalten keine Base64-Daten oder physischen Storage-Pfade. Providerbilder entstehen nur in der flüchtigen Requestprojektion.

Die verbindliche Grundpolicy für Session-Tools lautet:

| Aktion | Ressource | Ergebnis ohne Grant |
| --- | --- | --- |
| Session-Historie lesen | aktuelle Session des Runs | `allow` |
| Session-Historie lesen | andere Session | `ask` |
| Sessions beziehungsweise Session-Inhalte durchsuchen | Session-Suchressource | `ask` |

`current-session read allow` ist eine enge Host-Regel und kein persistierter Wildcard-Grant. `other session read ask` und `session search ask` gelten auch dann, wenn das Modell eine bekannte Session-ID oder einen vermeintlich harmlosen Suchtext liefert. Ein geeigneter Session-Grant kann eine spätere identische Anforderung erlauben; er erweitert nicht automatisch seinen Matcher-Typ oder Geltungsbereich.

## Historie und Request-Kontext

Die kanonische Historie ist die persistente, normalisierte Folge der Session-Nachrichten einschließlich Assistant-Tool-Calls und zugehöriger Tool-Ergebnisse. Sie bleibt vollständig und in stabiler Reihenfolge abrufbar. Provider-Nutzdaten dürfen ergänzend persistiert werden, ersetzen aber nicht die normalisierte Historie.

Der Request-Kontext ist dagegen eine flüchtige Projektion für genau einen Modellaufruf. Er kann Systemanweisungen, freigegebene Memories, eine Compaction-Zusammenfassung und einen budgetierten Ausschnitt der kanonischen Historie enthalten. Kürzen, Umordnen oder providerspezifisches Konvertieren des Request-Kontexts verändert oder löscht niemals kanonische Nachrichten.

Tool-Call und Tool-Ergebnis bilden beim Schneiden des Kontexts eine unteilbare Gruppe. Kein Request darf mit einem verwaisten Tool-Ergebnis oder einem Tool-Call ohne Ergebnis beginnen, sofern der Call nicht gerade der aktive, noch auszuführende Call ist. Der zuletzt eingegangene Benutzerauftrag muss im Request-Kontext erhalten bleiben.

## Compaction

Compaction erzeugt eine persistente, gekennzeichnete Zusammenfassung eines abgeschlossenen Präfixes der kanonischen Historie. Die Zusammenfassung enthält eine eindeutige Abdeckungsgrenze, damit der Request-Builder genau die Zusammenfassung und nur die Nachrichten nach dieser Grenze auswählt. Sie ersetzt, editiert oder löscht die abgedeckten Nachrichten nicht.

Eine Zusammenfassung muss Ziele, relevante Fakten, getroffene Entscheidungen, offene Punkte, Tool-Wirkungen und Freigabeergebnisse bewahren. Unbestätigte Modellbehauptungen dürfen nicht als ausgeführte Tool-Wirkung dargestellt werden. Ein fehlgeschlagener Compaction-Versuch lässt die bisherige Historie und die zuletzt gültige Zusammenfassung unverändert.

Compaction wird anhand des geschätzten Eingabebudgets ausgelöst, nicht anhand einer festen Nachrichtenzahl. Bereits kompaktierte Präfixe dürfen bei wiederholter Compaction nicht unkontrolliert erneut in den Kontext aufgenommen werden. Die Implementierung muss deterministisch festlegen, welche gültige Zusammenfassung bei mehreren Generationen aktiv ist.

## Modell-Kontexteinstellungen

Jede Modellkonfiguration besitzt explizite Grenzen für Kontextfenster und maximale Ausgabe. Die Werte sind typisierte positive Tokenzahlen mit sicheren Defaults. Ein Modell- oder Providerargument darf diese Host-Grenzen nicht erhöhen.

Für einen Modellaufruf gilt:

```text
input_budget = context_window_tokens - max_output_tokens - safety_reserve_tokens
```

Systemanweisungen, Toolschemas, Memories, Zusammenfassungen und Historie zählen gemeinsam gegen `input_budget`. Der Request-Builder schätzt das Budget vor dem Provideraufruf, hält die Ausgabereserve frei und compactiert beziehungsweise kürzt nur die flüchtige Projektion. Reicht selbst der minimale Kontext nicht, endet der Run mit einem stabilen Kontextfehler statt mit einem übergroßen Providerrequest.

Provider-Metadaten können sinnvolle Defaults liefern. Explizite, validierte Modellsettings sind jedoch maßgeblich. Änderungen gelten für neue Modellrequests; sie schreiben weder alte Runs noch die kanonische Historie um.

## Abnahmetests

Die folgenden unabhängigen Testfälle sind bei Einführung der jeweiligen Symbole zu automatisieren. Bis dahin sind sie absichtlich keine importierenden oder übersprungenen Tests, damit die Testsuite keine noch nicht vorhandene API vorgibt.

| ID | Given | When | Then |
| --- | --- | --- | --- |
| `COORD-01` | Ein Coordinator mit einem lebenden Approval-Waiter | Die passende Entscheidung wird übergeben | Genau dieser Waiter wird einmal aufgelöst und der Run läuft weiter. |
| `COORD-02` | Ein persistiertes pending Approval ohne prozesslokalen Waiter | Eine Entscheidung wird übergeben | Die API liefert `approval.runInactive`, das Approval verfällt und es entsteht kein Grant. |
| `COORD-03` | Persistente Runs in `queued`, `running` und `waiting_approval` sowie ein bereits abgeschlossener Run | Der Backend-Prozess startet | Nur die aktiven Zustände werden terminal beendet; pending Approvals verfallen, abgeschlossene Runs und bestehende Grants bleiben unverändert. |
| `COORD-04` | Zwei gleichzeitige Entscheidungen für dasselbe Approval | Beide erreichen den Coordinator | Höchstens eine Entscheidung gewinnt; Tool und Grant werden höchstens einmal ausgeführt beziehungsweise angelegt. |
| `COORD-05` | Ein aktiver Run mit offenem Providerrequest oder Approval | Der Benutzer fordert den Abbruch einmal oder wiederholt an | Der Task wird einmal gecancelt, Cleanup und Terminalisierung laufen zu Ende und der Run veröffentlicht genau den persistierten Zustand `cancelled`. |
| `TOOL-01` | Zwei Tooldefinitionen mit demselben Namen | Die Registry wird aufgebaut | Der Aufbau schlägt deterministisch fehl. |
| `TOOL-02` | Ein registriertes Tool und schemawidrige Argumente | Die Registry führt den Call aus | Das Tool wird nicht betreten und ein stabiler Validierungsfehler entsteht. |
| `TOOL-03` | Ein Tool mit einer nicht vorab erlaubten Permission | Das Tool ruft `Context.ask` auf | Die Wirkung tritt erst nach `allow_once` oder `allow_session` ein; bei `deny` nie. |
| `TOOL-04` | Ein Modellargument versucht Capability oder Matcher zu überschreiben | Der Call wird validiert | Die Host-Permission bleibt unverändert. |
| `MATCH-01` | Ein Pfadgrant für `project/a` | Der Agent fordert `project/ab` an | Der Grant passt nicht. |
| `MATCH-02` | Ein serialisierter Matcher jedes unterstützten Typs | Er wird deserialisiert | Typ und Match-Semantik bleiben identisch. |
| `FILE-01` | Eine Read-only-Session mit Arbeitsverzeichnis | Der Agent liest eine Datei innerhalb dieses Verzeichnisses | Der Zugriff erfolgt ohne Approval. |
| `FILE-02` | Eine Session ohne ausgewählten Workspace | Der Agent verwendet `root=workspace` | Der Call endet mit `file.workspaceNotSelected`; ein Approval kann die fehlende Rootbindung nicht umgehen. |
| `FILE-03` | Ein Symlink liegt innerhalb eines Datei-Roots | Der Agent liest, kopiert oder verschiebt über den Symlink | Permission und Tool-Ausführung lehnen den Zugriff mit `file.symlinkForbidden` ab. |
| `FILE-04` | Eine mehrzeilige oder große UTF-8-Datei | `read_file` erhält `offset` und `limit` | Das Ergebnis enthält genau den nummerierten Ausschnitt und einen Hinweis auf eine mögliche Fortsetzung. |
| `FILE-05` | Eine Datei unter `session:attachments`, `session:generated`, `session:downloads` oder `session:derived` | Der Agent kopiert sie nach `session:files` oder in den Workspace | Copy ist erlaubt; Write, Move und Delete an der Quelle bleiben unveränderlich gesperrt. |
| `WEBFETCH-01` | Globale Standardpolicy `ask` | Das Modell fordert eine URL an | Vor Approval erfolgen weder DNS-Auflösung noch HTTP-Zugriff. |
| `WEBFETCH-02` | Policy `auto_public` und ein öffentliches Ziel | Das Modell ruft die URL ab | Das Ziel wird SSRF-sicher geprüft und ohne Approval abgerufen. |
| `WEBFETCH-03` | Ein Redirect auf eine andere URL | Der Fetch verarbeitet den Redirect | Das Redirectziel durchläuft erneut Policy, DNS-Prüfung und Adress-Pinning. |
| `WEBFETCH-04` | Ein nicht allowlistetes privates oder ein Metadatenziel | Das Modell fordert den Abruf an | Der Zugriff bleibt unabhängig von Grants und Modell-Override gesperrt. |
| `WEBFETCH-05` | Eine Antwort über 5 MiB oder ein nicht unterstützter MIME-Typ | Der Body wird gestreamt | Der Abruf endet mit einem stabilen Fehler, bevor der Inhalt persistiert wird. |
| `ATTACH-01` | Webfetch liefert ein gültiges unterstütztes Bild | Das Tool-Ergebnis wird persistiert | Message und Attachment-Metadaten werden gemeinsam gespeichert; die Datei liegt unter `downloads` der aktuellen Session. |
| `ATTACH-02` | Das aktive Modell unterstützt Bildeingaben | Der nächste Providerrequest wird gebaut | Das Bild wird flüchtig als Medienpart übertragen, aber weder als Base64 persistiert noch geloggt. |
| `ATTACH-03` | Das aktive Modell unterstützt keine Bildeingaben | Webfetch liefert ein Bild | Das Bild bleibt im Chat sichtbar und das Modell erhält nur einen Textmarker. |
| `SESSION-01` | Ein Run in Session A | Das Session-Read-Tool liest A | `Context.ask` erzeugt kein Approval. |
| `SESSION-02` | Ein Run in Session A ohne passenden Grant | Das Session-Read-Tool liest B | Es entsteht ein Approval mit einem exakten Session-Matcher für B. |
| `SESSION-03` | Ein Run mit einem Read-Grant für Session B | Das Session-Search-Tool sucht über Sessions | Der Read-Grant passt nicht und es entsteht ein Search-Approval. |
| `HISTORY-01` | Eine kanonische Historie mit Tool-Call und Tool-Ergebnis | Ein kleiner Request-Kontext wird gebaut | Die Datenbankhistorie bleibt unverändert und im Request gibt es keine verwaiste Tool-Nachricht. |
| `HISTORY-02` | Eine gültige Zusammenfassung bis Grenze N | Der nächste Request wird gebaut | Genau die Zusammenfassung plus Nachrichten nach N werden verwendet. |
| `COMPACT-01` | Eine Historie über dem Eingabebudget | Compaction gelingt | Eine Summary mit Abdeckungsgrenze wird persistiert; kanonische Nachrichten bleiben erhalten. |
| `COMPACT-02` | Eine Historie über dem Eingabebudget | Compaction scheitert | Historie und vorherige Summary bleiben unverändert; der Run endet sicher. |
| `CONTEXT-01` | Kontextfenster, Ausgabelimit und Sicherheitsreserve | Ein Request wird gebaut | System, Tools, Memories, Summary und Historie überschreiten zusammen nicht das Eingabebudget. |
| `CONTEXT-02` | Ein zu kleines Fenster für den Minimalkontext | Ein Request wird gebaut | Kein Provider wird aufgerufen und ein stabiler Kontextfehler wird geliefert. |

Die bereits vorhandene Restart-Grenze wird unabhängig in `apps/backend/tests/test_harness_restart_contract.py` geprüft. Registry, Session-Tools, Matcher, Compaction und Modell-Kontextsettings erhalten erst ausführbare Tests, sobald ihre öffentlichen Verträge implementiert sind.
