# Debug-Zugang

Administratoren können unter **Einstellungen → Debug-Zugang** einen persönlichen Diagnosezugang aktivieren.

Der Zugang ist schreibgeschützt und fest an das eigene Benutzerkonto gebunden. Er erlaubt das Lesen der eigenen Sessions, Nachrichten, Tool-Ergebnisse, Agent-Läufe, wirksamen Einstellungen und Diagnosedaten. Daten anderer Benutzer, Zugangsdaten, beliebige Datenbankabfragen und Dateizugriffe sind nicht verfügbar.

## MCP-Client verbinden

1. Öffne **Einstellungen → Debug-Zugang**.
2. Aktiviere den Debug-Zugang und kopiere den Token aus der einmaligen Anzeige.
3. Konfiguriere die angezeigte MCP-Adresse als Streamable-HTTP-Endpunkt.
4. Verwende den Token als Bearer-Token.

Der Token wird nur als Hash gespeichert. Nach dem Schließen des Dialogs kann er nicht erneut angezeigt werden. **Token erneuern** ersetzt ihn sofort.

!!! warning
    Der Token erlaubt das Lesen potenziell vertraulicher eigener Chat-, Kontext- und Tool-Inhalte. Speichere ihn nur im Secret-Speicher des verwendeten MCP-Clients.

## Diagnoseaufzeichnung

Solange der Debug-Zugang aktiv ist, zeichnet MatoLoop strukturierte Ereignisse für Runs, Modellanfragen und Tool-Ausführungen des Kontos auf. Dazu gehören der tatsächlich vorbereitete Modellkontext, angebotene Tool-Schemas, Laufzeiten, Tokenverbrauch, Wiederholungen und kontrollierte Fehlerdaten. Zugangsdaten und rohe Provider-Antworten werden nicht aufgezeichnet.

Die Aufzeichnungen sind auf 1 MB pro Ereignis begrenzt und werden nach 14 Tagen automatisch entfernt. Ältere oder gekürzte Daten werden nicht nachträglich rekonstruiert.

## Zugriff beenden

Das Deaktivieren macht den Token sofort ungültig und beendet die weitere Diagnoseaufzeichnung. Bereits aufgezeichnete Daten bleiben bis zum Ablauf der Aufbewahrungsfrist gespeichert und sind nach einer erneuten Aktivierung wieder verfügbar.

Der MCP funktioniert nur bei laufendem MatoLoop-Backend und erreichbarer Datenbank. Deren Totalausfall gehört nicht zum Umfang dieses Zugangs.
