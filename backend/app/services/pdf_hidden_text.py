"""Detect text a human reader can't see but a text extractor (and so the LLM) would.

Hidden text is the classic carrier for indirect prompt injection in documents: white
text on a white page, 1pt fonts, text placed outside the visible page, or text drawn in
PDF's "invisible" render mode. Each text span is checked for:

- invisible  — drawn with render mode 3 (no fill, no stroke) or ~zero opacity
- low_contrast — its colour is nearly identical to the background actually rendered
                 under it (works on coloured backgrounds, not just white)
- tiny_font  — font size below MIN_VISIBLE_FONT_SIZE points
- off_page   — mostly outside the visible page area

Flagged spans are removed from the text that gets indexed and reported for review.

Known limitations: text hidden *behind* an opaque image or shape, and text that is
merely very small relative to a large page, are not detected; low-contrast detection
uses the dominant background colour in the span's box, so busy photographic backgrounds
can produce false positives or negatives.
"""

from collections import Counter
from dataclasses import dataclass

import fitz

MIN_VISIBLE_FONT_SIZE = 4.0
# Max per-channel difference (0-255) between text and background still considered invisible.
LOW_CONTRAST_THRESHOLD = 30
MIN_ON_PAGE_FRACTION = 0.5
MIN_VISIBLE_OPACITY = 0.1
# Render scale for background sampling; low resolution is plenty for colour checks.
SAMPLE_ZOOM = 1.0
MAX_SAMPLES_PER_SPAN = 400

# Plain-text extraction without clipping to the page (flag off AND an infinite clip box:
# PyMuPDF clips to the page rect otherwise), so text placed outside the visible page is
# seen and flagged rather than silently dropped.
TEXT_FLAGS = fitz.TEXTFLAGS_TEXT & ~fitz.TEXT_MEDIABOX_CLIP


@dataclass
class HiddenSpan:
    page_number: int
    reasons: list[str]
    text: str


def _rgb(color: int) -> tuple[int, int, int]:
    return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF


def _invisible_boxes(page: "fitz.Page") -> list[fitz.Rect]:
    boxes = []
    for span in page.get_texttrace():
        if span.get("type") == 3 or span.get("opacity", 1.0) < MIN_VISIBLE_OPACITY:
            boxes.append(fitz.Rect(span["bbox"]))
    return boxes


def _background_color(pix: "fitz.Pixmap", rect: fitz.Rect) -> tuple[int, int, int] | None:
    """Most common rendered colour inside `rect` (page coordinates). Glyph strokes cover a
    minority of a span's box, so the mode is the background the text sits on."""
    x0, y0 = max(int(rect.x0 * SAMPLE_ZOOM), 0), max(int(rect.y0 * SAMPLE_ZOOM), 0)
    x1, y1 = min(int(rect.x1 * SAMPLE_ZOOM), pix.width), min(int(rect.y1 * SAMPLE_ZOOM), pix.height)
    if x1 <= x0 or y1 <= y0:
        return None
    area = (x1 - x0) * (y1 - y0)
    step = max(1, int((area / MAX_SAMPLES_PER_SPAN) ** 0.5))
    counts = Counter(pix.pixel(x, y)[:3] for y in range(y0, y1, step) for x in range(x0, x1, step))
    return counts.most_common(1)[0][0]


def _reasons(span: dict, page_rect: fitz.Rect, invisible: list[fitz.Rect], pix: "fitz.Pixmap") -> list[str]:
    reasons = []
    bbox = fitz.Rect(span["bbox"])
    if bbox.is_empty or (bbox & page_rect).get_area() < MIN_ON_PAGE_FRACTION * bbox.get_area():
        reasons.append("off_page")
    if span["size"] < MIN_VISIBLE_FONT_SIZE:
        reasons.append("tiny_font")
    center = fitz.Point((bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2)
    if any(center in box for box in invisible) or span.get("alpha", 255) < MIN_VISIBLE_OPACITY * 255:
        reasons.append("invisible")
    elif "off_page" not in reasons:
        background = _background_color(pix, bbox & page_rect)
        if background is not None:
            text_color = _rgb(span["color"])
            if max(abs(a - b) for a, b in zip(text_color, background, strict=False)) <= LOW_CONTRAST_THRESHOLD:
                reasons.append("low_contrast")
    return reasons


def analyze_page(page: "fitz.Page", page_number: int) -> tuple[str, list[HiddenSpan]]:
    """Return (visible text, hidden spans) for one page."""
    page_rect = page.rect
    invisible = _invisible_boxes(page)
    pix = page.get_pixmap(matrix=fitz.Matrix(SAMPLE_ZOOM, SAMPLE_ZOOM), alpha=False)

    hidden: list[HiddenSpan] = []
    blocks_out = []
    for block in page.get_text("dict", flags=TEXT_FLAGS, clip=fitz.INFINITE_RECT())["blocks"]:
        lines_out = []
        for line in block.get("lines", []):
            kept = []
            for span in line["spans"]:
                if not span["text"].strip():
                    kept.append(span["text"])
                    continue
                reasons = _reasons(span, page_rect, invisible, pix)
                if reasons:
                    hidden.append(HiddenSpan(page_number=page_number, reasons=reasons, text=span["text"][:200]))
                else:
                    kept.append(span["text"])
            line_text = "".join(kept).strip()
            if line_text:
                lines_out.append(line_text)
        if lines_out:
            blocks_out.append("\n".join(lines_out))
    return "\n".join(blocks_out).strip(), hidden
