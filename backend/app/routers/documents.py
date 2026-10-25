import hashlib
import logging

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.param_functions import Query
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.config import Settings
from app.core.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidFileTypeError,
    QuotaExceededError,
    UploadRejectedError,
)
from app.core.file_validation import new_document_id, sanitize_filename
from app.db.audit import record_security_event
from app.dependencies import get_app_settings, get_document_service, get_registry
from app.ingestion.jobs import submit_document
from app.ingestion.validation import read_upload_capped, validate_upload
from app.models.document import DocumentRecord
from app.schemas.document import (
    ChunkResponse,
    DocumentChunksResponse,
    DocumentListResponse,
    DocumentMetadata,
    DocumentUploadResponse,
    ResetResponse,
)
from app.services.document_service import DocumentService, now_iso
from app.storage.document_registry import DocumentRegistry

logger = logging.getLogger("app.routers.documents")
router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(get_current_user)])


def _to_schema(record: DocumentRecord) -> DocumentMetadata:
    return DocumentMetadata(
        document_id=record.document_id,
        filename=record.filename,
        file_type=record.file_type,
        status=record.status,
        upload_timestamp=record.upload_timestamp,
        num_chunks=record.num_chunks,
        error_message=record.error_message,
        num_pages=record.num_pages,
        duration_seconds=record.duration_seconds,
        used_ocr=record.used_ocr,
        hidden_span_count=record.hidden_span_count,
    )


@router.post("/upload", response_model=DocumentUploadResponse, status_code=202)
async def upload_document(
    request: Request,
    file: UploadFile,
    force_ocr: bool = Query(default=False),
    user: AuthenticatedUser = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    registry: DocumentRegistry = Depends(get_registry),
) -> DocumentUploadResponse:
    services = request.app.state.services
    document_limit = settings.DEMO_MAX_DOCUMENTS if user.is_demo else settings.MAX_DOCUMENTS_PER_WORKSPACE
    if await run_in_threadpool(registry.count) >= document_limit:
        raise QuotaExceededError(f"This workspace is limited to {document_limit} documents.")

    try:
        file_bytes = await read_upload_capped(file, settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024)
        file_type = validate_upload(file.filename, file_bytes, settings)
    except (FileTooLargeError, InvalidFileTypeError, UploadRejectedError) as exc:
        await run_in_threadpool(
            record_security_event,
            services.session_factory,
            "upload_rejected",
            "upload",
            severity="low",
            workspace_id=user.workspace_id,
            user_id=user.user_id,
            details={"reason": exc.error_type, "message": exc.message, "filename": sanitize_filename(file.filename)},
        )
        raise

    safe_name = sanitize_filename(file.filename)
    document_id = new_document_id()
    storage_path = await run_in_threadpool(services.upload_store.save, user.workspace_id, file_bytes)

    record = DocumentRecord(
        document_id=document_id,
        filename=safe_name,
        file_type=file_type,
        status="queued",
        upload_timestamp=now_iso(),
        storage_path=storage_path,
        size_bytes=len(file_bytes),
        sha256=hashlib.sha256(file_bytes).hexdigest(),
    )
    await run_in_threadpool(registry.create, record)
    await submit_document(services, settings, user.scope, document_id, force_ocr)

    # "queued" in queue mode (the worker picks it up; clients poll GET /documents),
    # already "ready"/"failed" in inline mode.
    return DocumentUploadResponse(document=_to_schema(await run_in_threadpool(registry.get, document_id)))


@router.get("", response_model=DocumentListResponse)
async def list_documents(registry: DocumentRegistry = Depends(get_registry)) -> DocumentListResponse:
    records = registry.list_all()
    return DocumentListResponse(documents=[_to_schema(r) for r in records])


@router.delete("/{document_id}")
async def delete_document(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    registry: DocumentRegistry = Depends(get_registry),
    document_service: DocumentService = Depends(get_document_service),
) -> dict:
    record = registry.get(document_id)
    if record is None:
        raise DocumentNotFoundError(f"Document '{document_id}' was not found.")
    await document_service.delete_document(user.scope, document_id)
    return {"message": f"Document '{record.filename}' deleted.", "document_id": document_id}


@router.get("/{document_id}/chunks", response_model=DocumentChunksResponse)
async def get_document_chunks(
    document_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
    registry: DocumentRegistry = Depends(get_registry),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentChunksResponse:
    record = registry.get(document_id)
    if record is None:
        raise DocumentNotFoundError(f"Document '{document_id}' was not found.")
    raw_chunks = await run_in_threadpool(document_service.get_chunks, user.scope, document_id)
    chunks = [
        ChunkResponse(
            chunk_id=c["chunk_id"],
            text=c["text"],
            chunk_index=c["chunk_index"],
            page_number=c.get("page_number"),
            audio_start_time=c.get("audio_start_time"),
            audio_end_time=c.get("audio_end_time"),
            is_ocr=c.get("is_ocr", False),
        )
        for c in raw_chunks
    ]
    return DocumentChunksResponse(document_id=document_id, chunks=chunks)


@router.post("/reset", response_model=ResetResponse)
async def reset_documents(
    user: AuthenticatedUser = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
) -> ResetResponse:
    count = await document_service.reset_workspace(user.scope)
    return ResetResponse(message="All documents in your workspace have been deleted.", documents_removed=count)
