# Häufige Fragen

## Warum erscheint meine neue Session noch nicht in der Navigation?

Eine Session entsteht beim ersten Senden oder beim Öffnen einer sessiongebundenen Control-Center-App. Die Startseite und ein leerer Entwurf allein bleiben lokal. Die feste Sessionansicht und globale Benachrichtigungen erzeugen keine leere Session.

## Warum kann der Agent nicht auf meinen Arbeitsordner zugreifen?

Prüfen Sie, ob ein verbundener Computer und ein absoluter aktueller Ordner gewählt sind und die Dateizugriffsrichtlinie für Home- oder externe Pfade die Modellaktion erlaubt. Backend-Dateien bleiben auch ohne Computer verfügbar.

## Warum wird ein Modell nicht im Chat angeboten?

Provider und Modell müssen aktiviert sein. Das Modell muss außerdem verfügbar sein und die benötigte Chatfähigkeit besitzen.

## Warum kann ich keine Sprachnachricht aufnehmen oder transkribieren?

Der Browser braucht Mikrofonberechtigung. Für automatische Transkription müssen die Funktion verfügbar und ein geeignetes Modell unter **Standardmodelle > Transkription** gewählt sein. Das Agentenprofil enthält kein Transkriptionsmodell. Ohne Transkription bleiben Aufnahme, Versand und Wiedergabe möglich.

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
