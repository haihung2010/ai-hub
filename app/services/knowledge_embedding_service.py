from __future__ import annotations

import logging
import struct
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastembed import TextEmbedding

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class KnowledgeEmbeddingService:
    def __init__(self, model_name: str = _DEFAULT_MODEL) -> None:
        self._model_name = model_name
        self._model: TextEmbedding | None = None

    def _get_model(self) -> TextEmbedding:
        if self._model is None:
            from fastembed import TextEmbedding
            logger.info("Loading embedding model: %s", self._model_name)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                self._model = TextEmbedding(model_name=self._model_name)
            logger.info("Embedding model loaded")
        return self._model

    def embed(self, text: str) -> bytes:
        model = self._get_model()
        vector = list(model.embed([text]))[0]
        return struct.pack(f"{len(vector)}f", *vector.tolist())

    @staticmethod
    def similarity(a: bytes, b: bytes) -> float:
        if not a or not b:
            return 0.0
        n = len(a) // 4
        if len(b) // 4 != n:
            return 0.0
        va = struct.unpack(f"{n}f", a)
        vb = struct.unpack(f"{n}f", b)
        dot = sum(x * y for x, y in zip(va, vb))
        return max(0.0, min(1.0, dot))
