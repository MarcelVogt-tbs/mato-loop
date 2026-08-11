# Betrieb und Datensicherung

MatoLoop ist selbst gehostet. Der bereitgestellte Produktions-Compose-Stack ist eine Grundlage, aber keine vollständig abgesicherte Standardinstallation für jedes Zielsystem.

## Vor einem Produktivbetrieb

Planen und prüfen Sie mindestens:

- TLS und Reverse Proxy
- sichere Session-Cookies
- individuelle Datenbank-Zugangsdaten
- Netzwerkzugriff auf Anwendung, Datenbank und Embedding-Dienst
- regelmäßige Backups
- getestete Wiederherstellung
- Aktualisierungs- und Monitoring-Prozess

Die Standardwerte der vorbereiteten Compose-Datei sind nicht als sichere Internet-Konfiguration gedacht.

Setzen Sie bei einem Betrieb über HTTPS ausdrücklich `MATO_LOOP_SECURE_COOKIES=true`. Der vorbereitete Stack verwendet standardmäßig `false`; ohne die Umstellung besitzt das Anmelde-Cookie kein `Secure`-Attribut.

## Optionalen Computer einrichten

MatoLoop kann mehrere Computer verwalten. Öffnen Sie **Einstellungen > Computer**, wählen Sie **Computer hinzufügen**, vergeben Sie einen Namen und wählen Sie die Gültigkeit des Kopplungstokens zwischen 30 Minuten und 24 Stunden. Der Dialog zeigt Computer-ID, WebSocket-Adresse und das nur einmal sichtbare Token. Der Computer erzeugt damit beim ersten Start einen eigenen dauerhaften Zugangsschlüssel. Ein abgelaufenes oder verlorenes Kopplungstoken kann in der Übersicht ersetzt werden. Ein bereits gekoppelter Computer muss vor einer erneuten Kopplung ausdrücklich entkoppelt werden.

Für den vorbereiteten Compose-Computer setzen Sie mindestens:

```env
MATO_EXECUTION_AGENT_ID=<Computer-ID aus MatoLoop>
MATO_EXECUTION_AGENT_PAIRING_TOKEN=<einmalig angezeigtes Kopplungstoken>
MATO_EXECUTION_AGENT_WORKSPACE=./data/home
MATO_EXECUTION_AGENT_CONFIG=./apps/execution-agent/agent.example.json
```

Starten Sie anschließend das optionale Profil `execution-agent`, zum Beispiel mit `docker compose --profile execution-agent up -d`. In der Entwicklungsumgebung kann `COMPOSE_PROFILES=execution-agent ./scripts/dev.sh up -d` verwendet werden. `MATO_EXECUTION_AGENT_WORKSPACE` wird als `/workspace` eingebunden; `MATO_EXECUTION_AGENT_CONFIG` bestimmt Arbeitsbereichs-IDs, Cachegrenzen, erlaubte Programm-Aliase und die optionale Shell. Der Computer verbindet sich ausgehend mit dem Backend und speichert seinen dauerhaften Zugang unter `/data/agent-state`. Nach erfolgreicher Kopplung kann `MATO_EXECUTION_AGENT_PAIRING_TOKEN` entfernt werden. Linux und Docker sind derzeit die getesteten Umgebungen.

!!! warning "Prozess- und Shell-Zugriff"
    Der Compose-Computer aktiviert mit der Beispielkonfiguration Prozess- und Shell-Funktionen. Sie besitzen keine Betriebssystem-Sandbox. Der Arbeitsordner begrenzt nicht den Datei- oder Netzwerkzugriff eines gestarteten Prozesses. MatoLoop erlaubt deshalb pro Aufruf nur **Einmal erlauben** oder **Ablehnen**.

## Zu sichernde Daten

Für eine vollständige Wiederherstellung benötigen Sie gemeinsam:

- PostgreSQL-Datenbank
- MatoLoop-Datenverzeichnis `/data`
- insbesondere `/data/secrets/credential.key`
- alle benötigten Arbeitsbereiche der Computer
- die externe Computer-Konfiguration und der sichere Computer-Zustand unter `dataDir/agent-state`

Provider- und OAuth-Zugangsdaten liegen verschlüsselt in der Datenbank. Ohne den zugehörigen Schlüssel `credential.key` können sie nach einer Wiederherstellung nicht entschlüsselt werden.

Das Modell-Cache-Volume des Embedding-Dienstes und der Session-Cache des Computers müssen nicht gesichert werden. Der Credential-Bereich `agent-state` im Volume `execution_agent_data` ist dagegen für eine Wiederherstellung ohne erneute Kopplung erforderlich. Geht er verloren, entkoppeln Sie den Computer in MatoLoop und stellen ein neues Kopplungstoken aus. Session-Dateien bleiben im Backend gespeichert.

## Entwicklungsdaten

Die Entwicklungsumgebung verwendet benannte Docker-Volumes. Der Befehl `./scripts/dev.sh reset-storage` löscht Datenbank, Benutzer, Secrets, Session-Dateien und Computer-Caches vollständig und darf nicht als normale Aufräumaktion verwendet werden. Extern eingebundene Arbeitsbereiche sind davon getrennt.

## Aktualisierung

Sichern Sie Datenbank und Datenverzeichnis vor einem Update gemeinsam. Datenbankschemaänderungen werden beim Start über Alembic-Migrationen angewendet. Prüfen Sie nach dem Update Anmeldung, Providerverbindungen, Dateizugriff, Memory und laufende Integrationen.

### Veröffentlichtes Image verwenden

Die Compose-Datei kann MatoLoop und den optionalen Computer weiterhin lokal bauen. Für veröffentlichte Images setzen Sie stattdessen beide Image-Adressen mit einem festen, gleichen Versions-Tag:

```env
MATO_LOOP_IMAGE=git.example/owner/mato-loop:1.2.3
MATO_LOOP_EXECUTION_AGENT_IMAGE=git.example/owner/mato-loop-execution-agent:1.2.3
```

Melden Sie Docker bei einer privaten Registry an. Laden Sie die Images anschließend ausdrücklich und erstellen Sie die Container neu:

```bash
docker compose --profile execution-agent pull
docker compose --profile execution-agent up -d
```

Die veröffentlichten Images unterstützen derzeit nur `linux/amd64`. Erfolgreiche `develop`-Läufe stellen zusätzlich sieben Tage lang ein natives Computer-Archiv für Linux amd64 mit glibc 2.35 oder neuer bereit. Stabile Releases enthalten noch kein natives Archiv. Eine Installation oder Aktualisierung auf Ihrem Server erfolgt nicht automatisch.

Der Tag `develop` ist veränderlich und nur für Testinstallationen vorgesehen. Verwenden Sie dafür ein eigenes Bereitstellungsverzeichnis: Die relativen Pfade `./data` und `./db` bleiben dadurch von der Produktion getrennt. Führen Sie vor jedem Neustart `docker compose pull` aus. Verwenden Sie `develop` niemals mit Produktivdaten und nicht als Rollback-Ziel.

### Rollback

Setzen Sie für beide Images den vorherigen festen Versions-Tag, laden Sie ihn und erstellen Sie die Container erneut. Verwenden Sie weder `latest` noch `develop`, da beide Tags auf andere Images verschoben werden können.

!!! warning "Datenbankschema beim Rollback"
    Beim Start können Datenbankmigrationen ausgeführt werden. Ein älteres Image kann mit dem bereits migrierten Schema unvereinbar sein. Stellen Sie dann zusätzlich die gemeinsam vor dem Update gesicherte Datenbank und das zugehörige Datenverzeichnis wieder her.
