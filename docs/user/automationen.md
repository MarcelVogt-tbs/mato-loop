# Automationen

Eine Automation führt einen gespeicherten Agentenauftrag manuell oder nach Zeitplan aus. Unter **Einstellungen > Automationen** sehen Sie Status und nächsten Start. Wählen Sie eine Automation aus, um **Einstellungen** und **Verlauf** auf derselben Seite zu verwalten.

## Automation erstellen

Wählen Sie **Automation erstellen** und beantworten Sie drei Fragen:

1. **Auftrag**: Vergeben Sie einen Namen und beschreiben Sie Aufgabe und erwartetes Ergebnis.
2. **Ausführung**: Wählen Sie eine manuelle, einmalige oder wiederkehrende Ausführung. Manuelle Automationen benötigen keinen Zeitplan. Für wiederkehrende Ausführungen stehen tägliche, werktägliche, wöchentliche und monatliche Vorlagen bereit.
3. **Ergebnis**: Ergebnisse und Fehler erscheinen immer im Verlauf der Automation. Optional können erfolgreiche Ergebnisse zusätzlich an einen Chat gesendet werden.

Wählen Sie **Speichern**. Änderungen gelten erst nach dem Speichern. **Jetzt ausführen** startet den gespeicherten Auftrag; speichern oder verwerfen Sie offene Änderungen vor einem manuellen Start. Während der Bearbeitung verwendet der Zeitplan weiterhin die gespeicherte Definition.

## Status verstehen

- **Nur manuell**: Es sind keine automatischen Starts eingerichtet.
- **Geplant**: Mindestens ein künftiger geplanter Start ist aktiviert.
- **Zeitpläne pausiert**: Automatische Starts sind pausiert; eine manuelle Ausführung bleibt möglich.
- **Keine weiteren Termine**: Kein aktivierter Zeitplan besitzt einen weiteren Termin. Eine manuelle Ausführung bleibt möglich.
- **Läuft**, **Eingereiht**, **Freigabe erforderlich** oder **Eingabe erforderlich**: Eine Ausführung ist aktiv. Der Planungsstatus wird getrennt angezeigt, weil das Pausieren künftiger Starts die aktuelle Arbeit nicht abbricht.
- **Archiviert**: Der Verlauf bleibt zugänglich, die Automation kann aber weder bearbeitet noch gestartet werden.

Pro Automation kann höchstens eine Ausführung aktiv sein. Offene Freigaben oder Fragen bearbeiten Sie in **Benachrichtigungen**. Eine fehlgeschlagene oder unterbrochene Ausführung wird nicht automatisch wiederholt, weil frühere Tools bereits externe Änderungen verursacht haben können.

## Zeitplanoptionen

Neue wiederkehrende Zeitpläne verwenden die unter **Zeitplanoptionen** angezeigte Zeitzone des Browsers. Ein Gerätewechsel ändert die gespeicherte Zeitzone nicht. Einmalige Zeitpunkte und optionale Enddaten geben Sie in der lokalen Zeit des aktuellen Geräts ein.

**Benutzerdefiniert** akzeptiert einen Cron-Ausdruck mit fünf Feldern: Minute, Stunde, Tag, Monat und Wochentag. `0 8 * * 1-5` bedeutet beispielsweise Montag bis Freitag um 08:00 Uhr in der gewählten Zeitzone. Die Vorlagenauswahl überschreibt bestehende benutzerdefinierte Ausdrücke nicht. Ein monatlicher Tag, der in einem bestimmten Monat nicht existiert, hat in diesem Monat keinen Termin.

Unter **Zeitplanoptionen** können Sie die Zeitzone ändern, ein einschließlich geltendes Enddatum setzen oder einen einzelnen Zeitplan deaktivieren. Mit **Weitere Ausführungseinstellungen** fügen Sie einen weiteren Zeitplan hinzu. Der Hauptschalter **Zeitpläne aktiv** pausiert alle automatischen Starts, ohne ihre Konfiguration zu entfernen. Beim Wechsel in den manuellen Modus wird eine leere Zeitplanliste gespeichert; die alten Zeitplanentwürfe bleiben nur bis zum Speichern oder Verlassen der Seite erhalten.

Bei genau einem einmaligen Zeitplan archiviert **Nach erfolgreichem geplanten Termin archivieren** die Automation nach einer erfolgreichen geplanten Ausführung. Manuelle, fehlgeschlagene, abgebrochene oder übersprungene Ausführungen lösen keine Archivierung aus. Archivierte Automationen können einschließlich ihres Verlaufs unter **Archiv** dauerhaft gelöscht werden.

Bei Zeitumstellungen werden im Frühjahr übersprungene lokale Termine einmal nach dem Zeitsprung und im Herbst doppelt vorkommende Termine einmal ausgeführt. Während eines Serverausfalls verpasste Termine werden nicht nachgeholt. Gleichzeitige Zeitpläne werden zu einer Ausführung zusammengefasst; wird ein Termin fällig, während die vorherige Ausführung noch aktiv ist, entsteht ein übersprungener Verlaufseintrag.

## Weitere Auftragseinstellungen

Jede Automation besitzt einen eigenen dauerhaften Ausführungskontext. Frühere Ergebnisse, Dateien und Freigaben können spätere Ausführungen beeinflussen; sie beginnt nicht jedes Mal als neuer Chat.

**Weitere Auftragseinstellungen** übernimmt Einstellungen einmalig aus einem Chat, darunter Agentenprofil, Modell, Memory, Websuche, Computer und Arbeitsordner. Spätere Änderungen am Quellchat werden nicht synchronisiert. Ohne ausdrückliche Modellwahl wird das Agentmodell des übernommenen Profils verwendet.

Ein Skill ist optional. Der Agent kann mit dem normalen Skill-Tool einen passenden oder im Auftrag ausdrücklich genannten Skill auswählen. **Skill verbindlich verwenden (optional)** bindet stattdessen einen bestimmten Skill anhand seiner ID und schreibt dessen Laden vor. Skills ergänzen Anweisungen, aber niemals Tools oder Rechte. Die normalen Profilrechte und Freigaben gelten weiterhin.

## Ergebnisse und Verlauf

**Verlauf** zeigt Ergebnisse oder kontrollierte Fehler, Ausführungsstatus und Zeitangaben. **Konfiguration dieses Laufs** enthält den damaligen Auftrag und die für diese Ausführung verwendete Konfiguration; spätere Änderungen wirken sich darauf nicht aus. Benachrichtigungen über Abschluss oder Fehler öffnen direkt die betreffende Ausführung.

Ein Ausgabechat erhält die abschließende erfolgreiche Agentenantwort, nicht interne Überlegungen oder Tool-Datensätze. Ist der Chat beschäftigt, wartet die Zustellung. Ein Zustellungsfehler ist von einem Ausführungsfehler getrennt: Ein erfolgreiches Ergebnis bleibt auch bei fehlgeschlagener Zustellung verfügbar. Nach drei technischen Zustellversuchen wiederholt **Zustellung erneut versuchen** nur die Zustellung, nicht den Auftrag.

Nach Auswahl eines Ausgabechats bietet **Weitere Ergebniseinstellungen** den Standardkanal des Chats, die MatoLoop-App oder einen verfügbaren persönlichen Kanal an. Ein gespeicherter, aber nicht verfügbarer Kanal bleibt sichtbar; bei deaktivierten Providern fällt die Zustellung gemäß Kanalrichtlinie auf die App zurück.

**Automation an diesen Chat binden** ändert das Löschverhalten: Beim Löschen dieses Chats muss auch die abhängige Automation gelöscht werden. Bei einer optionalen Verknüpfung kann der Chat getrennt werden, während die Automation erhalten bleibt.

## Agenten-Tools und Rechte

Der Agent kann Automationen einschließlich rein manueller Aufträge auflisten, erstellen, bearbeiten, starten und löschen. Diese Aktionen verwenden die Rechte des gewählten Agentenprofils. Die Auswahl eines Skills oder das Erstellen einer Automation gewährt keinen zusätzlichen Tool-Zugriff und umgeht keine Freigaben.
