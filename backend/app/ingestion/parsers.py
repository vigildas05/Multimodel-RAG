"""Parsers for untrusted files. These run INSIDE the sandbox child process (see
sandbox.py): they open the raw file, enforce structural limits, and return plain,
picklable data. Model inference (OCR, Whisper, embeddings) happens afterwards in the
worker, on data these functions have already vetted.
"""

from dataclasses import dataclass, field

from app.ingestion.sandbox import ParseRejected
from app.services.pdf_service import PageContent


@dataclass
class ParsedPdf:
    pages: list[PageContent]
    # PNG renders of the pages that need OCR (scanned pages, or all pages with force_ocr).
    page_images: dict[int, bytes] = field(default_factory=dict)


@dataclass
class ImageInfo:
    width: int
    height: int
    format: str


def parse_pdf(path: str, max_pages: int, force_ocr: bool, render_zoom: float = 2.0) -> ParsedPdf:
    import fitz

    from app.services import pdf_service

    try:
        doc = fitz.open(path, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ParseRejected("This PDF could not be opened. It may be corrupted.") from exc
    with doc:
        if doc.needs_pass:
            raise ParseRejected("Password-protected PDFs aren't supported.")
        if doc.page_count == 0:
            raise ParseRejected("This PDF has no pages.")
        if doc.page_count > max_pages:
            raise ParseRejected(f"This PDF has {doc.page_count} pages; the limit is {max_pages}.")

        pages = pdf_service.extract_pages_from_document(doc)
        images = {
            page.page_number: pdf_service.render_document_page(doc, page.page_number, render_zoom)
            for page in pages
            if force_ocr or page.is_scanned
        }
    return ParsedPdf(pages=pages, page_images=images)


def inspect_image(path: str, max_pixels: int) -> ImageInfo:
    import warnings

    from PIL import Image

    # Treat oversized images as errors before any pixel data is decoded (decompression bombs).
    Image.MAX_IMAGE_PIXELS = max_pixels
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    try:
        with Image.open(path) as img:
            width, height, fmt = img.width, img.height, img.format
            if width * height > max_pixels:
                raise ParseRejected(f"This image is {width}x{height} pixels; the limit is {max_pixels:,} pixels.")
            img.verify()  # structural check
        with Image.open(path) as img:
            img.load()  # full decode, inside the sandbox
    except ParseRejected:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ParseRejected(f"This image exceeds the limit of {max_pixels:,} pixels.") from exc
    except Exception as exc:  # noqa: BLE001
        raise ParseRejected("This image could not be read. It may be corrupted.") from exc
    return ImageInfo(width=width, height=height, format=fmt or "")


def probe_audio(path: str, max_seconds: float) -> float:
    """Return the audio duration in seconds, refusing files longer than `max_seconds`."""
    import av

    try:
        container = av.open(path)
    except Exception as exc:  # noqa: BLE001
        raise ParseRejected("This audio file could not be read. It may be corrupted.") from exc
    with container:
        streams = container.streams.audio
        if not streams:
            raise ParseRejected("This file contains no audio track.")
        stream = streams[0]
        duration = None
        if container.duration:
            duration = container.duration / av.time_base
        elif stream.duration and stream.time_base:
            duration = float(stream.duration * stream.time_base)
        if duration is None:
            # No header duration: decode, but stop as soon as the limit is passed.
            duration = 0.0
            for frame in container.decode(stream):
                duration = float(frame.time or 0.0) + (frame.samples / frame.sample_rate if frame.sample_rate else 0)
                if duration > max_seconds:
                    break
    if duration > max_seconds:
        raise ParseRejected(
            f"This audio is {duration / 60:.1f} minutes long; the limit is {max_seconds / 60:.0f} minutes."
        )
    return duration
