from __future__ import annotations

import logging
import os
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


class WhisperService:
    def __init__(
        self,
        model_size: str = "large-v3-turbo",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: WhisperModel | None = None

    def _get_model(self) -> WhisperModel:
        if self._model is None:
            from faster_whisper import WhisperModel
            logger.info("Loading Whisper model: %s on %s", self._model_size, self._device)
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info("Whisper model loaded")
        return self._model

    def transcribe(self, audio_bytes: bytes, language: str | None = None) -> str:
        model = self._get_model()
        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            segments, _ = model.transcribe(tmp_path, language=language, beam_size=5)
            return "".join(seg.text for seg in segments).strip()
        finally:
            os.unlink(tmp_path)
