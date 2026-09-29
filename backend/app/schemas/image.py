from pydantic import BaseModel


class ImageAnalysisResponse(BaseModel):
    ocr_text: str
    ocr_confidence: float
    vision_description: str
