"""Sender synthesis: ICP + value proposition.

Synthesis sees ONLY validated observations, never raw page text. Each ICP
field and the value proposition reference the observation_ids that
supported them. Confidence is computed deterministically as a function of
evidence count and average observation confidence -- not invented by the
LLM.
"""
from __future__ import annotations

import logging
import re
import uuid
from statistics import mean

from pydantic import BaseModel, Field

from ..schemas import (
    FieldWithEvidence,
    ICP,
    Observation,
    ValueProposition,
)
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


# Observation kinds that are good candidates for VP evidence when the LLM
# fails to emit evidence_refs. These describe the offering and its target.
_VP_FALLBACK_KINDS = {
    "value_prop",
    "customer",
    "pain_point",
    "use_case",
    "capability",
    "buyer_role",
    "industry",
}

_STOPWORDS = {
    "the", "a", "an", "of", "to", "and", "or", "for", "with", "in", "on",
    "that", "this", "is", "are", "be", "by", "as", "at", "from", "into",
    "its", "their", "our", "we", "you", "they", "it", "have", "has", "had",
    "can", "will", "may", "such", "than", "more", "less", "very", "any",
    "all", "one", "two", "also", "but", "not", "so", "if", "then", "do",
    "does", "was", "were", "been", "being", "who", "what", "which",
    "when", "where", "how", "your", "his", "her", "them", "these", "those",
}


def _significant_tokens(text: str) -> set[str]:
    """Return lowercased alpha tokens with length>=4 that are not stopwords."""
    return {
        t
        for t in re.findall(r"[a-z]+", text.lower())
        if len(t) >= 4 and t not in _STOPWORDS
    }


# ---------- LLM I/O models ----------

class _ICPField(BaseModel):
    values: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class _ICPDraft(BaseModel):
    target_industries: _ICPField = Field(default_factory=_ICPField)
    size_bands: _ICPField = Field(default_factory=_ICPField)
    likely_buyers: _ICPField = Field(default_factory=_ICPField)
    common_triggers: _ICPField = Field(default_factory=_ICPField)
    negative_icp: _ICPField = Field(default_factory=_ICPField)


class _VPDraft(BaseModel):
    label: str = ""
    customer: str = ""
    pain: str = ""
    outcome: str = ""
    mechanism: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class _VPMultiDraft(BaseModel):
    value_propositions: list[_VPDraft] = Field(default_factory=list)


_SYSTEM_ICP = """You synthesize a structured Ideal Customer Profile (ICP) from validated observations only.

INPUTS:
- A bulleted list of OBSERVATIONS. Each has: observation_id, kind, text.
- These are the ONLY facts you may use. Do NOT add facts that are not present.

OUTPUTS (JSON):
{
  "target_industries":  { "values": [...], "evidence_refs": [observation_id, ...] },
  "size_bands":         { "values": [...], "evidence_refs": [...] },
  "likely_buyers":      { "values": [...], "evidence_refs": [...] },
  "common_triggers":    { "values": [...], "evidence_refs": [...] },
  "negative_icp":       { "values": [...], "evidence_refs": [...] }
}

Rules:
- Every field value must be backed by at least one observation_id from the input.
- If you have no evidence for a field, return an empty list. Do not invent.
- Keep each value to 1-5 words (e.g. "B2B SaaS", "50-500 employees", "VP of Sales", "Series A funding").
- size_bands should use clear bands: "SMB (<50)", "Mid-market (50-1000)", "Enterprise (1000+)", or specific revenue ranges if cited.
- negative_icp lists explicit non-customers if mentioned (e.g. "consumers", "<10 employees").
"""


_SYSTEM_VP = """You synthesize sender Value Propositions from validated observations only.

A sender may be a small company with one focused offering or a large corporation
with several business lines. Keep the existing VP shape for every item, but
ALWAYS return the broad company-level VP first.

The first value proposition is the GENERAL COMPANY VALUE PROPOSITION:
- It summarizes what the company broadly provides/sells.
- It should be usable when no narrower VP clearly fits a target.
- It must NOT be a single feature, article, initiative, announcement, or one
  narrow product page.
- For large corporations, describe the shared value across the main business
  lines, not a niche use case.

After the first VP, you may return additional narrower VPs when the observations
support distinct business lines, product lines, initiatives, or features.

OUTPUT (JSON):
{
  "value_propositions": [
    {
      "label": "General company value proposition",
      "customer":  "who this offering is for (one phrase)",
      "pain":      "the core problem solved (one sentence)",
      "outcome":   "the measurable benefit (one sentence)",
      "mechanism": "how the product delivers (one sentence)",
      "evidence_refs": ["obs_xxx", "obs_yyy", ...]
    }
  ]
}

HARD RULES:
- Use ONLY information present in the observations.
- Return 1-5 value propositions. VP #1 is ALWAYS the general company VP. VPs
  #2-#5 are optional narrower VPs. Do not invent lines without evidence.
- Each phrase is short, specific, and free of marketing fluff.
- VP #1 must be broad enough to represent the company overall. If the evidence
  contains only niche examples, generalize cautiously from multiple observations
  about the company's main products/services/customers; do not let a single
  feature become the company-wide VP.
- Additional VPs can be business lines, product lines, initiatives, or features,
  but their labels must make that narrower scope clear.
- Do not confuse economic buyers/customers with end users. If an offering helps
  passengers, employees, patients, or citizens, the customer is still the buyer
  named or implied by the evidence (e.g. airlines, employers, hospitals,
  governments).
- evidence_refs is MANDATORY. For EVERY value proposition you emit, you MUST
  list at least 2 observation_ids from the input that justify it (typically
  2-6). Copy the ids verbatim. A VP with empty evidence_refs is invalid; if
  you cannot ground a candidate VP in at least 2 observations, do not emit
  it at all.
- Prefer observations whose kind is one of: value_prop, customer, pain_point,
  use_case, capability, buyer_role, industry.
"""


def _format_observations(obs: list[Observation]) -> str:
    return "\n".join(
        f"- {o.observation_id} [{o.kind}] (conf={o.confidence:.2f}): {o.text}"
        for o in obs
    )


def _confidence(refs: list[str], obs_by_id: dict[str, Observation]) -> float:
    """Deterministic confidence: scale evidence count, weight by observation confidence."""
    backing = [obs_by_id[r] for r in refs if r in obs_by_id]
    if not backing:
        return 0.0
    avg = mean(o.confidence for o in backing)
    # Saturating function: 1 obs -> ~0.55, 3 -> ~0.85, 5+ -> ~0.95
    n = len(backing)
    quantity_factor = 1.0 - (0.5 ** n)
    return round(min(0.99, 0.5 * quantity_factor + 0.5 * avg), 3)


def _build_field(
    draft: _ICPField, obs_by_id: dict[str, Observation]
) -> FieldWithEvidence:
    refs = [r for r in draft.evidence_refs if r in obs_by_id]
    return FieldWithEvidence(
        values=draft.values,
        evidence_refs=refs,
        confidence=_confidence(refs, obs_by_id),
    )


def synthesize_icp(
    observations: list[Observation],
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> ICP:
    if not observations:
        return ICP()
    user = (
        "Validated observations:\n\n"
        + _format_observations(observations)
        + "\n\nReturn the ICP JSON now."
    )
    try:
        draft = llm.structured(
            system=_SYSTEM_ICP,
            user=user,
            schema=_ICPDraft,
            purpose="synthesize_icp",
            usage=usage,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("synthesize_icp failed: %s", e)
        return ICP()
    obs_by_id = {o.observation_id: o for o in observations}
    return ICP(
        target_industries=_build_field(draft.target_industries, obs_by_id),
        size_bands=_build_field(draft.size_bands, obs_by_id),
        likely_buyers=_build_field(draft.likely_buyers, obs_by_id),
        common_triggers=_build_field(draft.common_triggers, obs_by_id),
        negative_icp=_build_field(draft.negative_icp, obs_by_id),
    )


def _find_supporting_observations(
    draft: _VPDraft, observations: list[Observation]
) -> list[str]:
    """Pick observation_ids that lexically support a VP draft.

    Used when the LLM omitted evidence_refs. Scans observations whose kind
    is plausibly VP-supporting, scores them by significant-token overlap
    with the VP fields, and returns the top matches. Deterministic, so the
    UI's "Why?" expansion always has something to show.
    """
    vp_text = " ".join(
        [draft.customer, draft.pain, draft.outcome, draft.mechanism, draft.label]
    )
    vp_tokens = _significant_tokens(vp_text)
    if not vp_tokens:
        return []
    scored: list[tuple[int, float, Observation]] = []
    for o in observations:
        if o.kind not in _VP_FALLBACK_KINDS:
            continue
        overlap = len(vp_tokens & _significant_tokens(o.text))
        if overlap >= 2:
            scored.append((overlap, o.confidence, o))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [o.observation_id for _, _, o in scored[:6]]


def _draft_to_vp(
    draft: _VPDraft,
    obs_by_id: dict[str, Observation],
    observations: list[Observation],
) -> ValueProposition:
    refs = [r for r in draft.evidence_refs if r in obs_by_id]
    if not refs:
        # The LLM shipped a VP without grounding it. Recover deterministically
        # so the UI never shows "0 observations" for a populated VP. Logged
        # so the regression is visible in the run output.
        fallback = _find_supporting_observations(draft, observations)
        if fallback:
            log.warning(
                "vp synth: LLM omitted evidence_refs for label=%r; "
                "falling back to %d keyword-matched observations",
                draft.label or "(unlabeled)",
                len(fallback),
            )
            refs = fallback
        else:
            log.warning(
                "vp synth: no evidence_refs and no keyword fallback for "
                "label=%r; VP will render as ungrounded",
                draft.label or "(unlabeled)",
            )
    label = draft.label.strip() or "Primary offering"
    return ValueProposition(
        id=f"vp_{uuid.uuid4().hex[:10]}",
        label=label,
        customer=draft.customer.strip(),
        pain=draft.pain.strip(),
        outcome=draft.outcome.strip(),
        mechanism=draft.mechanism.strip(),
        evidence_refs=refs,
        confidence=_confidence(refs, obs_by_id),
    )


def synthesize_value_propositions(
    observations: list[Observation],
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> list[ValueProposition]:
    if not observations:
        return []
    user = (
        "Validated observations:\n\n"
        + _format_observations(observations)
        + "\n\nReturn the value propositions JSON now."
    )
    try:
        draft = llm.structured(
            system=_SYSTEM_VP,
            user=user,
            schema=_VPMultiDraft,
            purpose="synthesize_vp",
            usage=usage,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("synthesize_vp failed: %s", e)
        return []
    obs_by_id = {o.observation_id: o for o in observations}
    vps = [
        _draft_to_vp(d, obs_by_id, observations)
        for d in draft.value_propositions[:5]
    ]
    # Drop empty shells with no evidence and no copy.
    vps = [
        vp
        for vp in vps
        if vp.evidence_refs
        or any([vp.customer, vp.pain, vp.outcome, vp.mechanism])
    ]
    log.info(
        "vp synth: produced %d value propositions (primary=%r, labels=%s, evidence_counts=%s)",
        len(vps),
        vps[0].label if vps else None,
        [vp.label for vp in vps],
        [len(vp.evidence_refs) for vp in vps],
    )
    return vps or []


def synthesize_value_proposition(
    observations: list[Observation],
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> ValueProposition:
    """Backward-compatible single-VP entry point (returns primary)."""
    from .value_props_store import primary_value_proposition

    vps = synthesize_value_propositions(observations, llm=llm, usage=usage)
    return primary_value_proposition(vps)


def compute_field_gaps(icp: ICP) -> tuple[list[str], dict[str, int], dict[str, float]]:
    """Return (missing_fields, counts, confidences) for the Planner."""
    fields = {
        "target_industries": icp.target_industries,
        "size_bands": icp.size_bands,
        "likely_buyers": icp.likely_buyers,
        "common_triggers": icp.common_triggers,
        "negative_icp": icp.negative_icp,
    }
    missing = [
        name
        for name, f in fields.items()
        if not f.values and name != "negative_icp"  # negative ICP is optional
    ]
    counts = {name: len(f.evidence_refs) for name, f in fields.items()}
    confs = {name: f.confidence for name, f in fields.items()}
    return missing, counts, confs
