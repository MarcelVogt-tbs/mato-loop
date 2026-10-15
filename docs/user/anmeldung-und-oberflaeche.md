# Anmeldung und Oberfläche

## Anmeldung

MatoLoop verwendet eine lokale Cookie-Anmeldung. Geschützte Seiten leiten zur Anmeldung weiter; **Abmelden** beendet sie. Externe SSO-Anmeldung ist nicht implementiert. Das erste Konto ist Administrator. Unter **Konto** ändern Sie Ihr Passwort; dadurch werden bestehende Anmeldungen dieses Kontos beendet.

Unter **Administration > Benutzer** können Administratoren Konten anlegen, Passwörter zurücksetzen, Konten aktivieren/deaktivieren und Rollen vergeben. Ein Konto muss vor dem Löschen deaktiviert sein. Das eigene Konto und der letzte aktive Administrator sind geschützt.

Administratorrechte erlauben gemeinsame Konfiguration und Kontoverwaltung, keinen Zugriff auf persönliche Inhalte anderer Benutzer. **Administration** und **Einstellungen** haben getrennte gemeinsame bzw. persönliche Listen. Beim Löschen eines Kontos entfernt MatoLoop dessen Datenbankdaten und Backend-Sessionbäume; Computerdateien und externe Daten bleiben beim jeweiligen Dienst.

## Navigation

Die Seitenleiste enthält Home, Suche, Projekte und Unterhaltungen, Automationen, **Variablen & Secrets**, **Archiv** und das Benutzermenü. Statussymbole zeigen aktive Arbeit, offene Anfragen und ungelesene Antworten. Projekt-Sessions werden beim Aufklappen geladen; **Mehr anzeigen** lädt weitere Einträge. Listenfehler können direkt erneut versucht werden.

Home zeigt zuletzt aktive Unterhaltungen, offene Anfragen, kommende Automationen und Computerstatus. Manche Übersichtselemente sind auf kleinen Bildschirmen ausgeblendet; der Chat bleibt nutzbar. Eine ausdrückliche neue Unterhaltung öffnet einen leeren Entwurf.

### Suche

Die Suche findet eigene Projekte, Sessions und Nachrichten ab zwei Zeichen. **Strg+F** bzw. **Cmd+F** öffnet die Anwendungssuche; ist sie bereits offen, bleibt die Browsersuche verfügbar. Ohne Embedding-Dienst funktioniert die lexikalische Suche weiter. Suchergebnisse öffnen den passenden Gesprächskontext.

### Archiv

Über das Menü einer Session oder eines Projekts archivieren Sie Unterhaltungen. **Archiv** ermöglicht Lesen und Wiederherstellen. Ein archiviertes Projekt archiviert seine Sessions wirksam mit; einzeln archivierte Sessions bleiben nach der Projektwiederherstellung archiviert. Neue Aufträge benötigen die Wiederherstellung, bestehende Arbeit darf noch enden. Mehr zu Zeitplänen unter [Automationen](automationen.md).

## Verwaltungslisten

In den Listen für Skills, Agenten, Provider, Modelle, Integrationen und MCP-Server steht der Ein-/Ausschalter rechts, getrennt von den Aktionen. Eine Hauptaktion ist direkt erreichbar; weitere Aktionen wie Testen, Aktualisieren oder Löschen finden Sie im Drei-Punkte-Menü. Auf schmalen Bildschirmen steht diese Bediengruppe unter dem Text. Beschriftete Schalter behalten ihre Bedeutung: **Erlaubt** steuert eine Anbieterfreigabe, **Verfügbar** die Tool-Verfügbarkeit und **Admin** die Benutzerrolle. Benutzerkonten aktivieren oder deaktivieren Sie im zugehörigen Drei-Punkte-Menü.

## Control Center

Die schwebende Schaltfläche öffnet das Control Center. **Session** ist der feste Einstellungsbereich. Neue Sessions haben außerdem die App **Ablage**; **Computer-Dateien**, **Automationen**, **Terminal**, **Browser** und andere verfügbare Apps fügen Sie über den Launcher hinzu. Sessiongebundene Apps können einen Entwurf als leere Session speichern.

Apps lassen sich umordnen und schließen. Das Schließen eines App-Tabs löscht seine Daten oder Konfiguration nicht; eine Automation läuft beispielsweise weiter. Das Zuklappen des Panels erhält Tabs und Dateientwürfe. Nur beim Schließen einer Datei mit ungespeicherten Änderungen ist eine Entscheidung erforderlich. Der feste Sessionbereich kann nicht entfernt werden.

Reihenfolge, Dateiziele und Ansichten werden je Session gespeichert, Editorentwürfe bleiben im Browser-Arbeitsspeicher. Ab 1024 Pixeln stellt MatoLoop die gespeicherte Panel-Sichtbarkeit im geteilten Layout wieder her. Der mobile Vollbilddialog startet geschlossen und muss ausdrücklich geöffnet werden.

### Benachrichtigungen

Der globale Bereich **Benachrichtigungen** zeigt eine chronologische Liste. Sein Zähler zählt ausschließlich ungelesene Hinweise. Die schwebende Control-Center-Schaltfläche öffnet bei vorhandenem Zähler direkt diese Liste; sonst die zuletzt verwendete App.

Hinweise auf Freigaben und Rückfragen öffnen die Quell-Session; Dreaming-Vorschläge öffnen **Memory > Dreaming > Offene Vorschläge**. Normale Antworten und Laufzeitfehler erzeugen ebenfalls Hinweise, sofern ihre Session nicht sichtbar und fokussiert ist. Antwort- und Sessionanfrage-Hinweise verschwinden nach dem Lesen; andere Hinweise können einen gelesenen Verlauf behalten. Lesen oder Löschen entscheidet niemals die zugrunde liegende Anfrage.

Session-Freigaben verwalten Sie unter **Session > Session-Freigaben**. Computer und Arbeitsverzeichnis haben eigene Auswahlfelder im Sessionbereich. Push aktivieren Sie pro Gerät im Benachrichtigungsbereich, nachdem die Instanz dafür eingerichtet wurde; siehe [Betrieb](betrieb-und-datensicherung.md#app-und-push-benachrichtigungen).

## Desktop und Mobilgeräte

Auf größeren Bildschirmen teilen sich Chat und Control Center einen verstellbaren Bereich. Auf schmalen Geräten öffnet ein Vollbilddialog. Bei geschlossener Seitenleiste erscheint eine schwebende Navigationstaste.

## Heller und dunkler Modus

Das Benutzermenü schaltet den Farbmodus um. Die Dokumentation hat einen eigenen Schalter und berücksichtigt zunächst die Systemeinstellung.

### Darstellung der Instanz

Unter **Administration > Darstellung** ändern Administratoren Name und Beschreibung der Anwendung sowie Logo und Theme. **Anpassung** erlaubt einen Namen bis 80 Zeichen, Beschreibung bis 500 Zeichen und ein PNG-, JPEG- oder WebP-Logo bis 1 MiB; das Logo wird als quadratisches 512-Pixel-PNG normalisiert. Speichern und Zurücksetzen wirken auf die Instanz, einschließlich der Anmeldung.

Unter **Themes** wählen Sie ein aktiviertes Standardtheme. Der aktuelle Standard kann erst deaktiviert werden, wenn ein anderer gewählt wird. Derzeit ist nur MatoLoop mitgeliefert; eigene Theme-Auswahl je Benutzer oder Uploads gibt es nicht. Theme, Hell/Dunkel-Modus und Name/Persona des Assistenten sind getrennte Einstellungen. Andere geöffnete Browser laden die neue Darstellung beim nächsten Seitenaufruf.

## Ungespeicherte Änderungen

Ungespeicherte Dateiänderungen lösen vor Verlassen oder Verwerfen eine Rückfrage aus. Panelzuklappen und Tabwechsel erhalten sie. Ein Neuladen kann browserlokale Editorzustände verlieren; speichern Sie wichtige Änderungen. Die Dokumentation öffnet in einem eigenen Tab.
