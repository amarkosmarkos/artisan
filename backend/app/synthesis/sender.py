"""Sender synthesis: ICP + value proposition.

Synthesis sees ONLY validated observations, never raw page text. Each ICP
field and the value proposition reference the observation_ids that
supported them. Confidence is computed deterministically as a function of
evidence count and average observation confidence -- not invented by the
LLM.
"""
from __future__ import annotations

import logging
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
    customer: str = ""
    pain: str = ""
    outcome: str = ""
    mechanism: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


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


_SYSTEM_VP = """You synthesize a single Value Proposition from validated observations only.

OUTPUT (JSON):
{
  "customer":  "who the product is for (one phrase)",
  "pain":      "the core problem solved (one sentence)",
  "outcome":   "the measurable benefit (one sentence)",
  "mechanism": "how the product delivers (one sentence)",
  "evidence_refs": [observation_id, ...]
}

Rules:
- Use ONLY information present in the observations.
- Each phrase is short, specific, and free of marketing fluff.
- If you cannot determine a field, leave it as "" with empty evidence_refs.
- evidence_refs lists the observations that informed this VP (typically 2-6).
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


def synthesize_value_proposition(
    observations: list[Observation],
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> ValueProposition:
    if not observations:
        return ValueProposition()
    user = (
        "Validated observations:\n\n"
        + _format_observations(observations)
        + "\n\nReturn the value proposition JSON now."
    )
    try:
        draft = llm.structured(
            system=_SYSTEM_VP,
            user=user,
            schema=_VPDraft,
            purpose="synthesize_vp",
            usage=usage,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("synthesize_vp failed: %s", e)
        return ValueProposition()
    obs_by_id = {o.observation_id: o for o in observations}
    refs = [r for r in draft.evidence_refs if r in obs_by_id]
    return ValueProposition(
        customer=draft.customer.strip(),
        pain=draft.pain.strip(),
        outcome=draft.outcome.strip(),
        mechanism=draft.mechanism.strip(),
        evidence_refs=refs,
        confidence=_confidence(refs, obs_by_id),
    )


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
