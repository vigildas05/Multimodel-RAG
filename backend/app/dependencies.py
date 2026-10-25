"""FastAPI dependency providers. All heavy services are created once at app startup
(see main.py's lifespan) and stored on app.state; these Depends() functions just
fetch them from the current request, so nothing is ever re-instantiated per request.
"""

from fastapi import Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.config import Settings, get_settings
from app.services.bm25_service import BM25Service
from app.services.demo_service import DemoService
from app.services.document_service import DocumentService
from app.services.embedding_service import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.ocr_service import OCRService
from app.services.quota_service import QuotaService, QuotaTicket
from app.services.reranking_service import RerankingService
from app.services.retrieval_service import RetrievalService
from app.services.vector_store_service import VectorStoreService
from app.services.whisper_service import WhisperService
from app.storage.document_registry import DocumentRegistry


def get_app_settings() -> Settings:
    return get_settings()


def get_registry(request: Request, user: AuthenticatedUser = Depends(get_current_user)) -> DocumentRegistry:
    """Cheap per-request object: scopes every document lookup to the caller's workspace."""
    return DocumentRegistry(request.app.state.session_factory, user.workspace_id)


def get_embedding_service(request: Request) -> EmbeddingService:
    return request.app.state.embedding_service


def get_vector_store(request: Request) -> VectorStoreService:
    return request.app.state.vector_store


def get_bm25_service(request: Request) -> BM25Service:
    return request.app.state.bm25_service


def get_reranking_service(request: Request) -> RerankingService:
    return request.app.state.reranking_service


def get_ocr_service(request: Request) -> OCRService:
    return request.app.state.ocr_service


def get_whisper_service(request: Request) -> WhisperService:
    return request.app.state.whisper_service


def get_llm_provider(request: Request) -> LLMProvider:
    return request.app.state.llm_provider


def get_retrieval_service(request: Request) -> RetrievalService:
    return request.app.state.retrieval_service


def get_document_service(request: Request) -> DocumentService:
    return request.app.state.document_service


def get_quota_service(request: Request) -> QuotaService:
    return request.app.state.quota_service


def get_demo_service(request: Request) -> DemoService:
    return request.app.state.demo_service


async def consume_query_quota(
    user: AuthenticatedUser = Depends(get_current_user),
    quota: QuotaService = Depends(get_quota_service),
):
    """Charge one query up front (atomically); refund it if the request then fails, so
    errors like "no relevant results" or an LLM outage don't eat into the quota."""
    ticket: QuotaTicket = await run_in_threadpool(quota.consume, user)
    try:
        yield ticket
    except Exception:
        await run_in_threadpool(quota.refund, user.user_id, ticket.day)
        raise
