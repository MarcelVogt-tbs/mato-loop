# Anhänge, Diktat und Medien

## Nachrichtenanhänge

Sie können Dateien über die Dateiauswahl oder die Zwischenablage an einen Nachrichtenentwurf anhängen. Pro Nachricht sind bis zu acht Anhänge mit jeweils höchstens 64 MiB möglich. Browserlokale Anhangsentwürfe sind über alle Sessions zusammen auf 256 MiB begrenzt.

MatoLoop zeigt unterstützte Bilder, Audio, Text und Dokumente passend an. Office-Dokumente, PDFs, Audio und weitere Formate können serverseitig in lesbare Ableitungen umgewandelt werden. Die Originaldatei bleibt als Download verfügbar.

## Bildverarbeitung

Bildfähige Modelle können geeignete aktuelle oder ausdrücklich referenzierte Bilder erhalten. Große Bilder werden für die Modellanfrage normalisiert. Das gespeicherte Original bleibt unverändert. Bilder, Audio und andere Binärdaten werden vor der Übertragung nicht pseudonymisiert.

Bei einer Bildbearbeitung speichert MatoLoop sowohl die unmittelbaren Quellen als auch eine kontrollierte Ursprungslinie. Wenn Sie eine Bearbeitung derselben Bildlinie für die Session freigeben, können daraus erzeugte Bilder erneut bearbeitet werden, ohne bei jedem Zwischenergebnis nachzufragen. Andere Bilder, Sessions, Modelle oder Vertrauenslevel bleiben von dieser Freigabe getrennt.

Externe Markdown-Bilder werden nicht automatisch geladen. Sie entscheiden je Ursprung und Session, ob der Browser die externe Ressource abrufen darf.

## QR-Codes

Der Agent kann QR-Codes lokal und ohne Bildgenerator als scannbare PNG-Datei erzeugen. Zum Lesen hängen Sie ein Bild mit einem QR-Code an und formulieren einen kurzen Auftrag. Die Verarbeitung findet lokal statt; QR-Bilder und codierte Inhalte werden dafür nicht an einen Medienprovider gesendet.

## Diktat

Mit der Mikrofonschaltfläche nehmen Sie Sprache direkt im Browser auf. Die Aufnahme endet spätestens nach fünf Minuten und wird als direkte Benutzeraktion an das konfigurierte Standardmodell für Transkription übertragen. Dafür erscheint auch bei der Tool-Richtlinie **Nachfragen** keine separate Freigabekarte. Der erkannte Text wird in den aktuellen Entwurf eingefügt.

Bei einem Fehler können Sie die Aufnahme erneut verarbeiten, als Anhang übernehmen, herunterladen oder verwerfen.

Voraussetzungen sind:

- aktivierte Audiotranskription unter **Einstellungen > Tools**
- ein verfügbares Standardmodell für Transkription
- Browserfreigabe für das Mikrofon

## Mediengenerierung

Je nach Provider, Modellfähigkeiten und Tool-Richtlinien kann der Agent Bilder, Sprache oder Audio erzeugen und Bilder bearbeiten. Ergebnisse erscheinen als Tool-Anhänge und werden im Session-Speicher unter `generated` abgelegt.
