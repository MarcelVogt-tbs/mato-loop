# Skills

Skills sind persönliche, wiederverwendbare Anweisungen für bestimmte Aufgaben. Ein Skill kann dem Agenten zum Beispiel vorgeben, wie API-Beschreibungen geprüft oder Release Notes formuliert werden. Er stellt keine eigenen Tools bereit und kann Freigaben oder Sicherheitsgrenzen nicht umgehen.

## Skills verwenden

Bei einer passenden Anfrage kann der Agent automatisch genau einen Skill aus Ihrer Sammlung laden. Sie können einen Skill auch ausdrücklich mit seinem Namen anfordern. Für die Auswahl sieht das Modell Name und Kurzbeschreibung; nach dem Laden erhält es die vollständigen Anweisungen. Pro Agent-Lauf ist höchstens ein Skill aktiv.

Ist Webrecherche für die Session verpflichtend, darf der Agent zunächst einen eindeutig passenden Skill laden. Die vorgeschriebene Suche bleibt trotzdem erforderlich und folgt im nächsten Schritt.

Unter **Einstellungen > Tools** gelten getrennte Richtlinien für **Skills laden** und **Skills erstellen**:

- **Nicht erlaubt** deaktiviert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt die Aktion ohne einzelne Freigabekarte aus.

Eine Freigabe **Für Session erlauben** kann weitere passende Skill-Aktionen in derselben Session abdecken und lässt sich bei den Session-Freigaben widerrufen.

## Im Chat erstellen

Bitten Sie im Chat ausdrücklich um einen wiederverwendbaren Skill, zum Beispiel: „Erstelle einen Skill namens API-Prüfung, der API-Beschreibungen auf gültige Tool-Schemas prüft.“ Nur eine direkte Bitte in Ihrer neuesten Nachricht startet die Erstellung. Zitierte oder zu übersetzende Skill-Anweisungen werden als Text behandelt und lösen keine Erstellung aus.

Der Agent übergibt eine kurze Aufgabenbeschreibung und auf Wunsch einen Namensvorschlag an ein Builder-Modell. Das Modell für **Skill-Erstellung** wählen Sie unter **Einstellungen > Standardmodelle**. Ohne separate Auswahl wird das Sessionmodell verwendet. Kann ein separat ausgewähltes Builder-Modell keinen Entwurf erstellen, fragt MatoLoop, bevor es ersatzweise das Sessionmodell verwendet.

Fehlen für einen wiederverwendbaren Skill relevante Angaben, fragt der Agent diese vor der Erstellung gebündelt ab. Bereits vollständige Anfragen werden ohne zusätzliche Rückfragerunde an das Builder-Modell übergeben.

!!! info "Isolierte Erstellung"
    Das Builder-Modell erhält nicht den gesamten Chat. Es sieht nur Ihre neueste Anfrage, die kurze Aufgabenbeschreibung, den optionalen Namensvorschlag und die in diesem Schritt tatsächlich verfügbaren Tool-Schemas. Frühere Nachrichten, Memories, Anhänge, Tool-Ergebnisse, Persona und andere Skills werden nicht mitgesendet.

Die Modellwahl bestimmt weiterhin Vertrauenslevel und Pseudonymisierung. Bei einem öffentlichen Builder werden die genannten Daten an diesen Provider übertragen. Der Erstellungsversuch und eine mögliche Korrektur verbrauchen Modellanfragen aus dem normalen Agent-Limit.

## Entwurf prüfen und freigeben

Bei der Richtlinie **Nachfragen** zeigt die Freigabekarte Name, Kurzbeschreibung und Anweisungen. Über **Entwurf öffnen und bearbeiten** können Sie die strikte JSON-Datei im Session-Bereich `files` bearbeiten. Speichern Sie Änderungen vor der Entscheidung.

MatoLoop importiert nur exakt die Dateiversion, deren SHA-256-Revision Sie freigeben. Eine zwischenzeitlich erneut geänderte oder ungültige Datei wird nicht übernommen. **Einmal erlauben** importiert diesen Skill; **Für Session erlauben** kann spätere Skill-Erstellungen derselben Session ohne neue Karte zulassen. Bei **Automatisch** wird der unveränderte generierte Entwurf direkt importiert.

Die Entwurfsdatei bleibt nach Import, Ablehnung oder Fehler in den Session-Dateien erhalten. Sie können sie dort später prüfen oder manuell löschen.

## Verwalten

Unter **Einstellungen > Skills** können Sie Skills anlegen, bearbeiten und dauerhaft löschen. Ein Skill besteht aus:

- Name mit höchstens 64 Zeichen
- Kurzbeschreibung mit höchstens 240 Zeichen
- Anweisungen mit höchstens 8.000 Zeichen

Pro Benutzer sind höchstens 20 Skills möglich. Namen müssen eindeutig sein. Änderungen wirken ab dem nächsten Agent-Lauf; ein bereits laufender Agent verwendet weiterhin seinen Startzustand.

!!! warning "Keine Secrets speichern"
    Tragen Sie keine Passwörter, API-Schlüssel, Tokens oder andere Geheimnisse in Skills, Chat-Anfragen oder Entwurfsdateien ein. Skill-Inhalte können an das aktive Sessionmodell und bei der Erstellung an das ausgewählte Builder-Modell gesendet werden. Automatische Schutzmaßnahmen erkennen nicht jede Darstellung zuverlässig.
