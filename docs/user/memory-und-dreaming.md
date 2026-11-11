# Memory und Verarbeitung

Memory ermöglicht MatoLoop, passende Informationen über einzelne Sessions hinaus zu verwenden. Die Funktion muss in den Memory-Einstellungen aktiviert sein und wird zusätzlich je Session gesteuert.

Unter **Einstellungen > Memory** findest du drei Tabs: **Einstellungen** für Memory-Zugriff und automatischen Kontext, **Erinnerungen** zur Verwaltung gespeicherter Memories und **Verarbeitung** für die Memory-Extraktion und Session-Zusammenfassungen. Änderungen speicherst du über **Speichern** oben auf der Seite; Schalter werden direkt gespeichert.

## Session-Modi

| Modus | Frühere Sessions und Erinnerungen nutzen | Erinnerungen schreiben | Verarbeitung |
| --- | --- | --- | --- |
| Voll | Ja | Ja | Ja |
| Nur lesen | Ja | Nein | Nein |
| Deaktiviert | Nein | Nein | Nein |

Provider- und Modelleinstellungen können den zulässigen Umgang mit öffentlichen und privaten Inhalten weiter einschränken.

## Über mich

Unter **Einstellungen > Personalisierung > Über mich** fügst du Angaben aus einem festen Katalog hinzu und pflegst dort vollständigen und bevorzugten Namen, Anrede, Pronomen, Hauptsprache und Hauptwohnsitz. Diese Angaben sind kein Memory: Sie gelten unabhängig vom Memory-Modus und werden weder durchsucht noch extrahiert. Der Agent kann sie nur auf deine ausdrückliche Bitte über ein eigenes Tool ändern oder entfernen; die Tool-Richtlinie bestimmt, ob du die Änderung einzeln freigeben musst.

Für jede Angabe kannst du zwischen privaten und öffentlichen Modellen umschalten. Mit **Auch für öffentliche Modelle verwenden** übernimmt das öffentliche Feld den privaten Wert; andernfalls pflegst du beide Werte getrennt. Eine aktuelle direkte Aussage im Chat hat weiterhin Vorrang vor dem gepflegten Profil; das Profil hat Vorrang vor älteren Erinnerungen und Sessions.

## Sitzungsübergreifender Abruf

Geeignete Anfragen können begrenzte Ausschnitte aus anderen eigenen Sessions abrufen. In einem Projekt stammen automatische Gesprächsausschnitte ausschließlich aus demselben Projekt; ohne Projekt bleibt der Abruf projektübergreifend. Automatische Quellen benötigen vollen Memory-Modus; die aktuelle Session und Subagenten sind ausgeschlossen. Explizite Sessiontools und die eigene Oberfläche-Suche können auch Sessions anderer Memory-Modi lesen. Ein vorheriger Verarbeitungslauf ist für den Sessionabruf nicht erforderlich.

Der zusätzliche Projektindex enthält bis zu zehn neueste geeignete Sessions desselben Projekts, nur mit Titeln und Verweisen auf aktuelle Nachrichten. Relevante Ausschnitte danach können standardmäßig bis zu drei unterschiedliche eigene Sessions desselben Projekts berücksichtigen. Sie liefern den stärksten thematischen Anker, nie automatisch das vollständige Gespräch. Für einen konkreten projektübergreifenden Bedarf kann der Agent gezielt außerhalb des Projekts suchen, ohne jedes Mal nachzufragen. Leere Projekttreffer allein lösen keine globale Suche aus. Allgemeine Nutzerpräferenzen bleiben verfügbar; fremde Projekte bestimmen nicht das Thema der aktuellen Anfrage.

Beim ersten Beitrag oder bei einer ausdrücklichen Frage nach den letzten Sessions können zusätzlich kleine aktuelle Ausschnitte erscheinen. Ein ausdrücklicher Verzicht auf Memory oder vorherige Sessions und reine Textumformungen unterdrücken den automatischen Abruf.

Unter **Einstellungen > Memory > Einstellungen > Automatischer Kontext** begrenzt du die Anzahl der Memory-Treffer, relevanten und letzten Session-Auszüge sowie das gemeinsame Token-Budget. Über **Speichern** speicherst du nur diese vier Werte, unabhängig von ungespeicherten Einstellungen im Tab **Verarbeitung**.

Unter **Projekt bearbeiten > Projektanweisungen** kannst du optionale feste Regeln und Entscheidungen hinterlegen. Diese Anweisungen gelten autoritativ für alle Sessions des Projekts. Sie ergänzen die automatisch gefundene Projekthistorie, ersetzen sie aber nicht.

Die Textabschnitte und die Volltextsuche werden gemeinsam mit der jeweiligen Nachricht gespeichert und stehen nach dem Speichern sofort bereit. Semantische Embeddings entstehen im Hintergrund. Falls der Embedding-Dienst noch nicht verfügbar ist, funktioniert die Volltextsuche weiter. Beim Start und regelmäßig im Betrieb repariert MatoLoop fehlende Indexdaten. Beim Start werden passende endgültig fehlgeschlagene Embeddings einmal erneut versucht; im laufenden Betrieb werden solche Fehler nicht endlos wiederholt.

## Erinnerungen verwalten

Unter **Einstellungen > Memory > Erinnerungen** kannst du Erinnerungen suchen, filtern, anlegen, bearbeiten und löschen. Verfügbare Kategorien sind Kontext, Fakt, Ziel, Identität und Präferenz.

**Immer im Kontext** ist die einzige besondere Abrufart. Solche Erinnerungen werden bei jeder zulässigen Anfrage vollständig berücksichtigt. Eine Erinnerung mit **Bei passender Anfrage** kann unabhängig von ihrer manuellen, Chat- oder Extraktionsquelle automatisch als kurzer relevanter Treffer erscheinen. Quelle, Quell-Session und Zeitangaben helfen bei der Einordnung.

Eine ausdrückliche Bitte im Chat, eine Information künftig immer zu berücksichtigen, kann diese Markierung bereits beim Speichern setzen. Dafür gilt dieselbe konfigurierbare Freigabe wie für andere Schreibzugriffe auf Memory. Eine gewöhnliche Bitte, etwas zu merken, setzt die Markierung nicht.

Beim Ändern von **privat** auf **öffentlich** warnt MatoLoop, weil diese Einstufung die zulässige Übertragung an öffentliche Modelle beeinflusst.

## Verarbeitung {#dreaming}

Unter **Memory > Verarbeitung** schaltest du **Memories extrahieren** und **Session-Zusammenfassungen erstellen** unabhängig voneinander ein. Du kannst eines der Verfahren oder beide verwenden. Die Memory-Extraktion ist zunächst eingeschaltet, Session-Zusammenfassungen sind ausgeschaltet.

Unter **Gemeinsame Einstellungen** steuert **Automatisch verarbeiten** die zeitgesteuerten Läufe. Dieser Schalter ist zunächst ausgeschaltet. Nachrichtenlimit, Intervall und Leerlauf gelten für die eingeschalteten Verfahren. **Jetzt verarbeiten** startet sie auch ohne Automatik. Sind beide Verfahren ausgeschaltet, ist kein Start möglich. Modell und Reasoning-Stufe wählst du unter [**Standardmodelle > Memory / Dreaming**](provider-und-modelle.md#standardmodelle-und-sessionvorlagen).

### Memory-Extraktion

Die Extraktion verarbeitet geeignete abgeschlossene Chatnachrichten mit vollem Memory. Sessions mit Automationen folgen denselben Einstellungen; Subagenten und Fehlerausgaben sind keine Quellen. Extrahiert werden sollen dauerhafte benutzerspezifische Angaben, etwa eigene Geräte, stabile Umgebungen, Beziehungen, Vorlieben oder langfristige Ziele. Aktueller Aufgabenfortschritt, auch bestätigte Testergebnisse und offene Prüfungen, vorübergehende Geräte und Implementierungsdetails sollen im Verlauf bleiben. Bei unklarer Dauerhaftigkeit soll das Modell die Angabe auslassen. **Über mich** wird nicht kopiert. Die Extraktion sieht nur ihren Quellenabschnitt, keine vorhandenen Memories oder anderen Sessions.

Anforderungen an eine bestimmte Anwendung, Review-Abläufe und Automationsaufträge sollen nicht als persönliche Vorlieben oder langfristige Ziele gespeichert werden. Auch wiederholte Ausführungen ändern das nicht. Deine langfristige Mitarbeit an einem Projekt, bestehende Umgebungen und allgemeine Arbeitsweisen bleiben dagegen geeignet; eine klare Aussage genügt. Enthält eine Nachricht beides, sollen die dauerhaften Angaben erhalten bleiben und die Auftragsdetails entfallen. Unsichere Überlegungen sollen nicht zu festen Vorlieben werden.

Die Einordnung trifft das Modell; Fehlentscheidungen sind weiterhin möglich, etwa bei vagen „unverändert“-Meldungen oder fehlendem Projektkontext. Prüfe gespeicherte Angaben unter **Erinnerungen** und korrigiere oder entferne unpassende Einträge.

Hinweise, Änderungsrichtlinien und Kandidatenlimit findest du im Abschnitt **Memory-Extraktion**. Beim Ausschalten bleiben vorhandene Erinnerungen nutzbar und der Extraktionsfortschritt erhalten. Offene Extraktionsvorschläge verfallen. Nach dem erneuten Einschalten setzt die Extraktion am bisherigen Stand fort.

Anschließend sucht der Host für jeden Kandidaten getrennt nach möglichen vorhandenen Erinnerungen. Exakte Duplikate werden ohne weitere Modellentscheidung als unverändert behandelt, und normale Kandidaten ohne Treffer werden neu angelegt. Bei Unsicherheit bleiben ähnliche Angaben getrennt: Ein genauer bezeichnetes Gerät oder eine weitere Präferenz ersetzt keine allgemeinere Erinnerung, solange die Aussage nicht eindeutig denselben Gegenstand oder eine Korrektur bezeichnet. Nur bei möglichen inhaltlichen Überschneidungen entscheidet ein getrennter Abgleich zwischen:

- neu anlegen
- unverändert lassen und neue Belege ergänzen
- eine Erinnerung aktualisieren
- mehrere Erinnerungen zusammenführen
- eine exakt passende Erinnerung entfernen

Eine Entfernung benötigt eine direkte Aussage des Benutzers, dass genau diese Information vergessen werden soll, beendet, ungültig oder nicht mehr wahr ist. Nur ein normalisiert exakter Datensatz kann entfernt werden; ähnliche Treffer reichen nicht aus. Alter, Nichtnennung oder geringe Relevanz sind niemals Löschgründe.

Jede neu durch die Extraktion angelegte Erinnerung beginnt mit **Bei passender Anfrage**. Beim Ändern oder Zusammenführen bleibt eine vorhandene Einstellung **Immer im Kontext** erhalten; die Extraktion setzt sie jedoch nie selbst.

## Änderungsrichtlinien

**Manuell angelegte Erinnerungen einschließen** ist zunächst ausgeschaltet. Damit sind Erinnerungen mit der Quelle **Manuell** vor Änderungen, Zusammenführungen und Entfernungen durch die Extraktion geschützt. Dubletten werden weiterhin erkannt, ohne diese Erinnerungen zu verändern. Dein direktes Bearbeiten bleibt möglich. Schaltest du die Option ein, gelten auch für manuelle Erinnerungen die folgenden Richtlinien. Beim Ausschalten verfallen offene Vorschläge, die manuelle Erinnerungen betreffen.

Für das Ändern und Entfernen stehen jeweils diese Richtlinien zur Verfügung:

| Richtlinie | Wirkung |
| --- | --- |
| Deaktiviert | Die vorgeschlagene Änderung wird übersprungen. |
| Nachfragen | Die Änderung erscheint als offene Freigabe. |
| Automatisch | Die geprüfte Änderung wird ohne einzelne Freigabe angewendet. |

Der Standard ist **Automatisch** für Änderungen und **Nachfragen** für Entfernungen. Eine Zusammenführung benötigt beide Rechte: Ist eine Richtlinie deaktiviert, wird sie übersprungen. Steht mindestens eine auf Nachfragen, entsteht eine Freigabe. Nur zweimal Automatisch führt sofort zusammen.

Neue offene Entscheidungen prüfst du im Chat **Persönlicher Assistent**; Benachrichtigungen und die Laufdetails verlinken zum zugehörigen Chat. Bereits vorhandene Vorschläge bleiben in ihrer bisherigen Quell-Session erreichbar. Ältere Links ohne Sessionbezug zeigen nach dem Laden einen Link **Im Chat prüfen**. Die Vorschläge überleben einen Backend-Neustart. Vor Genehmigung werden Ziele, Belege und Richtlinien erneut geprüft; veraltete Anfragen verfallen ohne Teiländerung. Bei Änderungen/Zusammenführungen kannst du den vorgeschlagenen Inhalt und die Zielgruppe/Markierung bewusst bearbeiten. Die geprüften Zielrevisionen müssen trotzdem unverändert sein.

Beim Laden offener Vorschläge oder Benachrichtigungen werden nicht mehr gültige Vorschläge automatisch als abgelaufen markiert. Sie verschwinden zusammen mit ihrer Benachrichtigung; du musst sie nicht erst genehmigen oder ablehnen. Bereits zugestellte Betriebssystem-Pushmeldungen lassen sich nicht zuverlässig zurückziehen.

Die Verarbeitung kann manuell für alle oder für eine einzelne Session gestartet werden. Die gemeinsame Übersicht zeigt die letzten Läufe beider Verfahren. Über **Alle Läufe ansehen** öffnest du den vollständigen, seitenweisen Verlauf. Öffne einen Lauf, um seine Quell-Session, angewendete Memory-Änderungen mit Vorher/Nachher, offene Vorschläge und die damals erzeugte Zusammenfassung zu sehen. Später genehmigte Änderungen werden dem ursprünglichen Lauf zugerechnet. Bei älteren Läufen sind nur die bereits gespeicherten Zähler verfügbar. Beim Löschen einer Erinnerung werden zugehörige gespeicherte Änderungsinhalte entfernt.

Die Tabs **Einstellungen / Erinnerungen / Verarbeitung** bleiben auch im Verlauf und in den Laufdetails sichtbar. Über **Verarbeitung › Durchläufe › Details** wechselst du zurück zur Übersicht oder zur Verarbeitung.

### Session-Zusammenfassungen einschalten

Aktiviere unter **Memory > Verarbeitung > Session-Zusammenfassungen** den Schalter **Session-Zusammenfassungen erstellen** unter der Erklärung. Das Verfahren verwendet dasselbe Modell, denselben Zeitplan und **Jetzt verarbeiten** wie die Memory-Extraktion, funktioniert aber auch bei ausgeschalteter Extraktion. Zusätzliche Modellaufrufe können Kosten verursachen.

Automatisch werden nur neue oder ab der Aktivierung wieder aktive Sessions nach der eingestellten Ruhezeit berücksichtigt, nicht dein gesamter Altbestand. Für eine ältere Session wählst du sie gezielt aus und startest **Jetzt verarbeiten**. Der erste Lauf verarbeitet ihren bisherigen Verlauf in begrenzten Abschnitten; spätere Läufe aktualisieren die Zusammenfassung mit neuen abgeschlossenen Nachrichten.

Passende Zusammenfassungen helfen dem Agenten beim Einstieg in frühere Gespräche. Innerhalb eines Projekts bleiben dessen Sessions der automatische Gesprächskontext. Fehlende Details kann der Agent weiterhin aus dem Original nachlesen; Sessions ohne Zusammenfassung bleiben auffindbar. Zusammenfassungen sind keine vollständigen Belege und können Informationen auslassen.

Beim Ausschalten werden Zusammenfassungen weder weiter erstellt noch automatisch verwendet. Gespeicherte Fassungen bleiben in den Laufdetails sichtbar. Wenn du Quellnachrichten zurücknimmst, werden betroffene Zusammenfassungen verworfen. Zusammenfassungen und Memories haben getrennten Fortschritt: Ein fehlgeschlagener Schritt setzt einen bereits erfolgreichen nicht zurück.

!!! info "Memory ist kein exaktes Archiv"
    Erinnerungen sind verdichtete Arbeitsinformationen. Verwende für unveränderte Originalinhalte weiterhin Sessions und Dateien.

!!! info "Nach Benutzer getrennt"
    Erinnerungen, Duplikaterkennung, Verarbeitung und der Abruf aus früheren Sessions verwenden ausschließlich Daten des angemeldeten Benutzers. Auch Administratoren können Erinnerungen anderer Benutzer nicht öffnen.
