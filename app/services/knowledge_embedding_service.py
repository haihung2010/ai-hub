from __future__ import annotations

import logging
import struct
import threading
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
        self._load_lock = threading.Lock()

    def _get_model(self) -> TextEmbedding:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            from fastembed import TextEmbedding
            logger.info("Loading embedding model: %s", self._model_name)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                self._model = TextEmbedding(model_name=self._model_name, cuda=False)
            logger.info("Embedding model loaded")
        return self._model

    def embed(self, text: str) -> bytes:
        """Embed text and return raw float32 bytes (for DB storage)."""
        model = self._get_model()
        vector = list(model.embed([text]))[0]
        return struct.pack(f"{len(vector)}f", *vector.tolist())

    def embed_as_pgvector(self, text: str) -> str:
        """Embed text and return pgvector literal string '[0.1,0.2,...]'."""
        model = self._get_model()
        vector = list(model.embed([text]))[0]
        return "[" + ",".join(f"{v:.8f}" for v in vector.tolist()) + "]"

    @staticmethod
    def raw_bytes_to_pgvector(raw: bytes, dim: int = 384) -> str | None:
        """Convert stored raw float32 bytes to pgvector literal string."""
        if not raw or len(raw) < dim * 4:
            return None
        floats = struct.unpack(f"{dim}f", raw[: dim * 4])
        return "[" + ",".join(f"{v:.8f}" for v in floats) + "]"

    @staticmethod
    def similarity(a: bytes, b: bytes) -> float:
        """Cosine similarity between two raw float32 byte buffers."""
        if not a or not b:
            return 0.0
        n = len(a) // 4
        if len(b) // 4 != n:
            return 0.0
        va = struct.unpack(f"{n}f", a)
        vb = struct.unpack(f"{n}f", b)
        dot = sum(x * y for x, y in zip(va, vb))
        return max(0.0, min(1.0, dot))
