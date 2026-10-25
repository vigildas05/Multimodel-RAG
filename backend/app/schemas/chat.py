from pydantic import BaseModel, ConfigDict, Field

from app.schemas.settings import RetrievalSettingsSchema


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Hard cap on size; the security gateway separately flags unusually long questions.
    question: str = Field(min_length=1, max_length=8000)
    settings: RetrievalSettingsSchema = RetrievalSettingsSchema()


class SourceCitation(BaseModel):
    index: int
    document: str
    file_type: str
    page: int | None = None
    audio_start_time: float | None = None
    audio_end_time: float | None = None
    chunk_id: str
    score: float
    text_snippet: str


class RetrievalInfo(BaseModel):
    top_k: int
    reranked: bool
    hybrid: bool
    reranked_chunks: int
    dense_candidate_count: int
    bm25_candidate_count: int


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceCitation]
    retrieval: RetrievalInfo
