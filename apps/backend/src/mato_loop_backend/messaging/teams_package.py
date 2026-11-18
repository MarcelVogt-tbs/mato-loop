from __future__ import annotations

from base64 import b64decode
from io import BytesIO

from PIL import Image, ImageOps


def teams_brand_icons(logo_png: str) -> tuple[bytes, bytes]:
    with Image.open(BytesIO(b64decode(logo_png, validate=True)), formats=["PNG"]) as source:
        logo = source.convert("RGBA")

    def render(size: int, safe_area: int, *, outline: bool) -> bytes:
        mark = ImageOps.contain(logo, (safe_area, safe_area), Image.Resampling.LANCZOS)
        if outline:
            white = Image.new("RGBA", mark.size, "white")
            white.putalpha(mark.getchannel("A"))
            mark = white
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0) if outline else "#3858A0")
        canvas.alpha_composite(mark, ((size - mark.width) // 2, (size - mark.height) // 2))
        output = BytesIO()
        canvas.save(output, format="PNG")
        return output.getvalue()

    return render(32, 20, outline=True), render(192, 120, outline=False)
