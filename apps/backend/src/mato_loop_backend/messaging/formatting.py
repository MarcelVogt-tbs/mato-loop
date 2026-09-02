from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class RenderedChannelText:
    text: str
    syntax: str | None = None


ChannelTextRenderer = Callable[[str, bool], tuple[RenderedChannelText, ...]]
_renderers: dict[str, ChannelTextRenderer] = {}


def register_channel_text_renderer(provider: str, renderer: ChannelTextRenderer) -> None:
    if provider in _renderers and _renderers[provider] is not renderer:
        raise RuntimeError(f"Channel text renderer already registered: {provider}")
    _renderers[provider] = renderer


def split_plain_text(text: str, limit: int) -> tuple[RenderedChannelText, ...]:
    if limit < 1:
        raise ValueError("Channel text limit must be positive")
    remaining = text.strip()
    chunks = []
    while remaining:
        if len(remaining) <= limit:
            chunks.append(RenderedChannelText(remaining))
            break
        split_at = remaining.rfind("\n", 0, limit)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at < limit // 2:
            split_at = limit
        else:
            split_at += 1
        chunks.append(RenderedChannelText(remaining[:split_at]))
        remaining = remaining[split_at:]
    return tuple(chunks)


def render_channel_text(
    provider: str,
    text: str,
    *,
    content_format: str = "plain",
    partial: bool = False,
    plain_limit: int = 4_000,
) -> tuple[RenderedChannelText, ...]:
    renderer = _renderers.get(provider) if content_format == "markdown" else None
    return renderer(text, partial) if renderer else split_plain_text(text, plain_limit)
