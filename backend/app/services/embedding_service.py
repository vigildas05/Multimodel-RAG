"""Singleton embedding service wrapping BAAI/bge-small-en-v1.5.

The model is loaded exactly once (first access) and reused for every request —
re-instantiating a sentence-transformers model per request would reload ~130MB of
weights on every call, which is both slow and wasteful.
"""

import logging
import threading

import numpy as np
from sentence_transformers import SentenceTransformer

from app.core.exceptions import EmbeddingError

logger = logging.getLogger("app.services.embedding")

# bge models are trained to expect this instruction prefix on queries (not on the
# documents/chunks being indexed) — it measurably improves retrieval quality.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingService:
    _instance: "EmbeddingService | None" = None
    _lock = threading.Lock()

    def __init__(self, model_name: str, device: str = "cpu"):
        self._model_name = model_name
        try:
            self._model = SentenceTransformer(model_name, device=device)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Failed to load embedding model '{model_name}'.", detail=str(exc)) from exc

    @classmethod
    def get_instance(cls, model_name: str, device: str = "cpu") -> "EmbeddingService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(model_name, device)
        return cls._instance

    @property
    def dimension(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def embed_chunks(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """Embed document chunk texts (no query instruction prefix)."""
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)
        try:
            return self._model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError("Failed to generate chunk embeddings.", detail=str(exc)) from exc

    def embed_query(self, query: str) -> np.ndarray:
        try:
            vec = self._model.encode(
                BGE_QUERY_PREFIX + query,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            return vec
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError("Failed to generate query embedding.", detail=str(exc)) from exc
