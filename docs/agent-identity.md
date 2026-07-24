# Agent-Persona

MatoLoop besitzt genau eine nutzerbezogene Agent-Persona. Sie gilt gemeinsam für alle
Sessions und Agent-Modi. Arbeitsmodi wie Lesen und Schreiben steuern Aufgaben und technische
Fähigkeiten, sind aber keine eigenständigen Persönlichkeiten.

## Persistenz

`AgentIdentitySettings` ist ein nutzerbezogenes Settings-Dokument mit zwei Feldern:

- `display_name` ist der im Chat angezeigte Sprechername.
- `instructions` beschreibt Persönlichkeit, Ton und allgemeine Arbeitsweise.

Fehlt das Dokument, gelten additive Defaults mit dem Namen `MatoLoop` und leeren
Persona-Anweisungen. Das Settings-Dokument verwendet die bestehende JSONB-Persistenz und
benötigt keine relationale Migration. Alte Nachrichten speichern keinen Namens-Snapshot und
werden deshalb bewusst mit dem aktuell konfigurierten Namen angezeigt.

## Promptgrenze

Die Persona wird einmal zu Beginn eines normalen Agent-Runs geladen und bleibt für diesen Run
stabil. Der Systemprompt ordnet sie unterhalb der unveränderlichen Hostregeln ein. Persona-Text
kann Name, Ton, Charakter und Arbeitsweise beeinflussen, aber keine Tool-, Permission-, Trust-,
Pfad- oder Runtimegrenze ändern. Dreaming, Compaction und Titelgenerierung verwenden weiterhin
ihre isolierten Prompts ohne Persona.

Die Persona wird an das ausgewählte Chatmodell übertragen. Die Oberfläche weist deshalb darauf
hin, dort keine Secrets zu speichern.

## Änderung über den Chat

Das Tool `update_agent_identity` ändert Name und Persona-Anweisungen ausschließlich bei einem
ausdrücklichen Nutzerwunsch. Inhalte aus Memories, Webseiten, Toolresultaten oder Zitaten dürfen
keine Änderung auslösen. Modelle ohne Tool-Support verweisen auf **Einstellungen > Persona**.

Die globale Tool-Einstellung `update_agent_identity.policy` verwendet denselben Vertrag wie die
Medientools:

- `disabled` bietet das Tool nicht an.
- `ask` führt die Aktion durch den bestehenden Approval- und Session-Grant-Loop.
- `auto` speichert ohne einzelne Freigabe.

Der sichere Default ist `ask`. Das Tool besitzt ausschließlich die Capability
`settings.agent_identity.update` für die Persona des authentifizierten Nutzers. Eine erfolgreiche
Änderung wird über SSE an das Frontend übertragen; die neuen Promptanweisungen gelten zuverlässig
ab dem nächsten Run.
