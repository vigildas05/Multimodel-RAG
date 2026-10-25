from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Server ---
    # "development" | "test" | "production". Production refuses to start with insecure settings.
    APP_ENV: str = "development"
    CORS_ORIGINS: str = "http://localhost:5173"

    # --- Auth ---
    # HS256 signing key for access tokens, >= 32 bytes. Generate with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    # Outside production an empty value means a random per-process key (sessions die on restart).
    JWT_SECRET_KEY: str = ""
    JWT_ISSUER: str = "sentinel-rag"
    ACCESS_TOKEN_TTL_MINUTES: int = 15
    REFRESH_TOKEN_TTL_DAYS: int = 7
    PASSWORD_MIN_LENGTH: int = 12
    # Browsers treat http://localhost as a secure context, so Secure cookies work in local dev too.
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "lax"

    # --- Quotas ---
    # Queries (chat + search) per user per UTC day. Replaced by credits in Phase 3.
    DAILY_QUERY_LIMIT: int = 200

    # --- Demo ---
    # "Try the demo" gives each visitor a private, short-lived copy of the sample workspace.
    DEMO_ENABLED: bool = True
    DEMO_TTL_HOURS: int = 24
    DEMO_DAILY_QUERY_LIMIT: int = 20
    DEMO_MAX_DOCUMENTS: int = 6
    # Abuse limits on creating demo workspaces (each one copies the sample vectors).
    DEMO_MAX_ACTIVE_WORKSPACES: int = 200
    DEMO_MAX_STARTS_PER_IP_PER_HOUR: int = 5

    # --- Database ---
    # SQLAlchemy URL; psycopg 3 driver. For Neon, append ?sslmode=require.
    DATABASE_URL: str = "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 5

    # --- Background processing ---
    REDIS_URL: str = "redis://localhost:6379/0"
    # "queue": uploads are processed by the arq worker (`arq app.worker.WorkerSettings`).
    # "inline": processed inside the upload request (tests, or a quick run without a worker).
    INGEST_MODE: str = "queue"
    INGEST_JOB_TIMEOUT_SECONDS: int = 900
    WORKER_MAX_JOBS: int = 2

    # --- Storage ---
    DATA_DIR: str = "data"
    UPLOAD_DIR: str = "data/uploads"
    MAX_UPLOAD_SIZE_MB: int = 25
    ALLOWED_EXTENSIONS: str = ".pdf,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.ogg,.flac"

    # --- Qdrant ---
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    # chunks_v2: every point carries a workspace_id payload (tenant-indexed).
    QDRANT_COLLECTION: str = "chunks_v2"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
