from __future__ import annotations

import html
import re
from typing import Any

import httpx

from mato_loop_backend.web.fetch import WebFetchError, canonicalize_url, normalize_source_title

_HTML_COMMENT_PATTERN = re.compile(r"<!--.*?(?:-->|$)", re.DOTALL)
_INLINE_CODE_PATTERN = re.compile(r"(`+).*?\1", re.DOTALL)
_BARE_HTTP_URL_PATTERN = re.compile(r"https?://[^\s<]+", re.IGNORECASE)
_FENCE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
_FENCE_CLOSE_PATTERN = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})[ \t]*(?:\r?\n)?$")
_TEX_DISPLAY_PATTERN = re.compile(r"\$\$.*?\$\$|\\\[.*?\\\]", re.DOTALL)
_TEX_PAREN_PATTERN = re.compile(r"\\\(.*?\\\)", re.DOTALL)
_TEX_ENVIRONMENT_PATTERN = re.compile(
    r"\\begin\{([^{}]+)\}.*?\\end\{\1\}", re.DOTALL
)


def _add_research_sources(
    registry: dict[str, str],
    metadata: dict[str, Any],
    titles: dict[str, str] | None = None,
) -> None:
    sources = metadata.get("sources")
    if not isinstance(sources, list):
        return
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = source.get("sourceId")
        url = source.get("url")
        if source.get("citationEligible") is False:
            continue
        if not isinstance(source_id, str) or not isinstance(url, str):
            continue
        try:
            registry[source_id] = canonicalize_url(url, preserve_fragment=True)
        except WebFetchError:
            continue
        title = normalize_source_title(source.get("title"))
        if titles is not None and title is not None:
            titles[source_id] = title


def _linked_research_source_ids(content: str, sources: dict[str, str]) -> list[str]:
    source_ids_by_url: dict[str, str] = {}
    for source_id, url in sources.items():
        try:
            source_ids_by_url[canonicalize_url(url, preserve_fragment=True)] = source_id
        except WebFetchError:
            continue
    visible_lines: list[str] = []
    active_fence: tuple[str, int] | None = None
    for line in content.splitlines(keepends=True):
        fence = _FENCE_PATTERN.match(line)
        if active_fence is not None:
            closing_fence = _FENCE_CLOSE_PATTERN.match(line)
            if (
                closing_fence
                and closing_fence.group(1)[0] == active_fence[0]
                and len(closing_fence.group(1)) >= active_fence[1]
            ):
                active_fence = None
            continue
        if fence:
            active_fence = (fence.group(1)[0], len(fence.group(1)))
            continue
        visible_lines.append(line)
    visible_content = _HTML_COMMENT_PATTERN.sub("", "".join(visible_lines))
    visible_content = _INLINE_CODE_PATTERN.sub("", visible_content)
    visible_content = _TEX_DISPLAY_PATTERN.sub("", visible_content)
    visible_content = _TEX_PAREN_PATTERN.sub("", visible_content)
    visible_content = _TEX_ENVIRONMENT_PATTERN.sub("", visible_content)
    visible_content = _without_inline_dollar_math(visible_content)

    cited_source_ids: list[str] = []
    without_link_destinations: list[str] = []
    index = 0
    while index < len(visible_content):
        label_start = visible_content.find("[", index)
        if label_start < 0:
            without_link_destinations.append(visible_content[index:])
            break
        without_link_destinations.append(visible_content[index:label_start])
        label_end = visible_content.find("](", label_start + 1)
        if label_end < 0:
            without_link_destinations.append(visible_content[label_start:])
            break
        preceding_backslashes = 0
        preceding_index = label_start - 1
        while preceding_index >= 0 and visible_content[preceding_index] == "\\":
            preceding_backslashes += 1
            preceding_index -= 1
        escaped_link = preceding_backslashes % 2 == 1
        image_link = label_start > 0 and visible_content[label_start - 1] == "!"
        depth = 1
        cursor = label_end + 2
        escaped = False
        while cursor < len(visible_content) and depth:
            character = visible_content[cursor]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
            cursor += 1
        if depth:
            without_link_destinations.append(visible_content[label_start:])
            break
        destination = visible_content[label_end + 2 : cursor - 1]
        classified_destination = html.unescape(destination.strip())
        if classified_destination.startswith("<"):
            destination_end = classified_destination.find(">", 1)
            classified_destination = (
                classified_destination[1:destination_end]
                if destination_end >= 0
                else classified_destination
            )
        else:
            destination_parts = classified_destination.split(maxsplit=1)
            classified_destination = destination_parts[0] if destination_parts else ""
        classified_destination = classified_destination.replace("\\", "")
        if (
            not escaped_link
            and not image_link
            and classified_destination.lower().startswith(("http://", "https://", "//"))
        ):
            try:
                canonical_destination = canonicalize_url(classified_destination, preserve_fragment=True)
            except WebFetchError:
                canonical_destination = ""
            source_id = source_ids_by_url.get(canonical_destination)
            if source_id is None and canonical_destination:
                source_id = source_ids_by_url.get(canonicalize_url(classified_destination))
            if source_id is not None and source_id not in cited_source_ids:
                cited_source_ids.append(source_id)
        without_link_destinations.append("")
        index = cursor
    for match in _BARE_HTTP_URL_PATTERN.finditer("".join(without_link_destinations)):
        candidate = match.group(0).rstrip(".,;:!?)]}>")
        try:
            source_id = source_ids_by_url.get(canonicalize_url(candidate, preserve_fragment=True))
            if source_id is None:
                source_id = source_ids_by_url.get(canonicalize_url(candidate))
        except WebFetchError:
            continue
        if source_id is not None and source_id not in cited_source_ids:
            cited_source_ids.append(source_id)
    return cited_source_ids


def _research_snapshot(
    *,
    policy: str,
    used: bool,
    content: str,
    sources: dict[str, str],
    titles: dict[str, str],
) -> dict[str, Any]:
    linked_sources = []
    for source_id in _linked_research_source_ids(content, sources):
        url = sources[source_id]
        title = titles.get(source_id)
        if title is None:
            try:
                title = httpx.URL(url).host
            except (TypeError, ValueError):
                title = None
        linked_sources.append({"title": title or url, "url": url})
    return {
        "policy": policy,
        "used": used,
        "linkedSources": linked_sources,
    }


def _without_inline_dollar_math(content: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(content):
        start = content.find("$", index)
        if start < 0:
            parts.append(content[index:])
            break
        parts.append(content[index:start])
        if start + 1 >= len(content) or content[start + 1].isspace():
            parts.append("$")
            index = start + 1
            continue
        candidate = start + 1
        matched = False
        while (candidate := content.find("$", candidate)) >= 0:
            backslashes = 0
            cursor = candidate - 1
            while cursor > start and content[cursor] == "\\":
                backslashes += 1
                cursor -= 1
            if backslashes % 2:
                candidate += 1
                continue
            math_content = content[start + 1 : candidate]
            after = content[candidate + 1 : candidate + 2]
            if "\n" in math_content or "`" in math_content:
                break
            if math_content and not math_content[-1].isspace() and not after.isdigit():
                index = candidate + 1
                matched = True
                break
            candidate += 1
        if not matched:
            parts.append("$")
            index = start + 1
    return "".join(parts)
