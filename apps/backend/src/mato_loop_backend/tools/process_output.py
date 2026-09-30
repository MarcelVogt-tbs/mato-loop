from __future__ import annotations

import re

_AUTH_HEADER_LINE = re.compile(
    r"^.*\b(?:authorization|proxy-authorization|set-cookie|cookies?|x-csrftoken|x-csrf-token|x-xsrf-token)"
    r"(?:[ _-]headers?|[ _-]sent|[ _-]in[ _-]response)?[\"']?[ \t]*[:=].*$",
    re.IGNORECASE | re.MULTILINE,
)


def redact_process_output(
    output: dict[str, str], values: tuple[str, ...] = (), *,
    truncated: bool = False, complete: bool = True,
) -> dict[str, str]:
    """Mask known secrets and recognizable authentication-header lines before publication."""
    if truncated and values:
        return {key: "[redacted]" if text else "" for key, text in output.items()}
    safe = {}
    for key, text in output.items():
        # Partial lines may contain a header name or value split across transport chunks.
        if not complete:
            text = text[:text.rfind("\n") + 1]
        for secret in sorted(set(values), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[redacted]")
        safe[key] = _AUTH_HEADER_LINE.sub("[redacted authentication header]", text)
    return safe
