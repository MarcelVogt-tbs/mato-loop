# Debug-Zugang

Administratoren können unter **Einstellungen → Debug-Zugang** einen persönlichen Diagnosezugang aktivieren.

Der Zugang ist schreibgeschützt und fest an das eigene Benutzerkonto gebunden. Er erlaubt das Lesen der eigenen Sessions, Nachrichten, Tool-Ergebnisse, Agent-Läufe, wirksamen Einstellungen und Diagnosedaten. Zusätzlich sind instanzweite Backend-Logs lesbar, auch mit Einträgen zu anderen Benutzern. Zugangsdaten, beliebige Datenbankabfragen und Dateizugriffe sind nicht verfügbar.

## MCP-Client verbinden

1. Öffne **Einstellungen → Debug-Zugang**.
2. Aktiviere den Debug-Zugang und kopiere den Token aus der einmaligen Anzeige.
3. Konfiguriere die angezeigte MCP-Adresse als Streamable-HTTP-Endpunkt.
4. Verwende den Token als Bearer-Token.

Der Token wird nur als Hash gespeichert. Nach dem Schließen des Dialogs kann er nicht erneut angezeigt werden. **Token erneuern** ersetzt ihn sofort.

!!! warning
    Der Token erlaubt das Lesen potenziell vertraulicher eigener Chat-, Kontext- und Tool-Inhalte sowie instanzweiter Backend-Logs. Speichere ihn nur im Secret-Speicher des verwendeten MCP-Clients.

## Diagnoseaufzeichnung

Solange der Debug-Zugang aktiv ist, zeichnet MatoLoop strukturierte Ereignisse für Runs, Modellanfragen und Tool-Ausführungen des Kontos auf. Dazu gehören der tatsächlich vorbereitete Modellkontext, angebotene Tool-Schemas, Laufzeiten, Tokenverbrauch, Wiederholungen und kontrollierte Fehlerdaten. Zugangsdaten und rohe Provider-Antworten werden nicht aufgezeichnet.

Die Aufzeichnungen sind auf 1 MB pro Ereignis begrenzt und werden nach 14 Tagen automatisch entfernt. Ältere oder gekürzte Daten werden nicht nachträglich rekonstruiert.

## Backend-Logs lesen

Das MCP-Tool `get_backend_logs` liefert die letzten 100 Backend-Logeinträge. Mit `limit` sind bis zu 500 Einträge abrufbar; `logger_name` filtert einen Logger samt Unterloggern und `level` die Mindestschwere (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). Die Ausgabe ist auf 50.000 Zeichen begrenzt; `truncated` zeigt eine Kürzung durch dieses Limit an. Die Einträge stehen in zeitlicher Reihenfolge.

MatoLoop speichert die Backend-Anwendungslogs unabhängig vom Debug-Zugang unter `<Datenverzeichnis>/logs/backend.log` mit einer rotierenden Sicherung, jeweils maximal 1 MiB. Ältere Einträge werden bei der Rotation ersetzt. HTTP-Zugriffslogs und externe SDK-Logs gehören nicht dazu. MCP-Anmeldefehler enthalten die betroffene Phase, Fehlerklassen und Validierungsfelder ohne Eingabewerte.

## Zugriff beenden

Das Deaktivieren macht den Token sofort ungültig und beendet die weitere Diagnoseaufzeichnung. Bereits aufgezeichnete Daten bleiben bis zum Ablauf der Aufbewahrungsfrist gespeichert und sind nach einer erneuten Aktivierung wieder verfügbar.

Der MCP funktioniert nur bei laufendem MatoLoop-Backend und erreichbarer Datenbank. Deren Totalausfall gehört nicht zum Umfang dieses Zugangs.
