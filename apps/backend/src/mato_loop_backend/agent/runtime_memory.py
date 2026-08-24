from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from mato_loop_backend.memory.episode_retrieval import EpisodeReference
from mato_loop_backend.memory.service import memory_reference_timestamps
from mato_loop_backend.models import MemoryRecord, Message
from mato_loop_backend.settings import AgentRuntimeSettings
from mato_loop_backend.settings.models import ModelConfig

AUTOMATIC_EPISODE_MAX_ITEMS = 2
AUTOMATIC_EPISODE_REFERENCE_MAX_CHARACTERS = 320
AUTOMATIC_CONTEXT_MAX_TOKENS = 1_536
AUTOMATIC_CONTEXT_BUDGET_RATIO = 0.1
MEMORY_OVERVIEW_MAX_CHARACTERS = 12_000


def _memory_rag_queries(records: list[Message]) -> tuple[str | None, str | None]:
    user_messages = [item.content.strip() for item in records if item.role == "user"]
    if not user_messages or not user_messages[-1]:
        return None, None
    user_messages = [item for item in user_messages if item]
    current = user_messages[-1][:2_000]
    contextual = (
        (user_messages[-2][:1_000] + "\nFollow-up: " + current)[:2_000]
        if len(current) <= 120 and len(user_messages) > 1
        else None
    )
    return current, contextual


def _merge_contextual_results[T](
    primary: list[T],
    contextual: list[T],
    *,
    limit: int,
    identity: Callable[[T], object],
) -> list[T]:
    if not contextual or limit <= 1:
        return primary[:limit]
    primary_limit = min(len(primary), limit - 1)
    selected = primary[:primary_limit]
    seen = {identity(item) for item in selected}
    supplement = contextual[0] if identity(contextual[0]) not in seen else None
    if supplement is not None:
        selected.append(supplement)
        seen.add(identity(supplement))
    for item in (*primary[primary_limit:], *contextual):
        if len(selected) == limit:
            break
        if identity(item) not in seen:
            selected.append(item)
            seen.add(identity(item))
    return selected


def _episode_reference_request_text(records: list[Message]) -> str:
    current = next(
        (
            item.content.casefold()
            for item in reversed(records)
            if item.role == "user"
        ),
        "",
    )
    normalized = re.sub(r"\s+", " ", current).strip()
    if re.search(
        r"\b(?:translate|übersetze|uebersetze|rewrite|umschreiben|"
        r"explain the phrase)\b|\bwhat does .{0,40}\bmean\b",
        normalized,
    ) or re.search(
        r"\b(?:do not|don't|dont|ignore|avoid|without|no information from|"
        r"nicht|keine? informationen? aus)\b.{0,80}\b"
        r"(?:previous|prior|earlier|vorherigen|früheren|frueheren)\b",
        normalized,
    ) or re.search(
        r"\b(?:previous|prior|earlier|vorherige|frühere|fruehere)\b.{0,80}\b"
        r"(?:irrelevant|do not|don't|dont|ignore|nicht verwenden|nicht nutzen)\b",
        normalized,
    ):
        return ""
    return normalized


def _recent_episode_overview_requested(records: list[Message]) -> bool:
    normalized = _episode_reference_request_text(records)
    return bool(
        re.search(
            r"\b(?:was haben wir|worüber haben wir|woran haben wir) (?:zuletzt|in der letzten session|im letzten chat)\b"
            r"|\b(?:letzte|vorherige) session\b"
            r"|\bwhat did we (?:last|previously) discuss\b"
            r"|\b(?:last|previous) (?:session|chat|conversation)\b",
            normalized,
        )
    )


def _prior_session_history_requested(records: list[Message]) -> bool:
    if _recent_episode_overview_requested(records):
        return True
    normalized = _episode_reference_request_text(records)
    return bool(
        re.search(
            r"\b(?:im|in dem) (?:vorherigen|früheren|frueheren) (?:chat|session)\b"
            r"|\bwie wir (?:damals|zuvor|früher|frueher) (?:besprochen|entschieden|vereinbart)\b"
            r"|\b(?:mach|mache|machen wir) dort weiter,? wo wir aufgehört haben\b"
            r"|\b(?:in|from) (?:(?:an?|the) )?(?:previous|prior|earlier) (?:chat|session|conversation)\b"
            r"|\bas we (?:discussed|decided|agreed) (?:earlier|before|previously)\b"
            r"|\b(?:continue|pick up) (?:from )?where we left off\b",
            normalized,
        )
    )


def _session_bootstrap_requested(records: list[Message]) -> bool:
    return sum(item.role == "user" for item in records) == 1 and not any(
        item.role == "assistant" for item in records
    )


def _memory_overview_requested(records: list[Message]) -> bool:
    current_index = next(
        (index for index in range(len(records) - 1, -1, -1) if records[index].role == "user"),
        None,
    )
    if current_index is None:
        return False
    current = re.sub(r"\s+", " ", records[current_index].content.casefold()).strip()
    direct_patterns = (
        r"\bwas (?:hast|weißt|weisst|kennst) du .*\b(?:erinner|gedächtnis|über mich|ueber mich)",
        r"\bwas (?:weißt|weisst|kennst) du (?:alles )?(?:über|ueber|von) mir\b",
        r"\bwelche .*\b(?:erinnerungen|informationen) .*\b(?:gespeichert|gemerkt)\b",
        r"\bwhat (?:else )?do you (?:remember|know) (?:about me)?\b",
        r"\bwhat (?:memories|information) do you have (?:about me)?\b",
    )
    if any(re.search(pattern, current) for pattern in direct_patterns):
        return True
    follow_up = bool(
        re.search(
            r"\b(?:was|welche|und)?\s*(?:noch|sonst|mehr)\b|\b(?:what|anything) else\b|\bmore\b",
            current,
        )
    )
    if not follow_up:
        return False
    previous_user_index = next(
        (
            index
            for index in range(current_index - 1, -1, -1)
            if records[index].role == "user"
        ),
        -1,
    )
    return any(
        call.get("name") == "memory_retrieve"
        and (call.get("arguments") or {}).get("mode") == "browse"
        for item in records[previous_user_index + 1 : current_index]
        if item.role == "assistant"
        for call in (item.tool_calls or [])
        if isinstance(call, dict)
    ) or any(
        isinstance(item.provider_data, dict)
        and isinstance(item.provider_data.get("_matoloop"), dict)
        and isinstance(item.provider_data["_matoloop"].get("memoryOverview"), dict)
        for item in records[previous_user_index + 1 : current_index]
        if item.role == "assistant"
    )


def _memory_overview_continuation(records: list[Message]) -> dict[str, Any] | None:
    current_index = next(
        (index for index in range(len(records) - 1, -1, -1) if records[index].role == "user"),
        None,
    )
    if current_index is None:
        return None
    current = re.sub(r"\s+", " ", records[current_index].content.casefold()).strip()
    if not re.search(
        r"\b(?:was|welche|und)?\s*(?:noch|sonst|mehr)\b|\b(?:what|anything) else\b|\bmore\b",
        current,
    ):
        return None
    previous_user_index = next(
        (
            index
            for index in range(current_index - 1, -1, -1)
            if records[index].role == "user"
        ),
        -1,
    )
    for item in reversed(records[previous_user_index + 1 : current_index]):
        runtime_state = (
            item.provider_data.get("_matoloop")
            if item.role == "assistant" and isinstance(item.provider_data, dict)
            else None
        )
        overview = (
            runtime_state.get("memoryOverview")
            if isinstance(runtime_state, dict)
            else None
        )
        if isinstance(overview, dict):
            return overview
    return None


def _automatic_context_character_budget(
    model: ModelConfig, settings: AgentRuntimeSettings
) -> int:
    input_budget = max(
        0,
        model.context_window_tokens
        - model.max_output_tokens
        - settings.safety_reserve_tokens,
    )
    token_budget = min(
        AUTOMATIC_CONTEXT_MAX_TOKENS,
        int(input_budget * AUTOMATIC_CONTEXT_BUDGET_RATIO),
    )
    return max(0, int(token_budget * model.characters_per_token))


def _bounded_episode_payload(
    references: list[EpisodeReference], *, max_characters: int
) -> list[dict[str, Any]]:
    if max_characters <= 0:
        return []
    payload: list[dict[str, Any]] = []
    selected = references[:AUTOMATIC_EPISODE_MAX_ITEMS]
    remaining_budget = max_characters
    for index, reference in enumerate(selected):
        share = remaining_budget // (len(selected) - index)
        if share <= 0:
            break
        matched_text = reference.anchor_content[
            : min(share, AUTOMATIC_EPISODE_REFERENCE_MAX_CHARACTERS)
        ]
        local_used = len(matched_text)
        messages = []
        for message in reference.excerpt:
            if message.id == reference.anchor_message_id:
                continue
            remaining = share - local_used
            if remaining <= 0:
                break
            content = message.content[:remaining]
            if not content:
                continue
            messages.append(
                {
                    "role": message.role,
                    "content": content,
                    "at": message.created_at.isoformat(),
                }
            )
            local_used += len(content)
        if not matched_text:
            continue
        payload.append(
            {
                "sessionId": str(reference.session_id),
                "title": reference.session_title,
                "anchorMessage": {
                    "id": str(reference.anchor_message_id),
                    "role": reference.anchor_role,
                    "createdAt": reference.anchor_created_at.isoformat(),
                    "preview": matched_text,
                },
                "messages": messages,
            }
        )
        remaining_budget -= local_used
    return payload


def _cross_session_context_text(
    memory_references: list[tuple[str, MemoryRecord]],
    episode_payload: list[dict[str, Any]],
) -> str | None:
    if not memory_references and not episode_payload:
        return None
    return json.dumps(
        {
            "durableMemories": _durable_memory_payload(memory_references),
            "priorSessionEpisodes": episode_payload,
        },
        ensure_ascii=False,
    )


def _durable_memory_payload(
    memory_references: list[tuple[str, MemoryRecord]],
) -> list[dict[str, Any]]:
    return [
        {
            "source": source,
            "id": str(item.id),
            "revision": item.revision,
            "category": item.category,
            "audience": item.audience,
            "pinKind": item.pin_kind,
            "content": item.content,
            **memory_reference_timestamps(item),
        }
        for source, item in memory_references
    ]


def _bounded_memory_overview(
    items: list[MemoryRecord],
) -> tuple[list[dict[str, Any]], MemoryRecord | None, bool]:
    payload = []
    used_characters = 0
    content_truncated = False
    for item in items:
        separator = 2 if payload else 0
        available = MEMORY_OVERVIEW_MAX_CHARACTERS - used_characters - separator
        if available <= 0:
            break
        content = item.content
        if len(content) > available:
            if payload:
                break
            suffix = "... [truncated]"
            content = content[: max(0, available - len(suffix))] + suffix
            content_truncated = True
        payload.append(
            {
                "source": "overview",
                "id": str(item.id),
                "revision": item.revision,
                "category": item.category,
                "pinned": item.pinned,
                "audience": item.audience,
                "pinKind": item.pin_kind,
                "content": content,
                **memory_reference_timestamps(item),
                "sourceSession": (
                    {"id": str(item.source_session.id)}
                    if item.source_session
                    else None
                ),
            }
        )
        used_characters += separator + len(content)
        if content_truncated:
            break
    omitted_items = len(payload) < len(items)
    return payload, (items[len(payload) - 1] if payload else None), omitted_items
