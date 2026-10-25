import logging

from fastapi import APIRouter, Depends

from app.config import Settings
from app.auth.dependencies import get_current_user
from app.dependencies import get_app_settings, get_llm_provider, get_retrieval_service
from app.schemas.chat import ChatRequest, ChatResponse, RetrievalInfo, SourceCitation
from app.services.llm.base import LLMProvider
from app.services.prompt_builder import SYSTEM_PROMPT, build_user_prompt
from app.services.retrieval_service import RetrievalService, RetrievalSettings

logger = logging.getLogger("app.routers.chat")
router = APIRouter(tags=["chat"], dependencies=[Depends(get_current_user)])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: Settings = Depends(get_app_settings),
) -> ChatResponse:
    s = request.settings
    retrieval_settings = RetrievalSettings(
        retrieval_top_k=s.retrieval_top_k,
        final_context_chunks=s.final_context_chunks,
        use_hybrid_search=s.use_hybrid_search,
        use_reranking=s.use_reranking,
        similarity_threshold=s.similarity_threshold,
        document_ids=s.document_ids,
    )
    result = retrieval_service.retrieve(request.question, retrieval_settings)

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
