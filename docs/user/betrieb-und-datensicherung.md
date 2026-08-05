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

## Zu sichernde Daten

Für eine vollständige Wiederherstellung benötigen Sie gemeinsam:

- PostgreSQL-Datenbank
- MatoLoop-Datenverzeichnis `/data`
- insbesondere `/data/secrets/credential.key`

Provider- und OAuth-Zugangsdaten liegen verschlüsselt in der Datenbank. Ohne den zugehörigen Schlüssel `credential.key` können sie nach einer Wiederherstellung nicht entschlüsselt werden.

Das Modell-Cache-Volume des Embedding-Dienstes muss nicht gesichert werden; das gepinnte Modell kann erneut heruntergeladen werden.

## Entwicklungsdaten

Die Entwicklungsumgebung verwendet benannte Docker-Volumes. Der Befehl `./scripts/dev.sh reset-storage` löscht Datenbank, Benutzer, Secrets und Agent-Dateien vollständig und darf nicht als normale Aufräumaktion verwendet werden.

## Aktualisierung

Sichern Sie Datenbank und Datenverzeichnis vor einem Update gemeinsam. Datenbankschemaänderungen werden beim Start über Alembic-Migrationen angewendet. Prüfen Sie nach dem Update Anmeldung, Providerverbindungen, Dateizugriff, Memory und laufende Integrationen.
