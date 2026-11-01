# Tool-Rechte konfigurieren

Hier legst du fest, was der Agent automatisch ausführen darf und wann er fragen muss. Möchtest du eine einzelne Anfrage beantworten oder widerrufen, lies [Freigaben und Datenfreigabe](freigaben-und-datenfreigabe.md). Dienste verbindest du unter [Integrationen einrichten](integrationen.md).

## Freigabeverhalten wählen {#globale-tool-vorgaben-anwenden}

1. Öffne als Administrator **Administration > Tools > Einstellungen > Freigabeverhalten**.
2. Wähle einen Grundstandard. Bei aktiver Modelltrennung gibt es getrennte Auswahlen für **Privat** und **Öffentlich**.
3. Speichere die Änderung.

Ohne Modelltrennung bearbeitest du unter **Alle Modelle** die privaten Werte. Öffentliche Werte bleiben gespeichert. Eigene Aktionsregeln haben Vorrang; ein Wechsel des Grundstandards entfernt sie nicht. Das Info-Symbol am Preset zeigt dessen vollständige Vorgaben.

### Welche Auswahl passt?

| Grundstandard | Normales Lesen | Sensibles Lesen | Schreiben | Löschen |
| --- | --- | --- | --- | --- |
| Alles bestätigen | Nachfragen | Nachfragen | Nachfragen | Nachfragen |
| Ausgewogen | Nachfragen | Nachfragen | Nachfragen | Nachfragen |
| Komfortabel | Automatisch | Automatisch | Nachfragen | Nachfragen |
| Automatisch außer Löschen | Automatisch | Automatisch | Automatisch | Nachfragen |
| Vollautomatisch | Automatisch | Automatisch | Automatisch | Automatisch |

**Komfortabel** ist die Vorgabe für private, **Ausgewogen** für öffentliche Modelle. Beide erlauben öffentliche Recherche, Sessions lesen und durchsuchen, Memories lesen, Session-Dateien lesen und bearbeiten, Dateien bereitstellen, Medien erzeugen/verarbeiten, Skills laden und aktuelle Tool-Ergebnisse nachlesen automatisch. Diese Ausnahmen gelten auch in den großzügigeren Presets, nicht bei **Alles bestätigen**. Die Sichtbarkeit deiner Memories bleibt dabei unverändert. Zugriffe öffentlicher Modelle auf andere private Sessions bleiben sensibles Lesen und folgen der entsprechenden Preset-Spalte. Zusätzlich erforderliche Quell-, Ziel- und Verbindungsrechte bleiben bestehen.

**Variablen und Secrets verwenden** verlangt in jedem Preset eine Freigabe, sofern keine eigene Regel oder passende Freigabe besteht. Für Browser gelten [gesonderte Vorgaben](browser.md#rechte-und-risiken).

!!! warning "Automatisch ist keine Sicherheitsgarantie"
    Deaktivierte Fähigkeiten, Eigentümergrenzen und technische Sperren gelten weiter. Ein erlaubter Shell-Befehl oder Website-Klick kann trotzdem löschen, obwohl das Preset Löschaktionen bestätigen lässt. Die Kategorien erkennen nicht jede Wirkung beliebiger Programme oder Webseiten. Sensible Inhalte werden ebenfalls nicht vollständig erkannt.

## Rechte eines Agentenprofils anpassen

**Allgemein**, **Plan**, **Build** und **Sysadmin** sind mitgelieferte Profile. Ihre Namen und Arbeitsabläufe sind geschützt. Administratoren können deren Zugriffseinstellungen unter **Administration > Tools** anpassen; erlaubte persönliche Anpassungen stehen unter **Einstellungen > Tools** für eigene Profile zur Verfügung.

| Profil | Dateiaktionen im Arbeitsordner | Erkannte Shell-Aktionen |
| --- | --- | --- |
| Plan | Lesen automatisch; Schreiben/Löschen nachfragen | Lesen automatisch; Schreiben/Löschen nachfragen |
| Build | Lesen, Schreiben und Löschen automatisch | Lesen, Schreiben und Löschen automatisch |
| Sysadmin | Lesen automatisch; Schreiben/Löschen nachfragen | Lesen automatisch; Schreiben/Löschen nachfragen |

Diese Profilregeln können vom Grundstandard abweichen. Plan und Build fragen bei Dateiaktionen außerhalb des Arbeitsordners zusätzlich nach; Sysadmin erlaubt diesen Bereich standardmäßig. Shell ist nicht auf den Arbeitsordner beschränkt. Automatische Lesezugriffe gelten bei diesen Profilen auch für öffentliche Modelle; gelesene Inhalte können an den Provider übertragen werden.

**Plan implementiert nicht**, auch wenn eine Tool-Freigabe vorliegt. Wechsle für eine gewünschte Umsetzung zu Build und beauftrage sie ausdrücklich.

### Eine eigene Aktionsregel speichern

1. Wähle das Profil und öffne **Berechtigungen**.
2. Wähle **Eigene Regel hinzufügen**, dann **Tool oder Bereich** und die Aktion. Gruppenweite Zusatzrechte findest du unter **Zusatzrechte**.
3. Wähle gegebenenfalls den Modellbereich und **Automatisch**, **Nachfragen** oder **Deaktiviert**.
4. Wähle **Übernehmen**, anschließend **Speichern** auf der Seite.

Die Liste zeigt eigene Regeln. Entfernen stellt die Vererbung wieder her, getrennt je Modellbereich. Gemeinsam verwendete Aktionen ändern dieselbe Regel bei allen betroffenen Tools. **Alle eigenen Regeln entfernen** entfernt die Aktionsregeln des Formulars, nicht die Tool-Einstellungen; erst Speichern übernimmt dies dauerhaft.

Normale Benutzer benötigen die Freigabe persönlicher Profile und Tool-Anpassungen. Werden diese abgeschaltet, bleiben gespeicherte persönliche Werte erhalten, werden aber nicht angewendet. Administratoren behalten ihre Ausnahme. Mindestens ein globales Profil muss aktiv bleiben; Sessions mit deaktiviertem Profil bleiben lesbar.

## Tools ein- oder ausschalten

Unter **Tool-Einstellungen > Globaler Standard** sehen Administratoren die Tools und deren technische Einstellungen. Ausschalten erhält die Werte und Aktionsregeln. Ein Profil zeigt stattdessen nur eigene Abweichungen; **Eigene Einstellung hinzufügen** ergänzt Tool-Verfügbarkeit oder Shell-Präfixe. Entfernen stellt die Vererbung wieder her.

Ein Profil kann kein global deaktiviertes Tool einschalten. Einschalten gewährt außerdem keine zusätzlichen Aktionsrechte. Änderungen wirken beim nächsten Aktualisieren des Toolangebots; bereits angebotene oder gestartete Aufrufe einer laufenden Modellrunde können noch ausgeführt werden. Abbruch macht abgeschlossene externe Wirkungen nicht rückgängig.

## Vertrauliche Pfade schützen

Unter **Dateisystem > Vertrauliche Pfade und mögliche Secret-Ausgabe** steuerst du den zusätzlichen Schutz erkannter vertraulicher Computerpfade und möglicher Secret-Ausgaben der Shell:

- **Nachfragen** verlangt Zustimmung für die konkrete Aktion.
- **Deaktiviert** blockiert den Zugriff.
- **Automatisch** entfernt nur diese zusätzliche Nachfrage.

Normale Aktionsrechte und Computergrenzen gelten weiterhin. Die Erkennung ist unvollständig; speichere Geheimnisse nicht in ungeschützten Prompts oder temporären Arbeitsdateien.

## Shell-Präfixe konfigurieren

Unter **Tool-Einstellungen > Globaler Standard** öffnest du beim Shell-Tool **Shell-Präfixe konfigurieren**. Eigene Profile verwenden **Eigene Einstellung hinzufügen**. Trage getrennt für Lesen und Schreiben ein Tokenpräfix pro Zeile ein und speichere die Seite nach **Übernehmen**.

Jeder Teilbefehl muss passen. Ohne eigenen Eintrag wird geerbt; eine ausdrücklich leere Liste verwendet keine Präfixe. Präfixe können eine Aktionsrückfrage ersetzen, aber keine Löschfreigabe, Abschaltung, Computergrenze oder gesonderte Freigabe vertraulicher Daten. Zum tatsächlichen [Geltungsbereich von Shell-Freigaben](freigaben-und-datenfreigabe.md#shell-aktionen) siehe die Freigabeanleitung.

## Integrationsrechte

Rechte und Ressourcen einer Integration bearbeitest du an der [jeweiligen Verbindung](integrationen.md#rechte-und-profilverfugbarkeit), nicht in den globalen Tool-Regeln. Modellwahl gehört zu [Standardmodellen und Sessionvorlagen](provider-und-modelle.md#standardmodelle-und-sessionvorlagen), Arbeitsanweisungen zum Agentenprofil.

## Lokale Funktionen

Einrichtung und Bedienung stehen jetzt unter [Computer und Terminal](computer-und-terminal.md), [Git und Worktrees](git-und-worktrees.md) sowie [Browser verwenden](browser.md). Die Grenzen für Datei- und Shell-Aktionen erklärt [Freigaben und Datenfreigabe](freigaben-und-datenfreigabe.md).
