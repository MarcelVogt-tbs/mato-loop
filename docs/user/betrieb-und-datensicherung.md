# Betrieb und Datensicherung

MatoLoop wird selbst gehostet. Die Produktionsvorlagen verwenden veröffentlichte Images; Entwicklung und Prüfungen verwenden dagegen `./scripts/dev.sh`. Die folgenden Betriebsbefehle gehören in das Verzeichnis deiner Produktionskonfiguration.

## Erstinstallation

Benötigt werden Docker Engine, Compose v2 und für veröffentlichte Images ein `linux/amd64`-Host. Verwende `docker-compose.yaml` und `.env.example` aus dem zu installierenden Release des [GitHub-Repositorys](https://github.com/devmato/mato-loop). Kopiere `.env.example` nach `.env` und bearbeite die Werte vor dem Start:

- Zufälliges URL-sicheres `POSTGRES_PASSWORD`, beispielsweise aus Hex-Zeichen.
- `MATO_LOOP_IMAGE` und `MATOLOOP_AGENT_IMAGE` mit demselben tatsächlich veröffentlichten festen `YYYY-MM-DD-<SHA8>`-Tag aus einem erfolgreichen Veröffentlichungsdurchlauf.
- `MATO_LOOP_PUBLIC_BASE_URL` als deinen öffentlichen HTTP(S)-Ursprung ohne Pfad, nicht den Beispielhost. **Administration > Grundeinstellungen** überschreibt später diesen Fallback; ist beides leer, gilt der eingehende Serverursprung.
- Hostpfade für Datenbank, Backend und Agent sowie HTTP-Adresse/Port. Die Vorlage verwendet `/opt/mato-loop` und `/opt/matoloop-agent` und veröffentlicht HTTP-Port `10020` auf `0.0.0.0`.
- HTTPS am Reverse Proxy und `MATO_LOOP_SECURE_COOKIES=true`. Die Vorlage liefert TLS nicht selbst; ein entferntes reines HTTP-Login mit Secure-Cookie ist keine passende Produktionskonfiguration.

Bei einem Proxy auf demselben Host setze `MATO_LOOP_HOST_ADDRESS=127.0.0.1`; bei einem anderen Proxy eine geeignete private Adresse. Für ausdrücklich vertrauenswürdigen HTTP-Netzbetrieb kann Secure-Cookie deaktiviert werden. Speichere `.env` mit beschränkten Dateirechten, nicht im Repository.

```bash
cp .env.example .env
# Hier .env bearbeiten und HTTPS einrichten.
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail=100 mato-loop
```

Öffne anschließend den konfigurierten HTTPS-Ursprung, lege das erste Administratorkonto an und folge dem [Schnellstart](schnellstart.md). Infinity lädt beim ersten Start sein gepinntes Modell; das kann Minuten dauern. Lexikalische Suche bleibt ohne Embeddings verfügbar.

Für die Variante ohne Agent verwende bei allen Befehlen `docker compose -f docker-compose.no-agent.yaml`. Beim Wechsel eines bestehenden Stacks ergänzt `up -d --remove-orphans` die Entfernung des früheren Agent-Containers. Bestehende Installationen übernehmen ihre bisherigen Datenbankwerte und Hostpfade ausdrücklich; ein Umzug persistenter Daten ist eine gesonderte Migration.

### Reverse Proxy und Monitoring

Veröffentliche MatoLoop am Ursprung ohne URL-Unterpfad. Der Proxy muss WebSocket-Upgrades und lang laufende Verbindungen unterstützen sowie SSE ohne Antwortpufferung weiterleiten. Stimme Timeouts und Uploadlimits mit deinem Einsatz ab. PostgreSQL und Infinity bleiben intern; die Produktionsvorlagen veröffentlichen deren Ports nicht.

- `/api/health/live` zeigt Prozess-Liveness.
- `/api/health` ist Readiness; Datenbank-/pgvector-Fehler ergeben HTTP 503.
- `embedding: "degraded"` bei sonst `status: "ok"` bedeutet fehlende Embeddings, nicht ausgefallenen Chat.

Überwache Containerstatus, Fehlerlogs, freien Speicher, Backup-Erfolg und Embedding-Degradation getrennt. Der Produktionsdienst heißt `mato-loop`, der Entwicklungsdienst `backend`. API-Zugangsdaten und OAuth-Callback-Querywerte gehören nicht in Proxylogs.

## App und Push-Benachrichtigungen

Ein Administrator richtet Web Push unter **Administration > Benachrichtigungen** mit einer überwachten Kontaktadresse ein. MatoLoop benötigt vertrauenswürdiges HTTPS und erzeugt `/data/secrets/web-push-vapid.pem`. Sichere diesen Schlüssel gemeinsam mit Datenbank und Backenddaten; Verlust macht bestehende Geräteabonnements unbrauchbar.

Installation und Aktivierung pro Gerät stehen unter [App und Push verwenden](anmeldung-und-oberflaeche.md#app-und-push-verwenden).

## Optionalen Computer einrichten

Folge [Computer verbinden und Terminal verwenden](computer-und-terminal.md). Dort stehen Kopplung, Docker-, Linux- und Windows-Installation sowie die Betriebssystemrechte des Agents.

## Zu sichernde Daten

Eine vollständige Wiederherstellung braucht einen gemeinsamen Stand von:

- PostgreSQL-Datenbank und Backend-`/data`, einschließlich Dateien/Versionen und `/data/secrets/credential.key`.
- `/data/secrets/web-push-vapid.pem`, falls Push eingerichtet ist.
- Agent-Konfiguration und vollständigem dauerhaften Datenverzeichnis sowie benötigten Home-/Workspaces.
- `.env`, gewählter Compose-Konfiguration, Proxykonfiguration und tatsächlich eingesetzten Image-Tags.

Provider-, OAuth- und persönliche Variable-Zugangsdaten liegen verschlüsselt in der Datenbank. Ohne passenden Fernet-Schlüssel sind sie nicht entschlüsselbar. Aktuelle Chat-Browserprofile sind flüchtig und werden nach Laufzeitende nicht wiederhergestellt. Ältere persistente Browserprofile können weiterhin sensible unverschlüsselte Websitecookies im Agent-Datenverzeichnis enthalten; sie werden weder geladen noch automatisch gelöscht. Behandle solche Altbestände und ihre Sicherungen entsprechend vertraulich.

Infinity-Modellcache und wegwerfbarer Agent-Sessioncache sind reproduzierbar. Agent-Zugangsdaten sind dagegen für eine Wiederherstellung ohne neue Kopplung erforderlich. Linux verwendet `<config-parent>/agent-state/credential.json` mit Dateimodus `0600` und eigentümergebundenem Verzeichnis `0700`. Windows verwendet DPAPI-gebundenes `agent-state/credential.dat`: eine Kopie ist ohne denselben entschlüsselbaren Windows-Benutzerkontext nicht portabel. Fehlt dieser Kontext, installiere die Tasks neu und kopple erneut.

### Konsistente Sicherung mit PostgreSQL-Dump

Dieses Beispiel verwendet **die Standardhostpfade der Vorlage**. Passe Archivquellen an deine tatsächlichen `.env`-Pfade an; der Backupordner muss außerhalb aller gesicherten Daten liegen. Stoppe außerdem andere native Agents und Prozesse, die in gesicherte Workspaces schreiben. Verwende einen neuen privaten Backupordner:

```bash
umask 077
BACKUP_DIR="$HOME/matoloop-backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p -m 700 "$BACKUP_DIR"
docker compose config --images > "$BACKUP_DIR/images.txt"
cp .env docker-compose.yaml "$BACKUP_DIR/"
# Laufende Aufträge beenden, Browser schließen und externe Workspace-Schreiber stoppen.
docker compose stop mato-loop matoloop-agent
docker compose exec -T db sh -c 'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom' > "$BACKUP_DIR/database.dump"
sudo tar --acls --xattrs --numeric-owner -C /opt/mato-loop -cpf "$BACKUP_DIR/backend-data.tar" data
sudo tar --acls --xattrs --numeric-owner -C /opt/matoloop-agent -cpf "$BACKUP_DIR/agent-data.tar" data home
```

Prüfe **jeden Befehl auf Erfolg**, bevor du den Backupstand als vollständig markierst. Sichere Proxy-/native Agentkonfigurationen zusätzlich. Die Datenbank bleibt für den logischen Dump aktiv, Backend- und Agent-Schreiber dagegen gestoppt. Ein laufendes PostgreSQL-Datenverzeichnis darf nicht als gewöhnliche Dateikopie dieses Dumps behandelt werden; eine physische Kopie erfordert einen vollständig gestoppten Datenbankdienst und kompatible PostgreSQL-Version.

Liste Dump/Archive zur Prüfung auf und starte danach den vorherigen Betrieb:

```bash
docker compose exec -T db pg_restore --list < "$BACKUP_DIR/database.dump"
sudo tar -tf "$BACKUP_DIR/backend-data.tar"
sudo tar -tf "$BACKUP_DIR/agent-data.tar"
docker compose up -d
```

Archive und Konfiguration sind vertraulich und benötigen geschützte, möglichst getrennt gelagerte Kopien. Das erfolgreiche Auflisten ist noch kein Wiederherstellungstest.

### Wiederherstellung und Probe

Verwende einen frischen isolierten Zielhost bzw. leere geprüfte Zielverzeichnisse, mit gesperrten externen Integrations-/Agentverbindungen. Übernimm die gesicherten festen Image-Tags und die passende Compose-/`.env`-Konfiguration; veröffentliche den Dienst zunächst nicht. Passe Hostpfade bewusst an, ohne Browser-/Agent-Bindungsidentitäten stillschweigend umzuschreiben. Stelle mit gestopptem Backend/Agent die Dateien samt numerischen Eigentümern und Rechten wieder her, für die Standardpfade etwa:

```bash
sudo mkdir -p /opt/mato-loop /opt/matoloop-agent
sudo tar --acls --xattrs --numeric-owner -C /opt/mato-loop -xpf "$BACKUP_DIR/backend-data.tar"
sudo tar --acls --xattrs --numeric-owner -C /opt/matoloop-agent -xpf "$BACKUP_DIR/agent-data.tar"
docker compose pull
docker compose up -d db
# Auf eine gesunde, leere Ziel-Datenbank warten: docker compose ps
docker compose exec -T db sh -c 'pg_restore --exit-on-error --no-owner --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' < "$BACKUP_DIR/database.dump"
docker compose up -d embedding mato-loop
```

Prüfe vor externer Freigabe: Readiness, Anmeldung, Entschlüsselung eines bestehenden Providers, Sessiondateien und historische Versionen, Memory, persönliche Variablen, Automationsdefinitionen und deren Zeitpläne. Begrenze ausgehendes Netz auch beim Backendstart, damit wiederhergestellte Aufgaben keine echten externen Änderungen auslösen. Starte und prüfe den Agenten anschließend kontrolliert; prüfe Zugang, Betriebssystemrechte, Originalbindung, Browserzustand und Workspace. Nicht entschlüsselbare Windows-Zugänge erfordern neue Kopplung.

Erst nach der Probe stellst du Netzwerk und öffentliche URL wieder her und prüfst Integrationen und Push gezielt. Verpasste Automationstermine werden nicht rückwirkend ausgeführt. Dokumentiere Datum, Image-Tags und Prüfergebnis jeder Wiederherstellungsprobe. Dieser Ablauf ist eine Betriebsanleitung, kein Nachweis einer für deine Installation bereits ausgeführten Wiederherstellung.

## Entwicklungsdaten

Entwicklungsdaten liegen in benannten Compose-Volumes; Agentdaten in separaten Bindpfaden. `./scripts/dev.sh reset-storage` löscht alle benannten Entwicklungsvolumes einschließlich Datenbank, Konten, Secrets und Backenddateien. Es ist keine normale Aufräumaktion. Parallelcheckouts müssen eigene Compose-Projektnamen, Ports und Agentpfade verwenden; Standardcheckouts teilen sonst dasselbe Projekt.

## Aktualisierung

Verwende [MatoLoop aktualisieren](aktualisierung.md) für App- und Agent-Updates. Prüfe vor einem Update die dortigen Upgrade-Hinweise und erstelle eine gemeinsame Sicherung.

### Migration vom früheren Compose-Agenten

Siehe [Migration des Agent-Volumes](aktualisierung.md#migration-vom-fruheren-compose-agenten).

<a id="removed-git-integration"></a>

### Entfernte Git-Integration

Die erforderliche [gezielte Bereinigung](aktualisierung.md#removed-git-integration) steht im Upgrade-Guide. Entferne keine Repositories oder Zugangsdaten.

### Rollback

Folge dem [Rollback-Verfahren](aktualisierung.md#rollback). Ein bloßer Imagewechsel macht keine Datenmigration rückgängig.

Beim Löschen eines Benutzers entfernt MatoLoop dessen Datenbankdaten und Backend-Sessionbäume. Dateien, dauerhafter Agentzustand und bereits geschriebene externe Daten bleiben außerhalb dieser Löschung. Gemeinsame Instanzressourcen bleiben bestehen.
