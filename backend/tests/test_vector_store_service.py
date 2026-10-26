"""Integration tests against a live Qdrant instance (docker compose up -d qdrant).
Skipped automatically if Qdrant isn't reachable."""

import uuid

import httpx
import pytest

from app.core.tenancy import TenantScope
from app.models.chunk import Chunk
from app.services.embedding_service import EmbeddingService
from app.services.vector_store_service import VectorStoreService

TEST_COLLECTION = "test_vector_store_service"
SCOPE = TenantScope(workspace_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))


def _qdrant_available() -> bool:
    try:
        VectorStoreService("localhost", 6333, TEST_COLLECTION, 384)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _qdrant_available(), reason="Qdrant is not running on localhost:6333")


def enable_strict_mode(collection: str) -> None:
    """Make local Qdrant behave like Qdrant Cloud, which refuses to filter on payload
    fields that have no index (a production 503 that local runs never showed)."""
    resp = httpx.patch(
        f"http://localhost:6333/collections/{collection}",
        json={"strict_mode_config": {"enabled": True, "unindexed_filtering_retrieve": False, "unindexed_filtering_update": False}},
    )
    resp.raise_for_status()


@pytest.fixture
def store():
    s = VectorStoreService("localhost", 6333, TEST_COLLECTION, 384)
    s.reset_collection()
    enable_strict_mode(TEST_COLLECTION)
    yield s
    s.reset_collection()


@pytest.fixture(scope="module")
def embedder():
    EmbeddingService._instance = None
    return EmbeddingService.get_instance("BAAI/bge-small-en-v1.5", device="cpu")


def _make_chunk(doc_id, idx, text, embedding, page=None):
    return Chunk(
        chunk_id=f"{doc_id}_{idx}",
        document_id=doc_id,
        filename="test.pdf",
        file_type="pdf",
        text=text,
        chunk_index=idx,
        upload_timestamp="2026-01-01T00:00:00Z",
        page_number=page,
        embedding=embedding.tolist(),
    )


def test_upsert_and_search(store, embedder):
    doc_id = str(uuid.uuid4())
    texts = ["The sky is blue.", "Paris is the capital of France."]
    vectors = embedder.embed_chunks(texts)
    chunks = [_make_chunk(doc_id, i, t, v, page=i + 1) for i, (t, v) in enumerate(zip(texts, vectors, strict=False))]
    store.upsert_chunks(SCOPE, chunks)

    query_vector = embedder.embed_query("What color is the sky?")
    results = store.search(SCOPE, query_vector.tolist(), top_k=2)

    assert len(results) == 2
    assert results[0]["text"] == "The sky is blue."


def test_delete_by_document_id(store, embedder):
    doc_id = str(uuid.uuid4())
    vectors = embedder.embed_chunks(["some content"])
    store.upsert_chunks(SCOPE, [_make_chunk(doc_id, 0, "some content", vectors[0])])

    assert len(store.get_chunks_by_document(SCOPE, doc_id)) == 1
    store.delete_by_document_id(SCOPE, doc_id)
    assert len(store.get_chunks_by_document(SCOPE, doc_id)) == 0


def test_filter_by_document_id(store, embedder):
    doc_a, doc_b = str(uuid.uuid4()), str(uuid.uuid4())
    vectors = embedder.embed_chunks(["alpha content", "beta content"])
    store.upsert_chunks(SCOPE, [_make_chunk(doc_a, 0, "alpha content", vectors[0])])
    store.upsert_chunks(SCOPE, [_make_chunk(doc_b, 0, "beta content", vectors[1])])

    query_vector = embedder.embed_query("content")
    results = store.search(SCOPE, query_vector.tolist(), top_k=10, document_ids=[doc_a])

    assert all(r["document_id"] == doc_a for r in results)


def test_every_filter_works_under_strict_mode(store, embedder):
    # Exercises each filtered field (workspace, document_id, file_type, quarantined)
    # through search, scroll and delete with strict mode on.
    doc_id = str(uuid.uuid4())
    vectors = embedder.embed_chunks(["gamma content"])
    store.upsert_chunks(SCOPE, [_make_chunk(doc_id, 0, "gamma content", vectors[0])])
    query = embedder.embed_query("gamma").tolist()
    assert store.search(SCOPE, query, top_k=5, document_ids=[doc_id], file_types=["pdf"])
    assert store.get_chunks_by_document(SCOPE, doc_id)
    store.delete_by_document_id(SCOPE, doc_id)
    assert not store.search(SCOPE, query, top_k=5)
