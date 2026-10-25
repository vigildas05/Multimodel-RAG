from starlette.concurrency import run_in_threadpool

from app.core.exceptions import DocumentNotFoundError
from app.schemas.settings import RetrievalSettingsSchema
from app.services.retrieval_service import RetrievalSettings
from app.storage.document_registry import DocumentRegistry


async def build_retrieval_settings(s: RetrievalSettingsSchema, registry: DocumentRegistry) -> RetrievalSettings:
    """Translate request settings, rejecting any document_ids outside the caller's workspace.

    Qdrant would filter foreign IDs out anyway (the workspace filter is always applied);
    checking here as well turns a guessed ID into a clean 404 instead of a silent empty
    result, and 404 rather than 403 so it doesn't confirm the ID exists elsewhere.
    """
    if s.document_ids:
        owned = await run_in_threadpool(registry.owned_ids, s.document_ids)
        if set(s.document_ids) - owned:
            raise DocumentNotFoundError("One or more of the selected documents were not found.")
    return RetrievalSettings(
        retrieval_top_k=s.retrieval_top_k,
        final_context_chunks=s.final_context_chunks,
        use_hybrid_search=s.use_hybrid_search,
        use_reranking=s.use_reranking,
        similarity_threshold=s.similarity_threshold,
        document_ids=s.document_ids,
    )
