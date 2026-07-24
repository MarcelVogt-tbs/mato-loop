# MatoLoop Backend

Das MatoLoop-Backend basiert auf FastAPI, Pydantic v2, asynchronem SQLAlchemy, PostgreSQL und Alembic. Es stellt REST- und SSE-Schnittstellen bereit und enthält die providerneutrale Agent-Runtime.

## Entwicklung

Das Backend wird normalerweise als Teil des Docker-Devstacks vom Repository-Root gestartet:

```bash
./scripts/dev.sh up --build -d
./scripts/dev.sh logs backend
```

Danach sind erreichbar:

- API: `http://localhost:6020/api`
- OpenAPI: `http://localhost:6020/api/docs`
- Liveness: `http://localhost:6020/api/health/live`
- Readiness: `http://localhost:6020/api/health`

Der Backend-Container verwendet Reloading und bindet den Quellcode über `/workspace` ein. Python-Umgebung und `uv`-Cache liegen im Named Volume `backend_uv_data`.

## Struktur

- `src/mato_loop_backend/api`: Router, Dependencies und zentraler Fehlervertrag
- `src/mato_loop_backend/agent`: Modell-/Tool-Loop, Permissions und Freigaben
- `src/mato_loop_backend/providers`: Codex- und OpenAI-kompatible Transporte
- `src/mato_loop_backend/models`: relationale SQLAlchemy-Modelle
- `src/mato_loop_backend/settings`: typisierte operative Settings
- `src/mato_loop_backend/integrations/contracts.py`: statische Adapter- und Toolbeitragsverträge
- `src/mato_loop_backend/integrations/search.py`: No-Key-Metasuche und SearXNG mit kontrolliertem Fallback
- `src/mato_loop_backend/integrations/webfetch.py`: SSRF-geschützter Abruf konkreter HTTP(S)-URLs
- `src/mato_loop_backend/attachments.py`: validierter, persistenter Bild-Storage
- `src/mato_loop_backend/memory`: CRUD, Dreaming, Infinity-Client, Indexer und Hybrid-Retrieval
- `src/mato_loop_backend/workspace.py`: kanonisierte Pfade und Agent-Home-Grenze
- `alembic`: Migrationen und Alembic-Laufzeit
- `tests`: Backend-Unit- und Vertragstests

## Datenbank und Migrationen

Der Devstack führt beim Backendstart automatisch `alembic upgrade head` aus. Migrationen können gezielt geprüft oder ausgeführt werden:

```bash
./scripts/dev.sh db current
./scripts/dev.sh db history
./scripts/dev.sh db upgrade head
./scripts/dev.sh db check
```

Jede Änderung am relationalen Schema benötigt eine neue Alembic-Migration. Migrationen werden ausschließlich aus den aktuellen SQLAlchemy-Metadaten erzeugt:

```bash
./scripts/dev.sh db revision --autogenerate -m "kurze beschreibung"
```

Der generierte Diff muss vollständig geprüft werden. Renames, Datenmigrationen, Extensions und andere von Alembic nicht sicher erkannte Operationen werden anschließend in der generierten Revision manuell angepasst; vollständig handgeschriebene Revisionsdateien sind nicht zulässig. Danach müssen mindestens `upgrade head` und `check` erfolgreich laufen. Operative Settings bleiben typisierte Pydantic-Dokumente und erhalten für additive Felder Defaults.

Die Init-Migration aktiviert pgvector und legt `memory_embeddings` als Vektorspeicher und persistente Queue an. Bestehende Memories werden nach dem Backendstart asynchron indexiert; die Migration selbst ruft Infinity nicht auf. Das aktive Profil verwendet `intfloat/multilingual-e5-small`, 384 Dimensionen sowie die E5-Präfixe `query: ` und `passage: `. Inhalts-Hash und Profil-Fingerprint verhindern die Nutzung veralteter Vektoren.

Infinity ist keine harte Readiness-Abhängigkeit. `/api/health` meldet den Dienst bei einem Ausfall als `degraded`, während FTS, CRUD und Agent-Runs verfügbar bleiben. `./scripts/dev.sh doctor` zeigt Extension-Version, Spaltentyp, Queuezustände und Infinity-Health.

## Fehlervertrag

Erwartete API-Fehler werden als `ApiError` aus `src/mato_loop_backend/api/errors.py` ausgelöst. Antworten enthalten einen stabilen Code und bei Bedarf ein Feld sowie Parameter:

```json
{
  "error": {
    "code": "session.runAlreadyActive"
  }
}
```

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

Regeln:

- Keine deutschen oder englischen Benutzertexte im API-Vertrag ausliefern.
- Keine rohen Pydantic-Details, Exception-Texte oder Providerantworten zurückgeben.
- Technische Ursachen serverseitig protokollieren.
- Providerfehler auf sichere Codes wie `provider.authenticationFailed` oder `provider.unavailable` abbilden.
- Agent- und SSE-Fehler mit demselben Codevertrag veröffentlichen.
- Für unbekannte interne Fehler `internal.unexpected` verwenden.

## Provider und Agent-Runtime

Provideradapter transportieren ausschließlich Modellnachrichten, Toolschemas, Textdeltas und Tool-Calls. Die Agent-Runtime führt Tools aus, prüft Permissions und persistiert Runs sowie Freigaben.

Providerabhängige Datentypen dürfen nicht in den Agent-Kern gelangen. Technische Providerfehler werden nicht als Tool-Output, Run-Fehler oder API-Antwort weitergegeben.

Ein Provider kann mehrere Modellkonfigurationen besitzen. OpenAI-kompatible Discovery verwendet serverseitig den `/models`-Endpunkt. ChatGPT Codex verwendet nach der Device-Code-Anmeldung den privaten Codex-Modellkatalog. Beide Pfade normalisieren Antworten auf Modell-ID und eine begrenzte Metadatenmenge. Sync-Vorgänge erhalten interne UUIDs und Nutzereinstellungen, markieren verschwundene Discovery-Modelle als nicht verfügbar und verändern manuelle Modelle nicht. Neu entdeckte Modelle werden nicht automatisch aktiviert.

Agent-Dateizugriffe laufen über `RootFileService` mit den expliziten Roots `session` und `workspace`. Der aktuelle Session-Speicher liegt unter `/data/sessions/<session-id>`, der optionale Workspace unter dem ausgewählten Pfad in `/data/home`. Absolute Pfade, Parent-Segmente und Symlink-Ausbrüche sind verboten. Der Session-Root enthält flach `files`, `attachments`, `generated`, `downloads` und `derived`. Nur `files` ist veränderbar; die vier hostverwalteten Asset-Verzeichnisse sind lesbar und als Copy-Quelle nutzbar.

Webfetch-Zugriffe laufen über einen eigenen gepinnten Resolver und eine vom Modell nicht überschreibbare Netzwerkpolicy. Persistente Downloads liegen unter `downloads`, Nutzeruploads unter `attachments` und generierte Inhalte unter `generated`. Bilder werden über eine authentifizierte Session-URL ausgeliefert und nur für explizit bildfähige Modelle flüchtig in Providerrequests eingebettet. Text-, Dokument- und Audioableitungen liegen unter `derived`.

## Checks und Tests

Vom Repository-Root:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh test backend
./scripts/dev.sh test rag
```

Direkt aus `apps/backend` können dieselben Werkzeuge lokal ausgeführt werden, wenn die `uv`-Umgebung bereits vorhanden ist:

```bash
uv sync --frozen
uv run ruff check .
uv run pytest
```

`test rag` verwendet den laufenden Devstack und prüft Infinity sowie pgvector mit echten multilingualen Vektoren. Der normale Backendtest überspringt diese beiden Integrationstests. Neue Fehlercodes benötigen mindestens einen Vertragstest für die Backendantwort. Änderungen an Pfadgrenzen, Credentials, Permissions oder Providerkonvertierung benötigen gezielte Unit-Tests.
