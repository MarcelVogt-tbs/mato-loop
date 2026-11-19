from pydantic import JsonValue

PAIRING_PATH = "/settings/channels?pair=teams"
PAIRING_INSTRUCTIONS = (
    "Öffne in MatoLoop Einstellungen → Kanäle → Kanal hinzufügen → Microsoft Teams. "
    "Kopiere den angezeigten /pair-Befehl, sende ihn hier und bestätige anschließend "
    "dein Teams-Konto in MatoLoop."
)


def pairing_card(matoloop_origin: str | None, *, welcome: bool = False) -> dict[str, JsonValue]:
    instructions = PAIRING_INSTRUCTIONS
    if welcome:
        instructions += " Wenn dein Teams-Konto bereits verbunden ist, musst du nichts tun."
    card: dict[str, JsonValue] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.2",
        "fallbackText": instructions,
        "body": [
            {"type": "TextBlock", "text": "Teams-Konto mit MatoLoop verbinden", "weight": "Bolder", "wrap": True},
            {"type": "TextBlock", "text": instructions, "wrap": True},
        ],
    }
    if matoloop_origin:
        card["actions"] = [
            {"type": "Action.OpenUrl", "title": "Kopplung starten", "url": matoloop_origin + PAIRING_PATH}
        ]
    return card
