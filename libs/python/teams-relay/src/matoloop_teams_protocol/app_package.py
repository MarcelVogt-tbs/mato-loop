from __future__ import annotations

import json
import struct
import zlib
from io import BytesIO
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile

DEFAULT_PACKAGE_VERSION = "1.1.0"
PACKAGE_VERSION_PATTERN = r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"


def icon(size: int, *, outline: bool) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    pixels = bytearray()
    for y in range(size):
        pixels.append(0)
        for x in range(size):
            u, v = x / size, y / size
            mark = (
                0.2 <= u <= 0.8
                and 0.2 <= v <= 0.8
                and (
                    u <= 0.3
                    or u >= 0.7
                    or (0.3 < u < 0.5 and abs(v - u) < 0.07)
                    or (0.5 <= u < 0.7 and abs(v - (1 - u)) < 0.07)
                )
            )
            pixels.extend(
                (255, 255, 255, 255) if mark else (0, 0, 0, 0) if outline else (56, 88, 160, 255)
            )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(pixels)))
        + chunk(b"IEND", b"")
    )


def build_app_package(
    app_id: UUID,
    *,
    website: str,
    privacy_url: str,
    terms_url: str,
    version: str = DEFAULT_PACKAGE_VERSION,
    publisher: str = "MatoLoop",
    name: str = "MatoLoop",
    icons: tuple[bytes, bytes] | None = None,
) -> bytes:
    short_name = name[:30].rstrip()
    manifest = {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.22/MicrosoftTeams.schema.json",
        "manifestVersion": "1.22",
        "version": version,
        "id": str(app_id),
        "developer": {
            "name": publisher,
            "websiteUrl": website,
            "privacyUrl": privacy_url,
            "termsOfUseUrl": terms_url,
        },
        "name": {"short": short_name, **({"full": name} if short_name != name else {})},
        "description": {
            "short": f"Dein persönlicher Chat mit {short_name}.",
            "full": "Verbinde deinen persönlichen Teams-Chat mit deiner selbst gehosteten MatoLoop-Instanz.",
        },
        "icons": {"outline": "outline.png", "color": "color.png"},
        "accentColor": "#3858A0",
        "defaultInstallScope": "personal",
        "bots": [
            {
                "botId": str(app_id),
                "scopes": ["personal"],
                "supportsFiles": True,
                "isNotificationOnly": False,
                "commandLists": [
                    {
                        "scopes": ["personal"],
                        "commands": [
                            {"title": "/sessions", "description": "Chat auswählen"},
                            {"title": "/new", "description": "Neuen Chat starten"},
                            {"title": "/current", "description": "Aktuellen Chat anzeigen"},
                            {"title": "/help", "description": "Alle Befehle anzeigen"},
                        ],
                    }
                ],
            }
        ],
        "validDomains": [],
    }
    outline, color = icons if icons is not None else (icon(32, outline=True), icon(192, outline=False))
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("outline.png", outline)
        archive.writestr("color.png", color)
    return output.getvalue()
