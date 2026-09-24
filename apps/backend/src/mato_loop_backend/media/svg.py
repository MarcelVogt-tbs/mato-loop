from __future__ import annotations

import re
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

MAX_SAFE_SVG_BYTES = 5 * 1024 * 1024

_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_FORBIDDEN_ELEMENTS = {
    "animate",
    "animatemotion",
    "animatetransform",
    "audio",
    "discard",
    "embed",
    "foreignobject",
    "iframe",
    "link",
    "object",
    "script",
    "set",
    "video",
}
_URL_PATTERN = re.compile(r"url\s*\(([^)]*)\)", re.IGNORECASE)
_ACTIVE_CSS_PATTERN = re.compile(
    r"@import|@(?:-webkit-)?keyframes|(?:^|[;{\s])(?:-webkit-)?(?:animation|transition)(?:-[\w-]+)?\s*:",
    re.IGNORECASE,
)


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1].casefold()


def _validate_references(value: str) -> None:
    if _ACTIVE_CSS_PATTERN.search(value):
        raise ValueError("unsafeSvg")
    matches = list(_URL_PATTERN.finditer(value))
    if len(matches) != len(re.findall(r"url\s*\(", value, re.IGNORECASE)):
        raise ValueError("unsafeSvg")
    for match in matches:
        if not match.group(1).strip().strip("'\"").strip().startswith("#"):
            raise ValueError("unsafeSvg")


def validate_svg(data: bytes) -> None:
    if len(data) > MAX_SAFE_SVG_BYTES:
        raise ValueError("tooLarge")
    try:
        source = data.decode("utf-8", errors="strict")
        if "<?xml-stylesheet" in source.casefold():
            raise ValueError("unsafeSvg")
        root = DefusedElementTree.fromstring(
            source,
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except UnicodeDecodeError as exc:
        raise ValueError("invalidImage") from exc
    except (DefusedXmlException, ParseError) as exc:
        code = "unsafeSvg" if isinstance(exc, DefusedXmlException) else "invalidImage"
        raise ValueError(code) from exc

    if root.tag not in {"svg", f"{{{_SVG_NAMESPACE}}}svg"}:
        raise ValueError("invalidImage")
    count = 0
    pending = [(root, 1)]
    while pending:
        element, depth = pending.pop()
        count += 1
        if count > 10_000 or depth > 100:
            raise ValueError("unsafeSvg")
        name = _local_name(element.tag)
        if name in _FORBIDDEN_ELEMENTS:
            raise ValueError("unsafeSvg")
        for attribute, value in element.attrib.items():
            attribute_name = _local_name(attribute)
            if attribute_name.startswith("on") or attribute_name == "base":
                raise ValueError("unsafeSvg")
            if attribute_name in {"href", "src"} and value and not value.strip().startswith("#"):
                raise ValueError("unsafeSvg")
            _validate_references(value)
        if name == "style" and element.text:
            _validate_references(element.text)
        pending.extend((child, depth + 1) for child in element)
