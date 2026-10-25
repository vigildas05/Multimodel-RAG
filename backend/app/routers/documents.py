import logging

from fastapi import APIRouter, Depends, UploadFile
from fastapi.param_functions import Query

from app.auth.dependencies import get_current_user
from app.auth.service import AuthenticatedUser
from app.config import Settings
from app.core.exceptions import DocumentNotFoundError
from app.core.file_validation import (
    build_stored_path,
    classify_file_type,
    new_document_id,
    sanitize_filename,
    validate_extension,
    validate_size,
)
from app.dependencies import get_app_settings, get_document_service, get_registry
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
    )


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile,
    force_ocr: bool = Query(default=False),
    user: AuthenticatedUser = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    registry: DocumentRegistry = Depends(get_registry),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentUploadResponse:
    ext = validate_extension(file.filename, settings)
    file_bytes = await file.read()
    validate_size(len(file_bytes), settings)

    safe_name = sanitize_filename(file.filename)
    file_type = classify_file_type(ext)
    document_id = new_document_id()

    record = DocumentRecord(
        document_id=document_id,
        filename=safe_name,
        file_type=file_type,
        status="processing",
        upload_timestamp=now_iso(),
    )
    registry.create(record)

    try:
        if file_type == "pdf":
            updated = await document_service.ingest_pdf(user.scope, document_id, safe_name, file_bytes, force_ocr=force_ocr)
        elif file_type == "image":
            updated = await document_service.ingest_image(user.scope, document_id, safe_name, file_bytes)
        elif file_type == "audio":
            stored_path = build_stored_path(settings.upload_dir_path, document_id, safe_name)
            stored_path.write_bytes(file_bytes)
            updated = await document_service.ingest_audio(user.scope, document_id, safe_name, str(stored_path))
        else:
            raise ValueError(f"Unhandled file type: {file_type}")
    except Exception as exc:  # noqa: BLE001
        registry.update_status(document_id, "failed", error_message=str(exc))
        raise

    return DocumentUploadResponse(document=_to_schema(updated))


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
    registry: DocumentRegistry = Depends(get_registry),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentChunksResponse:
    record = registry.get(document_id)
    if record is None:
        raise DocumentNotFoundError(f"Document '{document_id}' was not found.")
    raw_chunks = document_service.get_chunks(document_id)
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
