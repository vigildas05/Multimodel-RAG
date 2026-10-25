"""File type detection from content (magic bytes), never from the name.

The extension is only used to cross-check: if a file's bytes say PNG but its name says
.pdf, it's rejected rather than guessed at, which closes off renamed executables and
"polyglot" files built to look like one type to a validator and another to a parser.
Signatures must match at offset 0 (e.g. PDF readers tolerate junk before "%PDF-"; this
deliberately doesn't).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectedType:
    kind: str  # "pdf" | "image" | "audio"
    label: str  # e.g. "PNG image", for messages


# How many leading bytes detection needs.
SNIFF_BYTES = 16

_MP4_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"mp42", b"mp41", b"isom", b"iso2", b"dash"}

EXTENSIONS_BY_KIND = {
    "pdf": {".pdf"},
    "image": {".png", ".jpg", ".jpeg", ".webp"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".flac"},
}


def detect_file_type(head: bytes) -> DetectedType | None:
    if head.startswith(b"%PDF-"):
        return DetectedType("pdf", "PDF document")
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return DetectedType("image", "PNG image")
    if head.startswith(b"\xff\xd8\xff"):
        return DetectedType("image", "JPEG image")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return DetectedType("image", "WebP image")
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return DetectedType("audio", "WAV audio")
    if head.startswith(b"fLaC"):
        return DetectedType("audio", "FLAC audio")
    if head.startswith(b"OggS"):
        return DetectedType("audio", "Ogg audio")
    if head.startswith(b"ID3") or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return DetectedType("audio", "MP3 audio")
    if head[4:8] == b"ftyp" and head[8:12] in _MP4_AUDIO_BRANDS:
        return DetectedType("audio", "MPEG-4 audio")
    return None


def extension_matches(kind: str, extension: str) -> bool:
    return extension.lower() in EXTENSIONS_BY_KIND.get(kind, set())
