from mato_loop_backend.messaging.formatting import render_channel_text, split_plain_text
from mato_loop_backend.messaging.telegram_formatting import (
    TELEGRAM_HTML,
    TELEGRAM_HTML_BYTE_LIMIT,
    TELEGRAM_TEXT_LIMIT,
)


def test_telegram_renders_supported_markdown_as_safe_html() -> None:
    parts = render_channel_text(
        "telegram",
        """# Titel

**Fett** und *kursiv* sowie ~~weg~~.

[Quelle](https://example.com/path?a=1&b=2) und `x < y`.

> Zitat

- eins
- zwei

```python
print("<tag>")
```
""",
        content_format="markdown",
    )

    assert len(parts) == 1
    assert parts[0].syntax == TELEGRAM_HTML
    assert "<b>Titel</b>" in parts[0].text
    assert "<b>Fett</b>" in parts[0].text
    assert "<i>kursiv</i>" in parts[0].text
    assert "<s>weg</s>" in parts[0].text
    assert '<a href="https://example.com/path?a=1&amp;b=2">Quelle</a>' in parts[0].text
    assert "<code>x &lt; y</code>" in parts[0].text
    assert "<blockquote>Zitat" in parts[0].text
    assert "- eins" in parts[0].text
    assert '<pre><code class="language-python">print(&quot;&lt;tag&gt;&quot;)</code></pre>' in parts[0].text


def test_telegram_escapes_html_and_rejects_unsafe_links() -> None:
    parts = render_channel_text(
        "telegram",
        '<script>alert("x")</script> [öffnen](https://user:secret@example.com)',
        content_format="markdown",
    )

    assert "<script>" not in parts[0].text
    assert "&lt;script&gt;" in parts[0].text
    assert "secret" not in parts[0].text
    assert "<a " not in parts[0].text


def test_telegram_renders_supported_contact_and_mention_links() -> None:
    part = render_channel_text(
        "telegram",
        "[E-Mail](mailto:user@example.com), [Telefon](tel:+49123), "
        "[Person](tg://user?id=123)",
        content_format="markdown",
    )[0]

    assert '<a href="mailto:user@example.com">E-Mail</a>' in part.text
    assert '<a href="tel:+49123">Telefon</a>' in part.text
    assert '<a href="tg://user?id=123">Person</a>' in part.text


def test_telegram_preserves_paragraph_breaks_in_loose_lists() -> None:
    part = render_channel_text(
        "telegram",
        "- erster Absatz\n\n  zweiter Absatz",
        content_format="markdown",
    )[0]

    assert "erster Absatz\n\nzweiter Absatz" in part.text


def test_telegram_renders_tables_and_mermaid_as_readable_code() -> None:
    parts = render_channel_text(
        "telegram",
        """| Name | Wert |
| --- | --- |
| A | 1 |

```mermaid
graph TD
  A --> B
```
""",
        content_format="markdown",
    )

    assert "<pre>Name | Wert\nA | 1</pre>" in parts[0].text
    assert "<pre><code class=\"language-mermaid\">graph TD" in parts[0].text


def test_telegram_chunks_blocks_and_falls_back_for_oversized_block() -> None:
    parts = render_channel_text(
        "telegram",
        f"**{'a' * 2_500}**\n\n**{'b' * 2_500}**\n\n{'c' * 4_100}",
        content_format="markdown",
    )

    assert [part.syntax for part in parts[:2]] == [TELEGRAM_HTML, TELEGRAM_HTML]
    assert all(len(part.text) <= TELEGRAM_TEXT_LIMIT for part in parts[2:])
    assert all(part.syntax is None for part in parts[2:])
    assert "".join(part.text for part in parts[2:]) == "c" * 4_100


def test_telegram_partial_render_removes_outer_markdown_fence() -> None:
    parts = render_channel_text(
        "telegram",
        "```markdown\n**Antwort**",
        content_format="markdown",
        partial=True,
    )

    assert parts[0].text == "<b>Antwort</b>"


def test_unknown_provider_and_plain_content_stay_plain() -> None:
    unknown = render_channel_text(
        "future-provider",
        "**sichtbar**",
        content_format="markdown",
    )
    telegram_plain = render_channel_text(
        "telegram",
        "**sichtbar**",
        content_format="plain",
    )

    assert unknown[0].text == "**sichtbar**"
    assert unknown[0].syntax is None
    assert telegram_plain == unknown


def test_telegram_does_not_nest_code_in_other_entities() -> None:
    part = render_channel_text(
        "telegram",
        "**run `pytest` now** und [siehe `Code`](https://example.com)",
        content_format="markdown",
    )[0]

    assert "<b>run </b><code>pytest</code><b> now</b>" in part.text
    assert '<a href="https://example.com">siehe </a><code>Code</code>' in part.text
    assert "<b>run <code>" not in part.text
    assert "<a href=\"https://example.com\">siehe <code>" not in part.text


def test_telegram_falls_back_before_html_payload_limit() -> None:
    source = " ".join(
        f"[x](https://example.com/{'a' * 200}/{index})" for index in range(200)
    )

    parts = render_channel_text("telegram", source, content_format="markdown")

    assert len(source.encode()) > TELEGRAM_HTML_BYTE_LIMIT
    assert all(part.syntax is None for part in parts)
    assert "".join(part.text for part in parts) == source


def test_telegram_partial_render_uses_a_stable_tail_window() -> None:
    parts = render_channel_text(
        "telegram",
        f"{'a' * TELEGRAM_TEXT_LIMIT}\n\n{'b' * 100}",
        content_format="markdown",
        partial=True,
    )

    assert len(parts) == 1
    assert parts[0].text.startswith("[...]")
    assert parts[0].text.endswith("b" * 100)


def test_plain_chunking_preserves_internal_whitespace() -> None:
    source = f"{'a' * 30}\n  {'b' * 30}"

    parts = split_plain_text(source, 40)

    assert "".join(part.text for part in parts) == source
