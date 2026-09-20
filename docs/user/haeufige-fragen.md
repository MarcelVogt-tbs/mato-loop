# Häufige Fragen

## Warum erscheint meine neue Session noch nicht in der Navigation?

Eine Session wird erst mit der ersten gesendeten Nachricht dauerhaft angelegt. Ein leerer Entwurf bleibt lokal im Browser.

## Warum kann der Agent nicht auf meinen Arbeitsordner zugreifen?

Prüfen Sie, ob ein verbundener Computer und ein absoluter aktueller Ordner gewählt sind und die Dateizugriffsrichtlinie für Home- oder externe Pfade die Modellaktion erlaubt. Backend-Dateien bleiben auch ohne Computer verfügbar.

## Warum wird ein Modell nicht im Chat angeboten?

Provider und Modell müssen aktiviert sein. Das Modell muss außerdem verfügbar sein und die benötigte Chatfähigkeit besitzen.

## Warum kann ich keine Sprachnachricht aufnehmen oder transkribieren?

Für die Aufnahme benötigt der Browser Mikrofonzugriff. Nur für die automatische Transkription nach dem Senden müssen Audiotranskription technisch aktiviert und im gewählten Agentenprofil ein verfügbares Transkriptionsmodell ausgewählt sein. Ohne Transkription können Sie den Originalanhang weiterhin aufnehmen, senden und abspielen.

## Warum fragt MatoLoop trotz automatischer Tools nach einer Freigabe?

Einzelne Ressourcen, private Netzwerkziele oder sicherheitsrelevante Aktionen können strengeren Hostregeln unterliegen. Profil- und Session-Einstellungen können eine Aktion ebenfalls einschränken. Technische Verfügbarkeit, Vertrauens-, Pfad-, Computer- und Delegationsgrenzen bleiben auch bei **Automatisch** wirksam.

## Macht „Rückgängig“ Dateiänderungen rückgängig?

Nein. Die Funktion entfernt Chatverlauf und lädt den ursprünglichen Nachrichtenentwurf neu. Bereits ausgeführte Tools, Dateiänderungen, Webzugriffe und Memory-Änderungen bleiben bestehen.

## Werden externe Bilder automatisch geladen?

Nein. Bilder aus MatoLoop-Anhängen werden lokal geladen. Für externe Bildursprünge in Markdown-Antworten ist eine Zustimmung pro Ursprung und Session erforderlich.

## Wo liegen meine Dateien?

Computer-Dateien liegen unter gewöhnlichen absoluten Pfaden des jeweiligen MatoLoop Agent. Beim Compose-Computer wird `MATOLOOP_AGENT_HOME` als `/workspace` eingebunden; Zustand und `/data/config.json` liegen unter `MATOLOOP_AGENT_DATA`. Die Produktionsvorlage verwendet dafür `/opt/matoloop-agent/home` und `/opt/matoloop-agent/data`. Das Backend greift nicht direkt auf diese Host-Pfade zu. Backend-Dateien einer eigenständigen Session liegen unter `/data/sessions/<session-id>/files`; Projekt-Sessions liegen getrennt unter `/data/projects/<project-id>/sessions/<session-id>/files`.

## Ist die Dokumentation nur nach Anmeldung erreichbar?

Nein. Die Dokumentation enthält keine persönlichen Daten und ist unter `/docs/` öffentlich erreichbar. Der Link befindet sich aus Platzgründen im Benutzermenü.
