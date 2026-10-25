from dataclasses import dataclass, field


@dataclass
class Chunk:
    chunk_id: str
    document_id: str
    filename: str
    file_type: str
    text: str
    chunk_index: int
    upload_timestamp: str
    page_number: int | None = None
    source_location: str | None = None
    audio_start_time: float | None = None
    audio_end_time: float | None = None
    is_ocr: bool = False
    # Set by the ingest guard: quarantined chunks are stored (so owners can review them)
    # but never retrieved.
    quarantined: bool = False
    quarantine_reasons: list[str] = field(default_factory=list)
    injection_risk: float | None = None
    embedding: list[float] | None = field(default=None, repr=False)
