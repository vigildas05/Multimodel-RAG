from pydantic import BaseModel, ConfigDict

from app.schemas.settings import RetrievalSettingsSchema


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    settings: RetrievalSettingsSchema = RetrievalSettingsSchema()


class RetrievedChunk(BaseModel):
    chunk_id: str
    document: str
    file_type: str
    text: str
    score: float
    rerank_score: float | None = None
    page: int | None = None
    audio_start_time: float | None = None
    audio_end_time: float | None = None


class SearchResponse(BaseModel):
    chunks: list[RetrievedChunk]
    top_k: int
    reranked: bool
    hybrid: bool
