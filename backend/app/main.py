import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.core.exception_handlers import register_exception_handlers
from app.core.exceptions import DatabaseUnavailableError
from app.core.logging_config import configure_logging
from app.db.bootstrap import ensure_local_workspace
from app.db.session import create_db_engine, create_session_factory
from app.dependencies import get_app_settings
from app.routers import audio, chat, documents, images, search
from app.services.bm25_service import BM25Service
from app.services.chunking_service import RecursiveChunker
from app.services.document_service import DocumentService
from app.services.embedding_service import EmbeddingService
from app.services.llm.factory import get_llm_provider
from app.services.ocr_service import OCRService
from app.services.reranking_service import RerankingService
from app.services.retrieval_service import RetrievalService
from app.services.vector_store_service import VectorStoreService
from app.services.whisper_service import WhisperService
from app.storage.document_registry import DocumentRegistry

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings = get_settings()
    logger.info("Starting up: loading models and connecting to services...")

    app.state.db_engine = create_db_engine(settings)
    app.state.session_factory = create_session_factory(app.state.db_engine)
    try:
        # TEMPORARY until step 1.3: everything runs in one local workspace (see db/bootstrap.py).
        local_workspace_id = ensure_local_workspace(app.state.session_factory)
    except SQLAlchemyError as exc:
        raise DatabaseUnavailableError(
            "Could not initialize the database. Is Postgres running and migrated?",
            detail="Run `docker compose up -d postgres` and `alembic upgrade head` from backend/.",
        ) from exc
    app.state.registry = DocumentRegistry(app.state.session_factory, local_workspace_id)
    app.state.embedding_service = EmbeddingService.get_instance(
        settings.EMBEDDING_MODEL_NAME, settings.EMBEDDING_DEVICE
    )
    app.state.vector_store = VectorStoreService(
        host=settings.QDRANT_HOST,
        port=settings.QDRANT_PORT,
        collection_name=settings.QDRANT_COLLECTION,
        vector_dim=settings.EMBEDDING_DIM,
    )
    app.state.bm25_service = BM25Service()
    app.state.reranking_service = RerankingService.get_instance(
        settings.RERANKER_MODEL_NAME, settings.RERANKER_DEVICE
    )
    app.state.ocr_service = OCRService.get_instance()
    app.state.whisper_service = WhisperService.get_instance(
        settings.WHISPER_MODEL_SIZE, settings.WHISPER_DEVICE, settings.WHISPER_COMPUTE_TYPE
    )
    app.state.llm_provider = get_llm_provider(settings)

    app.state.retrieval_service = RetrievalService(
        embedding_service=app.state.embedding_service,
        vector_store=app.state.vector_store,
        bm25_service=app.state.bm25_service,
        reranking_service=app.state.reranking_service,
        rrf_k=settings.RRF_K,
    )
    app.state.document_service = DocumentService(
        registry=app.state.registry,
        embedding_service=app.state.embedding_service,
        vector_store=app.state.vector_store,
        bm25_service=app.state.bm25_service,
        ocr_service=app.state.ocr_service,
        whisper_service=app.state.whisper_service,
        llm_provider=app.state.llm_provider,
        chunker=RecursiveChunker(),
        chunk_size=settings.CHUNK_SIZE,
        chunk_overlap=settings.CHUNK_OVERLAP,
    )

    # BM25 is in-memory only; rebuild it from Qdrant (the source of truth) on startup
    # so keyword search survives a backend restart.
    app.state.document_service.rebuild_bm25_from_store()

    logger.info("Startup complete.")
    yield
    logger.info("Shutting down.")
    app.state.db_engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Multimodal RAG Research Assistant", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    register_exception_handlers(app)

    app.include_router(documents.router)
    app.include_router(chat.router)
    app.include_router(search.router)
    app.include_router(audio.router)
    app.include_router(images.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
