import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from app.models.document import DocumentRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL,
    status TEXT NOT NULL,
    upload_timestamp TEXT NOT NULL,
    num_chunks INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    num_pages INTEGER,
    duration_seconds REAL,
    used_ocr INTEGER NOT NULL DEFAULT 0
);
"""


class DocumentRegistry:
    """SQLite-backed source of truth for document-level metadata and ingestion status."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def create(self, record: DocumentRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO documents
                (document_id, filename, file_type, status, upload_timestamp,
                 num_chunks, error_message, num_pages, duration_seconds, used_ocr)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.document_id,
                    record.filename,
                    record.file_type,
                    record.status,
                    record.upload_timestamp,
                    record.num_chunks,
                    record.error_message,
                    record.num_pages,
                    record.duration_seconds,
                    int(record.used_ocr),
                ),
            )
            conn.commit()

    def update_status(
        self,
        document_id: str,
        status: str,
        num_chunks: int | None = None,
        error_message: str | None = None,
        num_pages: int | None = None,
        duration_seconds: float | None = None,
        used_ocr: bool | None = None,
    ) -> None:
        fields = ["status = ?"]
        values: list = [status]
        if num_chunks is not None:
            fields.append("num_chunks = ?")
            values.append(num_chunks)
        if error_message is not None:
            fields.append("error_message = ?")
            values.append(error_message)
        if num_pages is not None:
            fields.append("num_pages = ?")
            values.append(num_pages)
        if duration_seconds is not None:
            fields.append("duration_seconds = ?")
            values.append(duration_seconds)
        if used_ocr is not None:
            fields.append("used_ocr = ?")
            values.append(int(used_ocr))
        values.append(document_id)

        with self._lock, self._connect() as conn:
            conn.execute(f"UPDATE documents SET {', '.join(fields)} WHERE document_id = ?", values)
            conn.commit()

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE document_id = ?", (document_id,)).fetchone()
            return self._row_to_record(row) if row else None

    def list_all(self) -> list[DocumentRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM documents ORDER BY upload_timestamp DESC").fetchall()
            return [self._row_to_record(row) for row in rows]

    def delete(self, document_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
            conn.commit()

    def reset(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM documents")
            conn.commit()

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            document_id=row["document_id"],
            filename=row["filename"],
            file_type=row["file_type"],
            status=row["status"],
            upload_timestamp=row["upload_timestamp"],
            num_chunks=row["num_chunks"],
            error_message=row["error_message"],
            num_pages=row["num_pages"],
            duration_seconds=row["duration_seconds"],
            used_ocr=bool(row["used_ocr"]),
        )
