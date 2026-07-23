# MatoLoop

MatoLoop ist eine selbst gehostete, providerneutrale AI-Chat- und Agent-Harness mit persistenten Sessions, internen Tools, Human-in-the-loop-Freigaben, optionalem Memory und erzwingbarer Websuche.

## Aktueller Stand

Der aktuelle Entwicklungsstand umfasst:

- Single-User-Setup und Cookie-basierte Anmeldung
- ChatGPT Codex über Device-Code-OAuth
- OpenAI-kompatible Chat-Completions- und Responses-Endpunkte
- persistente Sessions, Nachrichten und Agent-Runs
- einheitlichen Modell-/Tool-Loop für alle Provider
- Datei-Explorer und Arbeitsverzeichnisse unter `/data/home`
- persistente Permission-Grants und Freigabeentscheidungen
- externe SearXNG-Anbindung
- sprachneutrale API-Fehlercodes und menschenlesbare Frontend-Toasts

## Stack

- FastAPI und Pydantic v2
- SQLAlchemy 2.x async, PostgreSQL und Alembic
- pgvector und lokales Infinity für hybrides Memory-Retrieval
- Vue 3, Quasar, Pinia und Vue Router
- `uv` und Yarn 4
- Docker Compose

## Entwicklung

Der Entwicklungsstack ist der Standard für alle lokalen Arbeiten:

```bash
./scripts/dev.sh up --build -d
```

Danach sind erreichbar:

- WebUI: `http://localhost:10020`
- Backend: `http://localhost:6020/api`
- OpenAPI: `http://localhost:6020/api/docs`

Nützliche Kommandos:

```bash
./scripts/dev.sh status
./scripts/dev.sh doctor
./scripts/dev.sh logs backend
./scripts/dev.sh stop
./scripts/dev.sh down
```

Checks und Tests:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
./scripts/dev.sh test rag
```

Einzelne Bereiche können als `backend` oder `frontend` angegeben werden:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh test frontend
```

`test rag` startet PostgreSQL und Infinity, wartet auf deren Healthchecks und prüft echtes deutsch-englisches Hybrid-Retrieval, Trust-Filterung und den FTS-Fallback. Der normale Testlauf verwendet weiterhin keine Infrastrukturabhängigkeiten.

Migrationen:

```bash
./scripts/dev.sh db current
./scripts/dev.sh db upgrade head
```

Lokale Konfigurationsabweichungen können in `.env.dev.override` hinterlegt werden. Als Vorlage dient `.env.dev.override.example`.

## Entwicklungsdaten

Der Devstack verwendet Named Volumes:

- `app_data`: Agent-Home, Credential-Schlüssel und weitere MatoLoop-Laufzeitdaten
- `db_data`: PostgreSQL-Daten
- `embedding_cache`: reproduzierbarer Infinity-Modellcache
- `backend_uv_data`: Python-Umgebung und `uv`-Cache
- `frontend_node_modules`: installierte Frontend-Abhängigkeiten
- `frontend_yarn_cache`: zusätzlicher Frontend-Cache-Mount unter `/opt/mato-loop/yarn-cache`

Docker versieht die Volumenamen standardmäßig mit dem Compose-Projektnamen, beispielsweise `mato-loop_app_data`. Der Repository-Bind-Mount nach `/workspace` dient nur HMR und Live-Code-Änderungen.

Infinity lädt beim ersten Start die gepinnte Revision von `intfloat/multilingual-e5-small`. Dieser Cold Start kann einige Minuten dauern; Backend und lexikalische Memory-Suche bleiben dabei verfügbar. Der Cache muss nicht gesichert werden. `./scripts/dev.sh doctor` prüft pgvector, die Vektordimension, den Indexierungsstatus und Infinitys Health-Endpunkt.

Der folgende Befehl löscht Container und alle Named Volumes des Devstacks einschließlich Datenbank, Benutzer, Secrets und Agent-Dateien:

```bash
./scripts/dev.sh reset-storage
```

## Produktionsvorbereitung

`docker-compose.yaml` und `apps/backend/Dockerfile` bereiten einen kombinierten MatoLoop-Container, PostgreSQL mit pgvector und einen internen Infinity-CPU-Service vor. Der vorbereitete Infinity-Container ist für `linux/amd64` vorgesehen. Dieser Stack ist nicht der Entwicklungsstack und wird bei normalen Entwicklungsarbeiten nicht gestartet.

Die Produktionsvorbereitung verwendet die Bind-Mounts `./data:/data` und `./db:/var/lib/postgresql/data`. Vor einem produktiven Einsatz müssen insbesondere TLS, sichere Cookies, Zugangsdaten, Backup und Wiederherstellung für die konkrete Umgebung festgelegt und geprüft werden.

## Provider

Unter **Einstellungen** stehen zwei LLM-Pfade zur Verfügung:

- **ChatGPT Codex**: Device-Code-OAuth mit einem geeigneten ChatGPT-Konto. MatoLoop zeigt einen einmaligen Code an, der auf der geöffneten ChatGPT-Anmeldeseite bestätigt wird; ein lokaler Callback-Port ist nicht erforderlich. Verfügbare Codex-Modelle werden anschließend automatisch synchronisiert.
- **OpenAI-kompatibel**: Base URL, optionaler API-Key und `Chat Completions` oder `Responses API`. MatoLoop liest verfügbare Modelle über den kompatiblen `/models`-Endpunkt aus.

Provider sind ausschließlich Modelltransporte. Sessions, Tool-Calls, Dateien, Memory, Websuche und Freigaben laufen immer durch die MatoLoop-Harness.

Jeder Provider kann mehrere Modelle besitzen. OpenAI-kompatible Provider und ChatGPT Codex unterstützen die automatische Modellerkennung. Neu erkannte Modelle sind zunächst deaktiviert und werden erst nach expliziter Aktivierung im Chat angeboten. Ein erneuter Abgleich behält interne Modell-IDs und Laufzeitkonfigurationen bei; nicht mehr gemeldete Modelle werden als nicht verfügbar markiert statt gelöscht. Falls ein Provider keine Discovery unterstützt, kann eine Modell-ID über den erweiterten manuellen Fallback hinterlegt werden. Provider, Modelle und die globale LLM-Laufzeit können unabhängig aktiviert werden.

Unter **Einstellungen > Integrationen** können mehrere Suchintegrationen verwaltet werden. Aktuell wird SearXNG unterstützt; eine aktive Instanz wird als Standard ausgewählt. Erst dann stehen die erzwungene und agentengesteuerte Websuche zur Verfügung.

Unter **Einstellungen > Tools** wird `webfetch` unabhängig von SearXNG konfiguriert. Das Tool ruft konkrete HTTP(S)-URLs als Markdown, Text oder HTML ab und unterstützt Bildantworten bis 5 MiB. Standardmäßig fragt jeder Abruf nach einer Freigabe. Alternativ können öffentliche Ziele oder öffentliche und explizit erlaubte private Origins automatisch freigegeben werden. Pro Modell ist ein vollständiger Policy-Override möglich; der globale Schalter und die Origin-Allowlist bleiben Hostgrenzen.

Webfetch folgt Redirects nur nach erneuter Prüfung, pinnt validierte DNS-Ziele und blockiert Link-Local-, Metadaten- und andere gefährliche Adressen. Heruntergeladene Dateien werden im geschützten Session-Speicher unter `/data/sessions/<session-id>/downloads` gespeichert. Bildfähige Modelle erhalten aktuelle oder ausdrücklich referenzierte Bilder flüchtig im Providerrequest; Textmodelle erhalten ausschließlich sichere Metadaten und verfügbare Textableitungen.

## Arbeitsverzeichnisse

Neue Sessions starten ohne Arbeitsverzeichnis. Mit der ersten Nachricht entsteht ein eigener interner Session-Speicher unter `/data/sessions/<session-id>` mit den fünf flachen Verzeichnissen `files`, `attachments`, `generated`, `downloads` und `derived`. Nur `files` ist bearbeitbar; die übrigen Bereiche werden vom Host verwaltet. Dieser Speicher ersetzt keinen Workspace. Der rechte Datei-Explorer verwaltet weiterhin das Agent-Home unter `/data/home` und kann einen Ordner als optionalen Workspace auswählen.

Eine neue Session wird erst mit ihrer ersten Nachricht serverseitig angelegt und erscheint erst dann in der Seitenleiste. Nicht abgesendete Texte bleiben getrennt pro Session ausschließlich im lokalen Browserspeicher.

- `read-only`: Nur `list_directory` und `read_file` werden für Session-Speicher und optionalen Workspace angeboten.
- `read-write`: Zusätzlich stehen Schreiben, Erstellen, Löschen, Kopieren und Verschieben zur Verfügung. Im Session-Speicher ist ausschließlich `files` veränderbar; `attachments`, `generated`, `downloads` und `derived` bleiben schreibgeschützt und können nur gelesen oder als Copy-Quelle verwendet werden.
- Dateitools verwenden ein explizites `root`-Feld mit `session` oder `workspace`. Ohne Arbeitsverzeichnis ist ausschließlich `root=workspace` nicht verfügbar.
- `Einmal erlauben`: gilt nur für die aktuelle Aktion.
- `Für Session erlauben`: erzeugt einen separaten, widerrufbaren Permission-Grant.
- `Später`: lässt die Anfrage ausstehend und den Agent-Run pausiert.

## Konfiguration und Secrets

Infrastrukturwerte wie Datenbankverbindung, Ports und Datenverzeichnis kommen aus Environment-Variablen mit dem Präfix `MATO_LOOP_`. Operative Einstellungen werden über typisierte Settings-Dokumente und die WebUI verwaltet.

API-Keys und OAuth-Tokens liegen verschlüsselt in PostgreSQL. Der lokale Fernet-Schlüssel liegt unter `/data/secrets/credential.key` und muss gemeinsam mit der Datenbank gesichert werden.

## Dokumentation

- [Arbeitsregeln für Coding-Agents](AGENTS.md)
- [Architektur](docs/architecture.md)
- [Backend](apps/backend/README.md)
- [Frontend](apps/frontend/README.md)
