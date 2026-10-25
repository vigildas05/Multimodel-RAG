"""Upload security: content-based type detection, size/count limits in the API, and
structural limits enforced by the sandboxed parsers in the worker."""

import io
import struct
import time
import wave

import fitz
import pytest
from helpers import make_pdf, signup
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.core.exceptions import UploadRejectedError
from app.db.models import SecurityEvent
from app.ingestion.file_types import detect_file_type
from app.ingestion.sandbox import run_sandboxed
from app.ingestion.validation import validate_upload

# --- sample files ---------------------------------------------------------------------


def png_bytes(width=64, height=64, mode="RGB") -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (width, height)).save(buf, format="PNG")
    return buf.getvalue()


def jpeg_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(buf, format="JPEG")
    return buf.getvalue()


def wav_bytes(seconds: float, rate: int = 8000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(seconds * rate))
    return buf.getvalue()


def multi_page_pdf(pages: int) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 72), f"Page {i + 1} of a long report about soil chemistry.")
    return doc.tobytes()


def encrypted_pdf() -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Secret")
    return doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="owner")


def upload(client, name: str, data: bytes):
    return client.post("/documents/upload", files={"file": (name, io.BytesIO(data), "application/octet-stream")})


def rejection_events(session_factory) -> list[SecurityEvent]:
    with session_factory() as session:
        return list(session.scalars(select(SecurityEvent).where(SecurityEvent.event_type == "upload_rejected")))


# --- magic bytes (unit) -----------------------------------------------------------------


@pytest.mark.parametrize(
    "data,kind",
    [
        (make_pdf("x"), "pdf"),
        (png_bytes(), "image"),
        (jpeg_bytes(), "image"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image"),
        (wav_bytes(0.1), "audio"),
        (b"fLaC\x00\x00\x00\x22", "audio"),
        (b"OggS\x00\x02", "audio"),
        (b"ID3\x04\x00\x00", "audio"),
        (b"\x00\x00\x00\x20ftypM4A \x00\x00", "audio"),
    ],
)
def test_detects_supported_types(data, kind):
    assert detect_file_type(data[:16]).kind == kind


@pytest.mark.parametrize(
    "data",
    [
        b"MZ\x90\x00\x03\x00\x00\x00",  # Windows executable
        b"\x7fELF\x02\x01\x01",  # Linux executable
        b"PK\x03\x04\x14\x00",  # zip / docx
        b"<html><script>",  # HTML
        b"   %PDF-1.7",  # PDF signature not at offset 0
        b"RIFF\x00\x00\x00\x00AVI ",  # RIFF but video
        b"\x00\x00\x00\x20ftypqt  ",  # QuickTime video
    ],
)
def test_rejects_unsupported_signatures(data):
    assert detect_file_type(data) is None


@pytest.mark.parametrize(
    "name,data",
    [("photo.png", png_bytes()), ("photo.jpeg", jpeg_bytes()), ("talk.wav", wav_bytes(0.2)), ("paper.PDF", make_pdf("x"))],
)
def test_validate_accepts_matching_content(name, data):
    assert validate_upload(name, data, get_settings()) in {"pdf", "image", "audio"}


# --- API checks ---------------------------------------------------------------------------


def test_renamed_executable_is_rejected_and_logged(client, session_factory):
    signup(client)
    resp = upload(client, "quarterly-report.pdf", b"MZ\x90\x00" + b"\x00" * 200)
    assert resp.status_code == 422 and resp.json()["error_type"] == "upload_rejected"
    assert client.get("/documents").json()["documents"] == []
    [event] = rejection_events(session_factory)
    assert event.source == "upload" and event.details["filename"] == "quarterly-report.pdf"


@pytest.mark.parametrize(
    "name,data,label",
    [("notes.pdf", png_bytes(), "PNG image"), ("chart.png", make_pdf("x"), "PDF document"), ("song.mp3", jpeg_bytes(), "JPEG image")],
)
def test_extension_mismatch_is_rejected(client, name, data, label):
    signup(client)
    resp = upload(client, name, data)
    assert resp.status_code == 422
    assert label in resp.json()["message"]


def test_polyglot_with_leading_jpeg_header_is_rejected(client):
    signup(client)
    resp = upload(client, "invoice.pdf", jpeg_bytes() + make_pdf("hidden"))
    assert resp.status_code == 422


def test_empty_and_unknown_extension_files_are_rejected(client):
    signup(client)
    assert upload(client, "empty.pdf", b"").status_code == 422
    assert upload(client, "script.sh", b"#!/bin/sh\nrm -rf /").json()["error_type"] == "invalid_file_type"


def test_oversized_upload_is_rejected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_UPLOAD_SIZE_MB", 1)
    signup(client)
    resp = upload(client, "big.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10))
    assert resp.status_code == 413 and resp.json()["error_type"] == "file_too_large"


def test_documents_per_workspace_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_DOCUMENTS_PER_WORKSPACE", 1)
    signup(client)
    assert upload(client, "a.pdf", make_pdf("first")).status_code == 202
    resp = upload(client, "b.pdf", make_pdf("second"))
    assert resp.status_code == 429


def test_hostile_filename_is_only_display_metadata(app, client):
    user = signup(client)
    resp = upload(client, "../../../etc/passwd.pdf", make_pdf("fine"))
    assert resp.status_code == 202
    assert resp.json()["document"]["filename"] == "passwd.pdf"
    # Nothing was written outside the workspace's upload folder (and processed files are gone).
    root = app.state.upload_store.path(user["workspace_id"])
    assert not root.exists() or list(root.iterdir()) == []


# --- limits enforced by the sandboxed parsers --------------------------------------------


def _failed(client, name, data) -> dict:
    resp = upload(client, name, data)
    assert resp.status_code == 202, resp.text
    document = resp.json()["document"]
    assert document["status"] == "failed", document
    return document


def test_pdf_page_limit(client, session_factory, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_PDF_PAGES", 2)
    signup(client)
    document = _failed(client, "long.pdf", multi_page_pdf(3))
    assert "3 pages" in document["error_message"] and "2" in document["error_message"]
    assert any(e.source == "ingestion" for e in rejection_events(session_factory))


def test_password_protected_pdf_is_rejected(client):
    signup(client)
    assert "Password" in _failed(client, "locked.pdf", encrypted_pdf())["error_message"]


def test_corrupt_pdf_fails_cleanly(client):
    signup(client)
    document = _failed(client, "broken.pdf", b"%PDF-1.7\n" + b"\x00garbage\xff" * 50)
    assert "Traceback" not in document["error_message"]


def test_image_pixel_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_IMAGE_PIXELS", 10_000)
    signup(client)
    assert "pixels" in _failed(client, "wide.png", png_bytes(200, 200))["error_message"]


def test_decompression_bomb_is_refused_before_decoding(client):
    # 64 megapixels of a single colour compresses to a few kB: tiny upload, huge decode.
    bomb = png_bytes(8000, 8000, mode="L")
    assert len(bomb) < 200_000
    signup(client)
    assert "pixels" in _failed(client, "bomb.png", bomb)["error_message"]


def test_audio_duration_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "MAX_AUDIO_SECONDS", 1)
    signup(client)
    assert "limit" in _failed(client, "lecture.wav", wav_bytes(3))["error_message"]


# --- the sandbox itself -----------------------------------------------------------------


def test_sandbox_returns_results():
    assert run_sandboxed("math:sqrt", (16,), timeout_seconds=30) == 4.0


def test_sandbox_kills_a_hung_parser():
    started = time.monotonic()
    with pytest.raises(UploadRejectedError, match="too long"):
        run_sandboxed("time:sleep", (60,), timeout_seconds=2)
    assert time.monotonic() - started < 10


def test_sandbox_survives_a_crashing_parser():
    with pytest.raises(UploadRejectedError, match="could not be processed"):
        run_sandboxed("os:_exit", (3,), timeout_seconds=30)


def test_sandbox_hides_internal_errors_from_the_message():
    with pytest.raises(UploadRejectedError) as info:
        run_sandboxed("json:loads", ("{not json",), timeout_seconds=30)
    assert "JSONDecodeError" not in info.value.message
    assert "JSONDecodeError" in info.value.detail  # kept for server logs only
