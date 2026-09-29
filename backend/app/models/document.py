from dataclasses import dataclass


@dataclass
class DocumentRecord:
    document_id: str
    filename: str
    file_type: str
    status: str  # "processing" | "ready" | "failed"
    upload_timestamp: str
    num_chunks: int = 0
    error_message: str | None = None
    num_pages: int | None = None
    duration_seconds: float | None = None
    used_ocr: bool = False
