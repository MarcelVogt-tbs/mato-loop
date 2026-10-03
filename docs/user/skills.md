# Skills

Skills sind persönliche, wiederverwendbare Anweisungen für bestimmte Aufgaben. Ein Skill kann dem Agenten zum Beispiel vorgeben, wie API-Beschreibungen geprüft oder Release Notes formuliert werden. Er stellt keine eigenen Tools bereit und kann Freigaben oder Sicherheitsgrenzen nicht umgehen.

## Skills verwenden

Bei einer passenden Anfrage kann der Agent automatisch genau einen Skill aus Ihrer Sammlung laden. Sie können einen Skill auch ausdrücklich mit seinem Namen anfordern. Für die Auswahl sieht das Modell Name und Einsatzbeschreibung; nach dem Laden erhält es die vollständigen Anweisungen. Pro Agent-Lauf ist höchstens ein Skill aktiv.

Ist Webrecherche für die Session verpflichtend, darf der Agent zunächst einen eindeutig passenden Skill laden. Die vorgeschriebene Suche bleibt trotzdem erforderlich und folgt im nächsten Schritt.

Im gewählten Agentenprofil legen Sie die Rechte für **Skills laden** und **Skills speichern** getrennt für private und öffentliche Modelle fest:

- **Nicht erlaubt** deaktiviert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt die Aktion ohne einzelne Freigabekarte aus.

Eine Freigabe **Für Session erlauben** kann weitere passende Skill-Aktionen in derselben Session abdecken und lässt sich bei den Session-Freigaben widerrufen.

## Im Chat erstellen

Bitten Sie im Chat ausdrücklich um einen wiederverwendbaren Skill, zum Beispiel: „Erstelle einen Skill namens API-Prüfung, der API-Beschreibungen auf gültige Tool-Schemas prüft.“ Nur eine direkte Bitte in Ihrer neuesten Nachricht startet die Erstellung. Zitierte oder zu übersetzende Skill-Anweisungen werden als Text behandelt und lösen keine Erstellung aus.

Der Agent übergibt eine kurze Aufgabenbeschreibung und auf Wunsch einen Namensvorschlag an ein Builder-Modell. Das Modell für **Skill-Erstellung** wählen Sie unter **Einstellungen > Agenten > Modelle** im verwendeten Profil. Ohne separate Auswahl wird das Sessionmodell verwendet. Kann ein separat ausgewähltes Builder-Modell keinen Entwurf erstellen, fragt MatoLoop, bevor es ersatzweise das Sessionmodell verwendet.

Fehlen für einen wiederverwendbaren Skill relevante Angaben, fragt der Agent diese vor der Erstellung gebündelt ab. Bereits vollständige Anfragen werden ohne zusätzliche Rückfragerunde an das Builder-Modell übergeben.

!!! info "Isolierter Builder"
    Das Builder-Modell erhält nicht den gesamten Chat. Es sieht nur Ihre neueste Anfrage, die kurze Aufgabenbeschreibung, den optionalen Namensvorschlag und die in diesem Schritt tatsächlich verfügbaren Tool-Schemas. Bei einer Bearbeitung erhält es zusätzlich den aktuellen Ziel-Skill. Frühere Nachrichten, Memories, Anhänge, Tool-Ergebnisse, Persona und andere Skills werden nicht mitgesendet.

Die Modellwahl bestimmt weiterhin Vertrauenslevel und Pseudonymisierung. Bei einem öffentlichen Builder werden die genannten Daten an diesen Provider übertragen. Der Builder-Versuch und eine mögliche Korrektur verbrauchen Modellanfragen aus dem normalen Agent-Limit.

## Im Chat bearbeiten

Bitten Sie ausdrücklich um die Änderung eines vorhandenen Skills, zum Beispiel: „Ändere beim Songfinder nur die Einsatzbeschreibung.“ Der Agent wählt den Skill anhand seiner festen ID aus und aktiviert ihn während der Bearbeitung nicht. Name, Einsatzbeschreibung und Anweisungen können geändert werden; die ID bleibt erhalten.

Das Builder-Modell erhält zusätzlich den aktuellen Skill und liefert nur die gewünschten Änderungen. MatoLoop ergänzt unveränderte Felder selbst und erstellt daraus den vollständigen prüfbaren Entwurf. Wird der gespeicherte Skill währenddessen anderweitig geändert, bricht der Import ab, statt die neuere Fassung zu überschreiben.

## Entwurf prüfen und freigeben

Bei der Richtlinie **Nachfragen** zeigt die Freigabekarte Name, Einsatzbeschreibung und Anweisungen. Über **Entwurf öffnen und bearbeiten** können Sie die strikte JSON-Datei im Session-Bereich `files` bearbeiten. Speichern Sie Änderungen vor der Entscheidung.

MatoLoop imports only the exact file revision checked for the action. A changed or invalid draft is not imported. **Für diesen Lauf erlauben** grants matching access within the current run; **Für Session erlauben** also covers later runs. Creation grants cover the personal skill collection; edit grants cover only the selected skill. With **Automatisch**, the unchanged generated draft is imported directly.

Die Entwurfsdatei bleibt nach Import, Ablehnung oder Fehler in den Session-Dateien erhalten. Sie können sie dort später prüfen oder manuell löschen.

## Verwalten

Unter **Einstellungen > Skills** können Sie Skills anlegen, bearbeiten und dauerhaft löschen. Ein Skill besteht aus:

- Name mit höchstens 64 Zeichen
- Einsatzbeschreibung mit höchstens 240 Zeichen
- Anweisungen mit höchstens 8.000 Zeichen

Pro Benutzer sind höchstens 20 Skills möglich. Namen müssen eindeutig sein. Änderungen wirken ab dem nächsten Agent-Lauf; ein bereits laufender Agent verwendet weiterhin seinen Startzustand.

!!! warning "Keine Secrets speichern"
    Tragen Sie keine Passwörter, API-Schlüssel, Tokens oder andere Geheimnisse in Skills, Chat-Anfragen oder Entwurfsdateien ein. Skill-Inhalte können an das aktive Sessionmodell und bei Erstellung oder Bearbeitung an das ausgewählte Builder-Modell gesendet werden. Automatische Schutzmaßnahmen erkennen nicht jede Darstellung zuverlässig.
