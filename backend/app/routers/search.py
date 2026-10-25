from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.dependencies import get_registry, get_retrieval_service
from app.routers.retrieval_scope import build_retrieval_settings
from app.schemas.search import RetrievedChunk, SearchRequest, SearchResponse
from app.services.retrieval_service import RetrievalService
from app.storage.document_registry import DocumentRegistry

router = APIRouter(tags=["search"], dependencies=[Depends(get_current_user)])


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    registry: DocumentRegistry = Depends(get_registry),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
) -> SearchResponse:
    s = request.settings
    retrieval_settings = await build_retrieval_settings(s, registry)
    result = await run_in_threadpool(retrieval_service.retrieve, user.scope, request.query, retrieval_settings)

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
