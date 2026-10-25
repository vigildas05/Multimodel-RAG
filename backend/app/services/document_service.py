"""High-level ingestion orchestrator: raw file bytes -> extraction -> chunking ->
embedding -> storage (Qdrant), for all three modalities. Every write is bound to the
caller's TenantScope, and bumps the workspace's corpus_version afterwards so cached
BM25 indexes know to rebuild.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.core.exceptions import AppError, EmptyDocumentError, UploadRejectedError
from app.core.tenancy import TenantScope
from app.db.audit import record_security_event
from app.ingestion.sandbox import run_sandboxed
from app.models.chunk import Chunk
from app.models.document import DocumentRecord
from app.services.chunking_service import RecursiveChunker
from app.services.embedding_service import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.ocr_service import OCRService
from app.services.vector_store_service import VectorStoreService
from app.services.vision_service import describe_image
from app.services.whisper_service import WhisperService
from app.storage.document_registry import DocumentRegistry
from app.storage.upload_store import UploadStore
from app.storage.workspace_repository import WorkspaceRepository
from security.gateway import SecurityGateway
from security.types import Decision

logger = logging.getLogger("app.services.document")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class DocumentService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        embedding_service: EmbeddingService,
        vector_store: VectorStoreService,
        ocr_service: OCRService,
        whisper_service: WhisperService,
        llm_provider: LLMProvider,
        chunker: RecursiveChunker,
        chunk_size: int,
        chunk_overlap: int,
        upload_store: UploadStore,
        settings: Settings,
        security_gateway: SecurityGateway,
    ):
        self._session_factory = session_factory
        self._workspaces = WorkspaceRepository(session_factory)
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._ocr_service = ocr_service
        self._whisper_service = whisper_service
        self._llm_provider = llm_provider
        self._chunker = chunker
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap
        self._upload_store = upload_store
        self._settings = settings
        self._security_gateway = security_gateway

    def _registry(self, scope: TenantScope) -> DocumentRegistry:
        return DocumentRegistry(self._session_factory, scope.workspace_id)

    def get_chunks(self, scope: TenantScope, document_id: str) -> list[dict]:
        return self._vector_store.get_chunks_by_document(scope, document_id)

    async def ingest_file(
        self,
        scope: TenantScope,
        document_id: str,
        filename: str,
        file_type: str,
        source_path: Path,
        force_ocr: bool = False,
    ) -> DocumentRecord:
        """Dispatch to the right modality; marks the document failed if ingestion raises.

        Only an AppError's user-facing message is stored on the document. Anything else is
        logged server-side and recorded as a generic failure, so internal details (paths,
        library errors) never reach the client.
        """
        try:
            if file_type == "pdf":
                return await self.ingest_pdf(scope, document_id, filename, source_path, force_ocr=force_ocr)
            if file_type == "image":
                return await self.ingest_image(scope, document_id, filename, source_path)
            if file_type == "audio":
                return await self.ingest_audio(scope, document_id, filename, source_path)
            raise ValueError(f"Unhandled file type: {file_type}")
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AppError):
                message = exc.message
                logger.warning("Ingestion of %s failed: %s (%s)", document_id, exc.message, exc.detail)
                if isinstance(exc, UploadRejectedError):
                    await run_in_threadpool(
                        record_security_event,
                        self._session_factory,
                        "upload_rejected",
                        "ingestion",
                        severity="medium",
                        workspace_id=scope.workspace_id,
                        user_id=scope.user_id,
                        details={"document_id": document_id, "file_type": file_type, "message": exc.message},
                    )
            else:
                message = "Processing failed due to an internal error."
                logger.exception("Ingestion of %s failed unexpectedly", document_id)
            await run_in_threadpool(self._registry(scope).update_status, document_id, "failed", None, message)
            raise

    async def ingest_pdf(
        self,
        scope: TenantScope,
        document_id: str,
        filename: str,
        source_path: Path,
        force_ocr: bool = False,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> DocumentRecord:
        size, overlap = chunk_size or self._chunk_size, chunk_overlap or self._chunk_overlap
        parsed = await self._sandboxed(
            "app.ingestion.parsers:parse_pdf", str(source_path), self._settings.MAX_PDF_PAGES, force_ocr
        )
        pages = parsed.pages
        if not pages:
            raise EmptyDocumentError("The uploaded PDF has no pages.")
        hidden = [span for page in pages for span in page.hidden_spans]
        if hidden:
            await run_in_threadpool(self._record_hidden_text, scope, document_id, hidden)

        chunks: list[Chunk] = []
        chunk_index = 0
        any_ocr = False
        for page in pages:
            text = page.text
            used_ocr = force_ocr or page.is_scanned
            if used_ocr:
                any_ocr = True
                png_bytes = parsed.page_images[page.page_number]
                ocr_text, _confidence = await run_in_threadpool(self._ocr_service.extract_text, png_bytes)
                text = ocr_text or text

            if not text.strip():
                continue

            page_chunks = self._chunker.split(text, size, overlap)
            for pc in page_chunks:
                chunks.append(
                    Chunk(
                        chunk_id=f"{document_id}_{chunk_index}",
                        document_id=document_id,
                        filename=filename,
                        file_type="pdf",
                        text=pc.text,
                        chunk_index=chunk_index,
                        upload_timestamp=now_iso(),
                        page_number=page.page_number,
                        is_ocr=used_ocr,
                    )
                )
                chunk_index += 1

        if not chunks:
            raise EmptyDocumentError(
                "No extractable text was found in this PDF.",
                detail="The document may be empty, corrupted, or OCR failed to find any text.",
            )

        await self._embed_and_store(scope, chunks)
        return await self._finalize(
            scope,
            document_id,
            filename,
            "pdf",
            chunks,
            num_pages=len(pages),
            used_ocr=any_ocr,
            hidden_span_count=len(hidden),
        )

    async def ingest_image(
        self,
        scope: TenantScope,
        document_id: str,
        filename: str,
        source_path: Path,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> DocumentRecord:
        size, overlap = chunk_size or self._chunk_size, chunk_overlap or self._chunk_overlap
        # Fully decode once inside the sandbox (pixel limit, corrupt files) before OCR/vision see it.
        await self._sandboxed("app.ingestion.parsers:inspect_image", str(source_path), self._settings.MAX_IMAGE_PIXELS)
        file_bytes = source_path.read_bytes()

        ocr_text, _confidence = await run_in_threadpool(self._ocr_service.extract_text, file_bytes)
        vision_description = await describe_image(self._llm_provider, file_bytes)

        combined_parts = []
        if ocr_text.strip():
            combined_parts.append(f"Text extracted from image (OCR):\n{ocr_text.strip()}")
        if vision_description.strip():
            combined_parts.append(f"Visual description:\n{vision_description.strip()}")
        combined_text = "\n\n".join(combined_parts)

        if not combined_text.strip():
            raise EmptyDocumentError("No text or describable content was found in this image.")

        text_chunks = self._chunker.split(combined_text, size, overlap)
        chunks = [
            Chunk(
                chunk_id=f"{document_id}_{i}",
                document_id=document_id,
                filename=filename,
                file_type="image",
                text=tc.text,
                chunk_index=i,
                upload_timestamp=now_iso(),
                is_ocr=bool(ocr_text.strip()),
                source_location="ocr+vision",
            )
            for i, tc in enumerate(text_chunks)
        ]

        await self._embed_and_store(scope, chunks)
        return await self._finalize(scope, document_id, filename, "image", chunks, used_ocr=bool(ocr_text.strip()))

    async def ingest_audio(
        self,
        scope: TenantScope,
        document_id: str,
        filename: str,
        source_path: Path,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> DocumentRecord:
        size, overlap = chunk_size or self._chunk_size, chunk_overlap or self._chunk_overlap
        # Duration check in the sandbox, so an hours-long file is refused before transcription.
        await self._sandboxed("app.ingestion.parsers:probe_audio", str(source_path), self._settings.MAX_AUDIO_SECONDS)

        transcription = await run_in_threadpool(self._whisper_service.transcribe, str(source_path))
        if not transcription.segments:
            raise EmptyDocumentError("No speech was detected in this audio file.")

        packed = self._pack_segments(transcription.segments, size, overlap)
        chunks = [
            Chunk(
                chunk_id=f"{document_id}_{i}",
                document_id=document_id,
                filename=filename,
                file_type="audio",
                text=text,
                chunk_index=i,
                upload_timestamp=now_iso(),
                audio_start_time=start,
                audio_end_time=end,
            )
            for i, (text, start, end) in enumerate(packed)
        ]

        await self._embed_and_store(scope, chunks)
        duration = transcription.segments[-1].end if transcription.segments else None
        return await self._finalize(scope, document_id, filename, "audio", chunks, duration_seconds=duration)

    @staticmethod
    def _pack_segments(segments, chunk_size: int, chunk_overlap: int) -> list[tuple[str, float, float]]:
        """Greedily pack consecutive Whisper segments (already natural speech units)
        up to chunk_size, tagging each packed chunk with its covering time range."""
        packed: list[tuple[str, float, float]] = []
        buffer_text = ""
        buffer_start: float | None = None
        buffer_end: float | None = None

        for seg in segments:
            candidate = f"{buffer_text} {seg.text}".strip() if buffer_text else seg.text
            if len(candidate) <= chunk_size or buffer_start is None:
                buffer_text = candidate
                buffer_start = seg.start if buffer_start is None else buffer_start
                buffer_end = seg.end
            else:
                packed.append((buffer_text, buffer_start, buffer_end))
                buffer_text = seg.text
                buffer_start = seg.start
                buffer_end = seg.end
        if buffer_text:
            packed.append((buffer_text, buffer_start, buffer_end))
        return packed

    async def _guard_chunks(self, scope: TenantScope, chunks: list[Chunk]) -> None:
        """Layer 2 (ingest guard): scan every chunk — including OCR, transcript and image
        description text — for indirect prompt injection, and quarantine risky ones."""
        results = await run_in_threadpool(self._security_gateway.scan_chunks, [c.text for c in chunks])
        quarantined = []
        for chunk, result in zip(chunks, results, strict=False):
            chunk.injection_risk = round(result.risk, 3)
            if result.decision == Decision.BLOCK:
                chunk.quarantined = True
                chunk.quarantine_reasons = result.categories or ["classifier"]
                if result.classifier_score is not None and result.classifier_score >= 0.5:
                    chunk.quarantine_reasons = sorted({*chunk.quarantine_reasons, "classifier"})
                quarantined.append((chunk, result))
        if quarantined:
            await run_in_threadpool(
                record_security_event,
                self._session_factory,
                "chunk_quarantined",
                "ingest_guard",
                severity="high",
                workspace_id=scope.workspace_id,
                user_id=scope.user_id,
                risk_score=max(r.risk for _, r in quarantined),
                details={
                    "document_id": chunks[0].document_id,
                    "filename": chunks[0].filename,
                    "count": len(quarantined),
                    "of_chunks": len(chunks),
                    "samples": [
                        {"chunk_index": c.chunk_index, **r.summary(), "text": c.text[:150]} for c, r in quarantined[:5]
                    ],
                },
            )

    def _record_hidden_text(self, scope: TenantScope, document_id: str, hidden: list) -> None:
        """Recorded before chunking, so it's logged even if nothing visible is left."""
        by_reason: dict[str, int] = {}
        for span in hidden:
            for reason in span.reasons:
                by_reason[reason] = by_reason.get(reason, 0) + 1
        record_security_event(
            self._session_factory,
            "hidden_content_stripped",
            "ingestion",
            severity="medium",
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            details={
                "document_id": document_id,
                "count": len(hidden),
                "by_reason": by_reason,
                # Truncated samples for review in the admin dashboard.
                "samples": [
                    {"page": s.page_number, "reasons": s.reasons, "text": s.text[:120]} for s in hidden[:5]
                ],
            },
        )
        logger.info("Stripped %d hidden text spans from %s: %s", len(hidden), document_id, by_reason)

    async def _sandboxed(self, target: str, *args):
        return await run_in_threadpool(
            run_sandboxed,
            target,
            args,
            self._settings.PARSE_TIMEOUT_SECONDS,
            self._settings.PARSE_MEMORY_LIMIT_MB,
        )

    async def _embed_and_store(self, scope: TenantScope, chunks: list[Chunk]) -> None:
        texts = [c.text for c in chunks]
        await self._guard_chunks(scope, chunks)
        embeddings = await run_in_threadpool(self._embedding_service.embed_chunks, texts)
        for chunk, vec in zip(chunks, embeddings, strict=False):
            chunk.embedding = vec.tolist()
        await run_in_threadpool(self._vector_store.upsert_chunks, scope, chunks)
        # Bump only after the write is visible, so a reader that sees the new version
        # is guaranteed to load the new chunks.
        await run_in_threadpool(self._workspaces.bump_corpus_version, scope.workspace_id)

    async def _finalize(
        self,
        scope: TenantScope,
        document_id: str,
        filename: str,
        file_type: str,
        chunks: list[Chunk],
        num_pages: int | None = None,
        duration_seconds: float | None = None,
        used_ocr: bool = False,
        hidden_span_count: int = 0,
    ) -> DocumentRecord:
        registry = self._registry(scope)
        await run_in_threadpool(
            registry.update_status,
            document_id,
            "ready",
            len(chunks),
            None,
            num_pages,
            duration_seconds,
            used_ocr,
            hidden_span_count,
            sum(c.quarantined for c in chunks),
        )
        record = await run_in_threadpool(registry.get, document_id)
        return record

    async def delete_document(self, scope: TenantScope, document_id: str) -> None:
        registry = self._registry(scope)
        record = await run_in_threadpool(registry.get, document_id)
        await run_in_threadpool(self._vector_store.delete_by_document_id, scope, document_id)
        await run_in_threadpool(registry.delete, document_id)
        if record is not None:
            await run_in_threadpool(self._upload_store.delete, record.storage_path)
        await run_in_threadpool(self._workspaces.bump_corpus_version, scope.workspace_id)

    async def reset_workspace(self, scope: TenantScope) -> int:
        """Delete every document in the caller's workspace (and only that workspace)."""
        registry = self._registry(scope)
        records = await run_in_threadpool(registry.list_all)
        await run_in_threadpool(self._vector_store.delete_workspace, scope)
        await run_in_threadpool(registry.reset)
        await run_in_threadpool(self._upload_store.delete_workspace, scope.workspace_id)
        await run_in_threadpool(self._workspaces.bump_corpus_version, scope.workspace_id)
        return len(records)
