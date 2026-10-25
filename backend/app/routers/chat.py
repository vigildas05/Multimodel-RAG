import logging

from fastapi import APIRouter, Depends
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.config import Settings
from app.dependencies import consume_query_quota, get_app_settings, get_llm_provider, get_registry, get_retrieval_service
from app.routers.retrieval_scope import build_retrieval_settings
from app.schemas.chat import ChatRequest, ChatResponse, RetrievalInfo, SourceCitation
from app.services.llm.base import LLMProvider
from app.services.prompt_builder import SYSTEM_PROMPT, build_user_prompt
from app.services.retrieval_service import RetrievalService
from app.storage.document_registry import DocumentRegistry

logger = logging.getLogger("app.routers.chat")
router = APIRouter(tags=["chat"], dependencies=[Depends(get_current_user)])


@router.post("/chat", response_model=ChatResponse, dependencies=[Depends(consume_query_quota)])
async def chat(
    request: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    registry: DocumentRegistry = Depends(get_registry),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: Settings = Depends(get_app_settings),
) -> ChatResponse:
    s = request.settings
    retrieval_settings = await build_retrieval_settings(s, registry)
    result = await run_in_threadpool(retrieval_service.retrieve, user.scope, request.question, retrieval_settings)

    user_prompt = build_user_prompt(request.question, result.chunks)
    generation = await llm_provider.generate(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=s.temperature,
        top_p=s.top_p,
        max_output_tokens=s.max_output_tokens,
    )

    sources = [
        SourceCitation(
            index=i,
            document=chunk.get("filename", "unknown"),
            file_type=chunk.get("file_type", "unknown"),
            page=chunk.get("page_number"),
            audio_start_time=chunk.get("audio_start_time"),
            audio_end_time=chunk.get("audio_end_time"),
            chunk_id=chunk.get("chunk_id", ""),
            score=chunk.get("rerank_score", chunk.get("score", 0.0)),
            text_snippet=chunk.get("text", "")[:240],
        )
        for i, chunk in enumerate(result.chunks, start=1)
    ]

    return ChatResponse(
        answer=generation.text,
        sources=sources,
        retrieval=RetrievalInfo(
            top_k=result.metadata.top_k,
            reranked=result.metadata.reranked,
            hybrid=result.metadata.hybrid,
            reranked_chunks=result.metadata.final_chunk_count,
            dense_candidate_count=result.metadata.dense_candidate_count,
            bm25_candidate_count=result.metadata.bm25_candidate_count,
        ),
    )
