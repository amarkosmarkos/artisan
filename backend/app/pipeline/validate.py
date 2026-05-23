"""NLI-based observation validation, with optimization.

Real NLI on a CrossEncoder (DeBERTa-v3-xsmall) is the only thing that runs
on observation text. We do **not** swap it for an LLM-as-judge.

Optimization: we skip NLI for observation kinds that are typically
*directly stated* on the page (e.g. pricing, integrations, tech stack,
explicit industry mentions) when the extractor's confidence is high.
Those observations are marked ``entailed`` with their LLM confidence as
the score. NLI is reserved for *inferred / high-risk* kinds where
hallucination matters most: pain points, value-prop claims, triggers,
hiring intent, funding, expansion, leadership changes.

Observations the extractor was unsure about (confidence < 0.7) are always
validated regardless of kind.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Callable

from ..schemas import NliLabel, Observation
from ..services.nli import NliValidator

log = logging.getLogger(__name__)


# How many NLI pairs to score per chunk. Smaller chunks => more frequent UI
# updates at the cost of slightly more Python overhead. 32 keeps the
# tradeoff tight and lines up with sentence-transformers default batch.
_NLI_CHUNK = 32


# High-risk kinds always run NLI. These are commercial inferences the model
# is most likely to overstate.
_HIGH_RISK_KINDS: set[str] = {
    "pain_point",
    "value_prop",
    "trigger",
    "hiring",
    "funding",
    "expansion",
    "leadership",
    "negative_icp",
    "use_case",
}

# Low-risk kinds: usually directly stated on the page. Skip NLI when the
# extractor was confident.
_LOW_RISK_KINDS: set[str] = {
    "industry",
    "customer",
    "buyer_role",
    "pricing",
    "size_band",
    "tech_stack",
    "capability",
    "geography",
    "other",
}


_TRUST_LLM_CONFIDENCE = 0.8


def _needs_nli(obs: Observation) -> bool:
    if obs.kind in _HIGH_RISK_KINDS:
        return True
    if obs.confidence < _TRUST_LLM_CONFIDENCE:
        return True
    return False


def validate_observations(
    observations: list[Observation],
    sections: dict[str, dict],
    *,
    nli: NliValidator,
    on_chunk_done: Callable[[int, int], None] | None = None,
) -> tuple[list[Observation], dict[str, int]]:
    """Run NLI selectively. Returns ``(updated_observations, counts)``.

    NLI scoring is split into chunks so the UI can show live progress
    while CPU inference grinds through ~200 pairs.
    """
    if not observations:
        return [], {"entailed": 0, "neutral": 0, "contradicted": 0, "trusted": 0}

    pairs: list[tuple[str, str]] = []
    nli_idx: list[int] = []
    out = list(observations)

    for i, obs in enumerate(observations):
        sec = sections.get(obs.section_id)
        if not sec:
            continue
        if _needs_nli(obs):
            pairs.append((sec["text"], obs.text))
            nli_idx.append(i)
        else:
            # Trust the extractor: tag as entailed with LLM confidence.
            out[i] = obs.model_copy(
                update={
                    "validation": NliLabel.ENTAILED,
                    "validation_score": obs.confidence,
                }
            )

    counts: Counter[str] = Counter()

    if pairs:
        total = len(pairs)
        done = 0
        for start in range(0, total, _NLI_CHUNK):
            chunk_pairs = pairs[start : start + _NLI_CHUNK]
            chunk_idx = nli_idx[start : start + _NLI_CHUNK]
            results = nli.score_pairs(chunk_pairs)
            for idx, res in zip(chunk_idx, results, strict=False):
                out[idx] = out[idx].model_copy(
                    update={"validation": res.label, "validation_score": res.score}
                )
                counts[res.label.value] += 1
            done += len(chunk_pairs)
            if on_chunk_done:
                try:
                    on_chunk_done(done, total)
                except Exception:  # noqa: BLE001
                    log.debug("validate: on_chunk_done raised; ignoring", exc_info=True)

    # Tally entailed (NLI + trusted) and the breakdown of NLI verdicts.
    final_counts = {
        "entailed": sum(
            1 for o in out if o.validation == NliLabel.ENTAILED
        ),
        "neutral": counts.get("neutral", 0),
        "contradicted": counts.get("contradicted", 0),
        "nli_runs": len(pairs),
        "trusted_skipped": len(observations) - len(pairs),
    }
    log.info(
        "validate: %d observations -- nli_ran=%d trusted_skip=%d entailed=%d contradicted=%d",
        len(observations),
        final_counts["nli_runs"],
        final_counts["trusted_skipped"],
        final_counts["entailed"],
        final_counts["contradicted"],
    )
    return out, final_counts


def filter_for_synthesis(observations: list[Observation]) -> list[Observation]:
    """Keep observations safe to use for synthesis.

    Keep:
    - ENTAILED observations (whether from NLI or extractor-trusted)
    - NEUTRAL observations with extractor-confidence >= 0.7

    Drop CONTRADICTED observations entirely.
    """
    kept: list[Observation] = []
    for o in observations:
        if o.validation == NliLabel.CONTRADICTED:
            continue
        if o.validation == NliLabel.ENTAILED:
            kept.append(o)
            continue
        if o.validation is None or o.validation == NliLabel.NEUTRAL:
            if o.confidence >= 0.7:
                kept.append(o)
    return kept
