"""Background worker (arq). Run from backend/ with:

    arq app.worker.WorkerSettings

Heavy models (OCR, Whisper, vision LLM calls) load here, on first use, never in the API.
"""

from arq import cron, func
from arq.connections import RedisSettings
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.core.logging_config import configure_logging
from app.db.session import create_db_engine, create_session_factory
from app.ingestion.jobs import INGEST_JOB_NAME, process_document
from app.services.container import build_services

_settings = get_settings()


async def startup(ctx: dict) -> None:
    configure_logging(_settings.LOG_FORMAT, _settings.LOG_LEVEL)
    ctx["engine"] = create_db_engine(_settings)
    ctx["services"] = build_services(_settings, create_session_factory(ctx["engine"]), role="worker")


async def shutdown(ctx: dict) -> None:
    ctx["engine"].dispose()


async def ingest_document(ctx: dict, workspace_id: str, user_id: str, document_id: str, force_ocr: bool = False):
    await process_document(ctx["services"], workspace_id, user_id, document_id, force_ocr)


async def cleanup_demo_workspaces(ctx: dict) -> int:
    return await run_in_threadpool(ctx["services"].demo_service.cleanup_expired)


class WorkerSettings:
    functions = [func(ingest_document, name=INGEST_JOB_NAME)]
    cron_jobs = [cron(cleanup_demo_workspaces, minute={0, 15, 30, 45}, run_at_startup=True)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.REDIS_URL)
    job_timeout = _settings.INGEST_JOB_TIMEOUT_SECONDS
    max_jobs = _settings.WORKER_MAX_JOBS
    # Jobs are idempotent (see process_document), so an interrupted job is safe to retry.
    max_tries = 2
