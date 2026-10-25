"""Undo common obfuscation before scanning, and record that it was there.

Attackers hide instructions from keyword filters with zero-width characters ("ig​nore"),
Unicode look-alikes (Cyrillic "о" for Latin "o"), full-width or stylised letters, and
encodings (base64/hex). Scanning runs on the normalised text plus any decoded payloads; the
obfuscation itself is a signal, since ordinary questions rarely contain any of it.
"""

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

from security.types import Finding

# Zero-width and invisible formatting characters, plus bidirectional overrides.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩﻿­᠎]")

# Common Cyrillic/Greek homoglyphs of Latin letters (NFKC doesn't fold these).
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j",
        "ѕ": "s", "ԁ": "d", "ɡ": "g", "һ": "h", "ӏ": "l", "ո": "n", "ս": "u", "ν": "v", "ο": "o",
        "α": "a", "ε": "e", "ι": "i", "κ": "k", "ρ": "p", "τ": "t", "υ": "u", "χ": "x",
        "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
        "Т": "T", "Х": "X", "Ι": "I", "Ο": "O", "Ρ": "P", "Τ": "T",
        "І": "I", "Ј": "J", "Ѕ": "S", "Ү": "Y", "ү": "y", "ԛ": "q", "ԝ": "w", "ɑ": "a", "ӧ": "o",
        "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Κ": "K", "Μ": "M", "Ν": "N", "Χ": "X",
    }
)
_LATIN = re.compile(r"[A-Za-z]")
_NON_LATIN_LETTER = re.compile(r"[^\W\d_A-Za-z]")
_WORD = re.compile(r"\S+")

_BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{24,}={0,2}")
_HEX_RUN = re.compile(r"(?:0x)?((?:[0-9a-fA-F]{2}[\s:]?){16,})")


@dataclass
class NormalizedText:
    text: str
    decoded_payloads: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def scan_targets(self) -> list[str]:
        return [self.text, *self.decoded_payloads]


def _printable_ratio(text: str) -> float:
    return sum(ch.isprintable() or ch.isspace() for ch in text) / max(len(text), 1)


def _try_base64(run: str) -> str | None:
    try:
        decoded = base64.b64decode(run + "=" * (-len(run) % 4), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    return decoded if len(decoded) >= 8 and _printable_ratio(decoded) > 0.95 and " " in decoded else None


def _try_hex(run: str) -> str | None:
    try:
        decoded = bytes.fromhex(re.sub(r"[\s:]", "", run)).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded if _printable_ratio(decoded) > 0.95 and " " in decoded else None


def normalize(text: str) -> NormalizedText:
    findings: list[Finding] = []

    invisible = _INVISIBLE.findall(text)
    if invisible:
        findings.append(Finding("zero_width_chars", "obfuscation", 0.35, f"{len(invisible)} invisible characters"))
    cleaned = _INVISIBLE.sub("", text)

    folded = unicodedata.normalize("NFKC", cleaned)
    if folded != cleaned and sum(ord(c) > 0xFF00 or 0x1D400 <= ord(c) <= 0x1D7FF for c in cleaned) >= 3:
        findings.append(Finding("stylised_unicode", "obfuscation", 0.3, "full-width/mathematical letters"))

    mixed = [w for w in _WORD.findall(folded) if _LATIN.search(w) and _NON_LATIN_LETTER.search(w)]
    homoglyph_words = [w for w in mixed if w.translate(_CONFUSABLES) != w]
    if homoglyph_words:
        findings.append(Finding("homoglyphs", "obfuscation", 0.4, " ".join(homoglyph_words[:3])))
    normalized = folded.translate(_CONFUSABLES)

    decoded: list[str] = []
    for run in _BASE64_RUN.findall(normalized):
        payload = _try_base64(run)
        if payload:
            decoded.append(payload)
            findings.append(Finding("base64_payload", "obfuscation", 0.35, payload[:80]))
    for run in _HEX_RUN.findall(normalized):
        payload = _try_hex(run)
        if payload:
            decoded.append(payload)
            findings.append(Finding("hex_payload", "obfuscation", 0.35, payload[:80]))

    return NormalizedText(text=normalized, decoded_payloads=decoded, findings=findings)
