# Automationen

Eine Automation ist ein gespeicherter Agentenauftrag in einer gewöhnlichen sichtbaren Session. Ein Klick auf einen Eintrag unter **Automationen** in der Seitenleiste öffnet die zugehörige Unterhaltung und wählt die Automation in der Control-Center-App **Automationen** aus. Das Eintragsmenü bietet **Automation bearbeiten** und **Automation löschen**; Sessionaktionen findest du in der Projekt- oder Sessionliste.

## Erstellen und bearbeiten

Öffne **Control Center > App hinzufügen > Automationen** in einer Session. Für eine neue Unterhaltung verwende die Plusaktion bei Automationen. Das Öffnen der App legt bei Bedarf eine leere Session an.

Die App listet alle Aufgaben der Session. **Neu** legt eine Definition mit Name und Auftrag an. Füge einen Zeitplan oder einen Ereignisauslöser hinzu und speichere. Ohne beides läuft die Aufgabe nur auf Anforderung. Eine Session darf mehrere Aufgaben enthalten; das Schließen des App-Tabs stoppt oder löscht sie nicht.

Zeitplanzeilen zeigen lesbare Beschreibungen; öffne eine Zeile zum Bearbeiten. Lange Aufträge können über **Vollständigen Auftrag anzeigen** und **Bearbeiten** geöffnet werden. **Zeitregel bearbeiten** erhält ungewöhnliche bestehende Regeln.

Modell, Agent, Computer, Arbeitsordner und Rechte stammen bei der Ausführung aus den aktuellen Sessionvorgaben. Benötigte Skills nennst du im Auftrag ausdrücklich; eine zusätzliche Automations-Skillzuordnung gibt es nicht. Normale Chatnachrichten ändern die gespeicherte Definition nicht, können aber den Kontext späterer Läufe beeinflussen.

## Ausführen und interagieren

- **Jetzt ausführen** reiht den gespeicherten Auftrag ein; speichere oder verwirf Änderungen zuerst.
- **Deaktivieren** stoppt automatische Starts, nicht laufende Arbeit. Manuelle Starts bleiben möglich.
- Der Chat-Stoppknopf beendet den aktuellen Agentenlauf.
- Tools, Ergebnisse, Fehler, Freigaben und Fragen erscheinen im normalen Gespräch.
- Ist ein Agent bereits aktiv, wartet eingereihte Automation. Weitere fällige Auslösungen einer bereits eingereihten/laufenden Aufgabe werden als übersprungen protokolliert.

**Letzter Lauf** zeigt die letzte tatsächliche Ausführung mit Beginn, Status und Verweis auf Ergebnis oder Fehler; übersprungene Auslösungen ersetzen ihn nicht. Während der Arbeit führt der Link gegebenenfalls zur Eingabenachricht. **Abgeschlossen** bedeutet normales Laufende, nicht den Nachweis einer erfüllten überwachten Bedingung. Der Chat ist das vollständige Ausführungsprotokoll; eine zweite Detailhistorie gibt es in der App nicht.

### Hinweise und stille Prüfungen

Antworten verwenden dieselben Ungelesen-Markierungen und Benachrichtigungen wie interaktive Chats. Der Agent kann mit `notify` einen gezielten Hinweis erzeugen oder mit `silence` den gewöhnlichen Abschluss-Hinweis unterdrücken, etwa wenn eine verlangte Prüfung nichts Berichtenswertes fand. Antwort und Ungelesen-Status bleiben erhalten. Fehler, Freigaben, Fragen und schon erzeugte Hinweise werden dadurch nicht unterdrückt.

Eine sichtbar fokussierte Session erzeugt keinen automatischen Antwort-Hinweis. Web Push wird nur bei fehlender aktiver Anwendungspräsenz gesendet; siehe [Benachrichtigungen](anmeldung-und-oberflaeche.md#benachrichtigungen).

Vor Modellschritten erhält der Agent aktuelle Definitionen seiner Session, einschließlich deaktivierter Aufgaben und nächstem wirksamem Termin, soweit das Kontextbudget reicht. Bei Platzmangel wird ein kompakter Katalog mit Auslassungszahl verwendet; Details können über das normale Tool nachgeladen werden. Definitionen sind Konfigurationsdaten, keine automatische Aufforderung, jede Aufgabe zu starten.

## Zeitpläne

Vorlagen bieten täglich, werktags, wöchentlich und monatlich. **Eigener Rhythmus > Zeitregel bearbeiten** erlaubt fünfteilige Cron-Ausdrücke, etwa `0 8 * * 1-5` für werktags 08:00 in der gespeicherten Zeitzone. Neue Zeitpläne übernehmen die Browserzeitzone; ein Gerätewechsel ändert gespeicherte Regeln nicht.

Einmalige Termine und optionale Endzeitpunkte werden in der lokalen Gerätezeitzone eingegeben. Das Ende eines wiederkehrenden Plans ist einschließlich. Während Serverstillstand verpasste Termine werden nicht nachgeholt; gleichzeitig fällige Pläne werden gebündelt. Fehlgeschlagene oder unterbrochene Arbeit wird nicht automatisch wiederholt.

## Archivieren und entfernen

Archivieren betrifft die Session oder ihr Projekt, nicht die Automationsdefinition. Archivierte Sessions bleiben lesbar, neue Starts benötigen Wiederherstellung. Eingereihte Arbeit wird abgebrochen, aktive Arbeit darf enden. Die Aktivierungseinstellung bleibt gespeichert; verpasste Auslösungen werden nicht nachgeholt. Details stehen unter [Archiv](anmeldung-und-oberflaeche.md#archiv).

**Automation entfernen** in der App oder **Automation löschen** im Seitenleistenmenü löscht nach Bestätigung Definition, Zeitpläne und Auslösungsverlauf; Session, Dateien, Rechte und bereits laufender Agent bleiben erhalten. Das Löschen der Session oder ihres Projekts entfernt nach Bestätigung auch die abhängigen Automationen.

Hinweise zu früheren Ausführungssessions und Ausgabekopien stehen im [Upgrade-Guide](aktualisierung.md#automationen-und-aufnahmen).

## Ereignisauslöser und Rechte

**Agent hat geantwortet** und **Agent-Auftrag fehlgeschlagen** reagieren auf abgeschlossene normale Läufe deiner eigenen Sessions. Streaming und fremde Konten lösen nichts aus. Automationsbedingte Läufe, ihre Retries und Subagenten erzeugen keine weiteren Automationsauslösungen.

Optionale Bedingungen gehören in den Auftrag und werden vom ausgelösten Agenten beurteilt. Ereigniskontext enthält Session-/Nachrichtenverweise; Lesen folgt weiterhin den Sessionrechten. Eine bereits beschäftigte Aufgabe protokolliert weitere Auslösungen als übersprungen.

Vor der Aktivierung einer Automation bereitet der Agent die absehbar benötigten Session-Freigaben vor. `request_session_permissions` prüft unterstützte Datei-, Shell-, Session- und Integrationsaktionen ohne deren Ausführung und nutzt vorhandene Freigabekarten. Außerhalb der Automationseinrichtung bereitet der Agent Freigaben nur vor, wenn du ausdrücklich darum bittest; normale Tool-Aufrufe prüfen ihre Rechte bei der Ausführung. Deaktivierte Aktionen bleiben blockiert, einmalige oder nicht vorab prüfbare Rechte werden bei der tatsächlichen Ausführung angefragt. Das Speichern einer Automation erlaubt deren spätere Aktionen nicht automatisch.
