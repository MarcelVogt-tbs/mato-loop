from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from mato_loop_backend.message_content import canonical_assistant_content
from mato_loop_backend.messaging.formatting import (
    RenderedChannelText,
    register_channel_text_renderer,
    split_plain_text,
)

TELEGRAM_HTML = "telegram_html"
TELEGRAM_TEXT_LIMIT = 4_000
TELEGRAM_HTML_BYTE_LIMIT = 30_000
TELEGRAM_DRAFT_SOURCE_LIMIT = 3_800
_MARKDOWN_ENVELOPE = re.compile(
    r"^[ ]{0,3}(?P<fence>`{3,}|~{3,})[ \t]*(?:markdown|md)[ \t]*\r?\n",
    re.IGNORECASE,
)
_LANGUAGE = re.compile(r"[A-Za-z0-9_+-]{1,32}")
_MARKDOWN = MarkdownIt("commonmark", {"html": False}).enable(
    ["table", "strikethrough"]
)


def _safe_url(value: str | None) -> str | None:
    if not value or any(ord(char) < 32 or char.isspace() for char in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            valid = (
                parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
            )
        elif parsed.scheme == "tg":
            valid = parsed.hostname == "user" and parsed.query.startswith("id=")
        elif parsed.scheme in {"mailto", "tel"}:
            valid = bool(parsed.path) and not parsed.netloc
        else:
            valid = False
        if not valid:
            return None
    except ValueError:
        return None
    return value


def _append_text(html: list[str], plain: list[str], value: str) -> None:
    html.append(escape(value))
    plain.append(value)


def _render_inline(tokens: list[Token]) -> tuple[str, str]:
    html: list[str] = []
    plain: list[str] = []
    active: list[tuple[str, str, str]] = []
    tags = {
        "strong": ("<b>", "</b>"),
        "em": ("<i>", "</i>"),
        "s": ("<s>", "</s>"),
    }
    for token in tokens:
        if token.type == "text":
            _append_text(html, plain, token.content)
        elif token.type == "code_inline":
            html.extend(item[2] for item in reversed(active))
            html.append(f"<code>{escape(token.content)}</code>")
            active = [
                item if item[0] != "link" else ("link", "", "")
                for item in active
            ]
            html.extend(item[1] for item in active)
            plain.append(token.content)
        elif token.type.endswith("_open") and token.type.removesuffix("_open") in tags:
            name = token.type.removesuffix("_open")
            opening, closing = tags[name]
            active.append((name, opening, closing))
            html.append(opening)
        elif token.type.endswith("_close") and active:
            name = token.type.removesuffix("_close")
            if active[-1][0] == name:
                html.append(active.pop()[2])
        elif token.type == "link_open":
            href = _safe_url(token.attrGet("href"))
            opening = f'<a href="{escape(href, quote=True)}">' if href else ""
            active.append(("link", opening, "</a>" if href else ""))
            html.append(opening)
        elif token.type == "link_close":
            if active and active[-1][0] == "link":
                html.append(active.pop()[2])
        elif token.type == "image":
            source = _safe_url(token.attrGet("src"))
            label = token.content or source or "Bild"
            if source is not None:
                html.append(f'<a href="{escape(source, quote=True)}">{escape(label)}</a>')
            else:
                html.append(escape(label))
            plain.append(label)
        elif token.type in {"softbreak", "hardbreak"}:
            html.append("\n")
            plain.append("\n")
        elif token.type == "html_inline" and re.fullmatch(
            r"<br\s*/?>", token.content, re.IGNORECASE
        ):
            html.append("\n")
            plain.append("\n")
        elif token.content:
            _append_text(html, plain, token.content)
    return "".join(html), "".join(plain)


def _render_table(tokens: list[Token]) -> tuple[str, str]:
    rows: list[str] = []
    cells: list[str] = []
    for token in tokens:
        if token.type == "inline":
            _, value = _render_inline(token.children or [])
            cells.append(value.strip())
        elif token.type == "tr_close":
            rows.append(" | ".join(cells))
            cells = []
    plain = "\n".join(rows)
    return f"<pre>{escape(plain)}</pre>", plain


def _render_block(tokens: list[Token]) -> tuple[str, str]:
    if tokens and tokens[0].type == "table_open":
        return _render_table(tokens)
    html: list[str] = []
    plain: list[str] = []
    lists: list[list[str | int]] = []
    heading_bold = not any(
        child.type == "code_inline"
        for token in tokens
        for child in (token.children or [])
    )
    quote_safe = sum(token.type == "blockquote_open" for token in tokens) == 1 and not any(
        token.type in {"fence", "code_block"}
        or any(
            child.type in {"code_inline", "link_open", "image"}
            for child in (token.children or [])
        )
        for token in tokens
    )
    for token in tokens:
        if token.type == "inline":
            rendered, value = _render_inline(token.children or [])
            html.append(rendered)
            plain.append(value)
        elif token.type == "heading_open":
            if heading_bold:
                html.append("<b>")
        elif token.type == "heading_close":
            if heading_bold:
                html.append("</b>")
            html.append("\n\n")
            plain.append("\n\n")
        elif token.type == "paragraph_close" and not token.hidden:
            html.append("\n\n")
            plain.append("\n\n")
        elif token.type == "blockquote_open":
            if quote_safe:
                html.append("<blockquote>")
            else:
                _append_text(html, plain, "> ")
        elif token.type == "blockquote_close":
            if quote_safe:
                html.append("</blockquote>")
            html.append("\n\n")
            plain.append("\n\n")
        elif token.type == "bullet_list_open":
            lists.append(["bullet", 0])
        elif token.type == "ordered_list_open":
            start = token.attrGet("start")
            lists.append(["ordered", int(start) - 1 if start else 0])
        elif token.type == "list_item_open":
            kind, counter = lists[-1]
            lists[-1][1] = int(counter) + 1
            prefix = "- " if kind == "bullet" else f"{lists[-1][1]}. "
            value = f"{'  ' * (len(lists) - 1)}{prefix}"
            _append_text(html, plain, value)
        elif token.type == "list_item_close":
            html.append("\n")
            plain.append("\n")
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            lists.pop()
            if not lists:
                html.append("\n")
                plain.append("\n")
        elif token.type in {"fence", "code_block"}:
            language = token.info.strip().split(maxsplit=1)[0] if token.info else ""
            language = language if _LANGUAGE.fullmatch(language) else ""
            class_name = f' class="language-{language}"' if language else ""
            value = token.content.rstrip("\n")
            html.append(f"<pre><code{class_name}>{escape(value)}</code></pre>\n\n")
            plain.append(f"{value}\n\n")
        elif token.type == "hr":
            _append_text(html, plain, "---\n\n")
        elif token.type in {"html_block", "html_inline"}:
            _append_text(html, plain, token.content)
    return "".join(html).strip(), "".join(plain).strip()


def _blocks(tokens: list[Token]) -> list[list[Token]]:
    result = []
    index = 0
    while index < len(tokens):
        start = index
        depth = 0
        while index < len(tokens):
            depth += tokens[index].nesting
            index += 1
            if depth == 0:
                break
        result.append(tokens[start:index])
    return result


def _streaming_markdown(content: str) -> str:
    canonical = canonical_assistant_content(content)
    if canonical != content:
        return canonical
    opener = _MARKDOWN_ENVELOPE.match(content)
    return content[opener.end() :] if opener else content


def render_telegram_markdown(
    content: str, partial: bool = False
) -> tuple[RenderedChannelText, ...]:
    source = _streaming_markdown(content) if partial else canonical_assistant_content(content)
    if partial and len(source) > TELEGRAM_DRAFT_SOURCE_LIMIT:
        prefix = "[...]\n\n"
        source = prefix + source[-(TELEGRAM_DRAFT_SOURCE_LIMIT - len(prefix)) :]
    source_lines = source.splitlines(keepends=True)
    rendered: list[RenderedChannelText] = []
    current_html = ""
    current_length = 0
    current_bytes = 0

    def flush() -> None:
        nonlocal current_html, current_length, current_bytes
        if current_html:
            rendered.append(RenderedChannelText(current_html, TELEGRAM_HTML))
            current_html = ""
            current_length = 0
            current_bytes = 0

    for block in _blocks(_MARKDOWN.parse(source)):
        block_html, block_plain = _render_block(block)
        if not block_plain:
            continue
        mapping = block[0].map if block else None
        fallback = (
            "".join(source_lines[mapping[0] : mapping[1]]).strip()
            if mapping is not None
            else block_plain
        )
        block_bytes = len(block_html.encode())
        if (
            len(block_plain) > TELEGRAM_TEXT_LIMIT
            or block_bytes > TELEGRAM_HTML_BYTE_LIMIT
        ):
            flush()
            rendered.extend(split_plain_text(fallback, TELEGRAM_TEXT_LIMIT))
            continue
        separator = "\n\n" if current_html else ""
        separator_bytes = len(separator.encode())
        if (
            current_length + len(separator) + len(block_plain) > TELEGRAM_TEXT_LIMIT
            or current_bytes + separator_bytes + block_bytes
            > TELEGRAM_HTML_BYTE_LIMIT
        ):
            flush()
            separator = ""
            separator_bytes = 0
        current_html += separator + block_html
        current_length += len(separator) + len(block_plain)
        current_bytes += separator_bytes + block_bytes
    flush()
    return tuple(rendered)


register_channel_text_renderer("telegram", render_telegram_markdown)
