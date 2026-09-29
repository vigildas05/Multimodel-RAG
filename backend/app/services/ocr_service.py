"""PaddleOCR wrapper for scanned PDF pages and uploaded images.

Set before importing paddleocr: paddlepaddle's bundled protobuf-generated modules
require protobuf<=3.20, but qdrant-client's grpc stubs pull in a much newer protobuf.
Forcing the pure-Python protobuf implementation lets both coexist in one process
without pinning either package to an incompatible version (slightly slower protobuf
parsing, no functional difference for this app's usage).
"""

import os

os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")

import io
import logging
import threading

import numpy as np
from PIL import Image

from app.core.exceptions import OCRFailureError

logger = logging.getLogger("app.services.ocr")


class OCRService:
    _instance: "OCRService | None" = None
    _lock = threading.Lock()

    def __init__(self):
        try:
            from paddleocr import PaddleOCR

            self._ocr = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        except Exception as exc:  # noqa: BLE001
            raise OCRFailureError("Failed to initialize the OCR engine.", detail=str(exc)) from exc

    @classmethod
    def get_instance(cls) -> "OCRService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def extract_text(self, image_bytes: bytes) -> tuple[str, float]:
        try:
            image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            array = np.array(image)
            result = self._ocr.ocr(array, cls=True)
        except Exception as exc:  # noqa: BLE001
            raise OCRFailureError("OCR extraction failed on the provided image.", detail=str(exc)) from exc

        if not result or not result[0]:
            return "", 0.0

        lines = result[0]
        texts = [line[1][0] for line in lines]
        confidences = [line[1][1] for line in lines]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return "\n".join(texts), avg_confidence
