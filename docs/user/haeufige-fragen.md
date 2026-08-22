# Häufige Fragen

## Warum erscheint meine neue Session noch nicht in der Navigation?

Eine Session wird erst mit der ersten gesendeten Nachricht dauerhaft angelegt. Ein leerer Entwurf bleibt lokal im Browser.

## Warum kann der Agent nicht auf meinen Arbeitsordner zugreifen?

Prüfen Sie, ob ein verbundener Computer, einer seiner Arbeitsbereiche und ein aktueller Ordner gewählt sind und die benötigte Tool-Richtlinie die Aktion erlaubt. Backend-Dateien bleiben auch ohne Computer verfügbar.

## Warum wird ein Modell nicht im Chat angeboten?

Provider, globaler LLM-Betrieb und Modell müssen aktiviert sein. Das Modell muss außerdem verfügbar sein und die benötigte Chatfähigkeit besitzen.

## Warum kann ich kein Diktat starten?

Audiotranskription muss als Tool aktiviert und ein verfügbares Standardmodell für Transkription gewählt sein. Zusätzlich benötigt der Browser Zugriff auf das Mikrofon.

## Warum fragt MatoLoop trotz automatischer Tools nach einer Freigabe?

Einzelne Ressourcen, private Netzwerkziele oder sicherheitsrelevante Aktionen können strengeren Hostregeln unterliegen. Modell- und Session-Einstellungen können die globale Richtlinie ebenfalls einschränken.

## Macht „Rückgängig“ Dateiänderungen rückgängig?

Nein. Die Funktion entfernt Chatverlauf und lädt den ursprünglichen Nachrichtenentwurf neu. Bereits ausgeführte Tools, Dateiänderungen, Webzugriffe und Memory-Änderungen bleiben bestehen.

## Werden externe Bilder automatisch geladen?

Nein. Bilder aus MatoLoop-Anhängen werden lokal geladen. Für externe Bildursprünge in Markdown-Antworten ist eine Zustimmung pro Ursprung und Session erforderlich.

## Wo liegen meine Dateien?

Arbeitsdateien liegen in den Wurzeln des jeweiligen Computers. Beim Compose-Computer ist dies standardmäßig das Host-Verzeichnis `./data/home`, das nur dort als `/workspace` eingebunden wird, oder der über `MATO_EXECUTION_AGENT_WORKSPACE` gesetzte Pfad. Das Backend greift nicht auf diesen Host-Pfad zu. Backend-Dateien einer Session ohne Projekt liegen unter `/data/sessions/<session-id>/files`. Sessions eines Projekts teilen `/data/projects/<project-id>/files`; ihre privaten Nachrichtenanhänge bleiben trotzdem je Session getrennt.

## Ist die Dokumentation nur nach Anmeldung erreichbar?

Nein. Die Dokumentation enthält keine persönlichen Daten und ist unter `/docs/` öffentlich erreichbar. Der Link befindet sich aus Platzgründen im Benutzermenü.
