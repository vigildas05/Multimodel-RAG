"""Builds the service graph once, for both the API (main.py lifespan) and operator
commands (app.cli), so they can't drift apart."""

from dataclasses import dataclass, fields

from sqlalchemy.orm import Session, sessionmaker

from app.auth.service import AuthService
from app.auth.tokens import resolve_jwt_secret
from app.config import Settings
from app.services.bm25_service import BM25Service
from app.services.chunking_service import RecursiveChunker
from app.services.demo_service import DemoService
from app.services.document_service import DocumentService
from app.services.embedding_service import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.llm.factory import get_llm_provider
from app.services.ocr_service import OCRService
from app.services.quota_service import QuotaService
from app.services.reranking_service import RerankingService
from app.services.retrieval_service import RetrievalService
from app.services.vector_store_service import VectorStoreService
from app.services.whisper_service import WhisperService
from app.storage.workspace_repository import WorkspaceRepository


@dataclass
class Services:
    session_factory: sessionmaker[Session]
    auth_service: AuthService
    quota_service: QuotaService
    embedding_service: EmbeddingService
    vector_store: VectorStoreService
    bm25_service: BM25Service
    reranking_service: RerankingService
    ocr_service: OCRService
    whisper_service: WhisperService
    llm_provider: LLMProvider
    retrieval_service: RetrievalService
    document_service: DocumentService
    demo_service: DemoService

    def attach_to(self, state) -> None:
        """Expose every service as an attribute (e.g. FastAPI's app.state)."""
        for f in fields(self):
            setattr(state, f.name, getattr(self, f.name))


def build_services(settings: Settings, session_factory: sessionmaker[Session]) -> Services:
    auth_service = AuthService(session_factory, settings, resolve_jwt_secret(settings))
    embedding_service = EmbeddingService.get_instance(settings.EMBEDDING_MODEL_NAME, settings.EMBEDDING_DEVICE)
    vector_store = VectorStoreService(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        collection_name=settings.QDRANT_COLLECTION,
        vector_dim=settings.EMBEDDING_DIM,
    )
    bm25_service = BM25Service()
    reranking_service = RerankingService.get_instance(settings.RERANKER_MODEL_NAME, settings.RERANKER_DEVICE)
    ocr_service = OCRService.get_instance()
    whisper_service = WhisperService.get_instance(
        settings.WHISPER_MODEL_SIZE, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE
    )
    llm_provider = get_llm_provider(settings)

    return Services(
        session_factory=session_factory,
        auth_service=auth_service,
        quota_service=QuotaService(session_factory, settings),
        embedding_service=embedding_service,
        vector_store=vector_store,
        bm25_service=bm25_service,
        reranking_service=reranking_service,
        ocr_service=ocr_service,
        whisper_service=whisper_service,
        llm_provider=llm_provider,
        retrieval_service=RetrievalService(
            embedding_service=embedding_service,
            vector_store=vector_store,
            bm25_service=bm25_service,
            reranking_service=reranking_service,
            corpus_version=WorkspaceRepository(session_factory).get_corpus_version,
            rrf_k=settings.RRF_K,
        ),
        document_service=DocumentService(
            session_factory=session_factory,
            embedding_service=embedding_service,
            vector_store=vector_store,
            ocr_service=ocr_service,
            whisper_service=whisper_service,
            llm_provider=llm_provider,
            chunker=RecursiveChunker(),
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
        ),
        demo_service=DemoService(session_factory, vector_store, auth_service, settings),
    )
