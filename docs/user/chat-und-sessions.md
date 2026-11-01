# Chats und Sessions organisieren

Eine Session ist eine gespeicherte Unterhaltung mit eigenen Einstellungen, Dateien und Verlauf. Projekte gruppieren Sessions, teilen aber nicht deren Dateiablage.

## Eine Session starten und organisieren

Öffne Home oder eine neue Unterhaltung, wähle die Startvorgaben und sende eine Nachricht. Erst das Senden oder Öffnen einer sessiongebundenen Control-Center-App speichert die Session. Die feste Sessionansicht, Git-Ansicht und globale Benachrichtigungen allein erzeugen keine Session.

Über das Sessionmenü kannst du anpinnen, umbenennen, einem Projekt zuordnen, archivieren oder löschen. Projektvorgaben für Vorlage, Computer und Arbeitsordner gelten für neue Sessions, nicht rückwirkend. Ein Projektwechsel übernimmt einen Entwurf nur in ein leeres Ziel, niemals über dortige Eingaben oder Warteschlangen.

## Session-Einstellungen ändern

Unter **Control Center > Session** bearbeitest du die Vorgaben; Agent, Modell und Reasoning sind auch am Eingabefeld auswählbar.

| Einstellung | Zweck |
| --- | --- |
| Sessionvorlage | Verfügbare Agenten, Startagent, Memory, Nachrichtengröße, Worktree-Unterstützung und optionale Modellvorgaben |
| Agent | Arbeitsweise, Tool-Rechte und Integrationsauswahl |
| Modell und Reasoning | Modell und unterstützte Reasoning-Stufe |
| Memory | Voll, nur lesen oder deaktiviert |
| Websuche | Erzwingen, verfügbar oder deaktiviert |
| Computer und Arbeitsordner | Optionaler lokaler Ausführungsort |

**Allgemein** bietet Allgemein und Sysadmin, **Coding** Plan und Build. Plan und Build benötigen einen verbundenen Computer mit zugänglichem Arbeitsordner. Eine Vorlagenauswahl ersetzt deren Sessionwerte, aber nicht Projekt, Computer, Arbeitsordner oder Websuche. Spätere Vorlagenänderungen wirken nicht rückwirkend.

Modellvorgaben gehören zu [Standardmodellen und Sessionvorlagen](provider-und-modelle.md#standardmodelle-und-sessionvorlagen), nicht ins Agentenprofil. Ein laufender Auftrag behält sein Startprofil und seinen Modellkontext; Änderungen gelten für spätere Läufe. Instanzweite Verfügbarkeit und Rechte bleiben wirksam.

## Befehle verwenden

Gib `/` ins Eingabefeld ein. Tippen filtert die Befehlspalette; Pfeiltasten, Klick, Enter oder Tab wählen einen Eintrag. Ergänze Argumente und sende anschließend den Befehl. Auswahl allein startet nichts; Escape schließt die Palette. Vorhandene Argumente und Anhänge bleiben erhalten.

Die Benutzernachricht zeigt den aufgelösten Auftrag. Rückgängig stellt den ursprünglichen Befehlsaufruf wieder her. Nicht verfügbare Aktionen nennen ihre Voraussetzungen.

### Eine Unterhaltung kopieren

Mit `/fork` kopierst du eine ruhende gespeicherte Session und schreibst unabhängig weiter. Warte zuvor auf laufende und vorgemerkte Aufträge; der Befehl nimmt keine Argumente oder neuen Anhänge an und startet keinen Modellaufruf.

Verlauf, Einstellungen, interne Dateien samt Versionen und Anhänge werden kopiert. Änderungen an den kopierten Session-Dateien wirken nicht aufs Original. Freigaben, offene Fragen, Automationen und Subagenten werden nicht übernommen. Kopien brauchen zusätzlichen Speicher und unterliegen deinem Speicherlimit.

!!! warning "Computerdateien werden nicht isoliert"
    Computer und Arbeitsordner bleiben gleich. Die Kopie erzeugt weder Branch noch Worktree und übernimmt keine Worktree-Verwaltung. Bereits ausgeführte Tool-Aktionen werden auch durch Rückgängig nicht zurückgenommen.

### Git-Projekte und Worktrees

Einrichtung, Diffs, Branches und getrennte Arbeitskopien stehen unter [Git und Worktrees](git-und-worktrees.md).

## Während einer Antwort eingreifen

**Jetzt senden** ergänzt den laufenden Auftrag um deine Nachricht. Eine Modellanfrage wird neu aufgebaut; aktive Tool-Arbeit darf zuerst enden. Überholte Freigaben und Rückfragen des angesprochenen Laufs werden ohne unfertige Antworten geschlossen. Der Hauptagent kann dir auch antworten, während Subagenten weiterarbeiten. Deren Freigaben und Rückfragen bleiben bestehen, sofern er ihnen nicht gezielt eine Korrektur schickt. Neue Anweisungen erlauben keine gesperrten Aktionen.

**Nach dem Lauf senden** reiht eine Nachricht für einen anschließenden Auftrag ein. Der Browser-Postausgang erhält Nachrichten und Anhänge in Reihenfolge. Fehlerhafte Sendungen bleiben für einen erneuten Versuch oder die Übernahme in den Entwurf erhalten. Abmelden oder Kontowechsel entfernt den bisherigen Postausgang dieses Browsers.

Der Stoppknopf beendet den Agentenlauf. Ein Browserdisconnect beendet ihn nicht; beim Wiederverbinden lädt MatoLoop den gespeicherten Zustand nach.

Eine verfügbare Reasoning-Zusammenfassung ersetzt die Bezeichnung **Nachdenken**. Die Zeile lässt sich nur aufklappen, wenn zusätzlicher Reasoning-Text vorhanden ist. Zusammenfassungen zeigen Modellangaben, keine garantiert vollständigen internen Gedankengänge.

Gleichartige Tool-Aufrufe erscheinen als aufklappbare Gruppe mit ihrer Anzahl. Ein Fehlersymbol an der Gruppe weist auf mindestens einen fehlgeschlagenen Aufruf hin. Klappe die Gruppe auf, um die einzelnen Ressourcen, ihren Status und verfügbare Details zu sehen. Freigaben, Rückfragen und Ausgaben bleiben auch bei geschlossener Gruppe sichtbar.

Während einer internen Reparaturrunde bleibt die Aktivitätsanzeige neutral. Gelingt die Korrektur, erscheint der Fehlversuch nicht im Arbeitsverlauf; ungelöste Fehler werden am Laufende sichtbar. Freigaben, Rückfragen und Hinweise auf mögliche Teiländerungen bleiben sofort sichtbar.

## Rückfragen und delegierte Aufgaben

Strukturierte Rückfragen erscheinen direkt im Chat. **Weiter** navigiert, **Fertig** sendet auch unvollständige Antworten, **Ablehnen** verwirft den Entwurf. Mit **Jetzt senden** kannst du stattdessen nachfragen oder die Richtung ändern. Geheimnisse gehören nur in [geschützte Eingabefelder](variablen-und-secrets.md).

Aufgabenlisten zeigen gemeldeten Fortschritt. Delegierte Aufgaben erscheinen mit Titel und Status; ein Klick öffnet ihren schreibgeschützten Verlauf. Fragen und Freigaben beantwortest du in der Hauptsession. Kinder teilen den Computer-Arbeitsbereich, sind also nicht dateiseitig isoliert. Ein Stopp des Elternlaufs stoppt auch offene delegierte Arbeit.

Pro Hauptsession können höchstens vier Subagenten gleichzeitig aktiv sein; wartende Freigaben und Rückfragen zählen mit. Subagenten starten selbst keine weiteren Subagenten. Der Hauptagent kann einem früheren Subagenten Folgeaufträge mit erhaltenem Verlauf geben oder einen laufenden Auftrag korrigieren. Bitte ihn beispielsweise: „Lass denselben Subagenten die überarbeitete Version prüfen.“

Eine Zwischenantwort des Hauptagenten beendet laufende Subagenten nicht. Er übernimmt deren Ergebnisse anschließend. Eine bestätigte Weitergabe ist noch kein abgeschlossenes Ergebnis: Korrekturen können einen laufenden Toolaufruf abwarten und bereits ausgeführte Änderungen nicht rückgängig machen. Die Vierergrenze begrenzt die Parallelität, nicht die Gesamtkosten aufeinanderfolgender Aufträge.

## Nachrichten erneut versuchen oder zurücknehmen

- **Erneut versuchen** startet einen neuen Versuch der letzten fehlgeschlagenen Host-Antwort. Voraussetzungen werden erneut geprüft; bereits gestartete Läufe behalten ihren Retry-Kontext.
- **Rückgängig** übernimmt Text und Anhänge in den Entwurf und entfernt die Benutzernachricht mit dem folgenden Verlauf.
- **Kopieren** übernimmt den Nachrichteninhalt.

!!! warning "Externe Wirkungen bleiben bestehen"
    Rückgängig, Retry und Stopp machen ausgeführte Datei-, Integrations-, Freigabe- oder Memory-Änderungen nicht rückgängig. Prüfe unklare Ergebnisse vor einem erneuten Auftrag.

Antworten unterstützen Markdown, Tabellen, Code, Mathematik und Mermaid. Code und Diagrammquellen lassen sich kopieren oder herunterladen. Externe Bilder benötigen eine Zustimmung je Ursprung und Session. **Kleine Nachrichten** verdichtet die Darstellung, ohne Inhalt zu kürzen.
