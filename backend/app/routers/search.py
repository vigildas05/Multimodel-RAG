from fastapi import APIRouter, Depends

from app.dependencies import get_retrieval_service
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.services.retrieval_service import RetrievalService, RetrievalSettings

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
) -> SearchResponse:
    s = request.settings
    retrieval_settings = RetrievalSettings(
        retrieval_top_k=s.retrieval_top_k,
        final_context_chunks=s.final_context_chunks,
        use_hybrid_search=s.use_hybrid_search,
        use_reranking=s.use_reranking,
        similarity_threshold=s.similarity_threshold,
        document_ids=s.document_ids,
    )
    result = retrieval_service.retrieve(request.query, retrieval_settings)

    chunks = [
        RetrievedChunk(
            chunk_id=c.get("chunk_id", ""),
            document=c.get("filename", "unknown"),
            file_type=c.get("file_type", "unknown"),
            text=c.get("text", ""),
            score=c.get("score", 0.0),
            rerank_score=c.get("rerank_score"),
            page=c.get("page_number"),
            audio_start_time=c.get("audio_start_time"),
            audio_end_time=c.get("audio_end_time"),
        )
        for c in result.chunks
    ]

    return SearchResponse(
        chunks=chunks,
        top_k=result.metadata.top_k,
        reranked=result.metadata.reranked,
        hybrid=result.metadata.hybrid,
    )
