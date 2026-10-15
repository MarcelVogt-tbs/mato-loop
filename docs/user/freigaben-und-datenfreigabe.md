# Freigaben und Datenfreigabe

## Session-Freigaben

Unter **Session > Session-Freigaben** sehen Sie die erteilten Sessionrechte. Computer und Arbeitsverzeichnis wählen Sie in den eigenen Feldern des Sessionbereichs. Eine Ordnerwahl erlaubt Navigation, keine Datei- oder Shell-Aktionen. Automatische Regeln und konfigurierte Shell-Präfixe gelten auch bei leerer Freigabeliste. Eine Freigabe überstimmt keine deaktivierte Aktion oder harte Grenze.

## Freigabeanfragen

Benötigt eine zulässige Aktion Zustimmung, pausiert der ausführende Agent und zeigt beim Tool-Aufruf Aktion, Ressource, angefragten Umfang und gegebenenfalls Inhalte oder Risikohinweise. **Deaktiviert** und harte Grenzen sind dagegen keine durch Zustimmung überstimmbaren Rückfragen.

**Control Center > Benachrichtigungen** verweist auf die Quell-Session; dort entscheiden Sie im Gesprächskontext. Dreaming-Hinweise öffnen **Memory > Dreaming > Offene Vorschläge**. Lesen oder Löschen eines Hinweises entscheidet nichts. Anfragen bleiben bei geschlossenem Browser offen, solange ihr Lauf aktiv ist. Ein Backend-Neustart beendet aktive Läufe und lässt deren Anfragen ablaufen; unabhängige Memory-Vorschläge bleiben erhalten.

## Entscheidungen

| Entscheidung | Wirkung |
| --- | --- |
| Ablehnen | Die angefragte Aktion wird nicht ausgeführt. |
| Für diesen Lauf erlauben | Passender angezeigter Zugriff gilt bis zum Ende des ausführenden Laufs. |
| Für Session erlauben | Passender Zugriff gilt auch für spätere Läufe derselben Session bis zum Widerruf. |

Der Haken **Passende Aktionen in dieser Session nicht mehr nachfragen** ändert den Hauptbutton zur Session-Freigabe. Er ist bei neuen Anfragen ausgeschaltet. Wenn **Vertrauliche Pfade und mögliche Secret-Ausgabe** auf **Nachfragen** steht, bieten betroffene Aktionen keine Session-Freigabe; ihre Zustimmung bleibt an exakte Werte und geprüfte Revisionen gebunden. Dasselbe gilt für technisch nicht wiederverwendbare Aktionen.

Unter **Session > Session-Freigaben** sehen Sie Sessionrechte mit Zielpfad, Computer und Laufzeit. Alle Einträge sind einzeln oder gemeinsam widerrufbar. Laufrechte erscheinen dort nicht und bleiben von **Alle Session-Freigaben widerrufen** unberührt. Die Einträge sind keine offenen Anfragen und zählen nicht zum Benachrichtigungszähler. Reload beendet eine Freigabe nicht. Profil, Vertrauenslevel, Computer/Ressource, Lesen/Schreiben und gegebenenfalls Integrationsrevision grenzen sie weiterhin ein. **Inaktiv** kennzeichnet eine nicht mehr passende Bindung oder einen nicht mehr unterstützten alten Freigabetyp. Ein Subagenten-Laufrecht überträgt sich nicht auf andere Läufe.

Dateirechte gelten für den exakten angezeigten Pfad und dieselbe Lese- oder Schreibklasse, ohne Vererbung an Unterordner oder Shell-Befehle. Geänderte Dateiinhalte allein heben ein solches Recht nicht auf. Administrator-Dateizugriffe sind gesondert markiert und an den exakten Pfad gebunden. Frühere pauschale Bereichs- oder computerweite Administratorfreigaben erlauben keine neuen Aktionen.

### Shell-Aktionen

Agentenbefehle laufen in frischen Prozessen im Sessionordner. Eine Shell-Freigabe prüfen Sie an der Anfrage; die persönliche Terminalzeile ist kein Freigabemechanismus. **Strg+C** im persönlichen Terminal betrifft dessen Prozess, der Stoppknopf im Chat den Agentenlauf. Als Lesen eingestufte Befehle können unbekannte Nebenwirkungen haben; siehe [Tools und Integrationen](tools-und-integrationen.md#lokale-funktionen).

Eine wiederverwendbare Shell-Freigabe gilt für den exakten Befehl auf demselben Computer, mit gleichem Arbeitsverzeichnis, gleicher Umgebung und gleicher Administrator-Einstellung. Ein anderer Befehl benötigt bei **Nachfragen** eine eigene Freigabe. Automatische Regeln und passende konfigurierte Präfixe bleiben wirksam.

## Vertrauenslevel und Datenfreigabe

- **Privat** bezeichnet einen von Ihnen als vertrauenswürdig eingestuften Provider.
- **Öffentlich** behandelt ihn als externen Empfänger und verwendet die dafür konfigurierten Berechtigungen und Memory-Zielgruppen.

Unter **Administration > Provider > Einstellungen > Modelltrennung** aktivieren Sie die Unterscheidung privater und öffentlicher Modelle. Die Einstufung legen Sie pro Provider fest. Bei aktiver Modelltrennung gelten dafür getrennte Tool-Rechte, Erinnerungen und persönliche Profilangaben. **Grundeinstellungen** verlinkt unter demselben Namen direkt dorthin.

- Die Änderung wird sofort gespeichert.
- Ohne Trennung gelten für alle Modelle dieselben Datenfreigaben. Auch Cloud-Modelle können dann privat freigegebene Erinnerungen und Profilangaben erhalten.
- Die Auswahlfelder für die Trennung werden ausgeblendet; Berechtigungen verwenden den privaten Zweig unter **Alle Modelle**.
- Gespeicherte Einstufungen und öffentliche Regeln bleiben zum Wiedereinschalten erhalten.
- Laufende Läufe behalten ihre eingefrorene Einstufung.

Die Einstellung prüft weder Lokalität noch TLS oder Betreiber. Mit der Modellwahl entscheiden Sie über die Übertragung bereits autorisierter Nachrichten, Dateien, Tool-Ergebnisse und Integrationsdaten an diesen Provider. Es gibt dafür keine zweite pauschale Session-Datenfreigabe. Eigentümergrenzen, Memory-Zielgruppen und Aktionsrechte gelten trotzdem. Ein Widerruf kann bereits übertragene Daten nicht zurückholen.

Pseudonymisierung ist unabhängig vom Vertrauenslevel am Provider und optional am Modell einstellbar. Die ausdrückliche Auswahl **Öffentlich** schaltet sie im Formular zunächst ein; Sie können sie danach ausschalten. Die Auswahl **Privat** oder das Abschalten der Trennung ändert den gespeicherten Pseudonymisierungswert nicht. Sie erkennt bestimmte Kennungen und strukturierte Secrets, aber nicht beliebige vertrauliche Aussagen, Personen oder Orte. Bilder, Audio und andere Binärdaten werden nicht pseudonymisiert. Die Erkennung ersetzt keine bewusste Provider- und Datenwahl.

## Rückfragen und geschützte Eingaben

Normale Rückfragen erlauben Auswahl/Freitext und **Fertig** auch bei unbeantworteten Fragen. Sie erzeugen keine Tool-Rechte. Geschützte Credential-Felder verbergen Werte vor dem Modell und liefern nur Referenzen. Auch ein eingegebenes oder gemerktes Administratorpasswort dient ausschließlich der Authentifizierung; Aktionen benötigen weiterhin passende Regeln oder Freigaben. Die Einzelheiten stehen unter [Variablen und Secrets](variablen-und-secrets.md).

## Memory und Personalisierung

Änderungen an Memory, Persona und persönlichem Profil können eine Freigabe benötigen. Prüfen Sie Inhalt und privaten/öffentlichen Modellbereich, weil die Wirkung über diese Antwort hinausgeht. Memory-Vorschläge haben zusätzlich veraltete-Ziel-Prüfungen.

!!! warning "Keine Secrets in Prompts"
    Verwenden Sie geschützte Eingaben für Passwörter und Tokens. Normale Chat-, Persona- und Skilltexte können an Modelle übertragen werden; automatische Erkennung deckt nicht jede Darstellung ab.
