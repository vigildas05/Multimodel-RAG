"""PDF text extraction via PyMuPDF, with a heuristic scanned-page detector that
routes low-text pages to OCR."""

import logging
from dataclasses import dataclass

import fitz  # PyMuPDF

logger = logging.getLogger("app.services.pdf")

# A page with fewer than this many extracted characters is treated as scanned/image-only
# and routed to OCR. This is a heuristic, not perfect detection — hybrid text+image pages
# with sparse text may be false positives; `force_ocr` on upload is the escape hatch.
MIN_TEXT_CHARS_PER_PAGE = 20


@dataclass
class PageContent:
    page_number: int  # 1-indexed
    text: str
    is_scanned: bool


def extract_pages(pdf_bytes: bytes) -> list[PageContent]:
    pages: list[PageContent] = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            is_scanned = len(text) < MIN_TEXT_CHARS_PER_PAGE
            pages.append(PageContent(page_number=i + 1, text=text, is_scanned=is_scanned))
    return pages


def render_page_to_png(pdf_bytes: bytes, page_number: int, zoom: float = 2.0) -> bytes:
    """Render a page (1-indexed) to PNG bytes, used as OCR input for scanned pages."""
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[page_number - 1]
        matrix = fitz.Matrix(zoom, zoom)
        pixmap = page.get_pixmap(matrix=matrix)
        return pixmap.tobytes("png")
