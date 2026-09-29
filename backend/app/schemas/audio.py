from pydantic import BaseModel


class TranscriptSegmentSchema(BaseModel):
    text: str
    start: float
    end: float


class TranscriptionResponse(BaseModel):
    full_text: str
    segments: list[TranscriptSegmentSchema]
    language: str
