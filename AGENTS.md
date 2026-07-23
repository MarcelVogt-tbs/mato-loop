# AGENTS.md

Diese Datei beschreibt die verbindlichen Arbeitsregeln für Coding-Agents in MatoLoop.

## Ziel

- MatoLoop ist eine selbst gehostete, providerneutrale AI-Chat- und Agent-Harness.
- Sessions, Nachrichten, Agent-Runs, Freigaben und Permission-Grants sind persistente Kerndaten.
- ChatGPT Codex und OpenAI-kompatible APIs sind ausschließlich LLM-Transporte.
- Tool-Ausführung, Dateizugriff, Memory, Websuche und Human-in-the-loop-Freigaben werden von MatoLoop kontrolliert.
- Neue Sessions starten ohne Arbeitsverzeichnis. Mit der ersten persistierten Nachricht entsteht
  ausschließlich der interne Session-Speicher unter `/data/sessions`; er ist kein Workspace.

## Repository

- `apps/backend`: FastAPI, Pydantic, SQLAlchemy, Alembic, Provideradapter und Agent-Runtime.
- `apps/frontend`: Vue 3, Quasar, Pinia und Vue Router.
- `docs`: Architektur- und Betriebsentscheidungen.
- `docker-compose.dev.yaml`: lokale Entwicklungsumgebung mit getrenntem Frontend und Backend.
- `docker-compose.yaml`: vorbereiteter Produktionsstack.
- `scripts/dev.sh`: einheitlicher Einstieg für Entwicklung, Checks, Tests und Migrationen.

## Sprache und Stil

- Schreibe Dokumentation und nutzerseitige Texte auf Deutsch.
- Verwende Englisch für Code, APIs, Typen, Fehlercodes, technische Bezeichner,
  Codekommentare und Docstrings.
- Bevorzuge kleine, vertikale und verifizierbare Änderungen.
- Verwende Quasar-Komponenten und vorhandene Utilities vor neuem Custom CSS oder neuen Abstraktionen.
- Unterstütze Desktop, Mobile, Light Mode und Dark Mode.
- Dokumentiere neue Architekturentscheidungen in `docs/architecture.md` oder einer kleinen thematischen Datei unter `docs`.

## Entwicklungsumgebung

- Verwende für Entwicklungsarbeiten ausschließlich `docker-compose.dev.yaml` über `./scripts/dev.sh`.
- Starte den vorbereiteten Produktionsstack nur auf ausdrücklichen Auftrag.
- Der Devstack besteht aus `db`, `backend` und `frontend`.
- Persistente Entwicklungsdaten liegen in den Named Volumes `db_data` und `app_data`.
- Python- und Frontend-Abhängigkeiten liegen ebenfalls in Named Volumes.
- Der Repository-Bind-Mount nach `/workspace` dient ausschließlich HMR und Live-Code-Änderungen.
- `./scripts/dev.sh reset-storage` löscht die persistenten Entwicklungsdaten und darf nicht beiläufig ausgeführt werden.

## Backend

- Verwende SQLAlchemy 2.x mit asynchronen Sessions.
- Halte API-, Service-, Provider-, Agent- und Persistenzschicht getrennt.
- Verwende typisierte Pydantic-Modelle an Systemgrenzen.
- Führe Transaktionen in der Service- oder Anwendungsschicht; Settings-Objekte committen nicht selbst.
- Jede relationale Schemaänderung benötigt eine Alembic-Migration.
- Erzeuge jede Alembic-Migration aus den aktuellen SQLAlchemy-Metadaten ausschließlich mit `./scripts/dev.sh db revision --autogenerate -m "<beschreibung>"` und prüfe den generierten Diff vollständig.
- Passe autogenerierte Migrationen bei Renames, Datenmigrationen, Extensions und anderen von Alembic nicht sicher erkannten Operationen manuell an; lege keine vollständig handgeschriebenen Revisionsdateien an.
- Additive Settings-Felder erhalten Pydantic-Defaults.
- Binde den Agent-Kern nicht an providerspezifische SDK-Typen.
- Protokolliere technische Fehler serverseitig, ohne Secrets oder Tokens auszugeben.

## Provider und Agent

- Provideradapter transportieren Nachrichten, Toolschemas, Textdeltas und Tool-Calls, führen aber keine Tools aus.
- Alle Provider verwenden denselben MatoLoop-Agent- und Permission-Loop.
- Modellargumente dürfen Runtime-Policies und Pfadgrenzen nicht umgehen.
- Dateipfade werden kanonisiert und auf `/data/home` begrenzt.
- Symlinks dürfen die Pfadgrenze nicht umgehen.
- Nicht erlaubte Aktionen erzeugen persistente Freigabeanfragen mit `allow_once`, `allow_session` oder `deny`.
- Externe Providerantworten und Exception-Texte dürfen weder als API-Fehler noch als Toast ungefiltert ausgegeben werden.

## Fehlervertrag

- Fehlerantworten enthalten einen stabilen, sprachneutralen `error.code`.
- Dynamische Werte werden über `error.params` übertragen; feldbezogene Fehler können `error.field` enthalten.
- Das Backend ergänzt jeden Fehler um eine kontrollierte englische `error.message` als Fallback für Kanäle ohne eigene Lokalisierung.
- Englische Fallbacks stammen ausschließlich aus dem zentralen Fehlerkatalog; rohe Pydantic-, Provider- und Exception-Texte werden nie ausgegeben.
- Das Frontend übersetzt Fehlercodes zentral in `apps/frontend/src/utils/api-errors.js`.
- Axios-Fehler werden über den zentralen Response-Interceptor als negative Quasar-Toasts angezeigt.
- Persistente Agent- und Runtimefehler erscheinen als MatoLoop-Antwort im Chat; SSE dient nur der schnellen Zustellung dieses kanonischen Zustands.
- Nicht persistierbare SSE-Transportfehler und andere Fehler außerhalb von Axios verwenden denselben Formatter und Toast-Helper.
- Unbekannte Codes müssen auf eine sichere Meldung anhand des HTTP-Status zurückfallen.
- Frontend-Code darf Fehlertexte nicht vergleichen oder strukturierte Fehlerobjekte direkt rendern.

Beispiel:

```json
{
  "error": {
    "code": "validation.tooShort",
    "message": "The value is too short.",
    "field": "password",
    "params": {
      "minLength": 10
    }
  }
}
```

## Frontend

- Verwende Stores für Serverzustand und Komponenten für Darstellung und Benutzerinteraktion.
- Zeige den effektiven Zustand von Runtime-Policies, nicht nur gespeicherte Wunschwerte.
- Verwende `useConfirm` oder `v-confirm` für destruktive Benutzeraktionen.
- Verwende Quasar-Dialoge für Eingaben und Entscheidungen, die mehr als eine einfache Bestätigung benötigen.
- Erfolgsmeldungen werden lokal mit Quasar Notify angezeigt; API-Fehler nicht doppelt benachrichtigen.
- Halte Dialoge und Drawer auf kleinen Viewports vollständig bedienbar.
- Vermeide optimistische UI-Zustände, die nach einer fehlgeschlagenen Anfrage bestehen bleiben.

## Secrets

- Secrets gehören nicht in Settings-Dokumente, Logs, Fehlerantworten oder das Repository.
- API-Keys und OAuth-Tokens werden verschlüsselt in `credentials` gespeichert.
- Der Fernet-Schlüssel liegt im Datenverzeichnis unter `/data/secrets/credential.key`.

## Validierung

Führe Checks bevorzugt über den Docker-Devstack aus:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```

Gezielte Prüfungen sind ebenfalls möglich:

```bash
./scripts/dev.sh check backend
./scripts/dev.sh check frontend
./scripts/dev.sh test backend
./scripts/dev.sh test frontend
./scripts/dev.sh db current
```

Prüfe bei UI-Änderungen mindestens Desktop und Mobile sowie Light Mode und Dark Mode. Prüfe bei API-Fehlern sowohl den Backendvertrag als auch die menschenlesbare Frontendmeldung.
