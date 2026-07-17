# AGENTS

Diese Datei beschreibt die Arbeitsregeln fuer Coding-Agents in MatoLoop.

## Ziel

- MatoLoop ist eine selbst gehostete, providerneutrale AI-Chat- und Agent-Harness.
- Sessions, Nachrichten und Agent-Runs sind persistente Kerndaten.
- Memory- und Websuche-Freigaben werden durch die Host-Runtime durchgesetzt.
- OpenAI Codex muss ueber ein bestehendes ChatGPT-/Codex-Abonnement nutzbar sein.

## Repository

- `apps/backend`: FastAPI, Pydantic, SQLAlchemy, Alembic und Agent-Runtime.
- `apps/frontend`: Vue 3, Quasar, Pinia und Vue Router.
- `docs`: Architektur-, Entwicklungs- und Betriebsdokumentation.
- `docker-compose.dev.yaml`: lokale Entwicklungsumgebung.

## Grundregeln

- Schreibe Dokumentation und nutzerseitige Texte auf Deutsch.
- Verwende Englisch fuer Code, APIs, Typen und technische Bezeichner.
- Bevorzuge kleine, vertikale und verifizierbare Aenderungen.
- Jede relationale Schemaaenderung benoetigt eine Alembic-Migration.
- Fehlende additive Settings-Felder werden ueber Pydantic-Defaults ergaenzt.
- Secrets gehoeren nicht in normale Settings-Dokumente oder Logs.
- Laufzeit-Policies duerfen nicht durch Modellargumente umgangen werden.

## Backend

- Verwende SQLAlchemy 2.x mit asynchronen Sessions.
- Halte API-, Service-, Provider- und Persistenzschicht getrennt.
- Verwende typisierte Pydantic-Modelle an allen Systemgrenzen.
- Fuehre Transaktionen in der Service-Schicht; Settings-Objekte committen nicht selbst.
- Binde den Agent-Kern nicht an providerabhaengige SDK-Typen.

## Frontend

- Folge den Material- und Quasar-Mustern aus `../sonobin`.
- Verwende Quasar-Komponenten und Utilities vor Custom CSS.
- Unterstuetze Desktop, Mobile, Light Mode und Dark Mode.
- Zeige den effektiven Zustand von Runtime-Policies, nicht nur gespeicherte Wunschwerte.

## Validierung

Fuehre Checks bevorzugt ueber den Docker-Dev-Stack aus:

```bash
./scripts/dev.sh check
./scripts/dev.sh test
```
