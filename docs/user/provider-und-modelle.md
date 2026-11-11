# Provider und Modelle

## Provider verwalten

**Administration > Provider** öffnet zunächst **Einstellungen** mit dem Schalter für persönliche Provider. Unter **Verwaltung** legen Administratoren gemeinsame Verbindungen an und verwalten deren Modelle. **Einstellungen > Provider** verwaltet ausschließlich deine eigenen Provider, auch für Administratoren. Deaktivierte persönliche Konfiguration bleibt gespeichert; gemeinsame Modelle bleiben verfügbar.

### ChatGPT Codex

MatoLoop zeigt einen einmaligen Gerätecode und die ChatGPT-Anmeldeseite. Bestätige ihn mit einem geeigneten Konto. OAuth-Zugangsdaten werden verschlüsselt gespeichert; ein lokaler Callback-Port ist nicht nötig. Danach können Modelle synchronisiert werden.

**Fast** und **Ultrafast** erscheinen als eigene Modelle, zum Beispiel **GPT-6.1 Sol Fast**. Fast folgt dem Codex-Katalog; für GPT-6 Astra und GPT-6.1 Sol wird Ultrafast auch angeboten, wenn der Katalog diesen Modus nicht aufführt. Synchronisiere und aktiviere die gewünschten Varianten; die Reasoning-Stufe wählst du weiterhin unabhängig davon. Bestehende Modelle werden nicht umgestellt. Ob die Ausführung erlaubt ist und wie viel Kontingent oder Guthaben sie verbraucht, hängt von deinem ChatGPT-Tarif und den Workspace-Freigaben ab.

### OpenAI-kompatibel

Verwende Basis-URL, optionalen API-Schlüssel und entweder Chat Completions oder Responses. Modelle werden möglichst über `/models` erkannt; bei fehlender Erkennung ist eine manuelle Modell-ID möglich.

### Kimi Code (Abonnement)

Wähle **Kimi Code (Abonnement)** und trage einen API-Key aus der [Kimi-Code-Konsole](https://www.kimi.com/code/console) ein. Die Vorlage setzt `https://api.kimi.ai/coding/v1` und **Responses API** voraus; für China kann `https://api.kimi.com/coding/v1` verwendet werden. Gespeichert wird eine normale OpenAI-kompatible Verbindung mit verschlüsseltem Schlüssel. Moonshot-API-Keys und separat abgerechnetes API-Guthaben sind damit nicht austauschbar.

Synchronisiere und aktiviere die verfügbaren Modelle. Falls die Erkennung nicht angeboten wird, hinterlege die Modell-ID manuell, beispielsweise `kimi-for-coding` oder `k3.1-pro`. Prüfe das Kontextfenster gegen deinen Tarif; MatoLoop unterstellt keinen pauschalen 1-Mio.-Token-Zugang. Die unterstützten einstellbaren Modelle bieten **Low**, **High** und **Max** an. Thinking bleibt aktiviert, damit Kimi Anfragen nicht ablehnt oder auf ein anderes Modell umleitet; `kimi-for-coding-highspeed` bietet keine einstellbaren Reasoning-Stufen.

Das Abo ist laut [Kimi-Nutzungsrichtlinien](https://www.kimi.com/code/docs/en/kimi-code/community-guidelines.html) für persönliche interaktive Nutzung vorgesehen, nicht für unbeaufsichtigte Automatisierung, Batch-Jobs oder geteilten API-Zugang. Verwende es daher nicht für Hintergrundfunktionen wie Dreaming oder geplante Aufgaben. Kontingente und Ratenlimits gelten auch in MatoLoop; eine Freigabe für deinen Client und Tarif muss bei Kimi bestehen. MatoLoop verwendet seine echte Clientkennung, keine nachgeahmte Kimi-CLI-Identität.

### OpenAI-kompatible Bildserver

Verwende die `/v1`-Basis-URL des Bildservers beim allgemeinen OpenAI-kompatiblen Provider. Aktiviere am Modell nur die tatsächlich unterstützten Fähigkeiten **Bildgenerierung** und **Bildbearbeitung** und wähle es unter **Standardmodelle > Bilder**. Die Fähigkeiten werden nicht zwingend automatisch erkannt und können manuell gesetzt werden.

MatoLoop verwendet `/images/generations` (JSON) und `/images/edits` (Multipart mit geordneten `image[]`-Dateien) und erwartet Base64-Bilddaten in `data[].b64_json`. Reine URL-Antworten werden nicht unterstützt. Eine Chat-Anbindung allein genügt nicht.

Bei Qwen-Image-Turbo über Gufo lässt du die Schrittzahl weg oder verwendest acht Schritte. Sende keine vom Server nicht unterstützten Zusatzparameter. Modellladen und gegenseitiges Entladen verwaltet der Modellserver beziehungsweise ein vorgeschalteter Dienst wie llama-swap, nicht MatoLoop. Die Standardwartezeit beträgt 300 Sekunden und muss auch einen möglichen Modellwechsel abdecken. Prüfe Modelllizenz und Vertrauenslevel; Quellbilder werden nicht textlich pseudonymisiert.

### Lemonade

MatoLoop erkennt installierte Chat- und Medienmodelle eines Lemonade-Servers. Laden, Entladen und Speicherbelegung verwaltest du in Lemonade; MatoLoop übernimmt dessen Modellresidenz nicht. Nicht jede Modellart ist ein Chatmodell.

### Azure Speech

Verwende einen HTTPS-Ressourcen- oder regionalen Endpunkt und den API-Schlüssel. Synchronisierung prüft verfügbare Sprachen und bietet `azure-speech-fast-transcription` an. Aktiviere es und wähle es als **Transkription** unter Standardmodelle. Azure Speech verarbeitet aufgezeichnetes Audio, weder Chat noch Live-Transkription oder Sprachgenerierung. Das Originalaudio wird übertragen und kann nicht textlich pseudonymisiert werden. Dienstregion, Tarif, Quoten und Sprachen müssen bei Azure passend verfügbar sein.

## Modelle synchronisieren

Neu erkannte Modelle beginnen normalerweise deaktiviert; bestimmte hostseitig ergänzte native Medienmodelle können aktiv sein. Aktiviere und teste benötigte Modelle. Synchronisierung erhält interne IDs und eigene Laufzeiteinstellungen, kennzeichnet verschwundene Modelle als **Nicht verfügbar** und löscht sie nicht automatisch.

Bei OpenAI-kompatiblen Providern erkennt MatoLoop explizite Fähigkeitslisten und Fähigkeitsobjekte. Azures `chat_completion` wird als **Chat** und `image_to_image` als **Bildbearbeitung** übernommen; `vision` allein bedeutet nicht Bildbearbeitung. Unbekannte Fähigkeiten werden ignoriert. Fehlende oder unvollständige Angaben kannst du am Modell unter **Modellfähigkeiten** korrigieren, auch bei automatisch entdeckten Modellen. Verbindliche Providerangaben, etwa von Azure Speech oder Lemonade, bleiben schreibgeschützt. Automatisch übernommene Fähigkeiten folgen späteren Providerangaben, solange du sie nicht geändert hast. Eigene Änderungen bleiben bei der Synchronisierung erhalten. Ältere Modelle ohne gespeicherten Vergleichswert behalten ihre vorhandenen Fähigkeiten; ergänze fehlende Fähigkeiten dort bei Bedarf einmal selbst. Erkannte Fähigkeiten ersetzen keine Tool-Berechtigungen und erteilen keine Aktionsrechte.

Löschen entfernt passende Standardmodellzuordnungen und explizite sessionweite Verweise. Nicht verfügbare agentenspezifische Sessionwerte werden bei der Auflösung übersprungen. Eine spätere Synchronisierung kann das externe Modell mit einer neuen internen ID wieder anlegen. Ein Fehler bei der Synchronisierung ersetzt nicht den zuletzt gültigen Katalog.

## Laufzeiteinstellungen

Kontextfenster, Auto-/manuelles Ausgabelimit, Streaming, Tool-, Paralleltool- und Bildunterstützung, Memory, Vertrauenslevel und Reasoning werden je Modell konfiguriert. **Auto** lässt ein unbekanntes Ausgabelimit beim Provider; MatoLoop prüft trotzdem seinen lokalen Kontextbudget-Schätzwert. Ein manuelles Limit ist eine Obergrenze, keine reservierte feste Ausgabe.

Reasoning-Stufen erscheinen nur für unterstützte Modelle. Verfügbare Stufen und Standards hängen vom Provider und dessen erkanntem Modellprofil ab; synchronisiere nach Provideränderungen. Die Auswahl eines öffentlichen Modells gibt bereits autorisierte Inhalte an diesen Provider frei. [Datenfreigabe](freigaben-und-datenfreigabe.md) und Memory-Zielgruppen bleiben getrennte Themen.

- **Administration > Provider > Einstellungen > Modelltrennung:** Pro Provider legst du fest, ob seine Modelle als privat oder öffentlich gelten. Bei aktiver Modelltrennung gelten dafür getrennte Tool-Rechte, Erinnerungen und persönliche Profilangaben. Änderungen am Schalter werden sofort gespeichert.
- **Ohne Trennung:** Für alle Modelle gelten dieselben Datenfreigaben. Gespeicherte Einstufungen bleiben erhalten.
- **Kennungen und Secrets pseudonymisieren:** bleibt unabhängig einstellbar. Modelle können den Providerwert übernehmen oder überschreiben.
- **Grundeinstellungen:** enthält weiterhin einen direkten Link zu diesem Bereich. Dort steht die öffentliche Adresse an erster Stelle.

## Standardmodelle und Sessionvorlagen

Gemeinsame Zuordnungen stehen unter **Administration > Standardmodelle > Verwaltung**, erlaubte persönliche Vorgaben unter **Einstellungen > Standardmodelle**. Die administrative Einstellungsseite steuert die persönliche Freigabe. Zwecke sind:

- Agent und Reasoning-Vorgabe.
- Kontextkomprimierung und Titelgenerierung.
- Memory / Dreaming mit eigener Reasoning-Vorgabe.
- Bild-, Video- und Audioanalyse.
- Bild-, Sprach- und Audiogenerierung sowie Transkription.

Das Dropdown **Agentenprofil** zeigt unter **Standard** alle bisherigen Modellvorgaben. Wähle ein Profil, um nur dessen **Agentmodell** und **Reasoning-Stufe** anzupassen. Alle Modell- und Reasoning-Dropdowns bieten **Automatische Auswahl** als erste Option. Sie übernimmt die geerbte Vorgabe und ist ohne eigenen Wert vorausgewählt. Wähle diese Option erneut, um einen eigenen Wert zurückzusetzen. Der Hinweis **Effektiv** zeigt die bekannte Vorgabe; **Auswahl zur Laufzeit** bedeutet, dass der bestehende Fallback erst bei der Verwendung entscheidet. Änderungen werden mit **Speichern** übernommen. Hilfs- und Medienmodelle bleiben von Profilvorgaben unberührt. Die weiteren Einstellungen eines Agentenprofils verlinken direkt auf dessen Modellauswahl.

Die **Reasoning-Stufe** neben **Memory / Dreaming** gilt für Memory-Extraktion, den Abgleich mit vorhandenen Erinnerungen und Session-Zusammenfassungen. Du kannst nur unterstützte Stufen auswählen; ein Modellwechsel setzt eine unpassende eigene Stufe zurück. Ohne unterstützte geerbte Vorgabe verwendet **Automatische Auswahl** wie beim Agentmodell den Modell-/Providerstandard, nicht eine fest vorgegebene Stufe. Höhere Stufen können Laufzeit und Tokenverbrauch erhöhen; bessere Ergebnisse sind nicht garantiert.

**Bilder** legt das gemeinsame Standardmodell für Bilderzeugung und Bildbearbeitung fest. Unter **Abweichendes Modell für Bildbearbeitung** kannst du ein ausführbares, bildbearbeitungsfähiges Modell wählen. **Automatische Auswahl** übernimmt zunächst eine globale Bearbeitungsvorgabe; ohne diese verwendet die Bildbearbeitung das gemeinsame Bildmodell, sofern es Bildbearbeitung unterstützt. Eine persönliche Vorgabe für **Bilder** ersetzt keine geerbte globale Bearbeitungsvorgabe. Die bestehenden aktiven Modell-Fallbacks bleiben erhalten.

**Sessionvorlagen** wählen verfügbare Agenten, Startagent, Memory, Nachrichtengröße und optionale Chatmodell-/Reasoning-Werte für die Session oder einzelne Agenten. **Allgemein** bietet Allgemein/Sysadmin; **Coding** bietet Plan/Build und kleine Nachrichten. Beide beginnen mit vollem Memory. Eine Session kopiert die Vorlage einmal; spätere Vorlagenänderungen wirken nicht auf sie zurück.

Ein agentenspezifischer Sessionwert gewinnt vor dem sessionweiten Wert, dann folgen persönliche und globale Profilvorgaben und schließlich persönliche und globale Standardwerte. Modell und Reasoning werden unabhängig vererbt. Nicht verfügbare Modellwerte werden bei der Sessionauflösung übersprungen. Ohne Agentmodell-Vorgabe dient das erste verfügbare toolfähige Chatmodell in der effektiven Liste. Hilfs-/Medienmodelle verwenden Standardmodelle und ihre bestehenden aktiven Modell-Fallbacks. Plan und Build verlangen außerdem einen verbundenen Computer mit zugänglichem Arbeitsordner.
