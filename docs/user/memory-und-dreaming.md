# Memory und Dreaming

Memory ermöglicht MatoLoop, Informationen über einzelne Sessions hinaus zu verwenden. Die Funktion muss global aktiviert sein und wird zusätzlich je Session gesteuert.

## Session-Modi

| Modus | Lesen | Schreiben | Dreaming |
| --- | --- | --- | --- |
| Voll | Ja | Ja | Ja |
| Nur lesen | Ja | Nein | Nein |
| Deaktiviert | Nein | Nein | Nein |

Provider- und Modelleinstellungen können den zulässigen Umgang mit öffentlichen und privaten Erinnerungen weiter einschränken.

## Erinnerungen verwalten

Unter **Einstellungen > Memory** können Sie Erinnerungen suchen, filtern, anlegen, bearbeiten und löschen. Verfügbare Kategorien sind Kontext, Fakt, Ziel, Identität und Präferenz.

Eine Erinnerung kann automatisch verwaltet oder ausdrücklich im aktiven beziehungsweise langfristigen Gedächtnis fixiert werden. Quelle, Quell-Session und bestätigte Nutzung helfen bei der Einordnung.

Beim Ändern von **privat** auf **öffentlich** warnt MatoLoop, weil diese Einstufung die zulässige Übertragung an öffentliche Modelle beeinflusst.

## Dreaming

Dreaming verarbeitet geeignete Session-Inhalte im Hintergrund und kann Erinnerungen neu anlegen oder aktualisieren. In den Einstellungen steuern Sie Aktivierung, Hinweise, Zeichenbudget, Lifecycle-Schwellen, Intervall und Batchgröße.

Dreaming kann außerdem manuell für alle oder für eine einzelne Session gestartet werden. Die Oberfläche zeigt Laufstatus und Anzahl der erzeugten beziehungsweise aktualisierten Erinnerungen.

!!! info "Memory ist kein exaktes Archiv"
    Erinnerungen sind verdichtete Arbeitsinformationen. Verwenden Sie für unveränderte Originalinhalte weiterhin Sessions und Dateien.
