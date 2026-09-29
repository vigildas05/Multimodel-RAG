"""Standalone image analysis endpoint (OCR + vision description), does not ingest
into the knowledge base — use POST /documents/upload for that."""

from pathlib import Path

from fastapi import APIRouter, Depends, UploadFile
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.core.exceptions import InvalidFileTypeError
from app.core.file_validation import IMAGE_EXTENSIONS, validate_size
from app.dependencies import get_app_settings, get_llm_provider, get_ocr_service
from app.schemas.image import ImageAnalysisResponse
from app.services.llm.base import LLMProvider
from app.services.ocr_service import OCRService
from app.services.vision_service import describe_image

router = APIRouter(prefix="/images", tags=["images"])


@router.post("/analyze", response_model=ImageAnalysisResponse)
async def analyze_image(
    file: UploadFile,
    settings: Settings = Depends(get_app_settings),
    ocr_service: OCRService = Depends(get_ocr_service),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> ImageAnalysisResponse:
    ext = Path(file.filename).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise InvalidFileTypeError(
            f"File type '{ext}' is not a supported image format.",
            detail=f"Allowed: {', '.join(sorted(IMAGE_EXTENSIONS))}",
        )
    file_bytes = await file.read()
    validate_size(len(file_bytes), settings)

    ocr_text, confidence = await run_in_threadpool(ocr_service.extract_text, file_bytes)
    vision_description = await describe_image(llm_provider, file_bytes)

    return ImageAnalysisResponse(
        ocr_text=ocr_text,
        ocr_confidence=confidence,
        vision_description=vision_description,
    )
