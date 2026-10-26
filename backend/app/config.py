from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Server ---
    # "development" | "test" | "production". Production refuses to start with insecure settings.
    APP_ENV: str = "development"
    CORS_ORIGINS: str = "http://localhost:5173"
    # "text" (readable, local dev) or "json" (one object per line with request IDs; production).
    LOG_FORMAT: str = "text"
    LOG_LEVEL: str = "INFO"
    # Prometheus metrics at GET /metrics. With METRICS_TOKEN set, scrapers must send
    # "Authorization: Bearer <token>"; in production the endpoint is off unless a token is set.
    METRICS_ENABLED: bool = True
    METRICS_TOKEN: str = ""

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

    # --- Credits ---
    # Cost per action, deducted atomically when the action starts and refunded if it fails.
    CREDIT_COST_QUERY: int = 1
    CREDIT_COST_PDF: int = 0
    CREDIT_COST_IMAGE: int = 3
    CREDIT_COST_AUDIO: int = 5
    SIGNUP_BONUS_CREDITS: int = 20
    DEMO_BONUS_CREDITS: int = 30

    # --- Payments: Razorpay, TEST MODE ONLY ---
    # Keys come from env only. The app refuses to start with a live key (rzp_live_...) or
    # with PAYMENTS_TEST_MODE=false: this project never takes real money.
    PAYMENTS_TEST_MODE: bool = True
    RAZORPAY_KEY_ID: str = ""
    RAZORPAY_KEY_SECRET: str = ""
    RAZORPAY_WEBHOOK_SECRET: str = ""

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
    # Postgres URL. Neon's pooled DATABASE_URL works as-is (postgresql://...?sslmode=require);
    # the psycopg 3 driver is selected automatically.
    DATABASE_URL: str = "postgresql+psycopg://sentinel:sentinel@localhost:5432/sentinel"
    DB_POOL_SIZE: int = 5

    @field_validator("DATABASE_URL")
    @classmethod
    def _use_psycopg3(cls, url: str) -> str:
        # Neon and most hosts hand out plain postgresql:// (or postgres://) URLs, which
        # SQLAlchemy would map to psycopg2. Select the installed psycopg 3 driver instead.
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url
    DB_MAX_OVERFLOW: int = 5

    # --- LLM security gateway (backend/security) ---
    SECURITY_ENABLED: bool = True
    SECURITY_CLASSIFIER_ENABLED: bool = True
    SECURITY_CLASSIFIER_MODEL: str = "protectai/deberta-v3-base-prompt-injection-v2"
    SECURITY_CLASSIFIER_WEIGHT: float = 0.7
    SECURITY_BLOCK_THRESHOLD: float = 0.8
    SECURITY_FLAG_THRESHOLD: float = 0.5
    SECURITY_QUARANTINE_THRESHOLD: float = 0.75
    MAX_QUESTION_CHARS: int = 4000
    PII_REDACTION_ENABLED: bool = True

    # --- Rate limiting (Redis at REDIS_URL; fails open if Redis is down) ---
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_GLOBAL_PER_IP_PER_MINUTE: int = 300
    RATE_LIMIT_LOGIN_PER_IP_PER_MINUTE: int = 10
    RATE_LIMIT_LOGIN_PER_ACCOUNT_PER_HOUR: int = 20
    RATE_LIMIT_REGISTER_PER_IP_PER_HOUR: int = 10
    RATE_LIMIT_DEMO_PER_IP_PER_MINUTE: int = 3
    RATE_LIMIT_REFRESH_PER_IP_PER_MINUTE: int = 30
    RATE_LIMIT_CHAT_PER_USER_PER_MINUTE: int = 10
    RATE_LIMIT_SEARCH_PER_USER_PER_MINUTE: int = 30
    RATE_LIMIT_UPLOAD_PER_USER_PER_MINUTE: int = 10
    RATE_LIMIT_BILLING_PER_USER_PER_MINUTE: int = 10

    # --- Request limits ---
    # Max body for JSON requests; uploads are capped by MAX_UPLOAD_SIZE_MB instead.
    MAX_JSON_BODY_KB: int = 64

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
    # Red-team reports shown on the admin dashboard (read-only; mounted into the container
    # in production, see deploy/compose.prod.yml).
    REDTEAM_REPORTS_DIR: str = "../redteam/reports"
    ALLOWED_EXTENSIONS: str = ".pdf,.png,.jpg,.jpeg,.webp,.mp3,.wav,.m4a,.ogg,.flac"

    # --- Upload limits ---
    # Checked in the API before anything is stored:
    MAX_DOCUMENTS_PER_WORKSPACE: int = 100
    # Checked by the sandboxed parser in the worker:
    MAX_PDF_PAGES: int = 100
    MAX_IMAGE_PIXELS: int = 40_000_000
    MAX_AUDIO_SECONDS: int = 1800
    # Hard limits for each sandboxed parse (memory cap applies on Linux only).
    PARSE_TIMEOUT_SECONDS: int = 60
    PARSE_MEMORY_LIMIT_MB: int = 1536

    # --- Qdrant ---
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    # Managed cluster (Qdrant Cloud): set both; they take precedence over host/port.
    QDRANT_URL: str = ""
    QDRANT_API_KEY: str = ""
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

    # --- OpenAI-compatible hosted LLM (LLM_PROVIDER=openai_compatible) ---
    # Groq: https://api.groq.com/openai/v1 · Gemini: https://generativelanguage.googleapis.com/v1beta/openai
    # NVIDIA NIM: https://integrate.api.nvidia.com/v1 · OpenAI: https://api.openai.com/v1
    LLM_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL: str = ""
    LLM_VISION_MODEL: str = ""
    LLM_TIMEOUT_SECONDS: float = 60.0

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
