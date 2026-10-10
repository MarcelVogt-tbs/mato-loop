# Freigaben und Datenfreigabe

## Freigabeanfragen

Benötigt eine zulässige Aktion Zustimmung, pausiert der ausführende Agent und zeigt beim Tool-Aufruf Aktion, Ressource, angefragten Umfang und gegebenenfalls Inhalte oder Risikohinweise. **Deaktiviert** und harte Grenzen sind dagegen keine durch Zustimmung überstimmbaren Rückfragen.

**Control Center > Benachrichtigungen** verweist auf die Quell-Session; dort entscheiden Sie im Gesprächskontext. Dreaming-Hinweise öffnen **Memory > Dreaming > Offene Vorschläge**. Lesen oder Löschen eines Hinweises entscheidet nichts. Anfragen bleiben bei geschlossenem Browser offen, solange ihr Lauf aktiv ist. Ein Backend-Neustart beendet aktive Läufe und lässt deren Anfragen ablaufen; unabhängige Memory-Vorschläge bleiben erhalten.

## Entscheidungen

| Entscheidung | Wirkung |
| --- | --- |
| Ablehnen | Die angefragte Aktion wird nicht ausgeführt. |
| Für diesen Lauf erlauben | Passender angezeigter Zugriff gilt bis zum Ende des ausführenden Laufs. |
| Für Session erlauben | Passender Zugriff gilt auch für spätere Läufe derselben Session bis zum Widerruf. |

Der Haken **Passende Aktionen in dieser Session nicht mehr nachfragen** ändert den Hauptbutton zur Session-Freigabe. Er ist bei neuen Anfragen ausgeschaltet. Sensible oder nicht wiederverwendbare Aktionen bieten keine Session-Freigabe; ihre Zustimmung bleibt an exakte Werte und geprüfte Revisionen gebunden.

Unter **Session > Session-Freigaben** zeigen und widerrufen Sie gespeicherte Rechte einzeln oder gemeinsam. Sie sind keine offenen Anfragen und zählen nicht zum Benachrichtigungszähler. Reload beendet eine Freigabe nicht. Profil, Vertrauenslevel, Computer/Ressource, Lesen/Schreiben und gegebenenfalls Integrationsrevision grenzen sie weiterhin ein. Ein Subagenten-Laufrecht überträgt sich nicht auf andere Läufe.

### Shell-Aktionen

Agentenbefehle laufen in frischen Prozessen im Sessionordner. Eine Shell-Freigabe prüfen Sie an der Anfrage; die persönliche Terminalzeile ist kein Freigabemechanismus. **Strg+C** im persönlichen Terminal betrifft dessen Prozess, der Stoppknopf im Chat den Agentenlauf. Als Lesen eingestufte Befehle können unbekannte Nebenwirkungen haben; siehe [Tools und Integrationen](tools-und-integrationen.md#lokale-funktionen).

## Vertrauenslevel und Datenfreigabe

- **Privat** bezeichnet einen von Ihnen als vertrauenswürdig eingestuften Provider; Pseudonymisierung ist aus.
- **Öffentlich** behandelt ihn als externen Empfänger und erlaubt den Schutz erkannter Kennungen und Secrets.

Die Einstellung prüft weder Lokalität noch TLS oder Betreiber. Mit der Modellwahl entscheiden Sie über die Übertragung bereits autorisierter Nachrichten, Dateien, Tool-Ergebnisse und Integrationsdaten an diesen Provider. Es gibt dafür keine zweite pauschale Session-Datenfreigabe. Eigentümergrenzen, Memory-Zielgruppen und Aktionsrechte gelten trotzdem. Ein Widerruf kann bereits übertragene Daten nicht zurückholen.

Pseudonymisierung öffentlicher Textanfragen erkennt bestimmte Kennungen und strukturierte Secrets, aber nicht beliebige vertrauliche Aussagen, Personen oder Orte. Bilder, Audio und andere Binärdaten werden nicht pseudonymisiert. Die Erkennung ersetzt keine bewusste Provider- und Datenwahl.

## Rückfragen und geschützte Eingaben

Normale Rückfragen erlauben Auswahl/Freitext und **Fertig** auch bei unbeantworteten Fragen. Sie erzeugen keine Tool-Rechte. Geschützte Credential-Felder verbergen Werte vor dem Modell und liefern nur Referenzen; ausgefüllte Administratorfelder können gesonderte Lauf- bzw. Sessionrechte erzeugen. Ein ausgelassenes Passwort erlaubt keinen Administratorzugriff. Die Einzelheiten stehen unter [Variablen und Secrets](variablen-und-secrets.md).

## Memory und Personalisierung

Änderungen an Memory, Persona und persönlichem Profil können eine Freigabe benötigen. Prüfen Sie Inhalt und privaten/öffentlichen Modellbereich, weil die Wirkung über diese Antwort hinausgeht. Memory-Vorschläge haben zusätzlich veraltete-Ziel-Prüfungen.

!!! warning "Keine Secrets in Prompts"
    Verwenden Sie geschützte Eingaben für Passwörter und Tokens. Normale Chat-, Persona- und Skilltexte können an Modelle übertragen werden; automatische Erkennung deckt nicht jede Darstellung ab.
