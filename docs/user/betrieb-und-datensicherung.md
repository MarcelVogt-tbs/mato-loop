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

## App und Push-Benachrichtigungen

Der Produktionsbuild ist eine installierbare Web-App. Betreiben Sie MatoLoop über eine vertrauenswürdige HTTPS-Adresse und setzen Sie `MATO_LOOP_SECURE_COOKIES=true`. Unter iOS und iPadOS stehen Push-Benachrichtigungen ab Version 16.4 nur zur Verfügung, nachdem MatoLoop über **Zum Home-Bildschirm** installiert wurde. Die Berechtigung wird ausschließlich über den Schalter im Benachrichtigungsbereich angefordert.

Web Push ist standardmäßig deaktiviert. Aktivieren Sie nur die tatsächlich verwendeten Push-Dienste mit exakten HTTPS-Ursprüngen:

```env
MATO_LOOP_WEB_PUSH_ENABLED=true
MATO_LOOP_WEB_PUSH_VAPID_SUBJECT=mailto:admin@example.com
MATO_LOOP_WEB_PUSH_ALLOWED_ORIGINS=https://fcm.googleapis.com,https://updates.push.services.mozilla.com,https://web.push.apple.com
```

Die VAPID-Kennung muss eine betreute `mailto:`-Adresse oder eine öffentliche HTTPS-Adresse sein. MatoLoop erzeugt beim ersten Abruf der Push-Konfiguration den privaten Schlüssel `/data/secrets/web-push-vapid.pem`. Sichern Sie ihn zusammen mit Datenbank und Datenverzeichnis; ein verlorener Schlüssel macht bestehende Geräte-Abonnements unbrauchbar. Push-Zustellung erfolgt bestmöglich und ersetzt weder die gespeicherten Benachrichtigungen noch den Chatverlauf.

Für den PWA-Entwicklungsmodus auf dem normalen Entwicklungsport `10020` verwenden Sie:

```bash
./scripts/dev.sh up --pwa --build -d
```

`localhost` gilt im Desktop-Browser als vertrauenswürdig. Für Tests auf Mobilgeräten ist auch in der Entwicklung eine HTTPS-Adresse erforderlich. Der PWA-Worker speichert keine Anwendungs-, API-, SSE-, Datei- oder Authentifizierungsdaten offline.

## Optionalen Computer einrichten

MatoLoop kann mehrere Computer verwalten. Öffnen Sie **Einstellungen > Computer**, wählen Sie **Computer hinzufügen**, vergeben Sie einen Namen und wählen Sie die Gültigkeit des Kopplungstokens zwischen 30 Minuten und 24 Stunden. Der Dialog zeigt Computer-ID, WebSocket-Adresse und das nur einmal sichtbare Token. Der Computer erzeugt damit beim ersten Start einen eigenen dauerhaften Zugangsschlüssel. Ein abgelaufenes oder verlorenes Kopplungstoken kann in der Übersicht ersetzt werden. Ein bereits gekoppelter Computer muss vor einer erneuten Kopplung ausdrücklich entkoppelt werden.

Der Arbeitsbereich kann vor dem Start angepasst werden:

```env
MATO_EXECUTION_AGENT_WORKSPACE=./mein-agent-arbeitsbereich
```

Ohne diese Variable verwendet Compose das Host-Verzeichnis `./data/home`. Es wird ausschließlich als `/workspace` in den mitgelieferten Computer eingebunden. Das Backend greift nicht darauf zu und migriert oder löscht seinen Inhalt nicht.

Der normale Produktions- und Entwicklungsstack startet den Computer automatisch. Solange er ungekoppelt ist, wartet er auf die einmalige Kopplung. Führen Sie diese im laufenden Produktionscontainer aus:

```bash
docker compose exec execution-agent mato-loop-execution-agent pair
```

In der Entwicklungsumgebung lautet der Befehl:

```bash
docker compose -f docker-compose.dev.yaml exec execution-agent \
  uv run --no-sync mato-loop-execution-agent pair
```

Der Dialog fragt die Backend-WebSocket-Adresse, die Computer-ID und das verdeckt eingegebene Kopplungstoken ab. Bereits gesetzte Werte werden als Vorgabe angeboten. Für eine automatische Kopplung müssen `MATO_EXECUTION_AGENT_BACKEND_URL`, `MATO_EXECUTION_AGENT_ID` und `MATO_EXECUTION_AGENT_PAIRING_TOKEN` vollständig im Container gesetzt sein. Compose stellt seine interne Backend-Adresse bereits bereit; Betreiber setzen deshalb noch ID und Token. Unvollständige Verbindungsvariablen starten keine automatische Kopplung. Entfernen Sie das Token nach erfolgreicher Kopplung aus der Umgebung. `MATO_EXECUTION_AGENT_WORKSPACE` wird als `/workspace` eingebunden. Ohne `MATO_EXECUTION_AGENT_CONFIG` verwendet Compose `./apps/execution-agent/config.json`; eine abweichende Datei bestimmt Name, Arbeitsbereichs-IDs, Cachegrenzen, erlaubte Programm-Aliase und die optionale Shell. URL, ID und dauerhafter Zugang liegen im Named Volume `execution_agent_data` unter `/data/agent-state`. Verwenden Sie `docker compose -f docker-compose.no-agent.yaml up -d --remove-orphans` für den eigenständigen Produktionsstack ohne Computer. Linux und Docker sind derzeit die getesteten Umgebungen.

!!! warning "Prozess- und Shell-Zugriff"
    Der Compose-Computer aktiviert mit der Beispielkonfiguration Prozess- und Shell-Funktionen. Sie besitzen keine Betriebssystem-Sandbox. Der Arbeitsordner begrenzt nicht den Datei- oder Netzwerkzugriff eines gestarteten Prozesses. MatoLoop erlaubt deshalb pro Aufruf nur **Einmal erlauben** oder **Ablehnen**.

## Zu sichernde Daten

Für eine vollständige Wiederherstellung benötigen Sie gemeinsam:

- PostgreSQL-Datenbank
- MatoLoop-Datenverzeichnis `/data`
- insbesondere `/data/secrets/credential.key`
- `/data/secrets/web-push-vapid.pem`, falls Web Push aktiviert ist
- alle benötigten Arbeitsbereiche der Computer
- die externe Computer-Konfiguration und der sichere Computer-Zustand unter `dataDir/agent-state`

Provider- und OAuth-Zugangsdaten liegen verschlüsselt in der Datenbank. Ohne den zugehörigen Schlüssel `credential.key` können sie nach einer Wiederherstellung nicht entschlüsselt werden.

Das Modell-Cache-Volume des Embedding-Dienstes und der Session-Cache des Computers müssen nicht gesichert werden. Der Credential-Bereich `agent-state` im Volume `execution_agent_data` ist dagegen für eine Wiederherstellung ohne erneute Kopplung erforderlich. Geht er verloren, entkoppeln Sie den Computer in MatoLoop und stellen ein neues Kopplungstoken aus. Session-Dateien bleiben im Backend gespeichert.

Beim Löschen eines Benutzers entfernt MatoLoop dessen Datenbankdaten und Session-Bäume. Dateien, Caches, dauerhafter Zustand und Konfigurationen aller Computer, einschließlich des Compose-Computers unter `./data/home`, sowie bereits in externe Dienste geschriebene Inhalte liegen außerhalb dieser Löschung. Entfernen Sie diese bei Bedarf separat. Instanzressourcen sind gemeinsam und bleiben bestehen.

## Entwicklungsdaten

Die Entwicklungsumgebung verwendet benannte Docker-Volumes. Der Befehl `./scripts/dev.sh reset-storage` löscht Datenbank, Benutzer, Secrets, Session-Dateien und Computer-Caches vollständig und darf nicht als normale Aufräumaktion verwendet werden. Extern eingebundene Arbeitsbereiche sind davon getrennt.

## Aktualisierung

Sichern Sie Datenbank und Datenverzeichnis vor einem Update gemeinsam. Datenbankschemaänderungen werden beim Start über Alembic-Migrationen angewendet. Prüfen Sie nach dem Update Anmeldung, Providerverbindungen, Dateizugriff, Memory und laufende Integrationen.

### Veröffentlichtes Image verwenden

Die normale Compose-Datei kann MatoLoop und den Computer weiterhin lokal bauen. Für veröffentlichte Images setzen Sie stattdessen beide Image-Adressen mit einem festen, gleichen Versions-Tag:

```env
MATO_LOOP_IMAGE=git.example/owner/mato-loop:1.2.3
MATO_LOOP_EXECUTION_AGENT_IMAGE=git.example/owner/mato-loop-execution-agent:1.2.3
```

Melden Sie Docker bei einer privaten Registry an. Laden Sie die Images anschließend ausdrücklich und erstellen Sie die Container neu:

```bash
docker compose pull
docker compose up -d
```

Die veröffentlichten Images unterstützen derzeit nur `linux/amd64`. Erfolgreiche `develop`-Läufe stellen zusätzlich sieben Tage lang ein natives Computer-Archiv für Linux amd64 mit glibc 2.35 oder neuer bereit. Stabile Releases enthalten noch kein natives Archiv. Eine Installation oder Aktualisierung auf Ihrem Server erfolgt nicht automatisch.

Der Tag `develop` ist veränderlich und nur für Testinstallationen vorgesehen. Verwenden Sie dafür ein eigenes Bereitstellungsverzeichnis: Die relativen Pfade `./data` und `./db` bleiben dadurch von der Produktion getrennt. Führen Sie vor jedem Neustart `docker compose pull` aus. Verwenden Sie `develop` niemals mit Produktivdaten und nicht als Rollback-Ziel.

### Rollback

Setzen Sie für beide Images den vorherigen festen Versions-Tag, laden Sie ihn und erstellen Sie die Container erneut. Verwenden Sie weder `latest` noch `develop`, da beide Tags auf andere Images verschoben werden können.

!!! warning "Datenbankschema beim Rollback"
    Beim Start können Datenbankmigrationen ausgeführt werden. Ein älteres Image kann mit dem bereits migrierten Schema unvereinbar sein. Stellen Sie dann zusätzlich die gemeinsam vor dem Update gesicherte Datenbank und das zugehörige Datenverzeichnis wieder her.
