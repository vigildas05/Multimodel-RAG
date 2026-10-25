"""The only module that talks to Qdrant.

Tenant isolation is enforced here, at the single choke point: every read, write and
delete takes a TenantScope and always adds a `workspace_id == scope.workspace_id`
condition. There is intentionally no unscoped search, scroll or delete — callers can't
forget the filter because they can't express a query without a scope.
(tests/test_tenant_isolation.py checks both properties.)
"""

import logging
import uuid

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import UnexpectedResponse
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    KeywordIndexParams,
    KeywordIndexType,
    MatchAny,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.exceptions import VectorStoreUnavailableError
from app.core.tenancy import TenantScope
from app.models.chunk import Chunk

logger = logging.getLogger("app.services.vector_store")

WORKSPACE_FIELD = "workspace_id"


class VectorStoreService:
    def __init__(
        self,
        host: str,
        port: int,
        collection_name: str,
        vector_dim: int,
        client: QdrantClient | None = None,
    ):
        """`client` lets tests inject e.g. QdrantClient(":memory:") instead of a server."""
        self._collection_name = collection_name
        self._vector_dim = vector_dim
        try:
            self._client = client or QdrantClient(host=host, port=port, timeout=10)
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
            logger.info("Created Qdrant collection '%s'", self._collection_name)
        # Idempotent; also upgrades collections created before tenancy existed.
        # is_tenant=True tells Qdrant to co-locate each workspace's points on disk, so
        # filtered searches stay fast with many tenants in one collection.
        self._client.create_payload_index(
            self._collection_name,
            WORKSPACE_FIELD,
            field_schema=KeywordIndexParams(type=KeywordIndexType.KEYWORD, is_tenant=True),
        )
        self._client.create_payload_index(self._collection_name, "document_id", field_schema="keyword")
        self._client.create_payload_index(self._collection_name, "file_type", field_schema="keyword")

    def reset_collection(self) -> None:
        """Destructive and UNSCOPED: drops every tenant's data. Only for test and eval setup
        against their own collections; never called from a request path."""
        try:
            self._client.delete_collection(self._collection_name)
        except (UnexpectedResponse, ValueError):
            pass
        self._ensure_collection()

    @staticmethod
    def _scoped_filter(scope: TenantScope, *conditions: FieldCondition | Filter) -> Filter:
        return Filter(
            must=[FieldCondition(key=WORKSPACE_FIELD, match=MatchValue(value=scope.workspace_id)), *conditions]
        )

    def upsert_chunks(self, scope: TenantScope, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=chunk.embedding,
                payload={
                    # Stamped from the caller's scope, never from the chunk or the request.
                    WORKSPACE_FIELD: scope.workspace_id,
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

    def clone_documents(self, scope: TenantScope, target: TenantScope, document_id_map: dict[str, str]) -> int:
        """Copy `scope`'s chunks for the documents in `document_id_map` into `target`,
        re-stamped with the target workspace and the new document IDs. Vectors are copied,
        not recomputed (used to seed demo workspaces from the template). Returns the count."""
        if not document_id_map:
            return 0
        source_filter = self._scoped_filter(
            scope, FieldCondition(key="document_id", match=MatchAny(any=list(document_id_map)))
        )
        copied, offset = 0, None
        try:
            while True:
                points, offset = self._client.scroll(
                    collection_name=self._collection_name,
                    scroll_filter=source_filter,
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=True,
                )
                clones = []
                for point in points:
                    new_document_id = document_id_map[point.payload["document_id"]]
                    payload = {
                        **point.payload,
                        WORKSPACE_FIELD: target.workspace_id,
                        "document_id": new_document_id,
                        "chunk_id": f"{new_document_id}_{point.payload['chunk_index']}",
                    }
                    clones.append(PointStruct(id=str(uuid.uuid4()), vector=point.vector, payload=payload))
                if clones:
                    self._client.upsert(collection_name=self._collection_name, points=clones)
                    copied += len(clones)
                if offset is None:
                    break
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Failed to copy chunks in Qdrant.", detail=str(exc)) from exc
        return copied

    def search(
        self,
        scope: TenantScope,
        query_vector,
        top_k: int,
        document_ids: list[str] | None = None,
        file_types: list[str] | None = None,
    ) -> list[dict]:
        conditions = []
        if document_ids:
            conditions.append(FieldCondition(key="document_id", match=MatchAny(any=list(document_ids))))
        if file_types:
            conditions.append(FieldCondition(key="file_type", match=MatchAny(any=list(file_types))))

        try:
            results = self._client.search(
                collection_name=self._collection_name,
                query_vector=query_vector,
                query_filter=self._scoped_filter(scope, *conditions),
                limit=top_k,
                with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Qdrant search failed.", detail=str(exc)) from exc

        return [{"score": r.score, **r.payload} for r in results]

    def delete_by_document_id(self, scope: TenantScope, document_id: str) -> None:
        self._delete(scope, FieldCondition(key="document_id", match=MatchValue(value=document_id)))

    def delete_workspace(self, scope: TenantScope) -> None:
        self._delete(scope)

    def _delete(self, scope: TenantScope, *conditions: FieldCondition) -> None:
        try:
            self._client.delete(
                collection_name=self._collection_name,
                points_selector=self._scoped_filter(scope, *conditions),
            )
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Failed to delete chunks from Qdrant.", detail=str(exc)) from exc

    def get_chunks_by_document(self, scope: TenantScope, document_id: str) -> list[dict]:
        chunks = self._scroll(scope, FieldCondition(key="document_id", match=MatchValue(value=document_id)))
        chunks.sort(key=lambda c: c.get("chunk_index", 0))
        return chunks

    def get_workspace_chunks(self, scope: TenantScope) -> list[dict]:
        """Every chunk in one workspace; used to build that workspace's BM25 index."""
        return self._scroll(scope)

    def _scroll(self, scope: TenantScope, *conditions: FieldCondition) -> list[dict]:
        payloads: list[dict] = []
        offset = None
        try:
            while True:
                points, offset = self._client.scroll(
                    collection_name=self._collection_name,
                    scroll_filter=self._scoped_filter(scope, *conditions),
                    limit=500,
                    offset=offset,
                    with_payload=True,
                )
                payloads.extend(p.payload for p in points)
                if offset is None:
                    break
        except Exception as exc:  # noqa: BLE001
            raise VectorStoreUnavailableError("Failed to fetch chunks from Qdrant.", detail=str(exc)) from exc
        return payloads
