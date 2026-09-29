import re
import uuid
from pathlib import Path

from app.config import Settings
from app.core.exceptions import FileTooLargeError, InvalidFileTypeError

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")

PDF_EXTENSIONS = {".pdf"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def sanitize_filename(filename: str) -> str:
    """Strip directory components and disallowed characters to prevent path traversal."""
    name = Path(filename).name
    name = _SAFE_CHARS.sub("_", name)
    if not name or name in {".", ".."}:
        name = "file"
    return name


def validate_extension(filename: str, settings: Settings) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in settings.allowed_extensions_set:
        raise InvalidFileTypeError(
            f"File type '{ext}' is not supported.",
            detail=f"Allowed types: {', '.join(sorted(settings.allowed_extensions_set))}",
        )
    return ext


def validate_size(size_bytes: int, settings: Settings) -> None:
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if size_bytes > max_bytes:
        raise FileTooLargeError(
            f"File exceeds the maximum allowed size of {settings.MAX_UPLOAD_SIZE_MB}MB.",
            detail=f"Uploaded file was {size_bytes / (1024 * 1024):.2f}MB.",
        )


def classify_file_type(ext: str) -> str:
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in AUDIO_EXTENSIONS:
        return "audio"
    return "unknown"


def build_stored_path(upload_dir: Path, document_id: str, sanitized_filename: str) -> Path:
    return upload_dir / f"{document_id}_{sanitized_filename}"


def new_document_id() -> str:
    return str(uuid.uuid4())
