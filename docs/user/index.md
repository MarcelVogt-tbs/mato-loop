# MatoLoop Dokumentation

MatoLoop ist eine selbst gehostete Arbeitsumgebung für KI-gestützte Chats und Agenten. Sessions, Nachrichten, Dateien, Freigaben und Erinnerungen bleiben in MatoLoop gespeichert, während unterschiedliche Modellanbieter für die eigentliche Modellverarbeitung verwendet werden können.

## Erste Schritte

- [Schnellstart](schnellstart.md): erstes Administratorkonto einrichten, Provider verbinden und die erste Session starten
- [Chat und Sessions](chat-und-sessions.md): Nachrichten, Modelle und Session-Verwaltung
- [Dateien und Arbeitsordner](dateien-und-arbeitsordner.md): Computer, Arbeitsbereiche und Session-Dateien
- [Freigaben und Datenfreigabe](freigaben-und-datenfreigabe.md): Entscheidungen über sensible Aktionen und externe Modelle
- [Skills](skills.md): wiederverwendbare Anweisungen verwenden, im Chat erstellen und verwalten
- [Automationen](automationen.md): Agent-Aufträge manuell sowie mit einmaligen oder wiederkehrenden Zeitplänen ausführen
- [Kanäle](kanaele.md): persönliche Telegram-Bots für Chats und Automationen verbinden

## Administration

Die Einstellungen erreichen Sie über das Benutzermenü unten in der Navigation. Dort verwalten Sie Provider, **Standardmodelle**, Agentenprofile, Tools, Integrationen, Kanäle, Automationen, Personalisierung und Memory. Ihr eigenes Konto öffnen Sie direkt über **Konto** im Benutzermenü. Administratoren verwalten zusätzlich Benutzer und die gemeinsamen Instanzeinstellungen.

Persönliche Sessions, Dateien, Arbeitsbereiche, Erinnerungen, Kanäle und Zugangsdaten sind nach Benutzer getrennt. Gemeinsame Provider, Modelle und Integrationen der Instanz stehen dagegen absichtlich allen Benutzern nach den festgelegten Richtlinien zur Verfügung.

## Grundprinzip

Provider transportieren Modellnachrichten und Antworten. Dateizugriffe, Webzugriffe, Tool-Aufrufe, Freigaben und Memory werden weiterhin durch MatoLoop kontrolliert. Das Modell führt keine Werkzeuge direkt aus.
