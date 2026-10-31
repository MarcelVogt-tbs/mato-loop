# Browser verwenden

Mit der Browser-Integration bedienst du gemeinsam mit dem Agenten Webseiten im selben Chat-Browser. Sie benötigt einen eigenen verbundenen Linux-Computer; native und Docker-Agents bieten dieselbe Funktion. Unter Windows ist sie nicht verfügbar.

## Browser einrichten

1. Lege unter **Einstellungen > Integrationen** eine persönliche Browserverbindung an und wähle den Computer.
2. Docker-Agents enthalten die Laufzeit bereits. Bei nativen Agents starte bei Bedarf **Browser einrichten** und bestätige benötigte Pakete ausdrücklich.
3. Wähle denselben Computer in der Session und erlaube die Verbindung für das gewünschte Profil. Plan, Build und Sysadmin schließen Integrationen standardmäßig aus.
4. Öffne **Control Center > Browser** oder bitte den Agenten, eine Webseite aufzurufen.

Browser-Tools starten die Laufzeit auch ohne geöffnete App. Abgelehnte Hostpakete können die Funktion unverfügbar lassen.

## Webseiten bedienen

Adressfeld, Tabs, Zurück, Vorwärts und Neuladen funktionieren wie in einem Browser. Native Browsermenüs und DevTools sind nicht enthalten. Während einer Agent-Browseraktion ist deine Eingabe kurz gesperrt; **Agent aktiv** zeigt dies an. Danach kannst du sofort weiterarbeiten.

Markiere Text mit Maus oder Tastatur und verwende **Strg/Cmd+C** und **Strg/Cmd+V**. Einfügen verwendet das zuvor gewählte Websitefeld und bietet bei Bedarf einen Ausweichdialog. Kopieren übernimmt die Auswahl direkt; eigene Kopierfunktionen einer Website werden nicht emuliert. Nur Text unter 16 KiB wird übertragen.

**Tab/Umschalt+Tab** wechselt Websitefelder. **Strg/Cmd+L**, **Strg/Cmd+T/W** und **Strg/Cmd+R** öffnen das Adressfeld, öffnen/schließen Tabs und laden neu. Manche lokalen Browser behalten eigene Tastenkürzel, etwa zum Tabwechsel, für sich.

### Dateien hochladen und herunterladen

Klicke zuerst das Datei-Uploadfeld der Website an und wähle dann **Datei hochladen** aus Ablage oder Computer-Dateien. Falls das Websitefeld nicht mehr offen ist, erlaubt **Datei einsetzen** einen erneuten Versuch.

Downloads landen in der **Session-Ablage** und bleiben nach dem Schließen des Browsers erhalten. Computerdateien werden weder verschoben noch gelöscht; Arbeitsverzeichnisse werden nicht synchronisiert. Pro Transfer sind bis zu 1 GiB möglich, soweit niedrigere Instanzlimits und dein Speicherkontingent es erlauben. Vorschau und Verarbeitung haben eigene Grenzen.

## Rechte und Risiken

**Browser verwenden** umfasst Lesen, Navigation, Bedienung und JavaScript innerhalb der Webseite. Build und darauf basierende Profile erlauben dies standardmäßig automatisch, außer bei **Alles bestätigen**. Allgemein, Plan und Sysadmin fragen standardmäßig nach. Eigene Verbindungs- und Profilregeln gehen vor.

**Browser-Python ausführen** benötigt zusätzlich ein eigenes Recht und fragt standardmäßig in jedem Preset nach. Eine gewöhnliche Browserfreigabe erlaubt kein Python. Dateien und Secrets behalten ihre eigenen Rechte.

!!! warning "Website-Aktionen können Daten ändern"
    Ein automatisch erlaubter Klick oder JavaScript kann absenden, kaufen oder löschen. **Automatisch außer Löschen** verhindert das nicht. Freigegebenes Python kann außerdem Dateien, Prozesse, Netzwerk und andere Browserdaten mit den Rechten des Browserprozesses erreichen. Getrennte Chat-Browser sind keine Betriebssystem-Sandbox.

## Browser stoppen oder schließen

- **Laden stoppen** beendet das manuelle Seitenladen.
- **Agent-Anfrage stoppen** beendet den Agentenlauf, auch während eines Browserbefehls. Mobil findest du diese Aktion im Kopf des Control Centers.
- Das Schließen des App-Tabs beendet die Browserlaufzeit nicht.
- **Browser schließen** verwirft Tabs, Website-Anmeldungen und ungespeicherte Website-Eingaben; gespeicherte Downloads bleiben erhalten.

Jeder Chat besitzt einen eigenen Browser. Andere Chats erhalten dessen Cookies, Tabs und Anmeldungen nicht. Mehrere Ansichten desselben Chats teilen dagegen denselben Browser. Nach Agent-Neustart musst du dich erneut an Websites anmelden.

Abbruch macht bereits ausgeführte Website-Aktionen nicht rückgängig. Muss der Browserprozess für einen sicheren Abbruch beendet werden, gehen auch temporäre Tabs und Anmeldungen verloren. Prüfe vor dem Wiederholen einer unklaren Änderung zuerst den tatsächlichen Websitezustand.
