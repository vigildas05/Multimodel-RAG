import re
import uuid
from pathlib import Path

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def sanitize_filename(filename: str | None) -> str:
    """Display-safe version of a user-supplied filename (never used to build a path)."""
    name = Path(filename or "").name
    name = _SAFE_CHARS.sub("_", name)[:200]
    if not name or name in {".", ".."}:
        name = "file"
    return name


def new_document_id() -> str:
    return str(uuid.uuid4())
