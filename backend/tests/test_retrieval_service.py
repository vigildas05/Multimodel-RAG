"""Integration tests for the full retrieval orchestration (dense + hybrid + rerank)
against a live Qdrant instance."""

import uuid

import pytest

from app.core.tenancy import TenantScope
from app.models.chunk import Chunk
from app.services.bm25_service import BM25Service
from app.services.embedding_service import EmbeddingService
from app.services.reranking_service import RerankingService
from app.services.retrieval_service import RetrievalService, RetrievalSettings
from app.services.vector_store_service import VectorStoreService

TEST_COLLECTION = "test_retrieval_service"
SCOPE = TenantScope(workspace_id=str(uuid.uuid4()), user_id=str(uuid.uuid4()))


def _qdrant_available() -> bool:
    try:
        VectorStoreService("localhost", 6333, TEST_COLLECTION, 384)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _qdrant_available(), reason="Qdrant is not running on localhost:6333")

DOCS = [
    "The Eiffel Tower is located in Paris, France and was completed in 1889.",
    "Mount Everest is the tallest mountain above sea level, located in the Himalayas.",
    "Python is a popular programming language known for its readable syntax.",
    "The Great Wall of China stretches over 13,000 miles across northern China.",
]


@pytest.fixture(scope="module")
def embedder():
    EmbeddingService._instance = None
    return EmbeddingService.get_instance("BAAI/bge-small-en-v1.5", device="cpu")


@pytest.fixture(scope="module")
def reranker():
    RerankingService._instance = None
    return RerankingService.get_instance("BAAI/bge-reranker-base", device="cpu")


@pytest.fixture
def populated_service(embedder, reranker):
    store = VectorStoreService("localhost", 6333, TEST_COLLECTION, 384)
    store.reset_collection()

    doc_id = str(uuid.uuid4())
    vectors = embedder.embed_chunks(DOCS)
    chunks = [
        Chunk(
            chunk_id=f"{doc_id}_{i}",
            document_id=doc_id,
            filename="facts.pdf",
            file_type="pdf",
            text=text,
            chunk_index=i,
            upload_timestamp="2026-01-01T00:00:00Z",
            page_number=1,
            embedding=vec.tolist(),
        )
        for i, (text, vec) in enumerate(zip(DOCS, vectors, strict=False))
    ]
    store.upsert_chunks(SCOPE, chunks)

    service = RetrievalService(embedder, store, BM25Service(), reranker, corpus_version=lambda _: 0, rrf_k=60)
    yield service
    store.reset_collection()


def test_dense_only_retrieval_returns_most_relevant_chunk(populated_service):
    settings = RetrievalSettings(retrieval_top_k=4, final_context_chunks=1, use_hybrid_search=False, use_reranking=False)
    result = populated_service.retrieve(SCOPE, "Where is the Eiffel Tower?", settings)
    assert "Eiffel Tower" in result.chunks[0]["text"]


def test_reranking_improves_top_result(populated_service):
    settings = RetrievalSettings(retrieval_top_k=4, final_context_chunks=1, use_hybrid_search=True, use_reranking=True)
    result = populated_service.retrieve(SCOPE, "What programming language is known for readable syntax?", settings)
    assert "Python" in result.chunks[0]["text"]
    assert result.metadata.reranked is True


def test_final_context_chunks_limits_output_size(populated_service):
    settings = RetrievalSettings(retrieval_top_k=4, final_context_chunks=2, use_hybrid_search=True, use_reranking=True)
    result = populated_service.retrieve(SCOPE, "Tell me about famous landmarks", settings)
    assert len(result.chunks) <= 2


def test_metadata_reflects_settings(populated_service):
    settings = RetrievalSettings(retrieval_top_k=3, final_context_chunks=2, use_hybrid_search=False, use_reranking=False)
    result = populated_service.retrieve(SCOPE, "mountains", settings)
    assert result.metadata.top_k == 3
    assert result.metadata.hybrid is False
    assert result.metadata.reranked is False
