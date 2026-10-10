from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

from mato_loop_backend.message_content import canonical_assistant_content

_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])


def _token_text(token: Token) -> str:
    if token.children is not None:
        return "".join(_token_text(child) for child in token.children)
    if token.type in {"softbreak", "hardbreak"}:
        return " "
    if token.type in {"text", "code_inline", "code_block", "fence"}:
        return token.content
    return ""


def notification_body_preview(body: str | None, *, limit: int = 500) -> str | None:
    if not body:
        return None
    text = " ".join(
        " ".join(_token_text(token) for token in _MARKDOWN.parse(canonical_assistant_content(body))).split()
    )
    if len(text) > limit:
        prefix = text[:limit - 1]
        if not text[limit - 1].isspace():
            prefix = prefix.rsplit(" ", 1)[0]
        text = prefix.rstrip() + "…"
    return text or None
