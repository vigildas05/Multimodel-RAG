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
    embedding: list[float] | None = field(default=None, repr=False)
