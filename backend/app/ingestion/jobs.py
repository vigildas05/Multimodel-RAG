"""Document processing: the job the arq worker runs, and how the API submits it.

The API never does heavy work itself. It stores the upload, records a `queued` document
and enqueues a job; the worker process (app.worker) parses, OCRs/transcribes, embeds and
indexes it, so a slow or malicious file can't tie up the API. INGEST_MODE=inline runs the
very same job inside the request instead (tests, or a quick local run without a worker).
"""

import asyncio
import logging

from arq import create_pool
from arq.connections import RedisSettings
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.core.exceptions import QueueUnavailableError
from app.core.tenancy import TenantScope
from app.storage.document_registry import DocumentRegistry

logger = logging.getLogger("app.ingestion.jobs")

INGEST_JOB_NAME = "ingest_document"


async def process_document(services, workspace_id: str, user_id: str, document_id: str, force_ocr: bool = False):
    """Process one queued document. Idempotent: a document that's gone or no longer queued
    (deleted, or already handled by an earlier attempt) is skipped."""
    scope = TenantScope(workspace_id=workspace_id, user_id=user_id)
    registry = DocumentRegistry(services.session_factory, workspace_id)
    record = await run_in_threadpool(registry.get, document_id)
    if record is None or record.status not in ("queued", "processing"):
        logger.info("Skipping document %s (missing or already processed)", document_id)
        return
    if not record.storage_path:
        await run_in_threadpool(registry.update_status, document_id, "failed", None, "The uploaded file is missing.")
        await _refund(services, scope, document_id)
        return

    await run_in_threadpool(registry.update_status, document_id, "processing")
    try:
        source = services.upload_store.path(record.storage_path)
        await services.document_service.ingest_file(
            scope, document_id, record.filename, record.file_type, source, force_ocr=force_ocr
        )
    except asyncio.CancelledError:
        # arq cancels the job when it exceeds INGEST_JOB_TIMEOUT_SECONDS.
        await run_in_threadpool(registry.update_status, document_id, "failed", None, "Processing timed out.")
        await _refund(services, scope, document_id)
        raise
    except Exception:  # noqa: BLE001
        # ingest_file already recorded a user-safe failure on the document.
        await _refund(services, scope, document_id)
    finally:
        await run_in_threadpool(services.upload_store.delete, record.storage_path)
        await run_in_threadpool(registry.clear_storage_path, document_id)

    # Deleted while it was being processed? Then the chunks just written are orphans.
    if await run_in_threadpool(registry.get, document_id) is None:
        await run_in_threadpool(services.vector_store.delete_by_document_id, scope, document_id)


async def _refund(services, scope: TenantScope, document_id: str) -> None:
    """Give back the processing credits for a document that failed (idempotent)."""
    await run_in_threadpool(
        services.credit_service.refund_reference, scope.user_id, "document", document_id, "processing_failed"
    )


async def submit_document(services, settings: Settings, scope: TenantScope, document_id: str, force_ocr: bool) -> None:
    if settings.INGEST_MODE == "inline":
        await process_document(services, scope.workspace_id, scope.user_id, document_id, force_ocr)
        return
    try:
        # A short-lived pool per upload: uploads are infrequent, and it avoids sharing a
        # connection across event loops.
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            await pool.enqueue_job(
                INGEST_JOB_NAME,
                scope.workspace_id,
                scope.user_id,
                document_id,
                force_ocr,
                _job_id=f"ingest:{document_id}",
            )
        finally:
            await pool.aclose()
    except Exception as exc:  # noqa: BLE001
        registry = DocumentRegistry(services.session_factory, scope.workspace_id)
        record = await run_in_threadpool(registry.get, document_id)
        await run_in_threadpool(registry.update_status, document_id, "failed", None, "The processing queue is unavailable.")
        if record is not None:
            await run_in_threadpool(services.upload_store.delete, record.storage_path)
        raise QueueUnavailableError("The processing queue is unavailable. Please try again shortly.") from exc
