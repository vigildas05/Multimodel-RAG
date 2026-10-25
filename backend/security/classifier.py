"""Second-stage detector: an open-source prompt-injection classifier.

Default model: protectai/deberta-v3-base-prompt-injection-v2 (Apache-2.0, ~180M params,
labels SAFE / INJECTION). Loaded once, lazily, on CPU. Texts longer than the model's 512
token window are scored in overlapping windows and the most suspicious window wins, so an
injection can't hide by being buried in a long, otherwise benign chunk.
"""

import logging
import threading
from typing import Protocol

logger = logging.getLogger("security.classifier")


class InjectionClassifier(Protocol):
    def score(self, texts: list[str]) -> list[float]:
        """Probability (0..1) that each text is a prompt injection."""


class NullClassifier:
    """Used when the ML stage is disabled (e.g. CI without model downloads)."""

    def score(self, texts: list[str]) -> list[float]:
        return [0.0 for _ in texts]


def _windows(text: str, size: int, overlap: int, max_windows: int) -> list[str]:
    if len(text) <= size:
        return [text]
    step = size - overlap
    windows = [text[i : i + size] for i in range(0, len(text) - overlap, step)]
    if len(windows) > max_windows:
        # Keep the start, the end, and evenly spaced windows in between.
        stride = (len(windows) - 1) / (max_windows - 1)
        windows = [windows[round(i * stride)] for i in range(max_windows)]
    return windows


class HuggingFaceClassifier:
    def __init__(
        self,
        model_name: str = "protectai/deberta-v3-base-prompt-injection-v2",
        injection_label: str = "INJECTION",
        window_chars: int = 1500,
        window_overlap: int = 200,
        max_windows: int = 8,
    ):
        self._model_name = model_name
        self._injection_label = injection_label
        self._window_chars = window_chars
        self._window_overlap = window_overlap
        self._max_windows = max_windows
        self._pipeline = None
        self._lock = threading.Lock()

    def warm_up(self) -> None:
        self._get_pipeline()

    def _get_pipeline(self):
        if self._pipeline is None:
            with self._lock:
                if self._pipeline is None:
                    from transformers import pipeline

                    logger.info("Loading prompt-injection classifier %s", self._model_name)
                    self._pipeline = pipeline(
                        "text-classification",
                        model=self._model_name,
                        device=-1,
                        truncation=True,
                        max_length=512,
                    )
        return self._pipeline

    def score(self, texts: list[str]) -> list[float]:
        if not texts:
            return []
        windows, owners = [], []
        for i, text in enumerate(texts):
            for window in _windows(text, self._window_chars, self._window_overlap, self._max_windows):
                windows.append(window)
                owners.append(i)
        outputs = self._get_pipeline()(windows, batch_size=8)
        scores = [0.0] * len(texts)
        for owner, out in zip(owners, outputs):
            p = out["score"] if out["label"] == self._injection_label else 1.0 - out["score"]
            scores[owner] = max(scores[owner], p)
        return scores
