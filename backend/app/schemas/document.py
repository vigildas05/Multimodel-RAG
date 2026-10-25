from pydantic import BaseModel


class DocumentMetadata(BaseModel):
    document_id: str
    filename: str
    file_type: str
    status: str
    upload_timestamp: str
    num_chunks: int
    error_message: str | None = None
    num_pages: int | None = None
    duration_seconds: float | None = None
    used_ocr: bool = False
    hidden_span_count: int = 0
    quarantined_chunk_count: int = 0


class DocumentUploadResponse(BaseModel):
    document: DocumentMetadata


class DocumentListResponse(BaseModel):
    documents: list[DocumentMetadata]


class ChunkResponse(BaseModel):
    chunk_id: str
    text: str
    chunk_index: int
    page_number: int | None = None
    audio_start_time: float | None = None
    audio_end_time: float | None = None
    is_ocr: bool = False
    quarantined: bool = False
    quarantine_reasons: list[str] = []
    injection_risk: float | None = None


class DocumentChunksResponse(BaseModel):
    document_id: str
    chunks: list[ChunkResponse]


class ResetResponse(BaseModel):
    message: str
    documents_removed: int
