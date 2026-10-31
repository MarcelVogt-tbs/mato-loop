# Integrationen einrichten

Verbinde hier Kalender, E-Mail und andere Dienste mit MatoLoop. Gemeinsame Verbindungen stehen unter **Administration > Integrationen**, persönliche unter **Einstellungen > Integrationen**. Computergebundene Integrationen, OpenCode und tbs.Desk sind persönlich. Generische MCP-Server haben einen eigenen Menüpunkt.

## Eine Verbindung anlegen und testen

1. Öffne im passenden Bereich **Integrationen > Verwaltung > Integration hinzufügen**.
2. Wähle Typ, Anbieter und Name, dann **Weiter**.
3. Ergänze die in der folgenden Übersicht genannten Angaben und wähle **Speichern**. Erst jetzt wird die Verbindung angelegt.
4. Wähle **Verbindung testen**. Der Test verwendet die gespeicherten Werte; speichere Änderungen daher vorher.
5. Prüfe **Berechtigungen** und die Verfügbarkeit für das gewünschte Agentenprofil.

Der Stift in der Liste öffnet dieselbe Seite zum Bearbeiten. Leere Credential-Felder behalten gespeicherte Zugangsdaten. **Speichern** übernimmt den aktiven Tab. Tabwechsel erhalten Entwürfe; beim Verlassen kannst du sie verwerfen.

Zugangsdaten werden verschlüsselt gespeichert und nicht wieder im Klartext angezeigt. Verwende HTTPS außerhalb eines ausdrücklich vertrauenswürdigen Netzes; einige Anbieter verlangen es grundsätzlich.

## Benötigte Angaben

| Dienst | Verbindung | Zugang |
| --- | --- | --- |
| CalDAV | HTTPS-Serveradresse | Benutzername und Passwort/App-Passwort |
| IMAP/SMTP | Beide Server, TLS-Modus und fester Absender | Benutzername und Passwort/App-Passwort; kein unverschlüsselter Betrieb |
| Mealie | Basis-URL ohne `/api` | Langlebiger API-Token aus dem Mealie-Benutzerprofil |
| BookStack | HTTPS-Serveradresse | Token-ID und Token-Secret aus dem Benutzerprofil |
| Arcane | Manager-URL | API-Schlüssel mit passenden Umgebungsrechten |
| SonoBin | HTTPS-Basis-URL; `/mcp/` wird abgeleitet | Persönlicher Bearer-Token aus den Kontoeinstellungen |
| tbs.Desk | Basis-URL | Benutzername und Passwort |
| OpenCode | Basis-URL | Benutzername und optionales Passwort |
| OpenSCAD | Eigener verbundener Linux-Computer | Geprüfter Binary-Pfad über **Erkennen** |
| Browser | Eigener verbundener Linux-Computer | [Browser einrichten](browser.md#browser-einrichten) |
| SearXNG | Globale Serveradresse mit aktivierter JSON-Schnittstelle | Je nach Server |
| Jina Reader | Globale Reader-Basis-URL | Optionaler API-Schlüssel; [Webabrufe konfigurieren](webrecherche-und-quellen.md#webabrufe-konfigurieren) |

## Rechte und Profilverfügbarkeit

Im Tab **Berechtigungen** wählst du **Standard für diese Integration** oder ein Agentenprofil. **Eigene Regel hinzufügen** legt eine Abweichung für eine Aktion oder Ressource an. **Übernehmen** aktualisiert das Formular, **Speichern** die Verbindung. Entfernen einer Regel stellt die Vererbung wieder her.

Ohne eigene Regel gilt das [Freigabeverhalten](tools-und-integrationen.md#globale-tool-vorgaben-anwenden). Eigene Verbindungs-, Profil- und Ressourcenregeln können davon abweichen. Globale Aktionsregeln für eingebaute Tools sind keine zusätzliche Obergrenze für Integrationen. Die Rechte des Dienstkontos gelten immer zusätzlich.

Ressourcen wie Kalender oder Bücher lassen sich ein- und ausschalten, ohne ihre Regeln zu löschen. Profilabweichungen gehen dem gleichen Verbindungswert vor; Ressourcenrechte sind spezieller als allgemeine Aktionsrechte. Zum Anpassen einer nur geerbten Ressource wähle **Eigene Regel hinzufügen**, die Ressource und **Verfügbarkeit**.

Die allgemeine Auswahl **Integrationen standardmäßig verfügbar** steht im Agentenprofil unter **Weitere Einstellungen**. Ausnahmen für einzelne Verbindungen bearbeitest du ausschließlich an der Verbindung. Persönliche Verbindungen können eigene Regeln für zugängliche globale oder eigene Profile erhalten; globale Verbindungen verwalten Administratoren für globale Profile.

### Werkzeuge sofort oder bei Bedarf laden

**Agentenprofil > Weitere Einstellungen > Integrationen > Integrations-Tool-Verfügbarkeit** bietet:

- **Bei Bedarf:** Der Agent sucht passende Werkzeuge und lädt ihre Beschreibungen nach.
- **Native sofort:** Produktintegrationen werden sofort geladen, generische MCP-Tools bei Bedarf. Dies ist die Vorgabe für Allgemein.
- **Alle:** Alle erlaubten Werkzeuge werden sofort geladen; große Kataloge benötigen mehr Modellkontext.

Die anderen mitgelieferten Profile verwenden **Bei Bedarf**. **Tool-Ladeverhalten anpassen** an einer Verbindung überschreibt dies für die Verbindung oder ein Profil. Das Laden gewährt keine Ausführungsrechte. Nach Kontextverdichtung können Werkzeuge erneut gesucht werden müssen.

## Kalender und Aufgaben mit CalDAV

Nach dem Verbindungstest wähle unter **Standardressourcen dieses Kontos** einen Standardkalender und eine Standard-Aufgabenliste. Unspezifische Anfragen nutzen diese Auswahl; eine ausdrücklich benannte Ressource hat Vorrang.

Den Standardkalender bekommt das Modell als Hinweis mit, sofern die Berechtigungen ihn für die jeweilige Kalenderaktion zulassen. Deaktivierte Kalender werden nicht als Hinweis mitgegeben. Das Modell muss den Zielkalender bei jedem Terminaufruf ausdrücklich angeben; MatoLoop ergänzt ihn nicht automatisch. Prüfe bei einer Freigabe den angezeigten Kalender und das Konto, bevor du zustimmst.

Bitte beispielsweise: „Lege morgen um 10 Uhr einen Termin im Kalender an.“ Termine unterstützen bis zu zehn Erinnerungen. Wiederkehrende Termine und solche mit Einladungen oder nicht unterstützten Eigenschaften lassen sich lesen, aber nicht verändern.

Aufgaben unterstützen Titel, Notiz, Fälligkeit und offen/erledigt. Erledigte Aufgaben werden standardmäßig ausgeblendet. Bis zu 50 fehlende Aufgaben können gemeinsam erstellt werden; gleichnamige offene Aufgaben werden übersprungen. Wiederholungen, Unteraufgaben, Erinnerungen, Kategorien und Prioritäten lassen sich nicht bearbeiten.

## E-Mail lesen und senden

Bei einem aktiven Konto erscheint **E-Mail** im Control Center. Wähle einen Ordner und bestätige Suchbegriffe mit Enter oder dem Suchsymbol. **Weitere laden** zeigt ältere Nachrichten oder weitere Treffer. Du kannst Anhänge herunterladen, Entwürfe speichern und senden. Externe HTML-Inhalte werden nicht nachgeladen; eine lokale Mail-Synchronisationsdatenbank gibt es nicht.

Die Kontorechte unterscheiden Lesen, Entwürfe verwalten, Senden und Löschen. IMAP-Ordner besitzen keine eigenen Rechte.

Hinterlege bei Bedarf in den Kontoeinstellungen eine **Signatur (optional)**. Sie wird beim Erstellen einer neuen E-Mail einmal in den Mailtext eingefügt. Du kannst sie dort ändern oder entfernen; gespeicherte Entwürfe bekommen keine zusätzliche Signatur. Bei einer Versandfreigabe kannst du über **Argumente bearbeiten** den Betreff und den gesamten Mailtext einschließlich Signatur ändern. Empfänger und Anhänge bleiben dabei unverändert.

!!! warning "Versand nicht blind wiederholen"
    Die Annahme durch SMTP bestätigt keine Zustellung beim Empfänger. Fehlt die Bestätigung der Kopie unter **Gesendet**, zeigt MatoLoop eine Warnung und erhält den Entwurf. Die Nachricht wurde trotzdem bereits an den Mailserver übergeben. Sende den Entwurf nicht erneut; prüfe zuerst den Mailserver. Bei einem unklaren Versand erfolgt ebenfalls keine automatische Wiederholung.

## Rezepte und Einkaufsliste mit Mealie

Der Agent kann Rezepte suchen, lesen, erstellen, bearbeiten, per URL importieren und löschen sowie den Essensplan verwalten. Nenne bei Suchfiltern die exakten Tags, Kategorien oder Lebensmittel.

Nicht angegebene Rezeptfelder bleiben erhalten; übergebene Zutaten- oder Zubereitungslisten ersetzen die jeweilige Liste vollständig. Nach einem unklaren Schreibergebnis prüfe Mealie vor einer Wiederholung: Bei einer Erstellung kann bereits ein teilweise befülltes Rezept vorhanden sein.

Für eine Einkaufsliste richte eine CalDAV-Aufgabenliste ein und wähle sie als Standard. Bitte den Agenten, den Wochenplan und die Rezepte zu lesen und fehlende Einkaufsaufgaben anzulegen. Er fasst kompatible Mengen zusammen; unklare Einheiten bleiben getrennt. Bestehende offene Aufgaben bleiben erhalten. Es gibt weder automatische Synchronisierung noch native Mealie-Einkaufslisten.

## Wissen mit BookStack verwalten

Der Agent kann Seiten, Bücher, Kapitel und Regale verwalten; Seiteninhalte werden als Markdown ausgetauscht. Regalzuordnungen bearbeitest du über das Regal. Das Löschen eines Regals löscht seine Bücher nicht.

Rechte einzelner Bücher begrenzen auch Suche, Bücherlisten, Regalzuordnung und Anhänge. Nicht erlaubte Bücher bleiben bei einer vollständigen Regaländerung erhalten. Die Ressourcenverwaltung wird bei mehr als 500 sichtbaren Büchern abgelehnt, statt Regeln für unsichtbare Bücher zu übergehen.

Anhänge können aus einer URL, der Session-Ablage, einer Computerdatei oder einem reinen Link stammen. Uploads sind auf 32 MiB begrenzt; Quellzugriff und Webabrufe benötigen ihre eigenen Rechte. Bei einem fehlgeschlagenen Download kann der Agent stattdessen einen Link hinterlegen.

!!! warning "Eine Seitenänderung ersetzt den gesamten Inhalt"
    Der Agent liest die Seite vor dem Schreiben erneut und lehnt erkannte Versionskonflikte ab. BookStack unterstützt jedoch kein bedingtes Speichern: Eine zeitgleiche Änderung zwischen Prüfung und Speichern kann überschrieben werden. Sehr große Seiten werden deshalb nicht gekürzt zum Zurückschreiben geliefert.

## Docker mit Arcane verwalten

Wähle nach dem Test die erlaubten Umgebungen und Aktionen. Der Agent kann Container, Projekte, Images, Volumes und Netzwerke untersuchen und angebotene Verwaltungsaktionen ausführen. Compose- und Umgebungsinhalte können vertrauliche Daten enthalten; dafür gelten zusätzliche sensible Zugriffsrechte. Container-exec und Massenbereinigung gehören nicht dazu.

Das Speichern einer Projektkonfiguration startet kein Deployment. Nach einem unklaren Änderungsergebnis prüfe Arcane vor einer Wiederholung; MatoLoop wiederholt die Mutation nicht automatisch. Die Arcane-Rechte des API-Schlüssels bleiben bindend.

## Musik mit SonoBin verwenden

Suchen, Playlists, Likes und entfernte Imports folgen den SonoBin-Kontorechten. Playliständerungen laden keine Musik herunter. Bei einem Import verfolge den Auftrag bis zum bestätigten Bibliotheksimport; ein fertiger Download reicht dafür nicht.

SonoBin-Tools teilen derzeit die Kategorie **Schreiben**. Auch reine Leseaufrufe benötigen unter den ausgelieferten Grundstandards eine Freigabe, sofern keine eigene Regel sie erlaubt. Administration, Diagnostik und Assetdownloads brauchen zusätzlich passende SonoBin-Berechtigungen und Serverkonfiguration.

## Support mit tbs.Desk

Teste und aktiviere die persönliche Verbindung. Sie stellt den **Support-Assistent** und die tbs.Desk-App bereit. Der Agent sucht anhand eines Kundenhinweises oder aktiven Anrufs passende Kunden. Wähle den Kunden aus dem Ergebnis; Ticketzugriff wird auf ihn begrenzt.

## Projekte mit OpenCode verbinden

1. Lege die persönliche OpenCode-Verbindung an; ein MatoLoop-Computer ist dafür nicht nötig.
2. Öffne ein Projekt und wähle **Control Center > OpenCode > OpenCode verbinden**, dann Server, erkanntes Projekt und Arbeitsverzeichnis.
3. Wähle unter **OpenCode > Modelle** getrennte Analyse- und Ausführungsmodelle. **OpenCode-Standard** übernimmt die Modellvariante; andere angebotene Reasoning-Varianten gelten für das gewählte Modell.
4. Beauftrage im Projektchat Analyse und Planung. OpenCode untersucht das Repository schreibgeschützt; MatoLoop speichert daraus den Plan.
5. Wähle den gewünschten Plan und reihe ihn in die Ausführungswarteschlange ein.

Die Bindung kann bis zum ersten gespeicherten Plan geändert oder getrennt werden. Die Dateiansicht ist schreibgeschützt. Pro Projekt läuft höchstens eine Ausführung. Externe OpenCode-Aktivität oder ein unklarer Ausgang pausiert die Warteschlange; prüfe vor dem Fortsetzen den tatsächlichen Arbeitsbaum. Freigaben erscheinen mit **Einmal erlauben**, **Immer erlauben** oder **Ablehnen**.

## 3D-Modelle mit OpenSCAD erstellen

Der Docker-Computer enthält OpenSCAD bereits; native Linux-Computer benötigen eine Installation. Lege eine Verbindung an, wähle den Computer und übernimm den Binary-Pfad mit **Erkennen**. Der Test prüft auch ein begrenztes Rendering.

Beschreibe im Chat das Modell mit Abmessungen und Randbedingungen. Neue Dateien liegen standardmäßig in der Session-Ablage; vorhandene Computerprojekte können ausdrücklich gewählt werden. Der Agent rendert und prüft Diagnosen, Abmessungen, Topologie und Ansichten. Ohne bildfähiges Modell oder **Standardmodelle > Bildanalyse** bleibt das Ergebnis visuell ungeprüft.

Unter **Ablage** oder **Computer-Dateien** wechselst du bei `.scad` zwischen Quelle und 3D-Ansicht und exportierst ausdrücklich STL. Vorschaugeometrie und Prüfbilder sind temporär; STL-Exporte werden dauerhaft gespeichert. Rendering zählt als Schreiben und benötigt standardmäßig Zustimmung.

!!! warning "Keine Garantie für Druckbarkeit oder Festigkeit"
    Ein erfolgreicher Render und eine Sichtprüfung beweisen weder verdeckte Geometrie noch Passung, Wandstärke, Festigkeit oder Druckbarkeit. OpenSCAD arbeitet mit den Betriebssystemrechten des Agents, nicht in einer OS-Sandbox. Große Modelle können nur zum Download verfügbar sein.

## MCP-Server integrieren

Generische Server richtest du unter **Einstellungen > MCP-Server** oder **Administration > MCP-Server** ein. Persönliche Server benötigen administrative Freigabe. Unterstützt wird Streamable HTTP ohne Anmeldung, mit Bearer-Token oder OAuth. HTTP überträgt Zugangsdaten unverschlüsselt; verwende außerhalb eines vertrauenswürdigen Netzes HTTPS.

Prüfe nach dem Speichern den erkannten Tool-Katalog und dessen Rechte. Ein fehlgeschlagener Refresh erhält den vorherigen Katalog. Namen und Beschreibungen sind durchsuchbar, aber unbestätigte Serverangaben: Sie gewähren keine Rechte. MCP-Tools teilen derzeit ebenfalls die Kategorie **Schreiben**. Produktadapter wie SonoBin richtest du im eigenen Produktbereich ein.

### Mit OAuth anmelden

1. Prüfe die öffentliche Adresse unter **Administration > Grundeinstellungen**; sie muss den Ursprung ohne Pfad enthalten.
2. Wähle **Anmeldung öffnen** und bestätige beim Anbieter.
3. Beende die Anmeldung in MatoLoop.

Der Server muss MCP-Authentifizierungsmetadaten und Bearer-Zugriff unterstützen; Browser-SSO allein reicht nicht. Ein vorregistrierter Client benötigt `<ursprung>/api/mcp/oauth/callback`. Nach Ablauf oder Backend-Neustart starte den Anmeldeversuch erneut. Gespeicherte Verbindungen bleiben erhalten; nach abgelehnter Token-Erneuerung ist eine neue Anmeldung nötig. Tool-Aufrufe werden nicht automatisch wiederholt.
