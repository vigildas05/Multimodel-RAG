"""PDF text extraction via PyMuPDF, with a heuristic scanned-page detector that
routes low-text pages to OCR, and hidden-text stripping (see pdf_hidden_text)."""

import logging
from dataclasses import dataclass, field

import fitz  # PyMuPDF

from app.services.pdf_hidden_text import HiddenSpan, analyze_page

logger = logging.getLogger("app.services.pdf")

# A page with fewer than this many extracted characters is treated as scanned/image-only
# and routed to OCR. This is a heuristic, not perfect detection — hybrid text+image pages
# with sparse text may be false positives; `force_ocr` on upload is the escape hatch.
MIN_TEXT_CHARS_PER_PAGE = 20


@dataclass
class PageContent:
    page_number: int  # 1-indexed
    text: str  # visible text only; hidden spans are removed
    is_scanned: bool
    hidden_spans: list[HiddenSpan] = field(default_factory=list)


def extract_pages(pdf_bytes: bytes) -> list[PageContent]:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return extract_pages_from_document(doc)


def extract_pages_from_document(doc: "fitz.Document") -> list[PageContent]:
    pages: list[PageContent] = []
    for i, page in enumerate(doc):
        text, hidden = analyze_page(page, i + 1)
        is_scanned = len(text) < MIN_TEXT_CHARS_PER_PAGE
        pages.append(PageContent(page_number=i + 1, text=text, is_scanned=is_scanned, hidden_spans=hidden))
    return pages


def render_page_to_png(pdf_bytes: bytes, page_number: int, zoom: float = 2.0) -> bytes:
    """Render a page (1-indexed) to PNG bytes, used as OCR input for scanned pages."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return render_document_page(doc, page_number, zoom)


def render_document_page(doc: "fitz.Document", page_number: int, zoom: float = 2.0) -> bytes:
    page = doc[page_number - 1]
    pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    return pixmap.tobytes("png")
