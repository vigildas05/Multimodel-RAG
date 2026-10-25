from pydantic import BaseModel, ConfigDict, Field


class RetrievalSettingsSchema(BaseModel):
    """Shared settings block sent by the frontend with /chat and /search requests.
    Backend config values are used as defaults when a field is omitted."""

    # Unknown fields (e.g. a smuggled "workspace_id") are rejected, not silently ignored.
    model_config = ConfigDict(extra="forbid")

    retrieval_top_k: int = Field(default=10, ge=1, le=50)
    final_context_chunks: int = Field(default=4, ge=1, le=20)
    chunk_size: int | None = Field(default=None, ge=100, le=4000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=1000)
    similarity_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    use_reranking: bool = True
    use_hybrid_search: bool = True
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=800, ge=64, le=4096)
    document_ids: list[str] | None = None
