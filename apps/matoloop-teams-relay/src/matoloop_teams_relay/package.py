from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID
from zipfile import ZIP_DEFLATED, ZipFile


def https_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("Use an absolute HTTPS URL without credentials.")
    return value


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a personal-scope MatoLoop Teams app package."
    )
    parser.add_argument("--app-id", type=UUID, required=True)
    parser.add_argument("--website", type=https_url, required=True)
    parser.add_argument("--privacy-url", type=https_url, required=True)
    parser.add_argument("--terms-url", type=https_url, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = {
        "$schema": "https://developer.microsoft.com/en-us/json-schemas/teams/v1.22/MicrosoftTeams.schema.json",
        "manifestVersion": "1.22",
        "version": "1.1.0",
        "id": str(args.app_id),
        "developer": {
            "name": "MatoLoop",
            "websiteUrl": args.website,
            "privacyUrl": args.privacy_url,
            "termsOfUseUrl": args.terms_url,
        },
        "name": {"short": "MatoLoop", "full": "MatoLoop"},
        "description": {
            "short": "Dein persönlicher Chat mit MatoLoop.",
            "full": "Verbinde deinen persönlichen Teams-Chat mit deiner selbst gehosteten MatoLoop-Instanz.",
        },
        "icons": {"outline": "outline.png", "color": "color.png"},
        "accentColor": "#3858A0",
        "defaultInstallScope": "personal",
        "bots": [
            {
                "botId": str(args.app_id),
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
    with ZipFile(args.output, "x", compression=ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("outline.png", icon(32, outline=True))
        archive.writestr("color.png", icon(192, outline=False))
    print(f"Teams app package written to {args.output}")


if __name__ == "__main__":
    main()
