"""Singleton reranking service wrapping BAAI/bge-reranker-base.

Unlike the bi-encoder embedding model (which encodes query and chunk separately, so
similarity is a single dot product), a cross-encoder reranker feeds the (query, chunk)
pair through the model together, letting it attend across both texts jointly. That's
far more accurate for judging relevance but much more expensive per-pair — which is
exactly why it's used only to re-score a small candidate pool from retrieval, not the
whole corpus.
"""

import logging
import threading

from sentence_transformers import CrossEncoder

from app.core.exceptions import EmbeddingError

logger = logging.getLogger("app.services.reranking")


class RerankingService:
    _instance: "RerankingService | None" = None
    _lock = threading.Lock()

    def __init__(self, model_name: str, device: str = "cpu"):
        try:
            self._model = CrossEncoder(model_name, device=device)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError(f"Failed to load reranker model '{model_name}'.", detail=str(exc)) from exc

    @classmethod
    def get_instance(cls, model_name: str, device: str = "cpu") -> "RerankingService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(model_name, device)
        return cls._instance

    def rerank(self, query: str, candidates: list[dict], top_n: int) -> list[dict]:
        if not candidates:
            return []
        pairs = [(query, c["text"]) for c in candidates]
        try:
            scores = self._model.predict(pairs)
        except Exception as exc:  # noqa: BLE001
            raise EmbeddingError("Reranking failed.", detail=str(exc)) from exc

        for candidate, score in zip(candidates, scores, strict=False):
            candidate["rerank_score"] = float(score)

        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_n]
