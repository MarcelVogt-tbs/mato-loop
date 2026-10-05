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

Kopieren Sie vor dem ersten Start `.env.example` nach `.env`. Ersetzen Sie das Datenbankpasswort und beide Image-Platzhalter. Verwenden Sie für MatoLoop und den MatoLoop Agent denselben von Forgejo CI veröffentlichten Tag im Format `sha-<12 Zeichen der Commit-ID>`.

```bash
cp .env.example .env
docker compose pull
docker compose up -d
```

Der Stack veröffentlicht Port `10020` standardmäßig auf `0.0.0.0`. Begrenzen Sie den Zugriff mit der Host-Firewall und einem geeigneten Reverse Proxy. Die Standardwerte der vorbereiteten Compose-Datei sind nicht als sichere Internet-Konfiguration gedacht.

Bestehende Installationen, die bisher die eingebauten Compose-Fallbacks verwendet haben, tragen vor dem Wechsel ihre bisherigen Datenbankwerte sowie `./db`, `./data`, `./data/matoloop-agent` und `./data/home` ausdrücklich in `.env` ein. Verschieben Sie persistente Daten nur als separate, geprüfte Migration.

Die Produktionsvorlage setzt `MATO_LOOP_SECURE_COOKIES=true`. Ändern Sie den Wert nur für einen ausdrücklich HTTP-basierten Betrieb in einem vertrauenswürdigen Netz; ohne `true` besitzt das Anmelde-Cookie kein `Secure`-Attribut.

## App und Push-Benachrichtigungen

The production build is an installable web app. Serve MatoLoop over a trusted HTTPS address and set `MATO_LOOP_SECURE_COOKIES=true`. An administrator configures instance Web Push under **Administration > Benachrichtigungen** using a monitored contact email address. MatoLoop creates the private key `/data/secrets/web-push-vapid.pem` and supports the browser push services of Apple, Google, and Mozilla.

Benutzer aktivieren Push anschließend für jeden Browser oder jede PWA-Installation über den Schalter im Benachrichtigungsbereich. Unter iOS und iPadOS ist dafür mindestens Version 16.4 erforderlich und MatoLoop muss zuvor über **Zum Home-Bildschirm** installiert worden sein. Die Systemberechtigung kann nur durch diese ausdrückliche Benutzeraktion angefordert werden.

Solange MatoLoop in einem Browserfenster oder einer installierten PWA fokussiert ist, versendet MatoLoop keine Push-Benachrichtigungen an die Geräte dieses Benutzers. Die gespeicherten Benachrichtigungen und In-App-Aktionen bleiben davon unberührt.

Sichern Sie `/data/secrets/web-push-vapid.pem` zusammen mit Datenbank und Datenverzeichnis; ein verlorener Schlüssel macht bestehende Geräte-Abonnements unbrauchbar. Push-Zustellung erfolgt bestmöglich und ersetzt weder die gespeicherten Benachrichtigungen noch den Chatverlauf.

Für den PWA-Entwicklungsmodus auf dem normalen Entwicklungsport `10020` verwenden Sie:

```bash
./scripts/dev.sh up --pwa --build -d
```

`localhost` gilt im Desktop-Browser als vertrauenswürdig. Für Tests auf Mobilgeräten ist auch in der Entwicklung eine HTTPS-Adresse erforderlich. Der PWA-Worker speichert keine Anwendungs-, API-, SSE-, Datei- oder Authentifizierungsdaten offline.

## Optionalen Computer einrichten

MatoLoop kann mehrere Computer verwalten. Öffnen Sie **Einstellungen > Computer**, wählen Sie **Computer hinzufügen**, vergeben Sie einen Namen und wählen Sie die Gültigkeit des Kopplungstokens zwischen 30 Minuten und 24 Stunden. Der Dialog zeigt Computer-ID, WebSocket-Adresse und das nur einmal sichtbare Token. Der Computer erzeugt damit beim ersten Start einen eigenen dauerhaften Zugangsschlüssel. Ein abgelaufenes oder verlorenes Kopplungstoken kann in der Übersicht ersetzt werden. Ein bereits gekoppelter Computer muss vor einer erneuten Kopplung ausdrücklich entkoppelt werden.

Die beiden Host-Verzeichnisse des mitgelieferten MatoLoop Agent können vor dem Start angepasst werden:

```env
MATOLOOP_AGENT_DATA=/opt/matoloop-agent/data
MATOLOOP_AGENT_HOME=/opt/matoloop-agent/home
```

Der Produktionsstack bindet `MATOLOOP_AGENT_DATA` als `/data` und `MATOLOOP_AGENT_HOME` als `/workspace` ein. Die Produktionsvorlage setzt dafür `/opt/matoloop-agent/data` und `/opt/matoloop-agent/home`; Compose behält für bestehende Installationen die früheren relativen Fallbacks. Der Entwicklungsdienst bindet zusätzlich den Quellcode und ein wegwerfbares Abhängigkeits-Volume ein. Die Konfiguration liegt unter `/data/config.json`; Zugang, Transfers und Cache bleiben ebenfalls unter `/data`. Das Backend verwaltet, migriert oder löscht den Inhalt des Agent-Arbeitsbereichs nicht.

Der normale Produktions- und Entwicklungsstack startet den Computer automatisch. Solange er ungekoppelt ist, wartet er auf die einmalige Kopplung. Führen Sie diese im laufenden Produktionscontainer aus:

```bash
docker compose exec matoloop-agent matoloop-agent pair
```

In der Entwicklungsumgebung lautet der Befehl:

```bash
docker compose -f docker-compose.dev.yaml exec matoloop-agent \
  uv run --no-sync matoloop-agent pair
```

Der Dialog fragt die Backend-WebSocket-Adresse, die Computer-ID und das verdeckt eingegebene Kopplungstoken ab. Bereits gesetzte Werte werden als Vorgabe angeboten. Für eine automatische Kopplung müssen `MATOLOOP_AGENT_BACKEND_URL`, `MATOLOOP_AGENT_ID` und `MATOLOOP_AGENT_PAIRING_TOKEN` vollständig im Container gesetzt sein. Compose stellt seine interne Backend-Adresse bereits bereit; Betreiber setzen deshalb noch ID und Token. Unvollständige Verbindungsvariablen starten keine automatische Kopplung. Entfernen Sie das Token nach erfolgreicher Kopplung aus der Umgebung. Alte Variablen-Aliase für Konfiguration, Name, Token oder Workspace werden nicht unterstützt. Verwenden Sie `docker compose -f docker-compose.no-agent.yaml up -d --remove-orphans` für den Produktionsstack ohne Computer. Linux und Docker sind derzeit die getesteten Umgebungen.

### Nativen Agent unter Windows installieren

Öffnen Sie Windows PowerShell **als Administrator unter dem Benutzerkonto, unter dem der Agent später laufen soll**. Kopieren Sie den Windows-Installationsbefehl aus **Einstellungen > Computer**. Git und Zugriff auf das MatoLoop-Repository sind erforderlich; für ein vorhandenes Checkout kann das Skript mit `-Source C:\Pfad\zum\Checkout` ausgeführt werden. Der Installer fragt beim Anlegen des geplanten Tasks einmal das Kennwort dieses Kontos ab; es wird nicht in MatoLoop-Dateien gespeichert. Der Task läuft beim Systemstart auch ohne Anmeldung. Installationsdaten liegen unter `%LOCALAPPDATA%\matoloop-agent`, der vorgeschlagene Arbeitsbereich unter `%USERPROFILE%\mato-loop-agent\workspace`.

Erstellen Sie anschließend im Portal einen Computer und koppeln Sie ihn interaktiv mit `& "$env:LOCALAPPDATA\matoloop-agent\installer.ps1" pair`. Dasselbe Skript unterstützt `update`, `status`, `uninstall` und `uninstall -Purge`. Das Update verlangt einen sauberen Git-Branch mit Upstream und führt nur einen Fast-Forward aus. Die Deinstallation erhält den Arbeitsbereich auch mit `-Purge`. Der Windows-Agent verwendet PowerShell für Shell-Befehle; Browser, OpenSCAD und interaktive Terminals sind derzeit nicht verfügbar.

Unter Windows ist auch **Agent aktualisieren** in den Computer-Einstellungen verfügbar. Ein beim Setup angelegter Wartungs-Task führt dasselbe Update wie das lokale Skript aus und bleibt aktiv, während der Agent-Task neu gestartet wird. Die Oberfläche meldet das tatsächliche Ergebnis. Der Windows-Aufgabenplaner verwaltet das Kontokennwort; beim Portal-Update wird es nicht erneut abgefragt. Für Tests der Entwicklungsversion installieren Sie mit `-Branch develop` oder aus einem sauberen `develop`-Checkout mit Upstream. Git muss ohne interaktive Anmeldung auf das Repository zugreifen können.

### Nativen Agent unter Linux installieren

MatoLoop enthält zunächst für Linux-Systeme mit systemd eine native Benutzerinstallation. Starten Sie sie als normaler Benutzer mit `./scripts/install-matoloop-agent.sh`. Der Bootstrap installiert `uv` und Python ausschließlich unter `~/.local/share/matoloop-agent`, trägt nichts in Shell-Profile ein und verwendet `sudo` nur für den systemweiten Dienst sowie bestätigte optionale Systempakete. Der Dienst startet beim Booten, läuft aber mit den Rechten des installierenden Benutzers. Innerhalb eines Checkouts verwendet der Installer dessen aktuellen Stand; eine einzeln kopierte Scriptdatei klont das private Repository mit der normalen Git-Anmeldung und fragt den gewünschten Branch ab. Das gleiche Script wird als Befehl `matoloop-agent` installiert und bietet `install`, `update`, `status` und `uninstall`. `update` aktualisiert den aktuellen Tracking-Branch ausschließlich per Fast-Forward und bricht bei lokalen Änderungen ab; damit folgt `main` der stabilen und `develop` der aktuellen Entwicklungsversion. `install` übernimmt stattdessen den vorhandenen lokalen Stand ohne Pull. Neue Umgebungen werden vor dem Umschalten gestartet und geprüft. `uninstall` erhält Kopplungsdaten, `uninstall --purge` entfernt alle verwalteten Agent-Daten; Workspaces bleiben immer bestehen. Browser- und Shared-Terminal-Funktionen werden nur angeboten, wenn ihre Systemvoraussetzungen bereits vorhanden sind.

!!! warning "Prozess- und Shell-Zugriff"
    Der Compose-Computer aktiviert mit der Beispielkonfiguration Prozess- und Shell-Funktionen und läuft derzeit als `root`. MatoLoop Agent verwendet Betriebssystemrechte sowie Descriptor- und No-Follow-Prüfungen, ist aber keine Betriebssystem-Sandbox. Ein gestarteter Prozess kann grundsätzlich auf beide eingebundenen Bereiche und erreichbare Netzwerke zugreifen. Begrenzen Sie deshalb Mounts, Netzwerk und Host-Richtlinien passend zum Einsatzzweck.

## Zu sichernde Daten

Für eine vollständige Wiederherstellung benötigen Sie gemeinsam:

- PostgreSQL-Datenbank
- MatoLoop-Datenverzeichnis `/data`
- insbesondere `/data/secrets/credential.key`
- `/data/secrets/web-push-vapid.pem`, falls Web Push aktiviert ist
- alle benötigten Home- und Arbeitsverzeichnisse der Computer
- das vollständige Verzeichnis `MATOLOOP_AGENT_DATA` mit `/data/config.json`, Zugang und dauerhaftem Zustand

Provider- und OAuth-Zugangsdaten liegen verschlüsselt in der Datenbank. Ohne den zugehörigen Schlüssel `credential.key` können sie nach einer Wiederherstellung nicht entschlüsselt werden.

Das Modell-Cache-Volume des Embedding-Dienstes und der Session-Cache des Computers müssen nicht gesichert werden. Die Zugangsdaten im Verzeichnis `MATOLOOP_AGENT_DATA` sind dagegen für eine Wiederherstellung ohne erneute Kopplung erforderlich. Gehen sie verloren, entkoppeln Sie den Computer in MatoLoop und stellen ein neues Kopplungstoken aus. Session-Dateien bleiben im Backend gespeichert.

Beim Löschen eines Benutzers entfernt MatoLoop dessen Datenbankdaten und Session-Bäume. Dateien, Caches, dauerhafter Zustand und Konfigurationen aller MatoLoop Agents, einschließlich des Compose-Computers unter `MATOLOOP_AGENT_DATA` und `MATOLOOP_AGENT_HOME`, sowie bereits in externe Dienste geschriebene Inhalte liegen außerhalb dieser Löschung. Entfernen Sie diese bei Bedarf separat. Instanzressourcen sind gemeinsam und bleiben bestehen.

## Entwicklungsdaten

Die Entwicklungsumgebung verwendet benannte Docker-Volumes. Der Befehl `./scripts/dev.sh reset-storage` löscht Datenbank, Benutzer, Secrets und Session-Dateien vollständig und darf nicht als normale Aufräumaktion verwendet werden. Die separat eingebundenen Verzeichnisse `MATOLOOP_AGENT_DATA` und `MATOLOOP_AGENT_HOME` werden nicht gelöscht.

## Aktualisierung

Sichern Sie Datenbank und Datenverzeichnis vor einem Update gemeinsam. Datenbankschemaänderungen werden beim Start über Alembic-Migrationen angewendet. Prüfen Sie nach dem Update Anmeldung, Providerverbindungen, Dateizugriff, Memory und laufende Integrationen.

Beim Wechsel vom früheren Compose-Dienst liegt dessen Zugang gewöhnlich im benannten Volume `<compose-projekt>_execution_agent_data`. Ermitteln Sie den exakten Namen mit `docker volume ls`, stoppen Sie den alten Dienst und kopieren Sie den vollständigen Volume-Inhalt einschließlich Dateirechten vor dem ersten neuen Start in `MATOLOOP_AGENT_DATA`. Prüfen Sie danach, dass `credential.json` und gegebenenfalls `credential.pending.json` nicht für andere Benutzer lesbar sind. Bleiben diese Daten nicht erhalten, entkoppeln Sie den bestehenden Computer zuerst in MatoLoop und koppeln ihn anschließend neu. Alte Umgebungsvariablen-Aliase werden nicht unterstützt.

### Removed Git Integration

Before restarting an upgraded MatoLoop Agent, stop the agent and update its existing configuration JSON:

1. Remove only the `gitExecutable` and `allowNetwork` fields, if present. The strict schema rejects these removed fields; setting them to `null` or `false` is not sufficient. Preserve all other fields, especially pairing and connection configuration.
2. Remove only `<config-parent>/git-home`, if present. `<config-parent>` is the directory containing the agent configuration file; for Compose's `/data/config.json`, this is `/data/git-home` inside the agent container, or `MATOLOOP_AGENT_DATA/git-home` on the host. This directory is state owned by the removed integration. Do not remove the parent directory, pairing credentials, repositories, workspaces, or history.
3. Restart the upgraded agent. The backend data migration cleans obsolete Git integration settings automatically; do not manually delete database records or historical messages, tool results, or approvals. No re-pairing is needed when the existing connection configuration and credentials are preserved.

Git remains available through shell commands on a selected, connected agent with Git installed, under the existing shell permissions. The backend does not clone repositories.

### Veröffentlichtes Image verwenden

Die normale Compose-Datei verwendet veröffentlichte Images. Setzen Sie beide Image-Adressen auf denselben unveränderlichen Forgejo-CI-Tag:

```env
MATO_LOOP_IMAGE=git.devmato.net/devmato/matoloop/mato-loop:sha-0123456789ab
MATOLOOP_AGENT_IMAGE=git.devmato.net/devmato/matoloop/matoloop-agent:sha-0123456789ab
```

Melden Sie Docker bei einer privaten Registry an. Laden Sie die Images anschließend ausdrücklich und erstellen Sie die Container neu:

```bash
docker compose pull
docker compose up -d
```

Die veröffentlichten Images unterstützen derzeit nur `linux/amd64`. Native Agent-Installationen verwenden stattdessen den Installer aus einem ausgewählten Git-Branch; CI veröffentlicht dafür kein separates Archiv. Eine Installation oder Aktualisierung auf Ihrem Server erfolgt nicht automatisch.

Der Tag `develop` ist veränderlich und nur für kurzlebige Testinstallationen vorgesehen. Für reproduzierbare Installationen und Rollbacks verwenden Sie den zugehörigen `sha-<12>`-Tag und führen vor jeder Neuerstellung `docker compose pull` aus.

### Rollback

Setzen Sie für beide Images den vorherigen festen `sha-<12>`- oder Release-Tag, laden Sie ihn und erstellen Sie die Container erneut. Verwenden Sie weder `latest` noch `develop`, da beide Tags auf andere Images verschoben werden können.

!!! warning "Datenbankschema beim Rollback"
    Beim Start können Datenbankmigrationen ausgeführt werden. Ein älteres Image kann mit dem bereits migrierten Schema unvereinbar sein. Stellen Sie dann zusätzlich die gemeinsam vor dem Update gesicherte Datenbank und das zugehörige Datenverzeichnis wieder her.
