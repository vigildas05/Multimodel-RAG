import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Document
from app.models.document import DocumentRecord


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False
    return True


class DocumentRegistry:
    """Postgres-backed source of truth for document-level metadata and ingestion status.

    Every query is scoped to the registry's workspace: a document ID belonging to another
    workspace behaves exactly like one that doesn't exist.
    """

    def __init__(self, session_factory: sessionmaker[Session], workspace_id: str):
        self._session_factory = session_factory
        self._workspace_id = workspace_id

    def create(self, record: DocumentRecord) -> None:
        with self._session_factory.begin() as session:
            session.add(
                Document(
                    id=record.document_id,
                    workspace_id=self._workspace_id,
                    filename=record.filename,
                    file_type=record.file_type,
                    status=record.status,
                    num_chunks=record.num_chunks,
                    error_message=record.error_message,
                    num_pages=record.num_pages,
                    duration_seconds=record.duration_seconds,
                    used_ocr=record.used_ocr,
                    storage_path=record.storage_path,
                    size_bytes=record.size_bytes,
                    sha256=record.sha256,
                    created_at=datetime.fromisoformat(record.upload_timestamp),
                )
            )

    def update_status(
        self,
        document_id: str,
        status: str,
        num_chunks: int | None = None,
        error_message: str | None = None,
        num_pages: int | None = None,
        duration_seconds: float | None = None,
        used_ocr: bool | None = None,
        hidden_span_count: int | None = None,
    ) -> None:
        with self._session_factory.begin() as session:
            document = self._get_row(session, document_id)
            if document is None:
                return
            document.status = status
            if num_chunks is not None:
                document.num_chunks = num_chunks
            if error_message is not None:
                document.error_message = error_message
            if num_pages is not None:
                document.num_pages = num_pages
            if duration_seconds is not None:
                document.duration_seconds = duration_seconds
            if used_ocr is not None:
                document.used_ocr = used_ocr
            if hidden_span_count is not None:
                document.hidden_span_count = hidden_span_count

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._session_factory() as session:
            document = self._get_row(session, document_id)
            return self._to_record(document) if document else None

    def list_all(self) -> list[DocumentRecord]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(Document)
                .where(Document.workspace_id == self._workspace_id)
                .order_by(Document.created_at.desc())
            )
            return [self._to_record(row) for row in rows]

    def clear_storage_path(self, document_id: str) -> None:
        with self._session_factory.begin() as session:
            document = self._get_row(session, document_id)
            if document is not None:
                document.storage_path = None

    def count(self) -> int:
        with self._session_factory() as session:
            return session.scalar(
                select(func.count()).select_from(Document).where(Document.workspace_id == self._workspace_id)
            )

    def owned_ids(self, document_ids: list[str]) -> set[str]:
        """The subset of `document_ids` that exist in this workspace."""
        candidates = [d for d in set(document_ids) if _is_uuid(d)]
        if not candidates:
            return set()
        with self._session_factory() as session:
            rows = session.scalars(
                select(Document.id).where(Document.id.in_(candidates), Document.workspace_id == self._workspace_id)
            )
            return set(rows)

    def delete(self, document_id: str) -> None:
        if not _is_uuid(document_id):
            return
        with self._session_factory.begin() as session:
            session.execute(
                delete(Document).where(Document.id == document_id, Document.workspace_id == self._workspace_id)
            )

    def reset(self) -> None:
        with self._session_factory.begin() as session:
            session.execute(delete(Document).where(Document.workspace_id == self._workspace_id))

    def _get_row(self, session: Session, document_id: str) -> Document | None:
        # Malformed IDs (e.g. from a URL path) would otherwise surface as a Postgres
        # type error; treat them as "not found" like any other unknown ID.
        if not _is_uuid(document_id):
            return None
        return session.scalar(
            select(Document).where(Document.id == document_id, Document.workspace_id == self._workspace_id)
        )

    @staticmethod
    def _to_record(document: Document) -> DocumentRecord:
        return DocumentRecord(
            document_id=document.id,
            filename=document.filename,
            file_type=document.file_type,
            status=document.status,
            upload_timestamp=document.created_at.isoformat(),
            num_chunks=document.num_chunks,
            error_message=document.error_message,
            num_pages=document.num_pages,
            duration_seconds=document.duration_seconds,
            used_ocr=document.used_ocr,
            hidden_span_count=document.hidden_span_count,
            storage_path=document.storage_path,
            size_bytes=document.size_bytes,
            sha256=document.sha256,
        )
