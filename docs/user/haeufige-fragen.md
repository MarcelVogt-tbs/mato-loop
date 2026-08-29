# Häufige Fragen

## Warum erscheint meine neue Session noch nicht in der Navigation?

Eine Session wird erst mit der ersten gesendeten Nachricht dauerhaft angelegt. Ein leerer Entwurf bleibt lokal im Browser.

## Warum kann der Agent nicht auf meinen Arbeitsordner zugreifen?

Prüfen Sie, ob ein verbundener Computer und ein absoluter aktueller Ordner gewählt sind und die Dateizugriffsrichtlinie für Home- oder externe Pfade die Modellaktion erlaubt. Backend-Dateien bleiben auch ohne Computer verfügbar.

## Warum wird ein Modell nicht im Chat angeboten?

Provider und Modell müssen aktiviert sein. Das Modell muss außerdem verfügbar sein und die benötigte Chatfähigkeit besitzen.

## Warum kann ich kein Diktat starten?

Audiotranskription muss als Tool aktiviert und ein verfügbares Standardmodell für Transkription gewählt sein. Zusätzlich benötigt der Browser Zugriff auf das Mikrofon.

## Warum fragt MatoLoop trotz automatischer Tools nach einer Freigabe?

Einzelne Ressourcen, private Netzwerkziele oder sicherheitsrelevante Aktionen können strengeren Hostregeln unterliegen. Modell- und Session-Einstellungen können die globale Richtlinie ebenfalls einschränken.

## Macht „Rückgängig“ Dateiänderungen rückgängig?

Nein. Die Funktion entfernt Chatverlauf und lädt den ursprünglichen Nachrichtenentwurf neu. Bereits ausgeführte Tools, Dateiänderungen, Webzugriffe und Memory-Änderungen bleiben bestehen.

## Werden externe Bilder automatisch geladen?

Nein. Bilder aus MatoLoop-Anhängen werden lokal geladen. Für externe Bildursprünge in Markdown-Antworten ist eine Zustimmung pro Ursprung und Session erforderlich.

## Wo liegen meine Dateien?

Computer-Dateien liegen unter gewöhnlichen absoluten Pfaden des jeweiligen MatoLoop Agent. Beim Compose-Computer wird `${MATOLOOP_AGENT_HOME:-./data/home}` als `/workspace` eingebunden; Zustand und `/data/config.json` liegen unter `${MATOLOOP_AGENT_DATA:-./data/matoloop-agent}`. Das Backend greift nicht direkt auf diese Host-Pfade zu. Backend-Dateien einer Session ohne Projekt liegen unter `/data/sessions/<session-id>/files`. Sessions eines Projekts teilen `/data/projects/<project-id>/files`; ihre privaten Nachrichtenanhänge bleiben trotzdem je Session getrennt.

## Ist die Dokumentation nur nach Anmeldung erreichbar?

Nein. Die Dokumentation enthält keine persönlichen Daten und ist unter `/docs/` öffentlich erreichbar. Der Link befindet sich aus Platzgründen im Benutzermenü.
