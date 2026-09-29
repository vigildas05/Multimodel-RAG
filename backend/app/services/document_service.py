"""High-level ingestion orchestrator: raw file bytes -> extraction -> chunking ->
embedding -> storage (Qdrant + BM25 index), for all three modalities.
"""

import logging
from datetime import datetime, timezone

from starlette.concurrency import run_in_threadpool

from app.core.exceptions import EmptyDocumentError
from app.models.chunk import Chunk
from app.models.document import DocumentRecord
from app.services import pdf_service
from app.services.bm25_service import BM25Service
from app.services.chunking_service import RecursiveChunker
from app.services.embedding_service import EmbeddingService
from app.services.llm.base import LLMProvider
from app.services.ocr_service import OCRService
from app.services.vector_store_service import VectorStoreService
from app.services.vision_service import describe_image
from app.services.whisper_service import WhisperService
from app.storage.document_registry import DocumentRegistry

logger = logging.getLogger("app.services.document")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DocumentService:
    def __init__(
        self,
        registry: DocumentRegistry,
        embedding_service: EmbeddingService,
        vector_store: VectorStoreService,
        bm25_service: BM25Service,
        ocr_service: OCRService,
        whisper_service: WhisperService,
        llm_provider: LLMProvider,
        chunker: RecursiveChunker,
        chunk_size: int,
        chunk_overlap: int,
    ):
        self._registry = registry
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._bm25_service = bm25_service
        self._ocr_service = ocr_service
        self._whisper_service = whisper_service
        self._llm_provider = llm_provider
        self._chunker = chunker
        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def rebuild_bm25_from_store(self) -> None:
        self._bm25_service.rebuild(self._vector_store.get_all_chunks())

    def get_chunks(self, document_id: str) -> list[dict]:
        return self._vector_store.get_chunks_by_document(document_id)

    async def ingest_pdf(
        self,
        document_id: str,
        filename: str,
        file_bytes: bytes,
        force_ocr: bool = False,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> DocumentRecord:
        size, overlap = chunk_size or self._chunk_size, chunk_overlap or self._chunk_overlap
        pages = await run_in_threadpool(pdf_service.extract_pages, file_bytes)
        if not pages:
            raise EmptyDocumentError("The uploaded PDF has no pages.")

        chunks: list[Chunk] = []
        chunk_index = 0
        any_ocr = False
        for page in pages:
            text = page.text
            used_ocr = force_ocr or page.is_scanned
            if used_ocr:
                any_ocr = True
                png_bytes = await run_in_threadpool(pdf_service.render_page_to_png, file_bytes, page.page_number)
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

        await self._embed_and_store(chunks)
        return await self._finalize(document_id, filename, "pdf", chunks, num_pages=len(pages), used_ocr=any_ocr)

    async def ingest_image(
        self,
        document_id: str,
        filename: str,
        file_bytes: bytes,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> DocumentRecord:
        size, overlap = chunk_size or self._chunk_size, chunk_overlap or self._chunk_overlap

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

        await self._embed_and_store(chunks)
        return await self._finalize(document_id, filename, "image", chunks, used_ocr=bool(ocr_text.strip()))

    async def ingest_audio(
        self,
        document_id: str,
        filename: str,
        file_path: str,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
    ) -> DocumentRecord:
        size, overlap = chunk_size or self._chunk_size, chunk_overlap or self._chunk_overlap

        transcription = await run_in_threadpool(self._whisper_service.transcribe, file_path)
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

        await self._embed_and_store(chunks)
        duration = transcription.segments[-1].end if transcription.segments else None
        return await self._finalize(document_id, filename, "audio", chunks, duration_seconds=duration)

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

    async def _embed_and_store(self, chunks: list[Chunk]) -> None:
        texts = [c.text for c in chunks]
        embeddings = await run_in_threadpool(self._embedding_service.embed_chunks, texts)
        for chunk, vec in zip(chunks, embeddings):
            chunk.embedding = vec.tolist()
        await run_in_threadpool(self._vector_store.upsert_chunks, chunks)
        await run_in_threadpool(self.rebuild_bm25_from_store)

    async def _finalize(
        self,
        document_id: str,
        filename: str,
        file_type: str,
        chunks: list[Chunk],
        num_pages: int | None = None,
        duration_seconds: float | None = None,
        used_ocr: bool = False,
    ) -> DocumentRecord:
        await run_in_threadpool(
            self._registry.update_status,
            document_id,
            "ready",
            len(chunks),
            None,
            num_pages,
            duration_seconds,
            used_ocr,
        )
        record = await run_in_threadpool(self._registry.get, document_id)
        return record

    async def delete_document(self, document_id: str) -> None:
        await run_in_threadpool(self._vector_store.delete_by_document_id, document_id)
        await run_in_threadpool(self._registry.delete, document_id)
        await run_in_threadpool(self.rebuild_bm25_from_store)

    async def reset_all(self) -> None:
        await run_in_threadpool(self._vector_store.reset_collection)
        await run_in_threadpool(self._registry.reset)
        await run_in_threadpool(self._bm25_service.rebuild, [])
