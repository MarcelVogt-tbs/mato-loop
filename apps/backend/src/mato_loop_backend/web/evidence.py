from __future__ import annotations

import hashlib

from mato_loop_backend.web.fetch import canonicalize_url


def evidence_source_id(url: str) -> str:
    canonical_url = canonicalize_url(url)
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:12]
    return f"src-{digest}"
