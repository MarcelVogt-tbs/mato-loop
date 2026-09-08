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

## Voice Messages

Use the microphone button to record a voice message in the browser. Recording requires microphone permission and stops automatically after five minutes. Select **Als Sprachnachricht anhängen** to add the recording to the draft, where you can listen to it or remove it before sending. Multiple recordings can be attached within the normal attachment limits.

Sending persists the original audio as a normal message attachment. The shared runtime then uses the configured transcription model to add recognized text to the message. This is a direct user request, so the **Nachfragen** tool policy does not create a separate approval card. Automatic transcription requires enabled audio transcription and an available default transcription model; recording and sending audio do not. Failed or unavailable transcription leaves the original audio intact.

Sent recordings have an audio player and an original-file download. Only the original audio is stored permanently. Transcription and playback fallback generate a transient WAV from the verified original when needed, without adding a duplicate file to the session. Older WAV companions remain stored to preserve existing file references, but are no longer needed for audio playback or transcription. The model can access the attachment again later, including for another transcription. Taking a message back into the draft preserves the audio; an already included successful transcript is not automatically appended again on resend.

If an attachment limit prevents adding a recording, it remains available for another attempt, download, or explicit discard. Switching sessions preserves completed recordings in their original draft. Unsent recordings remain browser-memory drafts, not server-side session files; queued messages use the persistent browser outbox. Recordings discarded by older transcription-only behavior are not restored retroactively.

## Mediengenerierung

Je nach Provider, Modellfähigkeiten und Tool-Richtlinien kann der Agent Bilder, Sprache oder Audio erzeugen und Bilder bearbeiten. Dauerhafte erzeugte oder bearbeitete Bilder, QR-Codes, Sprach- und Audioausgaben, abgerufene Bilder, gerenderte PDF-Seiten sowie OpenSCAD-Exporte werden als verwaltete Dateien mit gespeicherten Vollversionen abgelegt. Die jeweilige Nachricht verweist auf genau diese Version. Normales Bearbeiten, Verschieben und das Aktivieren einer anderen Version erhalten diesen Verweis; das Löschen der vollständigen verwalteten Datei macht ihn unbrauchbar.

Die Bearbeitung eines einzelnen, bereits erzeugten Bildes ergänzt dessen Versionsverlauf. Ein hochgeladenes oder heruntergeladenes Original sowie mehrere gemeinsam bearbeitete Quellen bleiben unverändert; das Ergebnis wird als neue Datei gespeichert.
