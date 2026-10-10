# Personalisierung

Unter **Einstellungen > Personalisierung** enthält **Über mich** Ihre gepflegten Angaben; **Personas** verwaltet Assistentenname, wiederverwendbare Personas und Agentenzuordnungen. Angaben zu Ihrer Person werden unter [Memory und Dreaming](memory-und-dreaming.md#uber-mich) erläutert.

## Personas und Profilzuordnungen

Administratorinnen und Administratoren verwalten gemeinsame Personas unter **Administration > Personas > Verwaltung** und wählen eine **Globale Standard-Persona**. Name und Anweisungen beschreiben Ton und Arbeitsstil. Erlaubte persönliche Personas können Sie selbst anlegen, bearbeiten oder löschen.

Mit **Override hinzufügen** wählen Sie einen zugänglichen Agenten und eine globale oder eigene Persona. Die persönliche Zuordnung gilt nur für Ihr Konto; administrative Zuordnungen gelten für globale Profile. Die Reihenfolge ist persönliche Zuordnung, administrative Zuordnung, globale Standard-Persona. Es gibt keinen gesonderten persönlichen Standard und keine Persona-Vererbung über Basisprofile.

Zuordnungen referenzieren dieselbe Persona. Eine Textänderung beeinflusst daher alle Agenten mit dieser Persona. Das Löschen einer Zuordnung stellt die Vorgabe wieder her. Vor dem Löschen der globalen Standard-Persona müssen Sie einen anderen Standard wählen. **Weitere Einstellungen** im Agenteneditor führt direkt zu den passenden Persona-, Tool- und Integrationszuordnungen; neue Agenten müssen zuerst gespeichert werden.

## Ein Assistentenname

**Globaler Agentenname** gilt instanzweit. **Mein Agentenname** überschreibt ihn optional für alle Agenten Ihres Kontos; **Globalen Namen verwenden** entfernt die persönliche Vorgabe. Dieser Name ist unabhängig von Persona- und Profilnamen sowie von der Anwendungsmarke unter **Darstellung**.

## Persönliche Anpassung

**Administration > Personas > Einstellungen > Persönliche Personas erlauben** steuert persönliche Personas, Zuordnungen und Assistentennamen gemeinsam. Deaktivierte Werte bleiben gespeichert, sind für gewöhnliche Benutzer aber inaktiv und nur lesbar. Administratoren behalten ihre persönliche Ausnahme.

Auf ausdrückliche Bitte kann der Agent Ihre erlaubte Persona oder Ihren Namen ändern. Eine persönliche Personaänderung wirkt auf alle ihre Zuordnungen. Ist eine globale Persona aktiv, erzeugt das Tool stattdessen eine persönliche für den aktuellen Agenten. Die Tool-Richtlinie steuert die Freigabe; Änderungen wirken ab dem nächsten Lauf.

!!! warning "Keine Secrets in Persona-Texten"
    Persona-Anweisungen werden an das ausgewählte Modell gesendet. Speichern Sie darin keine Passwörter, Tokens oder andere Geheimnisse.
