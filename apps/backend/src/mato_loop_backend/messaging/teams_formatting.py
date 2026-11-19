from __future__ import annotations

import re

from markdown_it import MarkdownIt

from mato_loop_backend.messaging.formatting import (
    RenderedChannelText,
    split_plain_text,
)


def render_teams_text(text: str, partial: bool = False) -> tuple[RenderedChannelText, ...]:
    lines = text.splitlines(keepends=True)
    parts = []
    cursor = 0
    for token in MarkdownIt().parse(text):
        if token.type != "fence" or token.map is None:
            continue
        start, end = token.map
        parts.extend(
            RenderedChannelText(part.text, "markdown")
            for part in split_plain_text("".join(lines[cursor:start]), 4_000)
        )
        language = token.info.strip().split(maxsplit=1)[0] if token.info.strip() else ""
        if not re.fullmatch(r"[A-Za-z0-9_+-]{0,30}", language):
            language = ""
        for offset in range(0, max(1, len(token.content)), 4_000):
            content = token.content[offset : offset + 4_000]
            parts.append(RenderedChannelText(f"```{language}\n{content}\n```", "markdown"))
        cursor = end
    parts.extend(
        RenderedChannelText(part.text, "markdown")
        for part in split_plain_text("".join(lines[cursor:]), 4_000)
    )
    return tuple(parts)
