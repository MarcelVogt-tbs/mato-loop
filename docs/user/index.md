# MatoLoop Dokumentation

MatoLoop ist eine selbst gehostete Arbeitsumgebung für KI-Chats und Agenten. Sessions, Nachrichten, Dateien, Freigaben und Erinnerungen bleiben in MatoLoop gespeichert; unterschiedliche Modellanbieter übernehmen die Modellverarbeitung. Die Oberfläche und dieses Handbuch sind deutschsprachig.

## Einstieg und tägliche Arbeit

- [Schnellstart](schnellstart.md): Konto, Provider, Modell und erste Session.
- [Anmeldung und Oberfläche](anmeldung-und-oberflaeche.md): Navigation, Suche, Archiv, Control Center und Darstellung.
- [Chat und Sessions](chat-und-sessions.md): Sessionvorlagen, laufende Aufträge, Nachrichtenwarteschlange und Subagenten.
- [Dateien und Arbeitsordner](dateien-und-arbeitsordner.md): Ablage, Computer-Dateien, Bearbeitung und Versionen.
- [Anhänge, Diktat und Medien](anhaenge-und-medien.md): Uploads, Sprachnachrichten und Medienverarbeitung.
- [Webrecherche und Quellen](webrecherche-und-quellen.md): Suche, Webseiten und Untertitel.
- [Freigaben und Datenfreigabe](freigaben-und-datenfreigabe.md): Aktionsrechte, Modellwahl und Rückfragen.
- [Variablen und Secrets](variablen-und-secrets.md): geschützte Eingaben und gespeicherte Werte.
- [Automationen](automationen.md): Aufgaben, Zeitpläne und Ereignisauslöser.
- [Kanäle](kanaele.md): persönliche Telegram-Bots verbinden.

## Einstellungen und Administration

- [Provider und Modelle](provider-und-modelle.md): Verbindungen, Standardmodelle und Sessionvorlagen.
- [Tools und Integrationen](tools-und-integrationen.md): Rechte und Einrichtung der verfügbaren Dienste.
- [Personalisierung](persona.md): Personas, Zuordnungen und Agentenname.
- [Skills](skills.md): wiederverwendbare Anweisungen verwenden, erstellen und verwalten.
- [Memory und Dreaming](memory-und-dreaming.md): Erinnerungen und frühere Sessions nutzen.
- [Betrieb und Datensicherung](betrieb-und-datensicherung.md): Installation, Computer, Updates und Wiederherstellung.
- [Debug-Zugang](debug-zugang.md): persönlicher schreibgeschützter Diagnosezugang für Administratoren.
- [Häufige Fragen](haeufige-fragen.md) und [Changelog](changelog.md).

**Einstellungen** verwaltet Ihre persönlichen Einträge. **Administration** verwaltet gemeinsame Instanzressourcen und die Freigabe persönlicher Konfiguration. Bereiche mit **Einstellungen / Verwaltung** öffnen zuerst die Einstellungen; **Verwaltung** enthält die jeweiligen Einträge. Agentenprofile beschreiben Arbeitsweise und Rechte, **Standardmodelle** und **Sessionvorlagen** die Modellwahl. Integrationsrechte gehören zur jeweiligen Verbindung.

Persönliche Sessions, Dateien, Erinnerungen, Kanäle, Zugangsdaten und Computer sind nach Benutzer getrennt. Instanzprovider und -integrationen stehen dagegen absichtlich allen Benutzern nach ihren Richtlinien zur Verfügung. Administratorrechte erlauben keinen Zugriff auf persönliche Inhalte anderer Konten.

Persönliche Provider und eigene Tool-Rechte sind für gewöhnliche Benutzer anfangs deaktiviert; persönliche Personas, Skills und Agentenprofile sind aktiviert. Administratoren behalten ihre Ausnahme für persönliche Einstellungen. Kommunikationskanäle müssen unter **Administration > Kanäle > Einstellungen** auch für Administratoren global aktiviert werden; Telegram ist als Anbieter standardmäßig erlaubt. Ausdrücklich gespeicherte Einstellungen bleiben erhalten.

## Grundprinzip

Provider transportieren Modellnachrichten und Antworten. MatoLoop kontrolliert Tool-Ausführung, Dateizugriff, Webzugriff, Memory und Freigaben. Eine erfolgreiche Tool-Ausführung ist kein Beweis dafür, dass das gewünschte Ergebnis richtig ist; prüfen Sie wichtige Ergebnisse anhand der tatsächlichen Daten.
