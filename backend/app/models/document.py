from dataclasses import dataclass


@dataclass
class DocumentRecord:
    document_id: str
    filename: str
    file_type: str
    status: str  # "queued" | "processing" | "ready" | "failed"
    upload_timestamp: str
    num_chunks: int = 0
    error_message: str | None = None
    num_pages: int | None = None
    duration_seconds: float | None = None
    used_ocr: bool = False
    hidden_span_count: int = 0
    quarantined_chunk_count: int = 0
    # Store-relative path of the raw upload while it waits to be processed (see UploadStore).
    storage_path: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
