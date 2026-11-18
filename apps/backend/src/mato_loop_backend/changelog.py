from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from time import monotonic
from typing import Annotated, Literal
from urllib.parse import quote

import httpx
from pydantic import BaseModel, Field, model_validator

from mato_loop_backend import __version__
from mato_loop_backend.build_info import build_revision

Revision = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Channel = Literal["stable", "develop"]
Category = Literal["Added", "Changed", "Fixed", "Security"]
State = Literal["identical", "behind", "ahead", "diverged", "unknown"]
API_URL = "https://api.github.com/repos/devmato/mato-loop"
WEB_URL = "https://github.com/devmato/mato-loop"
MAX_PAGES = 10
PER_PAGE = 100
CACHE_LIMIT = 32
CACHE_SECONDS = 60
_TRAILER = re.compile(r"Release-Note:\s*(Added|Changed|Fixed|Security):\s*(\S.*)", re.I)
_TRAILER_LINE = re.compile(r"[A-Za-z0-9-]+:\s*.*")


class ChangelogQuery(BaseModel):
    channel: Channel | None = None
    base_revision: Revision | None = None
    target_revision: Revision | None = None

    @model_validator(mode="after")
    def validate_mode(self) -> ChangelogQuery:
        if (self.base_revision is None) != (self.target_revision is None):
            raise ValueError("Both revisions are required for an explicit comparison.")
        if self.base_revision is not None and self.channel is not None:
            raise ValueError("Explicit comparisons cannot select a remote channel.")
        return self


class ReleaseNote(BaseModel):
    category: Category
    text: str


class ChangelogResponse(BaseModel):
    base_revision: Revision | None = None
    base_version: str | None = None
    target_revision: Revision | None = None
    target_version: str | None = None
    state: State = "unknown"
    status: Literal["ok", "unavailable", "unknown_build"] = "ok"
    notes: list[ReleaseNote] = Field(default_factory=list)
    incomplete: bool = False
    compare_url: str | None = None


class _Reference(BaseModel):
    sha: Revision


class _Message(BaseModel):
    message: str


class _Commit(_Reference):
    commit: _Message
    parents: list[_Reference]


class _Comparison(BaseModel):
    status: Literal["identical", "ahead", "behind", "diverged"]
    total_commits: int = Field(ge=0)
    commits: list[_Commit] = Field(max_length=PER_PAGE)


class _Release(BaseModel):
    tag_name: str = Field(min_length=1, max_length=200)
    draft: Literal[False]
    prerelease: Literal[False]


class _Target(BaseModel):
    revision: Revision
    version: str | None = None


class _Cache:
    def __init__(self) -> None:
        self.entries: OrderedDict[tuple[str, ...], tuple[float, BaseModel]] = OrderedDict()

    def get(self, key: tuple[str, ...]) -> BaseModel | None:
        item = self.entries.get(key)
        if item is None:
            return None
        if monotonic() - item[0] >= CACHE_SECONDS:
            del self.entries[key]
            return None
        self.entries.move_to_end(key)
        return item[1].model_copy(deep=True)

    def put(self, key: tuple[str, ...], value: BaseModel) -> None:
        self.entries[key] = (monotonic(), value.model_copy(deep=True))
        self.entries.move_to_end(key)
        while len(self.entries) > CACHE_LIMIT:
            self.entries.popitem(last=False)


_targets = _Cache()
_comparisons = _Cache()


def release_notes(commit: _Commit) -> list[ReleaseNote]:
    if len(commit.parents) > 1:
        return []
    # Only the final trailer paragraph counts, not examples in the message body.
    paragraph = commit.commit.message.strip().split("\n\n")[-1].splitlines()
    if not paragraph or not all(_TRAILER_LINE.fullmatch(line) for line in paragraph):
        return []
    notes = []
    for line in paragraph:
        match = _TRAILER.fullmatch(line)
        if match:
            notes.append(ReleaseNote(category=match[1].capitalize(), text=match[2].strip()))
    return notes


async def _json(client: httpx.AsyncClient, path: str, **params: int) -> object:
    response = await client.get(f"{API_URL}/{path}", params=params)
    response.raise_for_status()
    return response.json()


async def _target(client: httpx.AsyncClient, channel: Channel) -> _Target:
    cached = _targets.get((channel,))
    if isinstance(cached, _Target):
        return cached
    if channel == "stable":
        release = _Release.model_validate(await _json(client, "releases/latest"))
        # target_commitish can be a moving branch: resolve the published tag instead.
        ref = _Reference.model_validate(
            await _json(client, f"commits/refs/tags/{quote(release.tag_name, safe='')}")
        )
        target = _Target(revision=ref.sha, version=release.tag_name)
    else:
        ref = _Reference.model_validate(await _json(client, "commits/heads/develop"))
        target = _Target(revision=ref.sha)
    _targets.put((channel,), target)
    return target


async def _compare(client: httpx.AsyncClient, base: str, target: str) -> ChangelogResponse:
    cached = _comparisons.get((base, target))
    if isinstance(cached, ChangelogResponse):
        return cached
    result = ChangelogResponse(
        base_revision=base, target_revision=target,
        compare_url=f"{WEB_URL}/compare/{base}...{target}",
    )
    if base == target:
        result.state = "identical"
        return result
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        comparison = _Comparison.model_validate(await _json(
            client, f"compare/{base}...{target}", per_page=PER_PAGE, page=page,
        ))
        state: State = {"ahead": "behind", "behind": "ahead"}.get(
            comparison.status, comparison.status,
        )
        if page > 1 and state != result.state:
            raise ValueError("Comparison state changed for fixed revisions.")
        result.state = state
        for commit in comparison.commits:
            if commit.sha not in seen:
                seen.add(commit.sha)
                result.notes.extend(release_notes(commit))
        result.incomplete = len(seen) < comparison.total_commits
        if not result.incomplete or not comparison.commits:
            break
    _comparisons.put((base, target), result)
    return result


async def get_changelog(query: ChangelogQuery) -> ChangelogResponse:
    explicit = query.base_revision is not None
    base = query.base_revision if explicit else build_revision()
    version = None if explicit else __version__
    clean = explicit or bool(base and re.fullmatch(
        rf"[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}-{base[:8]}", version or "",
    ))
    result = ChangelogResponse(base_revision=base, base_version=version)
    try:
        async with asyncio.timeout(30), httpx.AsyncClient(
            timeout=10, follow_redirects=False,
            headers={"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10"},
        ) as client:
            target = (
                _Target(revision=query.target_revision) if explicit
                else await _target(client, query.channel or "stable")
            )
            result.target_revision = target.revision
            result.target_version = target.version
            if not clean or base is None:
                result.status = "unknown_build"
                result.incomplete = True
                return result
            comparison = await _compare(client, base, target.revision)
            comparison.base_version = version
            comparison.target_version = target.version
            return comparison
    except (httpx.HTTPError, ValueError, TimeoutError):
        # External response bodies and exception text never cross the API boundary.
        result.status = "unavailable"
        result.incomplete = True
        return result
