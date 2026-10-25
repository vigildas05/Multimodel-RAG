import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.document import DocumentRecord
from app.storage.document_registry import DocumentRegistry


def _record(filename: str = "paper.pdf", uploaded_at: datetime | None = None) -> DocumentRecord:
    return DocumentRecord(
        document_id=str(uuid.uuid4()),
        filename=filename,
        file_type="pdf",
        status="processing",
        upload_timestamp=(uploaded_at or datetime.now(timezone.utc)).isoformat(),
    )


@pytest.fixture
def two_registries(session_factory, make_workspace):
    return (
        DocumentRegistry(session_factory, make_workspace("A")),
        DocumentRegistry(session_factory, make_workspace("B")),
    )


def test_create_and_get_round_trip(session_factory, make_workspace):
    registry = DocumentRegistry(session_factory, make_workspace())
    record = _record()
    registry.create(record)

    fetched = registry.get(record.document_id)
    assert fetched is not None
    assert fetched.filename == "paper.pdf"
    assert fetched.status == "processing"
    assert fetched.num_chunks == 0
    assert fetched.used_ocr is False
    assert datetime.fromisoformat(fetched.upload_timestamp) == datetime.fromisoformat(record.upload_timestamp)


def test_update_status_only_changes_given_fields(session_factory, make_workspace):
    registry = DocumentRegistry(session_factory, make_workspace())
    record = _record()
    registry.create(record)

    registry.update_status(record.document_id, "ready", num_chunks=7, num_pages=3, used_ocr=True)
    registry.update_status(record.document_id, "ready", duration_seconds=12.5)

    fetched = registry.get(record.document_id)
    assert fetched.status == "ready"
    assert fetched.num_chunks == 7
    assert fetched.num_pages == 3
    assert fetched.used_ocr is True
    assert fetched.duration_seconds == 12.5
    assert fetched.error_message is None


def test_list_all_is_newest_first(session_factory, make_workspace):
    registry = DocumentRegistry(session_factory, make_workspace())
    now = datetime.now(timezone.utc)
    older, newer = _record("old.pdf", now - timedelta(minutes=5)), _record("new.pdf", now)
    registry.create(older)
    registry.create(newer)

    assert [r.filename for r in registry.list_all()] == ["new.pdf", "old.pdf"]


@pytest.mark.parametrize("bad_id", ["does-not-exist", "", "1 OR 1=1", "../../etc/passwd"])
def test_malformed_ids_behave_as_not_found(session_factory, make_workspace, bad_id):
    registry = DocumentRegistry(session_factory, make_workspace())
    registry.create(_record())

    assert registry.get(bad_id) is None
    registry.update_status(bad_id, "failed")
    registry.delete(bad_id)
    assert len(registry.list_all()) == 1


def test_delete_removes_document(session_factory, make_workspace):
    registry = DocumentRegistry(session_factory, make_workspace())
    record = _record()
    registry.create(record)
    registry.delete(record.document_id)
    assert registry.get(record.document_id) is None


def test_workspaces_cannot_see_each_others_documents(two_registries):
    registry_a, registry_b = two_registries
    doc_a = _record("a.pdf")
    registry_a.create(doc_a)

    assert registry_b.get(doc_a.document_id) is None
    assert registry_b.list_all() == []


def test_workspaces_cannot_modify_each_others_documents(two_registries):
    registry_a, registry_b = two_registries
    doc_a = _record("a.pdf")
    registry_a.create(doc_a)

    registry_b.update_status(doc_a.document_id, "failed", error_message="tampered")
    registry_b.delete(doc_a.document_id)
    registry_b.reset()

    fetched = registry_a.get(doc_a.document_id)
    assert fetched is not None
    assert fetched.status == "processing"
    assert fetched.error_message is None


def test_reset_only_clears_own_workspace(two_registries):
    registry_a, registry_b = two_registries
    registry_a.create(_record("a.pdf"))
    registry_b.create(_record("b.pdf"))

    registry_a.reset()

    assert registry_a.list_all() == []
    assert [r.filename for r in registry_b.list_all()] == ["b.pdf"]
