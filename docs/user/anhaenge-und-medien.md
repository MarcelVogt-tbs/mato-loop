# Anhänge, Diktat und Medien

## Nachrichtenanhänge

Sie können Dateien über die Dateiauswahl oder die Zwischenablage an einen Nachrichtenentwurf anhängen. Anzahl, Einzelgröße und Gesamtgröße werden global von Administratoren unter **Einstellungen > Instanzlimits** festgelegt. Browserlokale Anhangsentwürfe bleiben über alle Sessions zusammen auf 256 MiB begrenzt.

MatoLoop zeigt unterstützte Bilder, Audio, Text und Dokumente passend an. Benutzer-Uploads bleiben private Nachrichtenanhänge. Office-Dokumente, PDFs, Audio und weitere Formate können serverseitig in lesbare Ableitungen umgewandelt werden; diese liegen unter dem jeweiligen Anhang. Die Originaldatei bleibt als Download verfügbar.

## Bildverarbeitung

Bildfähige Modelle können geeignete aktuelle oder ausdrücklich referenzierte Bilder erhalten. Große Bilder werden für die Modellanfrage normalisiert. Das gespeicherte Original bleibt unverändert. Bilder, Audio und andere Binärdaten werden vor der Übertragung nicht pseudonymisiert.

Bei einer Bildbearbeitung speichert MatoLoop sowohl die unmittelbaren Quellen als auch eine kontrollierte Ursprungslinie. Wenn Sie eine Bearbeitung derselben Bildlinie für die Session freigeben, können daraus erzeugte Bilder erneut bearbeitet werden, ohne bei jedem Zwischenergebnis nachzufragen. Andere Bilder, Sessions, Modelle oder Vertrauenslevel bleiben von dieser Freigabe getrennt.

Externe Markdown-Bilder werden nicht automatisch geladen. Sie entscheiden je Ursprung und Session, ob der Browser die externe Ressource abrufen darf.

## QR-Codes

Der Agent kann QR-Codes lokal und ohne Bildgenerator als scannbare PNG-Datei erzeugen. Zum Lesen hängen Sie ein Bild mit einem QR-Code an und formulieren einen kurzen Auftrag. Die Verarbeitung findet lokal statt; QR-Bilder und codierte Inhalte werden dafür nicht an einen Medienprovider gesendet.

## Sprachnachrichten

Mit der Mikrofontaste nehmen Sie im Browser eine Sprachnachricht auf. Dafür benötigt der Browser die Mikrofonberechtigung; die Aufnahme endet automatisch nach fünf Minuten. Mit **Als Sprachnachricht anhängen** fügen Sie die Aufnahme dem Entwurf hinzu. Dort können Sie sie vor dem Senden anhören oder entfernen. Innerhalb der normalen Anhangsgrenzen sind mehrere Aufnahmen möglich.

Beim Senden speichert MatoLoop das Originalaudio als normalen Nachrichtenanhang. Anschließend verwendet die gemeinsame Laufzeit das Transkriptionsmodell des gewählten Agentenprofils und ergänzt den erkannten Text in der Nachricht. Dies ist eine direkte Benutzeranfrage, daher erzeugt das Profilrecht **Nachfragen** keine zusätzliche Freigabekarte. Für die automatische Transkription müssen Audiotranskription technisch aktiviert und im Profil ein verfügbares Transkriptionsmodell gewählt sein; Aufnahme und Versand funktionieren auch ohne Transkription. Eine fehlgeschlagene oder nicht verfügbare Transkription lässt das Originalaudio unverändert.

Gesendete Aufnahmen besitzen einen Audioplayer und einen Download der Originaldatei. Nur das Originalaudio wird dauerhaft gespeichert. Für Transkription und Wiedergabe erzeugt MatoLoop bei Bedarf vorübergehend eine WAV-Datei aus dem geprüften Original, ohne der Session eine doppelte Datei hinzuzufügen. Ältere WAV-Begleitdateien bleiben wegen bestehender Dateiverweise gespeichert, werden für Wiedergabe und Transkription aber nicht mehr benötigt. Das Modell kann später erneut auf den Anhang zugreifen, auch für eine weitere Transkription. Wenn Sie eine Nachricht wieder in den Entwurf übernehmen, bleibt das Audio erhalten; ein bereits enthaltenes erfolgreiches Transkript wird beim erneuten Senden nicht nochmals angehängt.

Verhindert eine Anhangsgrenze das Hinzufügen, bleibt die Aufnahme für einen weiteren Versuch, einen Download oder das ausdrückliche Verwerfen verfügbar. Beim Wechsel der Session bleiben fertige Aufnahmen im ursprünglichen Entwurf erhalten. Ungesendete Aufnahmen sind Entwürfe im Browserspeicher und keine serverseitigen Sessiondateien; eingereihte Nachrichten verwenden den dauerhaften Browser-Postausgang. Durch das frühere reine Transkriptionsverhalten verworfene Aufnahmen werden nicht nachträglich wiederhergestellt.

## Mediengenerierung

Je nach Provider, Modellfähigkeiten und Rechten des gewählten Agentenprofils kann der Agent Bilder, Sprache oder Audio erzeugen und Bilder bearbeiten. Dauerhafte erzeugte oder bearbeitete Bilder, QR-Codes, Sprach- und Audioausgaben, abgerufene Bilder, gerenderte PDF-Seiten sowie OpenSCAD-Exporte werden als verwaltete Dateien mit gespeicherten Vollversionen abgelegt. Die jeweilige Nachricht verweist auf genau diese Version. Normales Bearbeiten, Verschieben und das Aktivieren einer anderen Version erhalten diesen Verweis; das Löschen der vollständigen verwalteten Datei macht ihn unbrauchbar.

Die Bearbeitung eines einzelnen, bereits erzeugten Bildes ergänzt dessen Versionsverlauf. Ein hochgeladenes oder heruntergeladenes Original sowie mehrere gemeinsam bearbeitete Quellen bleiben unverändert; das Ergebnis wird als neue Datei gespeichert.
