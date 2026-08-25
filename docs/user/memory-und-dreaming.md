# Memory und Dreaming

Memory ermöglicht MatoLoop, passende Informationen über einzelne Sessions hinaus zu verwenden. Die Funktion muss in den Memory-Einstellungen aktiviert sein und wird zusätzlich je Session gesteuert.

## Session-Modi

| Modus | Frühere Sessions und Erinnerungen nutzen | Erinnerungen schreiben | Dreaming |
| --- | --- | --- | --- |
| Voll | Ja | Ja | Ja |
| Nur lesen | Ja | Nein | Nein |
| Deaktiviert | Nein | Nein | Nein |

Provider- und Modelleinstellungen können den zulässigen Umgang mit öffentlichen und privaten Inhalten weiter einschränken.

## Sitzungsübergreifender Abruf

Beim ersten Beitrag einer neuen Session lädt MatoLoop automatisch begrenzte Ausschnitte aus bis zu zwei zuletzt verwendeten passenden Sessions. Projekt-Sessions bleiben dabei innerhalb ihres Projekts, normale Sessions verwenden vorherige normale Sessions. Dadurch kennt der Agent kurzfristigen Kontext, ohne vollständige Gespräche erneut zu übertragen. Ein ausdrücklicher Verzicht auf vorherige Sessions unterdrückt diesen Abruf. Spätere Fragen nach der letzten Session verwenden Aktualität; ausdrückliche Bezüge auf ältere Arbeit verwenden eine thematische Suche. Normale Folgefragen laden die Ausschnitte nicht erneut. Dafür müssen Inhalte nicht zuerst als dauerhafte Erinnerung gespeichert oder durch Dreaming verarbeitet werden.

Die Textabschnitte und die Volltextsuche werden gemeinsam mit der jeweiligen Nachricht gespeichert und stehen nach dem Speichern sofort bereit. Semantische Embeddings entstehen im Hintergrund. Falls der Embedding-Dienst noch nicht verfügbar ist, funktioniert die Volltextsuche weiter. Beim Start und regelmäßig im Betrieb repariert MatoLoop fehlende Indexdaten. Beim Start werden passende endgültig fehlgeschlagene Embeddings einmal erneut versucht; im laufenden Betrieb werden solche Fehler nicht endlos wiederholt.

## Erinnerungen verwalten

Unter **Einstellungen > Memory** können Sie Erinnerungen suchen, filtern, anlegen, bearbeiten und löschen. Verfügbare Kategorien sind Kontext, Fakt, Ziel, Identität und Präferenz.

**Immer berücksichtigen** ist die einzige besondere Abrufart. Solche Erinnerungen werden bei jeder zulässigen Anfrage berücksichtigt. Alle anderen Erinnerungen bleiben vollständig gespeichert und werden bei Bedarf passend zur Anfrage abgerufen. Quelle, Quell-Session und Zeitangaben helfen bei der Einordnung.

Eine ausdrückliche Bitte im Chat, eine Information künftig immer zu berücksichtigen, kann diese Markierung bereits beim Speichern setzen. Dafür gilt dieselbe konfigurierbare Freigabe wie für andere Schreibzugriffe auf Memory. Eine gewöhnliche Bitte, etwas zu merken, setzt die Markierung nicht.

Beim Ändern von **privat** auf **öffentlich** warnt MatoLoop, weil diese Einstufung die zulässige Übertragung an öffentliche Modelle beeinflusst.

## Dreaming

Dreaming ist optional. Es verarbeitet abgeschlossene Gespräche schrittweise und leitet daraus belegte Kandidaten für dauerhafte Erinnerungen ab. Die Extraktion sieht nur die Nachrichten des aktuellen Quellabschnitts und keine vorhandenen Erinnerungen oder Inhalte aus anderen Sessions.

Anschließend sucht der Host für jeden Kandidaten getrennt nach möglichen vorhandenen Erinnerungen. Exakte Duplikate werden ohne weitere Modellentscheidung als unverändert behandelt, und Kandidaten ohne Treffer werden neu angelegt. Nur bei möglichen inhaltlichen Überschneidungen entscheidet ein getrennter Abgleich zwischen:

- neu anlegen
- unverändert lassen und neue Belege ergänzen
- eine Erinnerung aktualisieren
- mehrere Erinnerungen zusammenführen

Dreaming kann außerdem vorschlagen, eine Erinnerung künftig immer zu berücksichtigen. Kernidentität bleibt auf bevorzugten Namen, Anrede, primären Wohnort, Pronomen und primäre Sprache begrenzt. Ohne Kernidentitätsart sind auch ausdrücklich für künftige Antworten relevante enge Familienfakten, wiederkehrende Präferenzen und Sicherheitsbeschränkungen möglich. Dreaming hebt diese Berücksichtigung nie selbst auf. Die automatische Markierung endet bei fünf entsprechend markierten Erinnerungen; manuelle Änderungen bleiben möglich.

## Änderungsrichtlinien

Für Aktualisieren, Zusammenführen und Berücksichtigung stehen jeweils diese Richtlinien zur Verfügung:

| Richtlinie | Wirkung |
| --- | --- |
| Deaktiviert | Die vorgeschlagene Änderung wird übersprungen. |
| Nachfragen | Die Änderung erscheint als offene Freigabe. |
| Automatisch | Die geprüfte Änderung wird ohne einzelne Freigabe angewendet. |

Die Standardwerte sind **Automatisch** für Aktualisierungen sowie **Nachfragen** für Zusammenführungen und Änderungen an **Immer berücksichtigen**.

Offene Dreaming-Entscheidungen finden Sie unter **Control Center > Freigaben**. Diese Memory-Freigaben bleiben auch nach einem Backend-Neustart offen. Beim Genehmigen prüft MatoLoop die betroffenen Erinnerungen, Belege und Richtlinien erneut. Wurde eine Erinnerung inzwischen geändert, läuft die Freigabe ab, ohne einen Teil der vorgeschlagenen Änderung anzuwenden. Zusammenführungen bewahren Belege, private Einstufung und vorhandene Markierungen gemeinsam in einer Transaktion.

Dreaming kann manuell für alle oder für eine einzelne Session gestartet werden. Die Oberfläche zeigt Laufstatus und die Anzahl neu angelegter beziehungsweise aktualisierter Erinnerungen.

!!! info "Memory ist kein exaktes Archiv"
    Erinnerungen sind verdichtete Arbeitsinformationen. Verwenden Sie für unveränderte Originalinhalte weiterhin Sessions und Dateien.

!!! info "Nach Benutzer getrennt"
    Erinnerungen, Duplikaterkennung, Dreaming und der Abruf aus früheren Sessions verwenden ausschließlich Daten des angemeldeten Benutzers. Auch Administratoren können Erinnerungen anderer Benutzer nicht öffnen.
