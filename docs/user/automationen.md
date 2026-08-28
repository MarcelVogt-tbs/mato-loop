# Automationen

Automationen führen einen festen Agent-Auftrag manuell oder nach Zeitplan aus. Jede Automation besitzt einen eigenen dauerhaften Ausführungskontext. Frühere Ergebnisse, Dateien und Freigaben dieses Kontexts stehen damit auch späteren Läufen zur Verfügung, ohne als normale Chat-Session in der Navigation zu erscheinen.

## Automation erstellen

Öffnen Sie **Einstellungen > Automationen** und wählen Sie **Automation erstellen**. Legen Sie mindestens Folgendes fest:

- einen Namen
- einen eindeutigen Auftrag
- mindestens einen Zeitplan

Optional können Sie einen Skill auswählen, Laufzeiteinstellungen aus einer vorhandenen Session übernehmen und eine Ausgabe-Session festlegen. Modell, Memory-Modus, Websuche, Computer und Arbeitsordner werden beim Übernehmen in den eigenen Automation-Kontext kopiert. Spätere Änderungen der Quell-Session verändern die Automation nicht.

Bei genau einem einmaligen Zeitplan kann die Automation nach einem erfolgreichen geplanten Lauf automatisch archiviert werden. Archivierte Automationen werden nicht mehr ausgeführt oder bearbeitet, behalten aber ihre Laufhistorie und können im Archiv endgültig gelöscht werden. Fehlgeschlagene, abgebrochene und übersprungene Läufe archivieren die Automation nicht.

## Zeitpläne

Eine Automation kann mehrere wiederkehrende oder einmalige Zeitpläne besitzen. Ein wiederkehrender Zeitplan verwendet einen Cron-Ausdruck mit fünf Feldern:

```text
Minute Stunde Tag Monat Wochentag
```

Das Beispiel `0 8 * * 1-5` startet die Automation montags bis freitags um 08:00 Uhr. Die IANA-Zeitzone, beispielsweise `Europe/Berlin`, bestimmt die lokale Uhrzeit.

Wiederkehrende Zeitpläne können optional ein Gültigkeitsende besitzen. Ein Termin genau zu diesem Zeitpunkt wird noch ausgeführt; spätere Cron-Termine entfallen und der Zeitplan wird als abgelaufen angezeigt. Damit lässt sich beispielsweise eine tägliche Erinnerung auf eine Woche begrenzen. Die Automation und ihre Laufhistorie bleiben danach erhalten und können weiterhin manuell gestartet werden.

Ein einmaliger Zeitplan verwendet stattdessen einen festen Ausführungszeitpunkt. Nach diesem Termin wird der Zeitplan deaktiviert und nicht erneut ausgeführt.

Bei der Zeitumstellung wird eine nicht existente lokale Cron-Uhrzeit im Frühjahr einmal direkt nach dem Zeitsprung ausgeführt. Eine doppelte Uhrzeit im Herbst läuft nur einmal. Während einer Serverunterbrechung verpasste wiederkehrende und einmalige Termine werden nicht nachgeholt. Treffen mehrere Zeitpläne derselben Automation gleichzeitig, werden sie zu einem Lauf zusammengefasst.

Ist ein vorheriger Lauf noch aktiv oder wartet auf eine Freigabe, wird der neue Termin übersprungen. Automatische Wiederholungen fehlgeschlagener Agent-Läufe gibt es nicht, da vorherige Tool-Aktionen bereits externe Wirkungen verursacht haben können.

## Manuelle Läufe

Mit **Jetzt ausführen** können Sie eine Automation unabhängig von ihrem Aktivstatus starten. Auch eine pausierte Automation bleibt manuell ausführbar. Pro Automation ist höchstens ein Lauf gleichzeitig möglich.

## Ergebnisse und Benachrichtigungen

Erfolgreiche und fehlgeschlagene Läufe erzeugen eine Benachrichtigung. Die Benachrichtigung öffnet den zugehörigen Lauf mit seinem vollständigen Ergebnis, Fehlerstatus und den beim Start gespeicherten Zeitplänen und Einstellungen.

Eine optionale Ausgabe-Session erhält erfolgreiche Antworten zusätzlich als eigene Agent-Nachricht. Ist dort gerade ein Lauf aktiv, wartet die Zustellung. Technische Zustellfehler werden bis zu dreimal versucht; anschließend erscheint eine Warnung mit einer manuellen Wiederholungsmöglichkeit.

Wenn die Ausgabe-Session als erforderlich markiert ist, kann sie nur zusammen mit den abhängigen Automationen gelöscht werden. Bei einer optionalen Verknüpfung können Sie die Bindung lösen und die Automation behalten.

## Agent-Tools und Freigaben

Der Agent kann Automationen anzeigen, erstellen, bearbeiten, starten und löschen. Diese Aktionen verwenden die wirksamen Vorgaben unter **Einstellungen > Tools** und mögliche Abweichungen des gewählten Profils unter **Einstellungen > Agenten**:

- Automation lesen
- Automation erstellen
- Automation bearbeiten
- Automation starten
- Automation löschen

Lesen, Erstellen, Bearbeiten und Starten fragen standardmäßig nach einer Freigabe. Löschen ist standardmäßig deaktiviert. Eine Automation-Freigabe ersetzt keine Skill-Freigabe; ein gebundener Skill folgt weiterhin seiner eigenen Richtlinie.
