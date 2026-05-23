"""Sentence embeddings for dedup, overlap, and repair-time evidence matching.

We deliberately avoid using embeddings for *initial* ICP extraction: that
phase is exploratory and must read sections broadly. Embeddings are used
only for:

- measuring angle overlap between the two generated emails
- finding the best supporting section when repairing an unsupported claim
- (optionally) deduplicating near-identical observations
"""
from __future__ import annotations

import logging
import threading

import numpy as np
from sentence_transformers import SentenceTransformer

from ..config import settings

log = logging.getLogger(__name__)


class Embedder:
    _instance: "Embedder | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        log.info("loading embedding model: %s", settings.embedding_model)
        self._model = SentenceTransformer(settings.embedding_model)

    @classmethod
    def instance(cls) -> "Embedder":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float32)
        emb = self._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            batch_size=16,
            show_progress_bar=False,
        )
        return emb.astype(np.float32)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two L2-normalized vectors."""
    if a.size == 0 or b.size == 0:
        return 0.0
    return float(np.dot(a, b))


def get_embedder() -> Embedder:
    return Embedder.instance()
