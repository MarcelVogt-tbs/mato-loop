from __future__ import annotations

import re

_MARKDOWN_ENVELOPE = re.compile(
    r"\A {0,3}(?P<fence>`{3,}|~{3,})[\t ]*(?:markdown|md)[\t ]*(?:\r\n|\n)",
    re.IGNORECASE,
)


def canonical_assistant_content(content: str) -> str:
    opening = _MARKDOWN_ENVELOPE.match(content)
    if opening is None:
        return content
    lines = content[opening.end() :].splitlines(keepends=True)
    closing_index = next(
        (index for index in range(len(lines) - 1, -1, -1) if lines[index].strip()),
        None,
    )
    if closing_index is None:
        return content
    marker = opening.group("fence")
    if re.fullmatch(
        rf" {{0,3}}{re.escape(marker[0])}{{{len(marker)},}}[\t ]*(?:\r\n|\n)?",
        lines[closing_index],
    ) is None:
        return content
    if any(line.strip() for line in lines[closing_index + 1 :]):
        return content
    return "".join(lines[:closing_index])
