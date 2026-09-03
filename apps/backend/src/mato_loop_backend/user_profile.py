from __future__ import annotations

import hashlib
import re
from typing import Any

from mato_loop_backend.settings import TrustLevel, UserProfileFact, UserProfileSettings

PROFILE_FIELDS = (
    ("full_name", "fullName"),
    ("preferred_name", "preferredName"),
    ("form_of_address", "formOfAddress"),
    ("pronouns", "pronouns"),
    ("primary_language", "primaryLanguage"),
    ("primary_residence", "primaryResidence"),
)


def effective_profile_fact(fact: UserProfileFact, trust_level: TrustLevel) -> str | None:
    override = fact.private_only if trust_level == TrustLevel.PRIVATE else fact.public_only
    return override if override is not None else fact.both


def normalize_profile_content(content: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", content.strip()).casefold()
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def user_profile_runtime_payload(
    settings: UserProfileSettings, trust_level: TrustLevel
) -> dict[str, Any]:
    return {
        **{
            output_name: effective_profile_fact(getattr(settings, field_name), trust_level)
            for field_name, output_name in PROFILE_FIELDS
        },
    }


def user_profile_content_hashes(
    settings: UserProfileSettings,
) -> frozenset[str]:
    return frozenset(
        normalize_profile_content(content)[1]
        for field_name, _output_name in PROFILE_FIELDS
        for content in (
            getattr(settings, field_name).both,
            getattr(settings, field_name).private_only,
            getattr(settings, field_name).public_only,
        )
        if content is not None
    )
