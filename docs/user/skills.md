# Skills

Skills sind wiederverwendbare Anweisungen für bestimmte Aufgaben. Der wirksame Katalog enthält aktivierte globale, mitgelieferte und erlaubte persönliche Skills. Sie stellen keine Tools bereit und umgehen keine Sicherheitsgrenzen; beispielsweise können sie API-Prüfung oder Release Notes anleiten.

## Skills verwenden

Bei einer klar passenden Anfrage lädt der Agent genau einen Skill aus dem wirksamen Katalog. Sie können ihn auch ausdrücklich nennen. Vor dem Laden sieht das Modell nur Name und Einsatzbeschreibung, danach die vollständigen Anweisungen. Pro Lauf ist höchstens ein Skill aktiv.

Ist Webrecherche für die Session verpflichtend, darf der Agent zunächst einen eindeutig passenden Skill laden. Die vorgeschriebene Suche bleibt trotzdem erforderlich und folgt im nächsten Schritt.

Im gewählten Agentenprofil legen Sie die Rechte für **Skills laden** und **Skills speichern** getrennt für private und öffentliche Modelle fest:

- **Nicht erlaubt** deaktiviert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt die Aktion ohne einzelne Freigabekarte aus.

Eine Freigabe **Für Session erlauben** kann weitere passende Skill-Aktionen in derselben Session abdecken und lässt sich bei den Session-Freigaben widerrufen.

## Im Chat erstellen

Bitten Sie im Chat ausdrücklich um einen wiederverwendbaren Skill, zum Beispiel: „Erstelle einen Skill namens API-Prüfung, der API-Beschreibungen auf gültige Tool-Schemas prüft.“ Nur eine direkte Bitte in Ihrer neuesten Nachricht startet die Erstellung. Zitierte oder zu übersetzende Skill-Anweisungen werden als Text behandelt und lösen keine Erstellung aus.

Das Builder-Modell erhält eine kurze Aufgabe und optional einen Namensvorschlag. **Skill-Erstellung** wählen Sie unter **Standardmodelle**; ohne separates Modell dient das Sessionmodell. Scheitert ein separat konfigurierter Builder, fragt MatoLoop vor einem Versuch mit dem Sessionmodell.

Fehlen für einen wiederverwendbaren Skill relevante Angaben, fragt der Agent diese vor der Erstellung gebündelt ab. Bereits vollständige Anfragen werden ohne zusätzliche Rückfragerunde an das Builder-Modell übergeben.

!!! info "Isolierter Builder"
    Das Builder-Modell erhält nicht den gesamten Chat. Es sieht nur Ihre neueste Anfrage, die kurze Aufgabenbeschreibung, den optionalen Namensvorschlag und die in diesem Schritt tatsächlich verfügbaren Tool-Schemas. Bei einer Bearbeitung erhält es zusätzlich den aktuellen Ziel-Skill. Frühere Nachrichten, Memories, Anhänge, Tool-Ergebnisse, Persona und andere Skills werden nicht mitgesendet.

Modellwahl bestimmt Vertrauen und Pseudonymisierung. Öffentliche Builder erhalten die genannten Daten. Builder-Versuche erhöhen Modellanfrage-Metriken; eine eigenständige Anfragezahlgrenze gibt es nicht. Skill-Tools zählen weiterhin zum endlichen Tool-Limit des Profils und behalten ihre begrenzten Korrekturversuche.

## Im Chat bearbeiten

Bitten Sie ausdrücklich um die Änderung eines vorhandenen Skills, zum Beispiel: „Ändere beim Songfinder nur die Einsatzbeschreibung.“ Der Agent wählt den Skill anhand seiner festen ID aus und aktiviert ihn während der Bearbeitung nicht. Name, Einsatzbeschreibung und Anweisungen können geändert werden; die ID bleibt erhalten.

Das Builder-Modell erhält zusätzlich den aktuellen Skill und liefert nur die gewünschten Änderungen. MatoLoop ergänzt unveränderte Felder selbst und erstellt daraus den vollständigen prüfbaren Entwurf. Wird der gespeicherte Skill währenddessen anderweitig geändert, bricht der Import ab, statt die neuere Fassung zu überschreiben.

## Entwurf prüfen und freigeben

Bei der Richtlinie **Nachfragen** zeigt die Freigabekarte Name, Einsatzbeschreibung und Anweisungen. Über **Entwurf öffnen und bearbeiten** können Sie die strikte JSON-Datei im Session-Bereich `files` bearbeiten. Speichern Sie Änderungen vor der Entscheidung.

MatoLoop importiert nur die exakt geprüfte Dateirevision. Geänderte oder ungültige Entwürfe werden abgelehnt. Lauf-Freigaben gelten im ausführenden Lauf, Session-Freigaben auch später. Erstellrechte gelten für die persönliche Sammlung; Bearbeitungsrechte nur für den ausgewählten Skill. **Automatisch** importiert den ursprünglichen generierten Entwurf direkt.

Die Entwurfsdatei bleibt nach Import, Ablehnung oder Fehler in den Session-Dateien erhalten. Sie können sie dort später prüfen oder manuell löschen.

## Verwalten

Eigene Skills verwalten Sie unter **Einstellungen > Skills**, globale unter **Administration > Skills > Verwaltung**. **Persönliche Skills erlauben** auf der administrativen Einstellungsseite ist anfangs aktiv. Deaktivierte persönliche Skills und Änderungen bleiben lesbar, werden aber nicht in neuen Läufen verwendet oder bearbeitet; globale Skills bleiben verfügbar. Dies gilt auch für Chat-Erstellung. Administratoren behalten ihre Ausnahme. Ein Skill besteht aus:

- Name mit höchstens 64 Zeichen
- Einsatzbeschreibung mit höchstens 240 Zeichen
- Anweisungen mit höchstens 8.000 Zeichen

Die persönliche Sammlung hat höchstens 20 Skills; auch der globale Einstellungsbereich ist begrenzt. Namen müssen im jeweiligen Bereich eindeutig sein. Aktivieren Sie gewünschte Skills ausdrücklich. Mit **Standard** gekennzeichnete mitgelieferte Skills sind nicht löschbar; ein veränderter Standard lässt sich zurücksetzen, ohne seinen Aktivierungsstatus zu ändern. Änderungen gelten ab dem nächsten Lauf; laufende Agenten behalten ihren Startkatalog.

!!! warning "Keine Secrets speichern"
    Tragen Sie keine Passwörter, API-Schlüssel, Tokens oder andere Geheimnisse in Skills, Chat-Anfragen oder Entwurfsdateien ein. Skill-Inhalte können an das aktive Sessionmodell und bei Erstellung oder Bearbeitung an das ausgewählte Builder-Modell gesendet werden. Automatische Schutzmaßnahmen erkennen nicht jede Darstellung zuverlässig.
