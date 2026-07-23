# Architektur

Die Trust-, Memory- und Dreaming-Grenzen sind in [memory.md](memory.md) beschrieben.

MatoLoop ist ein providerneutrales Monorepo. FastAPI stellt REST- und SSE-Schnittstellen bereit; die Quasar-SPA konsumiert ausschließlich diese API.

## Schichten

- API: HTTP-Verträge, Authentifizierung und Dependency Injection
- Services: Transaktionen und Anwendungslogik
- Agent: providerneutraler Modell-/Tool-Loop und Host-Policies
- Providers: austauschbare LLM- und Suchtransporte
- Memory: Auswahl und Injektion freigegebener Erinnerungen
- Embedding: lokaler Infinity-Transport und asynchroner semantischer Memory-Index
- Persistence: SQLAlchemy-Modelle, Alembic-Migrationen und Settings-Dokumente
- Frontend: Darstellung, Benutzerinteraktion und Lokalisierung

## Konfigurationsgrenzen

Bootstrap- und Infrastrukturwerte kommen aus Environment-Variablen. Operative Einstellungen liegen als typisierte Pydantic-Dokumente in der PostgreSQL-Tabelle `settings`.

Additive Settings-Felder erhalten Pydantic-Defaults. Settings-Dokumente deklarieren einen globalen oder nutzerbezogenen Scope. Globale Dokumente verwenden `settings.user_id IS NULL`; nutzerbezogene Dokumente werden mit einer expliziten `user_id` geladen und gespeichert. Partielle Unique-Indizes sichern beide Namensräume getrennt ab. Background-Verarbeitung leitet den Nutzerscope aus der persistenten Session-Ownership ab und verwendet keinen impliziten Requestnutzer. API-Keys und OAuth-Tokens werden nicht im Settings-JSON gespeichert, sondern über IDs aus der getrennten Tabelle `credentials` referenziert. Die Credential-Nutzdaten sind verschlüsselt; der Fernet-Schlüssel wird bei der ersten Nutzung unter `/data/secrets/credential.key` erzeugt.

Infinity ist ein hostkontrollierter lokaler Infrastrukturtransport und kein auswählbarer LLM-Provider. Der Dienst ist nur im Compose-Netz erreichbar und berechnet mit einem fest gepinnten multilingualen Modell Query- und Memory-Embeddings. pgvector speichert diese abgeleiteten Daten transaktional neben den kanonischen Memories. Der Backendprozess bleibt bei Infinity-Ausfall verfügbar und degradiert auf PostgreSQL-FTS. Trust- und Retrieval-Filter liegen vor jeder Vektorbegrenzung in SQL; der lokale Embedding-Dienst darf beide Trust-Level verarbeiten, gibt aber keine Sichtbarkeit vor.

## Providergrenze

Provideradapter transportieren Modellnachrichten, Toolschemas, Tool-Choice, Textdeltas und Tool-Calls. Sie führen keine Tools aus und verwalten weder Sessions noch Dateiberechtigungen.

- `openai-codex` verwendet den für selbst gehostete Oberflächen geeigneten ChatGPT-Device-Code-Flow und den isolierten Codex-Responses-Transport. Der Login benötigt weder eine öffentliche Callback-URL noch einen lokalen Callback-Port.
- `openai-compatible` unterstützt Chat Completions und Responses API.
- Beide Adapter erfüllen denselben internen Providervertrag.
- Der direkte ChatGPT-Codex-Endpunkt ist keine öffentliche OpenAI-Platform-API und bleibt vollständig im Adapter gekapselt.

Technische Providerantworten werden serverseitig protokolliert und auf sichere Domainfehler wie `provider.authenticationFailed`, `provider.rateLimited` oder `provider.unavailable` abgebildet.

Provider und Modelle besitzen eine 1:n-Beziehung innerhalb der typisierten AI-Settings. OpenAI-kompatible Provider synchronisieren Modell-IDs serverseitig über `GET {base_url}/models`. ChatGPT Codex verwendet mit dem OAuth-Credential den privaten Katalog `GET https://chatgpt.com/backend-api/codex/models`; dieser Vertrag bleibt vollständig im Codex-Adapter gekapselt. Credentials verlassen dabei das Backend nicht. Externe Modell-IDs bleiben bei OpenAI-kompatiblen Providern opake Strings; Codex-Slugs erhalten den Adapter-Namespace `openai-codex/`. Neu entdeckte Modelle starten deaktiviert, während manuell ergänzte Modelle als expliziter Fallback dienen.

Jeder LLM-Provider besitzt ein verpflichtendes Vertrauenslevel `public` oder `private`. Ein Modell kann dieses Level in beide Richtungen überschreiben oder mit `null` vom Provider erben. Das Backend liefert neben dem gespeicherten Override immer den effektiven Wert. Beim Start eines Agent-Runs werden Modell-ID, Modellanzeigename, effektives Vertrauenslevel und effektive Pseudonymisierung persistent am Run gespeichert. Assistant-Nachrichten übernehmen den Modellanzeigenamen als Snapshot, damit der Chatverlauf auch nach einer Umbenennung oder Löschung der Modellkonfiguration nachvollziehbar bleibt. Änderungen an der Sessionauswahl oder Provider-/Modellpolicy beeinflussen deshalb erst den nächsten Run. User-, Assistant- und Toolnachrichten erhalten das Vertrauenslevel des erzeugenden Runs; eine Session merkt sich zusätzlich konservativ, ob jemals ein privater Run beteiligt war.

Für effektiv öffentliche Modelle ist die lokale Pseudonymisierung standardmäßig aktiv. Sie analysiert Text mit Presidio und dem lokalen deutschen spaCy-Modell, ersetzt erkannte Personen-, Orts-, Kontakt-, Zahlungs- und Secret-Daten durch zufällige requestgebundene Marker und übersetzt Textdeltas, Reasoning-Summaries sowie Toolargumente vor der lokalen Persistenz zurück. Mapping und Nonce existieren ausschließlich für den einzelnen Request im Arbeitsspeicher. Opaque Continuation-Daten werden in diesem Modus weder an den Provider zurückgesendet noch persistiert, weil ihr Inhalt nicht lokal geprüft werden kann. Bilder und binäre Dateidaten besitzen keine gesonderte inhaltliche Pseudonymisierung. Die Erkennung ist Best-Effort und keine Garantie, dass beliebige vertrauliche Aussagen oder Quellcode erkannt werden. Wird sie bewusst deaktiviert, geht der freigegebene Kontext unverändert an den öffentlichen Provider.

Discovery ersetzt keine lokale Konfiguration. Bereits bekannte Modelle behalten ihre interne UUID, Aktivierung und Runtime-Werte. Nicht mehr gemeldete Discovery-Modelle werden als `missing` markiert und aus der effektiven Modellauswahl entfernt, aber nicht gelöscht. Ein fehlgeschlagener Abgleich verändert den letzten gültigen Stand nicht. Das effektive Modell setzt globale AI-Aktivierung, aktiven Provider, aktives Modell, vorhandene Discovery-Verfügbarkeit und bei Codex eine bestehende Verbindung voraus.

Die AI-Settings definieren getrennte Standardmodelle für Agent-Runs, Kontextkomprimierung und Titelgenerierung. Eine Sessionauswahl überschreibt den Agent-Standard. Ohne Komprimierungsstandard verwendet die Compaction das Sessionmodell und zuletzt eine deterministische Zusammenfassung. Ohne Titelstandard verwendet die Titelgenerierung das Sessionmodell beziehungsweise den Agent-Standard; nach einem fehlgeschlagenen Modellaufruf bleibt der lokal aus der ersten Nutzernachricht gebildete Titel erhalten. Nicht verfügbare Standardreferenzen werden beim Ändern des Modellkatalogs entfernt.

## Integrationsgrenze

Integrationen sind konfigurierte Instanzen einer Fähigkeit und eines konkreten Anbieters. Die Websuche verwendet `integration_type=search`; aktuell steht dafür der Adapter `searxng` bereit. Jede Instanz besitzt eine stabile ID, einen Aktivstatus und typisierte anbieterspezifische Konfiguration. Weitere Suchadapter ergänzen denselben Vertrag, ohne den Agent-Kern zu ändern.

Pro Fähigkeit wird genau eine aktive Standardinstanz aufgelöst. Das Backend liefert zusätzlich den effektiven Zustand der Fähigkeit. UI-Schalter und Agent-Tools verwenden ausschließlich diesen effektiven Zustand; ohne aktive Standardinstanz kann Websuche weder für eine Session erzwungen noch dem Modell als Tool angeboten werden.

## Agent und Tools

Der Agent-Runner besitzt den providerneutralen Modell-/Tool-Loop. Dateisystem und SearXNG werden ausschließlich über interne Tools angesprochen. Tools werden niemals vom Provider ausgeführt.

Die Tool-Harness folgt dem OpenCode-Muster: Eine zentrale Registry verbindet stabilen Namen, kurze Modellbeschreibung, typisiertes Pydantic-Eingabemodell und asynchronen Handler. Jeder Handler erhält einen `ToolContext` mit Run und Session sowie `Context.ask` für Permission-Anforderungen. Der Runner kennt keine toolspezifischen Dispatch-Zweige. Unbekannte, nicht angebotene oder schemawidrige Tool-Calls werden als strukturierte Tool-Ergebnisse beantwortet.

`web_search` dient der Suche über eine konfigurierte SearXNG-Integration. Das davon unabhängige Tool `webfetch` ruft eine konkrete HTTP(S)-URL mit den Ausgabeformaten Markdown, Text oder HTML ab. Der Host begrenzt Antwort und Bildattachment auf 5 MiB, verarbeitet höchstens fünf Redirects und erzwingt eine Gesamtfrist von maximal 120 Sekunden. DNS-Ziele werden vor dem Verbindungsaufbau klassifiziert und für die Verbindung gepinnt. Öffentliche Ziele sind grundsätzlich erreichbar; private oder Loopback-Ziele ausschließlich über eine explizite Origin-Allowlist. Link-Local-, Metadaten-, Multicast-, reservierte und nicht spezifizierte Adressen bleiben gesperrt.

Sessions steuern Websuche mit `required`, `available` oder `disabled`. `available` bietet `web_search` im normalen Toolset mit automatischer Toolwahl an, `disabled` entfernt es vollständig. Bei `required` bleibt das vollständige effektive Toolset verfügbar, der Providerrequest verwendet jedoch `tool_choice=required`, bis im aktuellen User-Turn mindestens ein `web_search` erfolgreich abgeschlossen wurde. Andere Tool-Calls werden normal ausgeführt und erfüllen diese Pflicht nicht. Liefert das Modell stattdessen eine finale Textantwort, wird dieser Entwurf nicht persistiert oder gestreamt; ausschließlich im flüchtigen Retry-Kontext erhält das Modell den verworfenen Entwurf zusammen mit einem expliziten Runtime-Fehlerhinweis. Der Modellrequest wird höchstens zweimal semantisch wiederholt. Transport-Retries zählen nicht gegen dieses Budget. Nach erfolgreicher Suche wechselt der Loop zurück auf automatische Toolwahl; der gemeinsame Research-Vertrag bleibt jedoch aktiv. Fakten, benannte Empfehlungen und ihre Begründungen müssen durch `web_search`-Ergebnisse aus dem aktuellen User-Turn gestützt und mit deren URLs belegt sein. Memories, Summaries, Chatverlauf, Modellwissen und andere Tools gelten dabei nur als Kontext, nicht als Web-Evidenz. Fehlende Abdeckung führt zu einer weiteren Suche oder zum Weglassen der Aussage. Der Host prüft diese semantische Abdeckung nicht selbst und ergänzt fehlende Quellen nicht nachträglich.

Jeder normale Agent-Modellrequest enthält nach optionalen Instant-Memories einen aktuellen lokalen Zeitkontext in der konfigurierten `GeneralSettings.default_timezone`. Das eindeutige ISO-Datum enthält Uhrzeit und UTC-Offset und erklärt auch kleinen lokalen Modellen, dass der Zeitstempel relative Zeitangaben auflöst, Modellwissen dadurch aber nicht aktuell wird. Der Zeitkontext wird nicht in spezialisierte interne Modellaufrufe für Dreaming, Compaction oder Titelgenerierung übernommen.

Die globale Webfetch-Policy ist `disabled`, `ask`, `auto_public` oder `auto_allowed`. Modelle können diese Policy vollständig überschreiben, aber weder den globalen Tool-Schalter noch die Netzwerk-Allowlist umgehen. Im Ask-Modus verwendet `network.fetch` einen exakten SHA-256-Matcher über die vollständige kanonische URL. Persistierte Anzeige-URLs enthalten keine Querywerte; Redirects werden vor DNS und HTTP erneut autorisiert.

Bildantworten werden als messagegebundene Attachments unter `/data/attachments` gespeichert; die Datenbank enthält ausschließlich Metadaten und einen internen Storage-Key. Die Session-API liefert eine authentifizierte Download-URL, niemals den Storage-Key oder Base64. Nur validierte PNG-, JPEG-, WebP- und GIF-Dateien sind zulässig. Für bildfähige Modelle materialisiert der Providerdienst die Binärdaten ausschließlich flüchtig als Data-URL. Textmodelle erhalten einen Marker, während das Bild im Chat erhalten bleibt. Aktive Bilder reservieren ein konservatives Kontextbudget; Compaction übernimmt nur einen Bildmarker.

Jeder Dateipfad wird kanonisiert und auf `/data/home` begrenzt. Absolute Pfade und Pfade außerhalb des Agent-Homes werden blockiert. Bei lesenden Agent-Tools wird ein Symlink auf sein kanonisches Ziel innerhalb des Agent-Homes aufgelöst; Permission, Approval, Grant und Tool-Ausführung beziehen sich auf dieses Ziel und nicht auf den Alias. Tool-Ausnahmen werden nicht roh an Modelle oder API-Clients weitergegeben.

Agent-Runs sind persistent und verwenden SSE für Textdeltas, freigegebenes Reasoning, Freigabeereignisse und terminale Zustände. Nach einem Fehler wird ein sicherer Fehlercode persistiert und als `run.failed` veröffentlicht. Das sprachneutrale Ereignis `run.phase` beschreibt überprüfbare Runtime-Phasen wie Kontextaufbau, Compaction, Modellrequest, Tool-Ausführung und Finalisierung. Öffentliches Reasoning stammt ausschließlich aus dokumentierten Provider-Summaries, explizitem `reasoning_content` oder eindeutig markierten führenden Think-Blöcken. Es wird getrennt vom Antworttext persistiert und weder in Memory, Compaction noch `session_read` übernommen. Verschlüsseltes Provider-Reasoning bleibt privater Continuation-State, wird nie an das Frontend übertragen und nur bei identischem Provider-, Modell-, Credential-, Endpoint-, Trust- und Pseudonymisierungs-Scope wiederverwendet. Bei aktiver Pseudonymisierung wird es vollständig verworfen.

Assistant-Antworten bleiben als roher Markdown-Text persistent und werden erst im Frontend dargestellt. Der zentrale Renderer deaktiviert Raw HTML, beschränkt Links auf erlaubte Schemes und sanitisiert das erzeugte HTML vor der DOM-Ausgabe. Fenced Codeblöcke erhalten einen Copy-Button; ihr Quelltext liegt außerhalb des DOM in einer renderlokalen Liste und wird nur über einen Index referenziert. Externe Markdown-Bilder erzeugen vor einer expliziten Origin-Freigabe keinen Browserrequest; die Freigabe gilt nur für die konkrete Chat-Session innerhalb der laufenden Frontendinstanz. Danach prüft das Frontend jede konkrete URL durch Laden und Decodieren, bevor es das sichtbare Bild erzeugt. Lade-, Decode- und Timeout-Fehler bleiben als Platzhalter mit Retry sichtbar. Der Agent darf externe Bild-URLs nicht erfinden und verwendet, sofern verfügbar, `webfetch` für die exakte URL; erfolgreich abgerufene Bilder werden als validierte interne Tool-Attachments bevorzugt. Ausschließlich validierte interne Attachment-Routen werden automatisch geladen. Usertexte, Reasoning und Tool-Ergebnisse bleiben von dieser HTML-Rendergrenze getrennt.

Mathematische Notation in Assistant-Antworten wird lokal mit KaTeX aus Inline- und Block-LaTeX gerendert. Unterstützt werden Dollar- und LaTeX-Klammerdelimitierungen. KaTeX läuft ohne vertrauensabhängige HTML-, Link- oder Bildbefehle und mit begrenzter Makroexpansion sowie Elementgröße. Die Sanitizer-Allowlist enthält nur die benötigten HTML-, MathML- und SVG-Strukturen; Inline-Styles dürfen ausschließlich innerhalb eines von KaTeX erzeugten Teilbaums verbleiben. CSS und Schriften werden als lokale Build-Artefakte ausgeliefert.

Die Reasoning-Expansion ist standardmäßig geschlossen. Der zuletzt gewählte Offen-/Geschlossen-Zustand liegt im authentifizierten `UserPreferences`-Dokument und wird geräteübergreifend angewendet. Die Darstellung kennzeichnet öffentliche Reasoning-Inhalte als vom Modell bereitgestellt und nicht als garantiert vollständigen internen Gedankengang.

Run-Tasks und Approval-Waiter sind bewusst prozesslokal. Der Backendprozess läuft mit einem Worker. Bei einem Neustart werden aktive Runs sicher beendet und offene Approvals ungültig; eine Wiederaufnahme oder verteilte Worker-Queue ist nicht Bestandteil dieser Ausbaustufe. Details und Abnahmekriterien stehen in `docs/harness.md`.

## Arbeitsverzeichnisse und Freigaben

Sessions besitzen optional ein Arbeitsverzeichnis und den Modus `read-only` oder `read-write`. Ohne Arbeitsverzeichnis existiert kein pauschaler Dateizugriff.

Im Lesemodus erhält das Modell ausschließlich lesende Dateitools. Bei einer Änderungsanforderung weist es auf den Wechsel von Agent-Modus **Lesen** zu **Schreiben** hin, statt die Fähigkeit generell zu verneinen. Im Schreibmodus stehen zusätzlich schreibende Dateitools bereit. Ohne Arbeitsverzeichnis erzeugt jeder noch nicht über einen Session-Grant abgedeckte Dateizugriff eine Freigabeanfrage; der Schreibmodus darf deshalb auch ohne Arbeitsverzeichnis gewählt werden. Mit Arbeitsverzeichnis sind im Lesemodus nur Lesezugriffe und im Schreibmodus auch Schreibzugriffe innerhalb dieses Pfads ohne Rückfrage erlaubt.

`read_file` liest UTF-8-Text zeilenweise mit 1-basiertem `offset`, begrenztem `limit` und Zeilennummern. Große Dateien werden portionsweise gelesen, überlange Einzelzeilen begrenzt und eine mögliche Fortsetzung wird im Tool-Ergebnis ausgewiesen. `list_directory` verwendet dieselbe kanonische Lese- und Permission-Policy.

Separate Session-Grants können einzelne Capabilities und Pfade freigeben, ohne das Arbeitsverzeichnis zu verändern. Nicht bereits erlaubte Aktionen erzeugen persistente Approval-Requests. Der Agent-Run wartet auf `allow_once`, `allow_session` oder `deny`; die Anfrage bleibt als Inline-Karte im Chat sichtbar, bis eine Entscheidung getroffen wurde.

Permissions verwenden ressourcentypspezifische Matcher. Pfade werden kanonisch und komponentenweise geprüft, URL-Zugriffe vergleichen exakte Hashes vollständiger kanonischer URLs, Sessionzugriffe vergleichen exakte UUIDs und Session-Suchen besitzen einen eigenen Collection-Scope. `session_read` darf die aktuelle Session ohne Approval lesen; eine andere Session benötigt einen exakten `session.read`-Grant oder eine Freigabe. `session_search` benötigt unabhängig davon `session.search`.

## Kontext und Compaction

Die vollständige Message-Historie bleibt kanonisch in der Datenbank. Vor jedem Modellrequest erzeugt der Context-Manager daraus eine flüchtige Projektion aus Systemprompt, effektiven Memories, optionaler Summary und jüngstem Originalverlauf. Private Memories werden nie an effektiv öffentliche Modelle übergeben. Dasselbe gilt für private Nachrichten, die über `session_read` oder `session_search` in ein Tool-Ergebnis gelangt sind; normale Chatnachrichten bleiben dagegen Bestandteil der Historie und werden bei aktivierter Public-Pseudonymisierung lokal transformiert. Tool-Call und Tool-Ergebnis werden als zusammengehörige Gruppe erhalten; unterbrochene Calls erhalten ein terminales Fehlerergebnis und werden nicht erneut ausgeführt.

Kontextfenster, maximale Ausgabe, Zeichen-pro-Token-Schätzung sowie Tool-, Parallel- und Streamingfähigkeit sind pro Modell konfigurierbar. Das Eingabebudget reserviert Ausgabe- und Sicherheits-Tokens. Bei Überschreitung wird ein abgeschlossener älterer Präfix zusammengefasst und als `context_summaries`-Generation persistiert. Das globale Komprimierungsmodell fällt auf das aktive Modell und zuletzt auf eine gekennzeichnete deterministische Zusammenfassung zurück. Originalnachrichten werden nie verändert oder gelöscht und bleiben über `session_read` abrufbar.

Direkt nach dem Persistieren einer neuen Session starten der Agent-Run und ein separater, toolfreier Modellaufruf für einen kurzen Sessiontitel parallel. Der Agent-Task wird zuerst eingeplant; der Titel benötigt ausschließlich die erste Nutzernachricht und blockiert den Run nicht. `chat_sessions.title_generation_status` persistiert den Lifecycle `pending`, `running`, `completed`, `failed` oder `skipped`; `pending` wird zusammen mit Message und Run atomar als `running` committed. Nur dieser Zustand und ein unveränderter lokaler Ausgangstitel erlauben das abschließende Update. Eine manuelle Umbenennung setzt `skipped` und gewinnt daher auch bei einem gleichzeitig laufenden Titelrequest. Erfolgreiche Titel werden über das Run-SSE als `session.title.updated` veröffentlicht; das Frontend-Polling bleibt als Reconnect-Fallback bestehen. Überlange erste Ausgabezeilen werden auf 80 Zeichen begrenzt. Leere beziehungsweise reine Reasoning-Ausgaben erhalten genau einen Reparaturversuch; dafür steigt das begrenzte Ausgabelimit von 512 auf 1024 Tokens, damit Reasoning-Modelle vor dem Limit noch eine sichtbare Titelzeile erzeugen können. Reasoning selbst wird nie als Titel verwendet. Providerfehler, weiterhin ungültige Antworten, Taskabbrüche und beim Backendstart gefundene verwaiste `running`-Zustände enden in `failed`; bestehende Sessions wurden bei Einführung als `skipped` übernommen.

## Fehlervertrag

API-Fehler enthalten ausschließlich einen stabilen, sprachneutralen Code und optionale strukturierte Daten:

```json
{
  "error": {
    "code": "validation.tooShort",
    "field": "password",
    "params": {
      "minLength": 10
    }
  }
}
```

Verbindlich sind:

- `error.code`: stabiler Fehlercode für Programmablauf und Übersetzung
- `error.field`: optionales betroffenes Eingabefeld
- `error.params`: optionale Werte für eine dynamische Meldung
- HTTP-Status: allgemeine Fehlerklasse und Fallback

Das Backend liefert keine nutzerseitigen Fehlertexte. Pydantic-Details, Exception-Texte und externe Providerantworten werden nicht an Clients weitergereicht.

Das Frontend übersetzt Codes zentral in `apps/frontend/src/utils/api-errors.js`. Axios-Fehler werden über einen Response-Interceptor als negative Quasar-Toasts angezeigt. SSE- und OAuth-Fehler außerhalb normaler HTTP-Fehler verwenden denselben Formatter. Unbekannte Codes fallen auf eine sichere Meldung anhand des HTTP-Status zurück.

Eine spätere i18n-Lösung kann dieselben Codes und Parameter verwenden. Der Backendvertrag muss dafür nicht geändert werden.

## Frontend-Interaktion

Quasar-Komponenten bilden die primäre UI-Schicht. Pinia-Stores halten Serverzustand; Komponenten koordinieren Darstellung und Benutzeraktionen.

Destruktive Aktionen verwenden `useConfirm` oder `v-confirm`. Umfangreichere Eingaben und Freigabeentscheidungen verwenden Quasar-Dialoge. Erfolgsmeldungen werden am auslösenden UI-Punkt angezeigt; API-Fehler werden ausschließlich zentral benachrichtigt, damit keine doppelten Toasts entstehen.

Nicht abgesendete Chattexte sind ausschließlich lokale Entwürfe im Browser und werden nicht an das Backend übertragen. Ein Entwurf existiert getrennt für die neue Session und für jede persistierte Session. Eine neue Session, ihre erste Nachricht und der erste Agent-Run werden erst beim Absenden gemeinsam persistiert; dadurch entstehen keine leeren Sessions in der Navigation.

Die Oberfläche muss auf Desktop und Mobile sowie in Light Mode und Dark Mode bedienbar bleiben.

## Laufzeitdaten

Der Entwicklungsstack verwendet Named Volumes:

```text
app_data -> /data
db_data  -> /var/lib/postgresql/data
```

Zusätzliche Named Volumes halten Python- und Frontend-Abhängigkeiten. Das Repository wird für HMR nach `/workspace` gebunden.

Der vorbereitete Produktionsstack verwendet zwei Bind-Mounts:

```text
./data -> /data
./db   -> /var/lib/postgresql/data
```

Die Produktions-SPA wird vom MatoLoop-Container zusammen mit FastAPI ausgeliefert. Der Entwicklungsstack behält getrennte Frontend- und Backend-Prozesse für HMR. Der Produktionsstack wird nicht für Entwicklungsarbeiten verwendet.
