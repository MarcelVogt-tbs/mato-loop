# Architektur

MatoLoop wird als providerneutrales Monorepo entwickelt. FastAPI stellt REST- und spaeter SSE-Schnittstellen bereit; die Quasar-SPA konsumiert ausschliesslich diese API.

## Konfigurationsgrenzen

Bootstrap- und Infrastrukturwerte kommen aus Environment-Variablen. Operative Einstellungen liegen als typisierte Pydantic-Dokumente in der PostgreSQL-Tabelle `settings`.

Additive Felder erhalten Pydantic-Defaults. API-Keys und OAuth-Tokens werden nicht im Settings-JSON gespeichert, sondern spaeter ueber eine getrennte verschluesselte Credential-Schicht referenziert.

## Geplante Schichten

- API: HTTP-Vertraege und Dependency Injection
- Services: Transaktionen und Anwendungslogik
- Agent: providerneutraler Loop und Host-Policies
- Providers: LLM- und Suchadapter
- Memory: Auswahl und Injektion freigegebener Erinnerungen
- Persistence: SQLAlchemy-Modelle und Repositories
