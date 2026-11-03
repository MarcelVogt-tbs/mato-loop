# Webrecherche und Quellen

## Websuchmodus der Session

| Modus | Verhalten |
| --- | --- |
| Erzwingen | Vor der normalen Antwort muss eine erfolgreiche Websuche stattfinden |
| Verfügbar | Der Agent kann bei Bedarf suchen |
| Deaktiviert | Das Suchwerkzeug wird nicht angeboten |

Die Auswahl des tatsächlichen Suchproviders erfolgt durch MatoLoop und kann vom Modell nicht überschrieben werden.

## Suchprovider

Ohne weitere Einrichtung verwendet MatoLoop eine eingebaute Suche ohne API-Schlüssel. Zusätzliche SearXNG-Instanzen werden ausschließlich global unter **Administration > Integrationen** verwaltet. Je einen Anbieter für Websuche und Webabrufe wählst du unter **Administration > Tools > Einstellungen**. Persönliche Anbieterwahl wird nicht unterstützt.

Bei Fehlern gibt es keinen automatischen Anbieterwechsel.

Für Jina und SearXNG steuerst du Verfügbarkeit und Berechtigungen über die normalen Tools **Webseite abrufen** und **Im Web suchen**. Die Integrationskonfiguration verweist darauf; die allgemeine Profilvorgabe für Integrationen und deren Tool-Ladeverhalten schalten diese Anbieter nicht zusätzlich aus. Die Verbindung selbst muss weiterhin aktiviert sein.

## Webseiten abrufen

Unter **Einstellungen > Grundeinstellungen > Bevorzugte Sprache** hinterlegst du
eine persönliche Sprachvorgabe. Sie gilt für Chatantworten einschließlich Überschriften
und Git-Rückmeldungen, für Transkriptionen und die Browser-Sprache von Jina Reader.
Eine ausdrücklich angegebene Sprache hat Vorrang. Bei **Automatisch** verwenden
Chatantworten die bevorzugte Sprache aus deinem persönlichen Profil, sofern für das
Modell verfügbar, sonst die Sprache deiner Anfrage; Dienste behalten ihre Erkennung
beziehungsweise Vorgabe bei. Die Einstellung übersetzt keine abgerufenen Inhalte und
ändert nicht die Oberflächensprache. Neue Agentenläufe übernehmen die Vorgabe beim Start.

`webfetch` ruft eine konkrete HTTP- oder HTTPS-Adresse ab und ist unabhängig von der Websuche konfiguriert. Das ausgewählte Agentenprofil legt dafür getrennt für private und öffentliche Modelle eine Richtlinie fest:

- **Deaktiviert**
- **Nachfragen**
- **Automatisch**

Der eingebaute Anbieter ruft Webseiten ohne JavaScript ab und prüft jede Weiterleitung. Jina Reader kann JavaScript verarbeiten und übernimmt den Zielabruf selbst. Große Ergebnisse bleiben vollständig gespeichert; fehlende Abschnitte können über den Ergebnisverweis ohne erneuten Abruf gelesen werden. Für nötige Seiteninteraktion verwendest du die [Browser-Integration](browser.md), nicht einen automatischen Anbieterwechsel.

Für Bildreferenzen kann der Agent Bildsuche verwenden und muss die konkrete Bildadresse vor Verwendung abrufen. Ein leerer Webfetch-Inhalt gilt als kontrollierter Fehler; ein identischer Wiederholungsversuch ist erlaubt, ohne automatische Browserübernahme. Originaldokumente können ausdrücklich als Datei in der Ablage heruntergeladen werden.

Bei ausgewähltem Jina Reader kann der Agent gezielt den Abrufmodus (`auto`,
`browser`, `curl`), Overlay-Entfernung, Cache-Umgehung, Inhalts-/Warte-/Ausschlussselektoren
sowie Link- und Bildübersichten wählen. Browser-Modus ermöglicht JavaScript,
erteilt aber keine Zustimmung und löst keine Login- oder Bot-Sperren automatisch.
Link- und Bildübersichten sind keine bereits abgerufenen Bilder oder geprüften Quellen.

## Webabrufe konfigurieren

Als Administrator wählst du unter **Administration > Tools > Einstellungen > Webabrufe und Downloads** **Eingebaut** oder eine globale Jina-Verbindung. Jina richtest du zuvor unter **Administration > Integrationen** als **Webabruf** ein: Reader-Basis-URL, beispielsweise `https://r.jina.ai`, und bei Bedarf ein API-Schlüssel. Persönliche Anbieterwahl gibt es nicht. Die Tool-Verfügbarkeit steuerst du unter **Tool-Einstellungen**.

Gemeinsame Einstellungen sind **Nur öffentliche Zieladressen zulassen**, **User-Agent** und **Maximale Antwortgröße**. Vorgaben sind öffentliche Zieladressen, `MatoLoop/1.0` und 64 MiB. Direkte Abrufe haben standardmäßig 30 Sekunden Gesamtzeit, Jina 90 Sekunden einschließlich 10 Sekunden Puffer; ausdrücklich angegebene Tool-Zeitgrenzen gehen vor.

!!! warning "Netzwerkgrenzen unterscheiden sich"
    Abschalten der öffentlichen Adressbeschränkung erlaubt direkte Abrufe auch zu privaten, vom Backend erreichbaren Zielen. Bei direkten Anfragen wird jede Weiterleitung neu geprüft; eine Freigabe überstimmt die aktive Beschränkung nicht. Jina erhält dagegen die vollständige URL und führt Abruf, Weiterleitungen und JavaScript selbst aus: MatoLoops lokale Adressprüfung und DNS-Bindung schützen Jinas Zielanfragen nicht. Vertraue der Reader-Instanz entsprechend.

Originaldownloads, Untertitel, URL-Dateiimports und ausdrücklich angeforderte PDF-Seitenbilder verwenden weiterhin den direkten Abrufweg. Dessen gemeinsame Grenzen gelten beispielsweise auch für BookStack-Anhänge. Shell, Computer-Browser, Suchdienste und andere Integrationen haben eigene Zugriffsregeln. Jinas Bildauswertung liefert Reader-Inhalt, nicht automatisch Originalbilder als Anhänge.

### Cookies für Jina hinterlegen

In der Jina-Verbindung kannst du Standardzeitlimit, Overlay-Entfernung und Cookies mit Zielhost, Name und Wert speichern. Cookies werden verschlüsselt und nur für den exakten HTTPS-Zielhost übergeben. Neue Einträge ersetzen nach Bestätigung alle gespeicherten Cookies; ohne neue Angaben bleiben sie erhalten. API-Schlüssel und Cookies lassen sich getrennt entfernen.

Passende Cookies erzwingen einen frischen Browserabruf; `curl` ist dafür nicht zulässig. Die Reader-Instanz erhält die Cookies und muss deren Browsergrenzen einhalten. **Alle berechtigten Nutzer der globalen Verbindung können sie verwenden: Speichere hier keine persönlichen Login-Sitzungen.** MatoLoop beschafft keine Cookies und bestätigt keine Zustimmungsdialoge automatisch.

## Veröffentlichte Untertitel

`fetch_transcript` ruft vorhandene veröffentlichte Untertitel unterstützter öffentlicher YouTube-Videos ab und verwendet dieselbe Webzugriffsrichtlinie. Das Werkzeug lädt kein Video herunter, übersetzt keine Untertitel und startet keine automatische Transkription. Ist eine gewünschte Sprache nicht vorhanden, wird die ursprüngliche verfügbare Untertitelsprache gekennzeichnet zurückgegeben. Bildinhalt, Kameraführung und sichtbare Bedienoberflächen können damit nicht ausgewertet werden.

## Quellen in Antworten

Aktuelle Recherchequellen erscheinen als hervorgehobene Links in der Antwort. Prüfe wichtige Aussagen weiterhin anhand der verlinkten Originalquelle; Suchergebnisse und Webseiten können unvollständig oder fehlerhaft sein.
