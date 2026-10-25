import logging
import time

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.config import Settings
from app.core.exceptions import InputBlockedError, ResponseBlockedError
from app.core.rate_limit import limit_by_user
from app.db.audit import record_security_event
from app.dependencies import consume_query_quota, get_app_settings, get_llm_provider, get_registry, get_retrieval_service
from app.routers.retrieval_scope import build_retrieval_settings
from app.schemas.chat import ChatRequest, ChatResponse, RetrievalInfo, SecurityNotes, SourceCitation
from app.services.llm.base import LLMProvider
from app.services.prompt_builder import to_source_blocks
from app.services.query_log_service import QueryTimings
from app.services.retrieval_service import RetrievalService
from app.storage.document_registry import DocumentRegistry
from security.gateway import SecurityGateway
from security.types import Decision

logger = logging.getLogger("app.routers.chat")
router = APIRouter(tags=["chat"], dependencies=[Depends(get_current_user)])


@router.post(
    "/chat",
    response_model=ChatResponse,
    # Rate limit first: a throttled request must not consume (or touch) the daily quota.
    dependencies=[Depends(limit_by_user("chat_user")), Depends(consume_query_quota)],
)
async def chat(
    request: ChatRequest,
    http_request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    registry: DocumentRegistry = Depends(get_registry),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    llm_provider: LLMProvider = Depends(get_llm_provider),
    settings: Settings = Depends(get_app_settings),
) -> ChatResponse:
    state = http_request.app.state
    gateway: SecurityGateway = state.security_gateway
    started = time.perf_counter()
    timings = QueryTimings()

    # --- Layer 1: input guard ---
    scan = await run_in_threadpool(gateway.scan_input, request.question)
    timings.security_ms += scan.latency_ms
    if scan.decision != Decision.ALLOW:
        await run_in_threadpool(_record_input_event, state, user, request.question, scan)
    if scan.decision == Decision.BLOCK:
        timings.total_ms = (time.perf_counter() - started) * 1000
        await run_in_threadpool(state.query_log_service.log, user, request.question, blocked=True, timings=timings)
        raise InputBlockedError(
            "This question was blocked by the security filter: it looks like an attempt to override "
            "the assistant's instructions or extract its configuration. Please rephrase it as a question "
            "about your documents."
        )

    s = request.settings
    retrieval_settings = await build_retrieval_settings(s, registry)
    t0 = time.perf_counter()
    result = await run_in_threadpool(retrieval_service.retrieve, user.scope, request.question, retrieval_settings)
    timings.retrieval_ms = (time.perf_counter() - t0) * 1000

    # --- Layer 3: spotlighted prompt with instruction hierarchy and canary ---
    bundle = gateway.build_prompt(request.question, to_source_blocks(result.chunks))
    t0 = time.perf_counter()
    generation = await llm_provider.generate(
        system_prompt=bundle.system_prompt,
        user_prompt=bundle.user_prompt,
        temperature=s.temperature,
        top_p=s.top_p,
        max_output_tokens=s.max_output_tokens,
    )
    timings.generation_ms = (time.perf_counter() - t0) * 1000

    # --- Layer 4: output guard ---
    output = gateway.scan_output(generation.text, bundle)
    timings.security_ms += output.latency_ms
    timings.total_ms = (time.perf_counter() - started) * 1000
    await run_in_threadpool(_record_output_events, state, user, output)
    await run_in_threadpool(
        state.query_log_service.log,
        user,
        request.question,
        blocked=output.blocked,
        timings=timings,
        num_chunks=len(result.chunks),
        usage=generation.raw_usage,
        llm_provider=llm_provider.provider_name,
        llm_model=llm_provider.model_name,
    )
    if output.blocked:
        raise ResponseBlockedError(
            "The answer was withheld because it appeared to disclose the assistant's internal "
            "instructions. This can happen when a question or document tries to extract them."
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
        answer=output.text,
        sources=sources,
        retrieval=RetrievalInfo(
            top_k=result.metadata.top_k,
            reranked=result.metadata.reranked,
            hybrid=result.metadata.hybrid,
            reranked_chunks=result.metadata.final_chunk_count,
            dense_candidate_count=result.metadata.dense_candidate_count,
            bm25_candidate_count=result.metadata.bm25_candidate_count,
        ),
        security=SecurityNotes(
            input_flagged=scan.decision == Decision.FLAG,
            pii_redactions=output.pii_redactions,
            removed_citations=output.invalid_citations,
            removed_images=output.removed_images,
        ),
    )


def _record_input_event(state, user: AuthenticatedUser, question: str, scan) -> None:
    blocked = scan.decision == Decision.BLOCK
    record_security_event(
        state.session_factory,
        "input_blocked" if blocked else "input_flagged",
        "input_guard",
        severity="high" if blocked else "low",
        workspace_id=user.workspace_id,
        user_id=user.user_id,
        risk_score=scan.risk,
        # The question is kept (truncated) so admins can review blocks and false positives.
        details={**scan.summary(), "question": question[:500]},
    )


def _record_output_events(state, user: AuthenticatedUser, output) -> None:
    common = {"workspace_id": user.workspace_id, "user_id": user.user_id}
    if output.blocked:
        record_security_event(
            state.session_factory, "prompt_leak", "output_guard", severity="critical",
            details={"reasons": output.leak_reasons}, **common,
        )
        return
    if output.pii_redactions:
        # Counts only: the redacted values themselves are never logged.
        record_security_event(
            state.session_factory, "pii_redacted", "output_guard", severity="info",
            details={"counts": output.pii_redactions}, **common,
        )
    if output.invalid_citations or output.removed_images:
        record_security_event(
            state.session_factory, "output_sanitized", "output_guard", severity="low",
            details={"removed_citations": output.invalid_citations, "removed_images": output.removed_images},
            **common,
        )
