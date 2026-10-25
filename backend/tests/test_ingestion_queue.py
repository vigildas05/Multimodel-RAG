"""Background ingestion: the real queue path (API -> Redis -> arq worker) and the job's
failure handling (timeouts, internal errors, deletes during processing, idempotency)."""

import asyncio
import io

import pytest
import redis
from arq.connections import RedisSettings
from arq.worker import Worker, func

import app.ingestion.jobs as jobs
from app.config import get_settings
from app.ingestion.jobs import INGEST_JOB_NAME, process_document
from app.services.container import Lazy
from app.storage.upload_store import UploadStore
from app.core.tenancy import TenantScope
from helpers import make_pdf, signup

TEST_REDIS_URL = "redis://localhost:6379/15"


@pytest.fixture
def queue_mode(monkeypatch):
    try:
        redis.Redis.from_url(TEST_REDIS_URL).flushdb()
    except redis.ConnectionError:
        pytest.skip("Redis is not running on localhost:6379 (docker compose up -d redis)")
    monkeypatch.setattr(get_settings(), "INGEST_MODE", "queue")
    monkeypatch.setattr(get_settings(), "REDIS_URL", TEST_REDIS_URL)


def run_worker_until_idle(app) -> None:
    """Run a real arq worker in burst mode against the app's services."""

    async def ingest(ctx, workspace_id, user_id, document_id, force_ocr=False):
        await process_document(app.state.services, workspace_id, user_id, document_id, force_ocr)

    async def main():
        worker = Worker(
            functions=[func(ingest, name=INGEST_JOB_NAME)],
            redis_settings=RedisSettings.from_dsn(TEST_REDIS_URL),
            burst=True,
            poll_delay=0.1,
        )
        try:
            await worker.main()
        finally:
            await worker.close()

    asyncio.run(main())


def _upload(client, text="Ribosomes synthesize proteins from amino acids.", name="bio.pdf"):
    return client.post("/documents/upload", files={"file": (name, io.BytesIO(make_pdf(text)), "application/pdf")})


def _document(client, document_id):
    return next(d for d in client.get("/documents").json()["documents"] if d["document_id"] == document_id)


def _stored_files(app, workspace_id):
    root = app.state.upload_store.path(workspace_id)
    return list(root.iterdir()) if root.exists() else []


def test_upload_is_queued_then_processed_by_worker(app, client, queue_mode):
    user = signup(client)
    resp = _upload(client)
    assert resp.status_code == 202
    document = resp.json()["document"]
    assert document["status"] == "queued"
    assert len(_stored_files(app, user["workspace_id"])) == 1  # raw file waits on disk

    run_worker_until_idle(app)

    processed = _document(client, document["document_id"])
    assert processed["status"] == "ready" and processed["num_chunks"] >= 1
    assert _stored_files(app, user["workspace_id"]) == []  # deleted after processing
    search = client.post("/search", json={"query": "What do ribosomes synthesize?"})
    assert "proteins" in search.json()["chunks"][0]["text"]


def test_queue_outage_fails_cleanly(app, client, queue_mode, monkeypatch):
    user = signup(client)
    monkeypatch.setattr(get_settings(), "REDIS_URL", "redis://localhost:6390/0")  # nothing listens here
    resp = _upload(client)
    assert resp.status_code == 503 and resp.json()["error_type"] == "queue_unavailable"
    [document] = client.get("/documents").json()["documents"]
    assert document["status"] == "failed"
    assert _stored_files(app, user["workspace_id"]) == []


def _queued_document(app, client, monkeypatch):
    """Upload in inline mode but skip processing, leaving a queued document behind."""
    user = signup(client)

    async def skip(*args, **kwargs):
        return None

    with monkeypatch.context() as m:
        m.setattr(jobs, "process_document", skip)
        document = _upload(client).json()["document"]
    assert document["status"] == "queued"
    return app.state.services, user, document["document_id"]


def _run(services, user, document_id):
    asyncio.run(process_document(services, user["workspace_id"], user["user_id"], document_id))


def test_processing_is_idempotent(app, client, monkeypatch):
    services, user, document_id = _queued_document(app, client, monkeypatch)
    _run(services, user, document_id)
    first = _document(client, document_id)
    _run(services, user, document_id)  # e.g. an arq retry
    assert _document(client, document_id) == first
    chunks = client.get(f"/documents/{document_id}/chunks").json()["chunks"]
    assert len(chunks) == first["num_chunks"]  # not indexed twice


def test_internal_errors_are_not_leaked_to_the_client(app, client, monkeypatch):
    services, user, document_id = _queued_document(app, client, monkeypatch)

    async def explode(*args, **kwargs):
        raise RuntimeError(r"C:\secret\internal\path.py exploded")

    monkeypatch.setattr(services.document_service, "ingest_pdf", explode)
    _run(services, user, document_id)
    document = _document(client, document_id)
    assert document["status"] == "failed"
    assert "secret" not in document["error_message"]
    assert _stored_files(app, user["workspace_id"]) == []


def test_timeout_marks_document_failed(app, client, monkeypatch):
    services, user, document_id = _queued_document(app, client, monkeypatch)

    async def hang(*args, **kwargs):
        raise asyncio.CancelledError()  # what arq does when job_timeout is exceeded

    monkeypatch.setattr(services.document_service, "ingest_file", hang)
    with pytest.raises(asyncio.CancelledError):
        _run(services, user, document_id)
    document = _document(client, document_id)
    assert document["status"] == "failed" and "timed out" in document["error_message"]


def test_document_deleted_during_processing_leaves_no_orphan_chunks(app, client, monkeypatch):
    services, user, document_id = _queued_document(app, client, monkeypatch)
    original_ingest = services.document_service.ingest_file

    async def ingest_then_user_deletes(scope, doc_id, *args, **kwargs):
        result = await original_ingest(scope, doc_id, *args, **kwargs)
        client.delete(f"/documents/{doc_id}")  # user deletes while the job is finishing
        return result

    monkeypatch.setattr(services.document_service, "ingest_file", ingest_then_user_deletes)
    _run(services, user, document_id)

    scope = TenantScope(workspace_id=user["workspace_id"], user_id=user["user_id"])
    assert services.vector_store.get_chunks_by_document(scope, document_id) == []


def test_upload_store_paths_cannot_escape_root(tmp_path):
    store = UploadStore(tmp_path / "uploads")
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")
    with pytest.raises(ValueError):
        store.save("../../not-a-uuid", b"x")
    workspace = "3f2b8c1e-0000-4000-8000-000000000001"
    relative = store.save(workspace, b"data")
    assert relative.startswith(workspace + "/") and "." not in relative.split("/")[1]
    assert store.path(relative).read_bytes() == b"data"


def test_lazy_services_load_only_on_first_use():
    built = []
    lazy = Lazy(lambda: built.append(1) or "loaded")
    assert built == []
    assert lazy.upper() == "LOADED"
    assert lazy.lower() == "loaded" and built == [1]
