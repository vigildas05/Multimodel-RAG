from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Server ---
    CORS_ORIGINS: str = "http://localhost:5173"

    # --- Storage ---
    DATA_DIR: str = "data"
    UPLOAD_DIR: str = "data/uploads"
    REGISTRY_DB_PATH: str = "data/registry.db"
    MAX_UPLOAD_SIZE_MB: int = 25
    ALLOWED_EXTENSIONS: str = ".pdf,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.ogg,.flac"

    # --- Qdrant ---
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION: str = "document_chunks"

    # --- Chunking ---
    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 100

    # --- Embeddings ---
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    EMBEDDING_DEVICE: str = "cpu"

    # --- Reranking ---
    RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-base"
    RERANKER_DEVICE: str = "cpu"
    USE_RERANKING_DEFAULT: bool = True

    # --- Retrieval ---
    RETRIEVAL_TOP_K_DEFAULT: int = 10
    FINAL_CONTEXT_CHUNKS_DEFAULT: int = 4
    USE_HYBRID_SEARCH_DEFAULT: bool = True
    SIMILARITY_THRESHOLD_DEFAULT: float | None = None
    RRF_K: int = 60

    # --- Generation ---
    LLM_PROVIDER: str = "ollama"
    TEMPERATURE_DEFAULT: float = 0.2
    TOP_P_DEFAULT: float = 0.9
    MAX_OUTPUT_TOKENS_DEFAULT: int = 800

    # --- Ollama ---
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_TEXT_MODEL: str = "llama3.2:3b"
    OLLAMA_VISION_MODEL: str = "moondream"
    OLLAMA_REQUEST_TIMEOUT: float = 120.0

    # --- Whisper ---
    WHISPER_MODEL_SIZE: str = "small"
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"

    # --- OCR ---
    OCR_ENGINE: str = "paddleocr"

    @property
    def allowed_extensions_set(self) -> set[str]:
        return {ext.strip().lower() for ext in self.ALLOWED_EXTENSIONS.split(",") if ext.strip()}

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def upload_dir_path(self) -> Path:
        path = Path(self.UPLOAD_DIR)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def registry_db_full_path(self) -> Path:
        path = Path(self.REGISTRY_DB_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()
