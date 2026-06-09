"""GPU-accelerated Whisper transcription (faster-whisper large-v3-turbo).

Stub: lazy-loaded model. Implementation pending.
"""
from __future__ import annotations

import logging
from typing import BinaryIO

logger = logging.getLogger(__name__)


class WhisperService:
    def __init__(self, model_size: str = "large-v3-turbo", device: str = "cuda") -> None:
        self._model_size = model_size
        self._device = device
        self._model = None

    def _load(self) -> None:
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            self._model = WhisperModel(self._model_size, device=self._device, compute_type="float16")
            logger.info("whisper model %s loaded on %s", self._model_size, self._device)

    def transcribe(self, audio: BinaryIO, language: str | None = None) -> dict:
        self._load()
        segments, info = self._model.transcribe(audio, language=language)
        return {
            "language": info.language,
            "language_probability": info.language_probability,
            "text": " ".join(seg.text for seg in segments),
        }
