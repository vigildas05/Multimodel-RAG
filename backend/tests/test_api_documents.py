"""End-to-end API tests via FastAPI's TestClient, against live Postgres and Qdrant.
Skipped automatically if either isn't reachable. Ollama is not required for these
tests since only non-generation endpoints are exercised."""

import io
import uuid

import fitz
import pytest

from helpers import signup


def _make_test_pdf_bytes() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Photosynthesis converts light energy into chemical energy in plants.")
    return doc.tobytes()


@pytest.fixture
def user_client(client_factory):
    def _make():
        c = client_factory()
        signup(c)
        return c

    return _make


def _upload_pdf(client) -> dict:
    resp = client.post(
        "/documents/upload", files={"file": ("bio.pdf", io.BytesIO(_make_test_pdf_bytes()), "application/pdf")}
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["document"]


def test_health_check(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_reject_invalid_file_type(user_client):
    client = user_client()
    resp = client.post("/documents/upload", files={"file": ("virus.exe", b"not a real file", "application/octet-stream")})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error_type"] == "invalid_file_type"


def test_upload_list_search_delete_pdf(user_client):
    client = user_client()
    document = _upload_pdf(client)
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


def test_other_users_documents_are_invisible(user_client):
    """Document metadata is workspace-scoped. (Qdrant/BM25 search scoping is step 1.3.)"""
    owner, intruder = user_client(), user_client()
    document_id = _upload_pdf(owner)["document_id"]

    assert intruder.get("/documents").json()["documents"] == []
    assert intruder.get(f"/documents/{document_id}/chunks").status_code == 404
    assert intruder.delete(f"/documents/{document_id}").status_code == 404
    assert intruder.post("/documents/reset").json()["documents_removed"] == 0

    # The owner's document survived all of that.
    assert owner.get(f"/documents/{document_id}/chunks").status_code == 200
    owner.delete(f"/documents/{document_id}")


@pytest.mark.parametrize("bad_id", ["does-not-exist", str(uuid.uuid4())])
def test_delete_nonexistent_document_returns_404(user_client, bad_id):
    resp = user_client().delete(f"/documents/{bad_id}")
    assert resp.status_code == 404
    assert resp.json()["error_type"] == "document_not_found"


def test_search_with_no_documents_returns_404(user_client):
    client = user_client()
    client.post("/documents/reset")
    resp = client.post("/search", json={"query": "anything at all"})
    assert resp.status_code == 404
    assert resp.json()["error_type"] == "no_relevant_results"
