"""Standalone transcription endpoint (does not ingest into the knowledge base —
use POST /documents/upload for that). Useful for previewing a transcript before
deciding whether to add the audio as a document, or for debugging Whisper output.
"""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile

from app.config import Settings
from app.core.exceptions import InvalidFileTypeError
from app.core.file_validation import AUDIO_EXTENSIONS, validate_size
from app.dependencies import get_app_settings, get_whisper_service
from app.schemas.audio import TranscriptionResponse, TranscriptSegmentSchema
from app.services.whisper_service import WhisperService
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/audio", tags=["audio"])


@router.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile,
    settings: Settings = Depends(get_app_settings),
    whisper_service: WhisperService = Depends(get_whisper_service),
) -> TranscriptionResponse:
    ext = Path(file.filename).suffix.lower()
    if ext not in AUDIO_EXTENSIONS:
        raise InvalidFileTypeError(
            f"File type '{ext}' is not a supported audio format.",
            detail=f"Allowed: {', '.join(sorted(AUDIO_EXTENSIONS))}",
        )
    file_bytes = await file.read()
    validate_size(len(file_bytes), settings)

    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        result = await run_in_threadpool(whisper_service.transcribe, tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return TranscriptionResponse(
        full_text=result.full_text,
        segments=[TranscriptSegmentSchema(text=s.text, start=s.start, end=s.end) for s in result.segments],
        language=result.language,
    )
