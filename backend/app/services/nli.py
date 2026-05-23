"""NLI entailment validator.

Uses a small CrossEncoder NLI model (DeBERTa-v3-xsmall, ~70MB) on CPU.
Given a premise (a section text or evidence snippet) and a hypothesis (an
observation or claim), it returns one of:

    ENTAILED, NEUTRAL, CONTRADICTED

plus a confidence score in [0, 1] over the predicted label.

This is the heart of the "evidence-first" guarantee: synthesis output is
only trusted if the NLI model says it is grounded in the corresponding
section text. The model is loaded lazily so import is cheap.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import numpy as np
from sentence_transformers import CrossEncoder

from ..config import settings
from ..schemas import NliLabel

log = logging.getLogger(__name__)


@dataclass
class NliResult:
    label: NliLabel
    score: float  # probability of the chosen label


class NliValidator:
    """Wrapper around a NLI CrossEncoder.

    The CrossEncoder used (DeBERTa-v3-xsmall-mnli) outputs three logits:
        [0] contradiction
        [1] entailment
        [2] neutral

    Some checkpoints use a different ordering; we read the label2id mapping
    from the underlying model config when available.
    """

    _instance: "NliValidator | None" = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        log.info("loading NLI model: %s", settings.nli_model)
        self._model = CrossEncoder(settings.nli_model, max_length=512)
        # Try to read the label ordering from the HF config.
        label2id = getattr(self._model.config, "label2id", None) or {}
        if label2id:
            normalized: dict[str, int] = {}
            for k, v in label2id.items():
                normalized[k.lower()] = int(v)
            self._idx_contradiction = next(
                (
                    i
                    for k, i in normalized.items()
                    if "contradict" in k
                ),
                0,
            )
            self._idx_entailment = next(
                (i for k, i in normalized.items() if "entail" in k), 1
            )
            self._idx_neutral = next(
                (i for k, i in normalized.items() if "neutral" in k), 2
            )
        else:
            self._idx_contradiction, self._idx_entailment, self._idx_neutral = 0, 1, 2
        log.info(
            "NLI model loaded (idx contradiction=%d entail=%d neutral=%d)",
            self._idx_contradiction,
            self._idx_entailment,
            self._idx_neutral,
        )

    @classmethod
    def instance(cls) -> "NliValidator":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @staticmethod
    def _softmax(x: np.ndarray) -> np.ndarray:
        x = x - x.max(axis=-1, keepdims=True)
        e = np.exp(x)
        return e / e.sum(axis=-1, keepdims=True)

    def score_pairs(self, pairs: list[tuple[str, str]]) -> list[NliResult]:
        """Score (premise, hypothesis) pairs in a single batch."""
        if not pairs:
            return []
        # Truncate aggressively to keep latency predictable.
        trimmed = [
            (p[:2000], h[:500]) for p, h in pairs
        ]
        # show_progress_bar=False is critical: sentence-transformers defaults
        # to True when the root logger is at INFO, which spams "Batches: N/N"
        # tqdm lines into our structured logs and adds zero signal for users.
        scores = self._model.predict(
            trimmed,
            convert_to_numpy=True,
            batch_size=8,
            show_progress_bar=False,
        )
        if scores.ndim == 1:
            # Single pair without batch dim
            scores = scores.reshape(1, -1)
        probs = self._softmax(scores)
        results: list[NliResult] = []
        for row in probs:
            p_ent = float(row[self._idx_entailment])
            p_con = float(row[self._idx_contradiction])
            p_neu = float(row[self._idx_neutral])
            if p_ent >= settings.nli_entailment_threshold and p_ent >= p_con:
                results.append(NliResult(NliLabel.ENTAILED, p_ent))
            elif p_con > p_ent and p_con > p_neu:
                results.append(NliResult(NliLabel.CONTRADICTED, p_con))
            else:
                # Includes the case where entailment exists but below threshold.
                top = max(p_ent, p_neu, p_con)
                results.append(NliResult(NliLabel.NEUTRAL, top))
        return results

    def score(self, premise: str, hypothesis: str) -> NliResult:
        return self.score_pairs([(premise, hypothesis)])[0]


def get_nli() -> NliValidator:
    return NliValidator.instance()
