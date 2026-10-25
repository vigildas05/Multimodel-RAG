"""End-to-end API tests via FastAPI's TestClient, against a live Qdrant instance.
Skipped automatically if Qdrant isn't reachable. Ollama is not required for these
tests since only non-generation endpoints are exercised."""

import io

import fitz
import pytest
from fastapi.testclient import TestClient

from app.services.vector_store_service import VectorStoreService


def _qdrant_available() -> bool:
    try:
        VectorStoreService("localhost", 6333, "test_api_documents_probe", 384)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _qdrant_available(), reason="Qdrant is not running on localhost:6333")


def _make_test_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Photosynthesis converts light energy into chemical energy in plants.")
    return doc.tobytes()


@pytest.fixture(scope="module")
def client(db_engine):  # db_engine: migrated Postgres test database
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c
        c.post("/documents/reset")


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reject_invalid_file_type(client):
    resp = client.post("/documents/upload", files={"file": ("virus.exe", b"not a real file", "application/octet-stream")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_type"] == "invalid_file_type"


def test_upload_list_search_delete_pdf(client):
    pdf_bytes = _make_test_pdf_bytes()
    upload_resp = client.post("/documents/upload", files={"file": ("bio.pdf", io.BytesIO(pdf_bytes), "application/pdf")})
    assert upload_resp.status_code == 200
    document = upload_resp.json()["document"]
    assert document["status"] == "ready"
    assert document["num_chunks"] >= 1
    document_id = document["document_id"]

    list_resp = client.get("/documents")
    assert any(d["document_id"] == document_id for d in list_resp.json()["documents"])

    chunks_resp = client.get(f"/documents/{document_id}/chunks")
    assert chunks_resp.status_code == 200
    assert len(chunks_resp.json()["chunks"]) == document["num_chunks"]

    search_resp = client.post("/search", json={"query": "What does photosynthesis convert?"})
    assert search_resp.status_code == 200
    assert "energy" in search_resp.json()["chunks"][0]["text"].lower()

    delete_resp = client.delete(f"/documents/{document_id}")
    assert delete_resp.status_code == 200

    list_after = client.get("/documents")
    assert not any(d["document_id"] == document_id for d in list_after.json()["documents"])


def test_delete_nonexistent_document_returns_404(client):
    resp = client.delete("/documents/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error_type"] == "document_not_found"


def test_search_with_no_documents_returns_404(client):
    client.post("/documents/reset")
    resp = client.post("/search", json={"query": "anything at all"})
    assert resp.status_code == 404
    assert resp.json()["error_type"] == "no_relevant_results"
