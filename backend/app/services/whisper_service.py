"""Singleton Whisper transcription service via faster-whisper (CTranslate2 backend,
much faster than the reference openai-whisper implementation on both CPU and GPU).
"""

import logging
import threading
from dataclasses import dataclass

from faster_whisper import WhisperModel

from app.core.exceptions import TranscriptionError

logger = logging.getLogger("app.services.whisper")


@dataclass
class TranscriptSegment:
    text: str
    start: float
    end: float


@dataclass
class TranscriptionOutput:
    full_text: str
    segments: list[TranscriptSegment]
    language: str


class WhisperService:
    _instance: "WhisperService | None" = None
    _lock = threading.Lock()

    def __init__(self, model_size: str, device: str = "cpu", compute_type: str = "int8"):
        try:
            self._model = WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError(f"Failed to load Whisper model '{model_size}'.", detail=str(exc)) from exc

    @classmethod
    def get_instance(cls, model_size: str, device: str = "cpu", compute_type: str = "int8") -> "WhisperService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(model_size, device, compute_type)
        return cls._instance

    def transcribe(self, audio_path: str) -> TranscriptionOutput:
        try:
            segments_iter, info = self._model.transcribe(audio_path, beam_size=5)
            segments = [TranscriptSegment(text=s.text.strip(), start=s.start, end=s.end) for s in segments_iter]
        except Exception as exc:  # noqa: BLE001
            raise TranscriptionError("Audio transcription failed.", detail=str(exc)) from exc

        if not segments:
            raise TranscriptionError(
                "No speech was detected in the uploaded audio.",
                detail="The file may be silent, corrupted, or in an unsupported format.",
            )

        full_text = " ".join(s.text for s in segments)
        return TranscriptionOutput(full_text=full_text, segments=segments, language=info.language)
