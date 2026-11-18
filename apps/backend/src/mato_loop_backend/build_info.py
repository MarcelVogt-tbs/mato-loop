from pathlib import Path

_REVISION_FILE = Path("/app/BUILD_REVISION")


def build_revision() -> str | None:
    try:
        revision = _REVISION_FILE.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    return revision if len(revision) == 40 and all(c in "0123456789abcdef" for c in revision) else None
