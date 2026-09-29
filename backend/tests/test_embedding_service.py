import numpy as np
import pytest

from app.services.embedding_service import EmbeddingService


@pytest.fixture(scope="module")
def embedding_service():
    EmbeddingService._instance = None
    return EmbeddingService.get_instance("BAAI/bge-small-en-v1.5", device="cpu")


def test_singleton_returns_same_instance(embedding_service):
    other = EmbeddingService.get_instance("BAAI/bge-small-en-v1.5", device="cpu")
    assert other is embedding_service


def test_embed_chunks_shape(embedding_service):
    vectors = embedding_service.embed_chunks(["hello world", "another sentence"])
    assert vectors.shape == (2, 384)


def test_embed_query_shape(embedding_service):
    vector = embedding_service.embed_query("what is attention?")
    assert vector.shape == (384,)


def test_embeddings_are_normalized(embedding_service):
    vectors = embedding_service.embed_chunks(["some arbitrary text about transformers"])
    norm = np.linalg.norm(vectors[0])
    assert abs(norm - 1.0) < 1e-3


def test_similar_texts_have_higher_cosine_similarity(embedding_service):
    vectors = embedding_service.embed_chunks(
        [
            "The cat sat on the mat.",
            "A feline rested on the rug.",
            "Quarterly revenue increased by 18 percent.",
        ]
    )
    sim_related = np.dot(vectors[0], vectors[1])
    sim_unrelated = np.dot(vectors[0], vectors[2])
    assert sim_related > sim_unrelated


def test_empty_input_returns_empty_array(embedding_service):
    vectors = embedding_service.embed_chunks([])
    assert vectors.shape == (0, 384)
