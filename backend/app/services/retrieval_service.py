"""Core retrieval orchestration: dense search (+ optional BM25 hybrid via Reciprocal
Rank Fusion) -> optional similarity threshold -> optional cross-encoder reranking.

Retrieval favors recall (cast a wide net with top_k candidates); reranking favors
precision (cut that pool down to the few chunks actually worth sending to the LLM).
Conflating the two — e.g. sending all retrieval_top_k chunks straight to the LLM —
wastes context window on marginally-relevant text and measurably hurts answer quality.
"""

import logging
from dataclasses import dataclass, field

from app.core.exceptions import NoRelevantResultsError
from app.services.bm25_service import BM25Service
from app.services.embedding_service import EmbeddingService
from app.services.reranking_service import RerankingService
from app.services.vector_store_service import VectorStoreService

logger = logging.getLogger("app.services.retrieval")


@dataclass
class RetrievalSettings:
    retrieval_top_k: int = 10
    final_context_chunks: int = 4
    use_hybrid_search: bool = True
    use_reranking: bool = True
    similarity_threshold: float | None = None
    document_ids: list[str] | None = None
    file_types: list[str] | None = None


@dataclass
class RetrievalMetadata:
    top_k: int
    hybrid: bool
    reranked: bool
    dense_candidate_count: int
    bm25_candidate_count: int
    fused_candidate_count: int
    final_chunk_count: int
    rerank_top_n: int = field(default=0)


@dataclass
class RetrievalResult:
    chunks: list[dict]
    metadata: RetrievalMetadata


class RetrievalService:
    def __init__(
        self,
        embedding_service: EmbeddingService,
        vector_store: VectorStoreService,
        bm25_service: BM25Service,
        reranking_service: RerankingService,
        rrf_k: int = 60,
    ):
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._bm25_service = bm25_service
        self._reranking_service = reranking_service
        self._rrf_k = rrf_k

    def retrieve(self, query: str, settings: RetrievalSettings) -> RetrievalResult:
        query_vector = self._embedding_service.embed_query(query)

        candidate_pool_size = (
            max(settings.retrieval_top_k * 2, 20) if settings.use_reranking else settings.retrieval_top_k
        )

        dense_results = self._vector_store.search(
            query_vector=query_vector.tolist(),
            top_k=candidate_pool_size,
            document_ids=settings.document_ids,
            file_types=settings.file_types,
        )

        bm25_results: list[dict] = []
        if settings.use_hybrid_search:
            bm25_results = self._bm25_service.search(query, top_k=candidate_pool_size)
            if settings.document_ids:
                bm25_results = [r for r in bm25_results if r["document_id"] in settings.document_ids]
            if settings.file_types:
                bm25_results = [r for r in bm25_results if r["file_type"] in settings.file_types]

        if settings.use_hybrid_search and bm25_results:
            fused = self._reciprocal_rank_fusion(dense_results, bm25_results)
        else:
            fused = [{**r, "dense_score": r["score"]} for r in dense_results]

        if settings.similarity_threshold is not None:
            fused = [c for c in fused if c.get("dense_score", 0.0) >= settings.similarity_threshold]

        fused = fused[:candidate_pool_size]

        reranked = settings.use_reranking
        if settings.use_reranking and fused:
            final_chunks = self._reranking_service.rerank(query, fused, top_n=settings.final_context_chunks)
        else:
            final_chunks = fused[: settings.final_context_chunks]

        if not final_chunks:
            raise NoRelevantResultsError(
                "No relevant content was found in the knowledge base for this query.",
                detail="Try rephrasing the question, uploading relevant documents, or lowering the similarity threshold.",
            )

        metadata = RetrievalMetadata(
            top_k=settings.retrieval_top_k,
            hybrid=settings.use_hybrid_search,
            reranked=reranked,
            dense_candidate_count=len(dense_results),
            bm25_candidate_count=len(bm25_results),
            fused_candidate_count=len(fused),
            final_chunk_count=len(final_chunks),
            rerank_top_n=settings.final_context_chunks if reranked else 0,
        )
        return RetrievalResult(chunks=final_chunks, metadata=metadata)

    def _reciprocal_rank_fusion(self, dense_results: list[dict], bm25_results: list[dict]) -> list[dict]:
        """Combine two ranked lists by rank position rather than raw score, since cosine
        similarity (0-1) and BM25 scores (unbounded) live on incomparable scales."""
        rrf_scores: dict[str, float] = {}
        chunk_by_id: dict[str, dict] = {}
        dense_score_by_id: dict[str, float] = {}

        for rank, result in enumerate(dense_results, start=1):
            cid = result["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (self._rrf_k + rank)
            chunk_by_id[cid] = result
            dense_score_by_id[cid] = result["score"]

        for rank, result in enumerate(bm25_results, start=1):
            cid = result["chunk_id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0.0) + 1.0 / (self._rrf_k + rank)
            chunk_by_id.setdefault(cid, result)

        fused = []
        for cid, rrf_score in rrf_scores.items():
            chunk = dict(chunk_by_id[cid])
            chunk["score"] = rrf_score
            chunk["rrf_score"] = rrf_score
            chunk["dense_score"] = dense_score_by_id.get(cid, 0.0)
            fused.append(chunk)

        fused.sort(key=lambda c: c["rrf_score"], reverse=True)
        return fused
