# MatoLoop

MatoLoop wird eine selbst gehostete AI-Chat- und Agent-Harness mit persistenten Sessions, providerabhaengigem Memory-Kontext, erzwingbarer Websuche und Codex-OAuth-Unterstuetzung.

## Aktueller Stand

Der erste technische Slice stellt das Monorepo, FastAPI, PostgreSQL, SQLAlchemy, Alembic und eine responsive Quasar-Shell bereit. Fachliche Chat- und Providerfunktionen folgen in weiteren vertikalen Slices.

## Stack

- FastAPI, Pydantic v2
- SQLAlchemy 2.x async, PostgreSQL, Alembic
- Vue 3, Quasar, Pinia, Vue Router
- `uv`, Yarn 4
- Docker Compose

## Entwicklung

```bash
./scripts/dev.sh up --build
```

Danach sind erreichbar:

- WebUI: `http://localhost:10020`
- Backend: `http://localhost:6020/api`
- OpenAPI: `http://localhost:6020/api/docs`

Checks und Tests:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```

Migrationen:

```bash
./scripts/dev.sh db current
./scripts/dev.sh db upgrade
```

Lokale Abweichungen koennen in `.env.dev.override` hinterlegt werden. Als Vorlage dient `.env.dev.override.example`.

## Konfiguration

Infrastrukturwerte wie Datenbankverbindung und Ports kommen aus Environment-Variablen mit dem Prefix `MATO_LOOP_`. Operative Einstellungen werden spaeter ueber typisierte Settings-Dokumente und die WebUI verwaltet.
