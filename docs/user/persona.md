# Personalisierung

Unter **Einstellungen > Personalisierung** enthält **Über mich** deine gepflegten Angaben; **Personas** verwaltet Assistentenname, wiederverwendbare Personas und Agentenzuordnungen. Angaben zu deiner Person werden unter [Memory und Verarbeitung](memory-und-dreaming.md#uber-mich) erläutert.

## Personas und Profilzuordnungen

Administratorinnen und Administratoren verwalten gemeinsame Personas unter **Administration > Personas > Verwaltung** und wählen eine **Globale Standard-Persona**. Name und Anweisungen beschreiben Ton und Arbeitsstil. Erlaubte persönliche Personas kannst du selbst anlegen, bearbeiten oder löschen.

Im Dialog **Persona erstellen** oder **Persona bearbeiten** aktivierst du **Als meinen Standard verwenden** und wählst **Speichern**. Die Persona wird mit **Mein Standard** gekennzeichnet und gilt für alle Agenten ohne persönliche Zuordnung. Ein anderer Standard ersetzt den bisherigen. Zum Aufheben bearbeite die Standard-Persona, entferne das Häkchen und speichere. **Abbrechen** verwirft die Änderungen an Persona und Standardauswahl.

Unter **Abweichende Persona je Agent > Zuordnung hinzufügen** wählst du einen zugänglichen Agenten und eine globale oder eigene Persona. Die persönliche Zuordnung gilt nur für dein Konto; administrative Zuordnungen gelten für globale Profile. Die Reihenfolge ist **persönliche Zuordnung → persönlicher Standard → globale Zuordnung → globale Standard-Persona**. Ein persönlicher Standard überstimmt damit auch globale Profilzuordnungen. Es gibt keine Persona-Vererbung über Basisprofile.

Zuordnungen und Standardauswahl referenzieren dieselbe Persona. Eine Textänderung beeinflusst daher alle Agenten mit dieser Persona. Das Löschen einer Zuordnung stellt die Vorgabe wieder her. Wird deine persönliche Standard-Persona gelöscht, wird auch deine Standardauswahl aufgehoben. Vor dem Löschen der globalen Standard-Persona musst du einen anderen Standard wählen. **Weitere Einstellungen** im Agenteneditor führt direkt zu den passenden Persona-, Tool- und Integrationszuordnungen; neue Agenten müssen zuerst gespeichert werden.

## Ein Assistentenname

**Globaler Agentenname** gilt instanzweit. **Mein Agentenname** überschreibt ihn optional für alle Agenten deines Kontos; **Globalen Namen verwenden** entfernt die persönliche Vorgabe. Dieser Name ist unabhängig von Persona- und Profilnamen sowie von der Anwendungsmarke unter **Darstellung**.

## Persönliche Anpassung

**Administration > Personas > Einstellungen > Persönliche Personas erlauben** ist standardmäßig aktiviert und steuert persönliche Personas, Standardauswahl, Zuordnungen und Assistentennamen gemeinsam. Deaktivierte Werte bleiben gespeichert, sind für gewöhnliche Benutzer aber inaktiv und nur lesbar. Beim erneuten Aktivieren gelten sie wieder. Administratoren behalten ihre persönliche Ausnahme.

Auf ausdrückliche Bitte kann der Agent über `customize_matoloop` deine erlaubte Persona oder deinen Namen ändern. Mit „Verwende diese Persona als meinen Standard“ oder „Hebe meinen persönlichen Persona-Standard auf“ kannst du auch die Standardauswahl ändern. Eine persönliche Personaänderung wirkt auf alle Agenten, die diese Persona verwenden, und erzeugt keine zusätzliche Profilzuordnung. Ist eine globale Persona aktiv, erzeugt das Tool beim Bearbeiten oder Setzen als Standard eine persönliche Kopie. Diese wird dem aktuellen Agenten zugeordnet, sofern sie nicht als Standard gesetzt wird. Bestehende persönliche Profilzuordnungen haben weiterhin Vorrang. Die Tool-Richtlinie steuert die Freigabe; Änderungen wirken ab dem nächsten Lauf.

!!! warning "Keine Secrets in Persona-Texten"
    Persona-Anweisungen werden an das ausgewählte Modell gesendet. Speichere darin keine Passwörter, Tokens oder andere Geheimnisse.
