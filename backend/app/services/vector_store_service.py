import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.exceptions import VectorStoreUnavailableError
from app.models.chunk import Chunk

logger = logging.getLogger("app.services.vector_store")


class VectorStoreService:
    def __init__(self, host: str, port: int, collection_name: str, vector_dim: int):
        self._collection_name = collection_name
        self._vector_dim = vector_dim
        try:
            self._client = QdrantClient(host=host, port=port, timeout=10)
            self._ensure_collection()
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError(
                "Could not connect to Qdrant. Is the Qdrant container running?",
                detail=str(exc),
            ) from exc

    def _ensure_collection(self) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection_name not in existing:
            self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=self._vector_dim, distance=Distance.COSINE),
            )
            self._client.create_payload_index(self._collection_name, "document_id", field_schema="keyword")
            self._client.create_payload_index(self._collection_name, "file_type", field_schema="keyword")
            logger.info("Created Qdrant collection '%s'", self._collection_name)

    def reset_collection(self) -> None:
        try:
            self._client.delete_collection(self._collection_name)
        except (UnexpectedResponse, ValueError):
            pass
        self._ensure_collection()

    def upsert_chunks(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=chunk.embedding,
                payload={
                    "document_id": chunk.document_id,
                    "chunk_id": chunk.chunk_id,
                    "filename": chunk.filename,
                    "file_type": chunk.file_type,
                    "text": chunk.text,
                    "chunk_index": chunk.chunk_index,
                    "page_number": chunk.page_number,
                    "source_location": chunk.source_location,
                    "audio_start_time": chunk.audio_start_time,
                    "audio_end_time": chunk.audio_end_time,
                    "upload_timestamp": chunk.upload_timestamp,
                    "is_ocr": chunk.is_ocr,
                },
            )
            for chunk in chunks
        ]
        try:
            self._client.upsert(collection_name=self._collection_name, points=points)
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Failed to store chunks in Qdrant.", detail=str(exc)) from exc

    def search(
        self,
        query_vector,
        top_k: int,
        document_ids: list[str] | None = None,
        file_types: list[str] | None = None,
    ) -> list[dict]:
        query_filter = None
        conditions = []
        if document_ids:
            conditions.append(
                Filter(should=[FieldCondition(key="document_id", match=MatchValue(value=d)) for d in document_ids])
            )
        if file_types:
            conditions.append(
                Filter(should=[FieldCondition(key="file_type", match=MatchValue(value=f)) for f in file_types])
            )
        if conditions:
            query_filter = Filter(must=conditions)

        try:
            results = self._client.search(
                collection_name=self._collection_name,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Qdrant search failed.", detail=str(exc)) from exc

        return [{"score": r.score, **r.payload} for r in results]

    def delete_by_document_id(self, document_id: str) -> None:
        try:
            self._client.delete(
                collection_name=self._collection_name,
                points_selector=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]),
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Failed to delete document chunks from Qdrant.", detail=str(exc)) from exc

    def get_chunks_by_document(self, document_id: str) -> list[dict]:
        try:
            points, _ = self._client.scroll(
                collection_name=self._collection_name,
                scroll_filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]),
                limit=1000,
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Failed to fetch chunks from Qdrant.", detail=str(exc)) from exc
        chunks = [p.payload for p in points]
        chunks.sort(key=lambda c: c.get("chunk_index", 0))
        return chunks

    def get_all_chunks(self) -> list[dict]:
        """Used to rebuild the in-memory BM25 index from Qdrant on startup."""
        all_points = []
        offset = None
        while True:
            points, offset = self._client.scroll(
                collection_name=self._collection_name, limit=500, offset=offset, with_payload=True
            )
            all_points.extend(p.payload for p in points)
            if offset is None:
                break
        return all_points
