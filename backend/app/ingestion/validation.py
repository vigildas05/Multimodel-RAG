"""Upload checks that run in the API, before anything is stored or queued. They only
look at sizes and leading bytes; anything that requires parsing the file's structure
happens later, inside the worker's sandbox."""

from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.core.exceptions import FileTooLargeError, InvalidFileTypeError, UploadRejectedError
from app.ingestion.file_types import SNIFF_BYTES, detect_file_type, extension_matches

_READ_CHUNK = 1024 * 1024


async def read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read the upload in chunks and stop as soon as it exceeds `max_bytes`, so an
    oversized file is never held in memory in full."""
    buffer = bytearray()
    while chunk := await file.read(_READ_CHUNK):
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise FileTooLargeError(f"File exceeds the maximum allowed size of {max_bytes // (1024 * 1024)}MB.")
    return bytes(buffer)


def validate_upload(filename: str, data: bytes, settings: Settings) -> str:
    """Return the file kind ("pdf" | "image" | "audio") determined from its content."""
    extension = Path(filename or "").suffix.lower()
    if extension not in settings.allowed_extensions_set:
        raise InvalidFileTypeError(
            f"File type '{extension or '(none)'}' is not supported.",
            detail=f"Allowed types: {', '.join(sorted(settings.allowed_extensions_set))}",
        )
    if not data:
        raise UploadRejectedError("The uploaded file is empty.")
    detected = detect_file_type(data[:SNIFF_BYTES])
    if detected is None:
        raise UploadRejectedError(
            "This file's contents don't match any supported format.",
            detail=f"Claimed extension {extension}; unrecognised file signature.",
        )
    if not extension_matches(detected.kind, extension):
        raise UploadRejectedError(
            f"This file contains a {detected.label}, which doesn't match its {extension} extension.",
        )
    return detected.kind
