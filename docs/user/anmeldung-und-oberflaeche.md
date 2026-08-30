# Anmeldung und Oberfläche

## Anmeldung

MatoLoop verwendet derzeit ausschließlich eine lokale, Cookie-basierte Anmeldung. Geschützte Seiten leiten automatisch zur Anmeldeseite weiter. Über **Abmelden** im Benutzermenü beenden Sie die Sitzung. Eine Anmeldung über einen externen OAuth- oder SSO-Anbieter ist noch nicht implementiert.

Das erste bei der Ersteinrichtung angelegte lokale Konto erhält Administratorrechte. Über **Konto** im Benutzermenü ändern Sie Ihr eigenes Passwort; danach werden alle bestehenden Anmeldungen dieses Kontos beendet.

Administratoren können unter **Einstellungen > Benutzer** lokale Konten anlegen, Passwörter zurücksetzen, Konten aktivieren oder deaktivieren und Administratorrechte zuweisen oder entziehen. Vor dem endgültigen Löschen muss ein Konto deaktiviert werden. Das eigene Konto kann nicht deaktiviert oder gelöscht werden; außerdem muss mindestens ein aktiver Administrator bestehen bleiben.

Administratorrechte erlauben die Benutzerverwaltung und Änderungen an gemeinsamen Instanzeinstellungen. Sie erlauben keinen Zugriff auf Sessions, Nachrichten, Dateien, Erinnerungen, Zugangsdaten, Arbeitsbereiche oder Computer anderer Benutzer. Der Umschalter **Persönlich** / **Instanz** bearbeitet beim Administrator entweder die eigenen Einstellungen oder die gemeinsam genutzte Instanz, niemals das persönliche Profil eines anderen Benutzers.

Beim Löschen entfernt MatoLoop die zugehörigen Datenbankdaten und Session-Bäume. Dateien, Caches und Konfigurationen auf Computern, einschließlich des mitgelieferten Compose-Computers, sowie Daten in externen Diensten werden nicht gelöscht und müssen dort bei Bedarf separat entfernt werden. Gemeinsam konfigurierte Instanzressourcen bleiben erhalten.

## Navigation

Die linke Seitenleiste enthält:

- Benachrichtigungsglocke als direkter Zugang zu **Benachrichtigungen** im Control Center
- aufklappbare Projekte mit ihren Sessions und einer Schaltfläche für eine neue Projekt-Session
- Bereich **Sessions** für Unterhaltungen ohne Projekt
- Statushinweise für aktive Läufe und offene Freigaben
- Benutzermenü mit Einstellungen, Dokumentation, Farbschema und Abmeldung

Projektordner laden ihre Sessions erst beim Aufklappen. **Mehr anzeigen** lädt innerhalb des jeweiligen Projekts oder im Bereich **Sessions** weitere Einträge. Ladefehler können direkt an der betroffenen Liste erneut versucht werden.

## Control Center

Die schwebende Schaltfläche **Control Center** öffnet eine gemeinsame Arbeitsfläche für globale Bereiche und die Integrationen der aktuellen Session. **Benachrichtigungen** und **Freigaben** stehen als feste globale Schaltflächen bereit. **Dateien** ist die eingebaute Session-Integration; weitere mit MatoLoop ausgelieferte Integrationen können eigene Ansichten ergänzen. Integrationstabs lassen sich per Drag-and-drop sortieren. Auf Touch-Geräten und mit der Tastatur stehen dafür zusätzlich **Nach links verschieben** und **Nach rechts verschieben** im Menü des Inhalts-Headers bereit. Reihenfolge, aktive Integration, geöffneter Zustand und die zuletzt verwendete Datei- oder Pfadansicht werden serverseitig für die Session gespeichert. Beim erneuten Öffnen einer Session wird dieser Zustand wiederhergestellt; ungespeicherte Änderungen gehören nicht dazu. Neue Sessions beginnen mit **Dateien**.

Der Schließen-Button im Inhalts-Header klappt das gesamte Control Center zu, ohne eine Integration zu entfernen. **Integration aus Session entfernen** ist davon getrennt im Drei-Punkte-Menü untergebracht und erfordert eine Bestätigung. Gespeicherte Daten werden dabei nicht gelöscht; die Integration kann später erneut hinzugefügt werden.

Die Glocke öffnet direkt **Benachrichtigungen**; dieser globale Bereich wird nicht als Session-Integration gespeichert. Dauerhafte Hinweise können einzeln oder gemeinsam als gelesen markiert werden. **Freigaben** zeigt normale offene Entscheidungen und Agent-Rückfragen aus allen Sessions; Entscheidungen sind dort möglich, Rückfragen öffnen ihre Session zur Beantwortung im Chat. Die gleichen Freigaben und Rückfragen bleiben zusätzlich im zugehörigen Chat sichtbar. Bestätigungsvorschläge im verbundenen Terminal bleiben im Terminal-Tab.

Normale Chat-Antworten erscheinen nicht zusätzlich als Benachrichtigung. Ergebnisse und Fehler von Automationsläufen erscheinen dagegen dauerhaft in dieser Zentrale.

## Desktop und Mobilgeräte

Auf größeren Bildschirmen können Chat und Control Center nebeneinander angezeigt und in der Breite angepasst werden. Auf schmalen Bildschirmen öffnet sich das Control Center als Vollbilddialog. Ist die Navigation geschlossen, erscheint eine schwebende Menüschaltfläche.

## Heller und dunkler Modus

Das Benutzermenü schaltet zwischen hellem und dunklem Modus um. Die Dokumentation besitzt einen eigenen Schalter und berücksichtigt beim ersten Aufruf die Systemeinstellung.

## Ungespeicherte Änderungen

MatoLoop warnt, wenn Sie eine bearbeitete Datei verlassen würden. Die Dokumentation öffnet sich deshalb in einem neuen Tab und unterbricht Ihre aktuelle Arbeit nicht.
