# Skills

Skills sind wiederverwendbare Anweisungen für bestimmte Aufgaben. Ein Skill-Paket enthält eine `SKILL.md` mit Metadaten und Anweisungen sowie optional Skripte, Referenzen und Vorlagen. MatoLoop verwaltet die Originale zentral im Backend. Der wirksame Katalog enthält aktivierte globale, mitgelieferte und erlaubte persönliche Skills. Sie stellen keine Tools bereit und umgehen keine Sicherheitsgrenzen; beispielsweise können sie API-Prüfung oder Release Notes anleiten.

## Skills verwenden

Bei einer klar passenden Anfrage lädt der Agent den Skill aus dem wirksamen Katalog. Sie können Skills auch ausdrücklich nennen und mehrere in einem Lauf kombinieren. Vor dem Laden sieht das Modell nur Name und Einsatzbeschreibung, danach die vollständigen Anweisungen. Referenzdateien liest es bei Bedarf nach.

Ist Webrecherche für die Session verpflichtend, darf der Agent zunächst einen eindeutig passenden Skill laden. Die vorgeschriebene Suche bleibt trotzdem erforderlich und folgt im nächsten Schritt.

Im gewählten Agentenprofil legen Sie die Rechte für **Skills laden** und **Skills speichern** getrennt für private und öffentliche Modelle fest:

- **Nicht erlaubt** deaktiviert die Aktion.
- **Nachfragen** zeigt vor der Aktion eine Freigabe.
- **Automatisch** führt die Aktion ohne einzelne Freigabekarte aus.

Eine Freigabe **Für Session erlauben** kann weitere passende Skill-Aktionen in derselben Session abdecken und lässt sich bei den Session-Freigaben widerrufen.

Für Skripte und lokale Ressourcen stellt MatoLoop eine Arbeitskopie auf dem ausgewählten Computer bereit. Die normalen Datei- und Shell-Rechte gelten weiterhin; benötigte Programme müssen dort verfügbar sein. Nach einem Computerwechsel wird die Arbeitskopie auf dem neuen Computer bereitgestellt. Änderungen an Arbeitskopien verändern nicht automatisch die zentrale Bibliothek.

## Im Chat erstellen

Bitten Sie im Chat ausdrücklich um einen wiederverwendbaren Skill, zum Beispiel: „Erstelle einen Skill namens api-pruefung, der API-Beschreibungen auf gültige Tool-Schemas prüft.“ Nur eine direkte Bitte in Ihrer neuesten Nachricht startet die Erstellung. Zitierte oder zu übersetzende Skill-Anweisungen werden als Text behandelt und lösen keine Erstellung aus.

Neue Chat-Skills sind standardmäßig persönlich, auch bei Administratoren. Administratoren können ausdrücklich einen globalen Skill für alle Benutzer anfordern. Der Geltungsbereich ist im Entwurf sichtbar und wird vor dem Speichern erneut geprüft.

Der Agent erstellt Skills mit dem normalen Sessionmodell und den verfügbaren Werkzeugen. Er kann Dokumentation recherchieren, Dateien lesen oder herunterladen und ein Paket aus `SKILL.md`, Referenzen, Vorlagen und Skripten zusammenstellen. Bereits vorhandene Dateien kann er unverändert in das Paket kopieren.

Fehlen für einen wiederverwendbaren Skill relevante Angaben, fragt der Agent gezielt nach. Bei einer vollständigen Anfrage beginnt er direkt mit Recherche und Dateierstellung.

Beispiel: „Erstelle einen Skill für Anwendung X. Recherchiere die offizielle API-Dokumentation, lege die benötigten Referenzen unter `references/` ab und verweise aus der `SKILL.md` darauf.“ Die Dateien bleiben Bestandteil des gespeicherten Pakets und können bei späterer Nutzung nachgelesen werden. Sie werden nicht automatisch mit der externen Dokumentation synchronisiert.

Es gelten die normalen Modell-, Kontext- und Werkzeuglimits sowie die Rechte der Session. Ein separates Skill-Modell oder ein eigenes kleines Generierungsbudget gibt es nicht. Zum Schreiben benötigt der Agent die entsprechenden Dateiwerkzeuge; für Webrecherche, Downloads oder Skripttests die jeweiligen weiteren Werkzeuge.

## Im Chat bearbeiten

Bitten Sie ausdrücklich um die Änderung eines vorhandenen Skills, zum Beispiel: „Ändere beim Songfinder nur die Einsatzbeschreibung.“ Der Agent wählt den Skill anhand seiner festen ID aus. Name, Einsatzbeschreibung, Anweisungen, zusätzliche Metadaten und unterstützende Textdateien können geändert werden; die ID bleibt erhalten. Globale Änderungen erfordern Administratorrechte und eine ausdrückliche Anfrage.

MatoLoop stellt eine vollständige Arbeitskopie bereit, ohne die Anweisungen des Skills für die Bearbeitung zu aktivieren. Der Agent liest und ändert die gewünschten Dateien mit den normalen Dateiwerkzeugen und reicht anschließend das vollständige Paket zum Speichern ein. Unveränderte Dateien und Metadaten bleiben in der Arbeitskopie erhalten. Wird der gespeicherte Skill währenddessen anderweitig geändert, bricht das Speichern ab, statt die neuere Fassung zu überschreiben.

## Entwurf prüfen und freigeben

Bei der Richtlinie **Nachfragen** zeigt die Freigabekarte Geltungsbereich, Name, Einsatzbeschreibung, Anweisungen und Paketdateien. Über **Entwurf öffnen und bearbeiten** öffnen Sie die `SKILL.md` im Session-Dateibereich. Begleitdateien liegen im selben Entwurfsordner. Speichern Sie Änderungen und wählen Sie **Paketvorschau aktualisieren**, bevor Sie entscheiden.

MatoLoop importiert nur den exakt geprüften Paketstand einschließlich aller Skripte und Begleitdateien. Geänderte oder ungültige Entwürfe werden abgelehnt. Lauf-Freigaben gelten im ausführenden Lauf, Session-Freigaben auch später. Persönliche und globale Freigaben sind getrennt; Bearbeitungsrechte gelten nur für den ausgewählten Skill. **Automatisch** importiert den ursprünglichen generierten Entwurf direkt.

Der Entwurfsordner bleibt nach Import, Ablehnung oder Fehler in den Session-Dateien erhalten. Sie können ihn dort später prüfen oder manuell löschen.

## Verwalten

Eigene Skills verwalten Sie unter **Einstellungen > Skills**, globale unter **Administration > Skills > Verwaltung**. **Persönliche Skills erlauben** auf der administrativen Einstellungsseite ist anfangs aktiv. Deaktivierte persönliche Skills und Änderungen bleiben lesbar, werden aber nicht in neuen Läufen verwendet oder bearbeitet; globale Skills bleiben verfügbar. Dies gilt auch für Chat-Erstellung. Administratoren behalten ihre Ausnahme. Ein Skill besteht aus:

- Name mit höchstens 64 Zeichen aus Kleinbuchstaben, Ziffern und einzelnen Bindestrichen; reservierte Dateinamen sind ausgeschlossen
- Einsatzbeschreibung mit höchstens 1.024 Zeichen
- `SKILL.md` mit höchstens 128 KiB sowie optionale Begleitdateien

Über **Importieren** übernehmen Sie eine `SKILL.md` oder ein ZIP-Paket. **Skill erstellen** und das Stiftsymbol öffnen eine eigene Bearbeitungsseite. Dort erhalten Sie über **Paket herunterladen** ein ZIP mit dem vollständigen gespeicherten Skill. Im Paketeditor wählen Sie einzelne Dateien aus; Textdateien lassen sich bearbeiten, Binärdateien herunterladen oder ersetzen. Die gemeinsame Editor-Werkzeugleiste bietet unter anderem Suche, Zeilenumbruch und Markdown-Vorschau. Änderungen speichern Sie mit **Speichern** oder Strg/Cmd+S. Beim Dateiwechsel oder Verlassen der Seite werden Sie auf ungespeicherte Änderungen hingewiesen. Ein Paket darf insgesamt 8 MiB und 256 Dateien enthalten, höchstens 4 MiB je Datei. Die Verzeichnisstruktur ist auf 16 Ebenen und 512 Verzeichnisse einschließlich Paketwurzel begrenzt.

Die persönliche Sammlung hat höchstens 20 Skills; auch der globale Einstellungsbereich ist begrenzt. Namen müssen im jeweiligen Bereich eindeutig sein. Aktivieren Sie gewünschte Skills über den Schalter in der Liste. Löschen und gegebenenfalls Zurücksetzen finden Sie im Drei-Punkte-Menü. Mit **Standard** gekennzeichnete mitgelieferte Skills sind nicht löschbar; ein veränderter Standard lässt sich zurücksetzen, ohne seinen Aktivierungsstatus zu ändern. Änderungen gelten ab dem nächsten Lauf; laufende Agenten behalten ihren Startkatalog.

!!! warning "Keine Secrets speichern"
    Tragen Sie keine Passwörter, API-Schlüssel, Tokens oder andere Geheimnisse in Skills, Chat-Anfragen oder Entwurfsdateien ein. Skill-Inhalte können bei Erstellung, Bearbeitung und Nutzung an das aktive Sessionmodell gesendet werden. Automatische Schutzmaßnahmen erkennen nicht jede Darstellung zuverlässig.
