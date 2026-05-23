"""Target synthesis: fit assessment + strategy in a single LLM call.

The strategy artifact is the bridge between evidence and outreach. It
takes:

- the sender ICP and value proposition (synthesized from sender evidence)
- validated target observations (synthesized from target evidence)
- the recipient persona (role + seniority)

and produces a typed StrategyArtifact whose every angle references the
target observation_ids that motivate it. Persona shapes behavior here
already: the persona_alignment object determines framing for the writer.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..schemas import (
    Angle,
    AngleType,
    ContactDecision,
    FitAssessment,
    FitLevel,
    ICP,
    Observation,
    PersonaAlignment,
    PersonaInput,
    Strategy,
    StrategyArtifact,
    ValueProposition,
)
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


# ---------- Draft schema ----------

class _AngleDraft(BaseModel):
    type: AngleType
    hypothesis: str = Field(min_length=10, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list)


class _PersonaDraft(BaseModel):
    role_relevance: str = "medium"
    role_relevance_reason: str = ""
    preferred_framing: str = ""
    preferred_framing_reason: str = ""
    avoid: list[str] = Field(default_factory=list)
    avoid_reason: str = ""


class _FitDraft(BaseModel):
    level: FitLevel
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class _StrategyDraft(BaseModel):
    fit_assessment: _FitDraft
    contact_decision: ContactDecision
    angles: list[_AngleDraft] = Field(default_factory=list)
    persona_alignment: _PersonaDraft = Field(default_factory=_PersonaDraft)


_SYSTEM = """You produce a single STRATEGY ARTIFACT for outbound, combining fit assessment and strategy.

You are given:
  - The SENDER's ICP and value proposition (already synthesized).
  - The TARGET company's validated observations (each with an observation_id you may cite).
  - The recipient PERSONA (role + seniority).

Your job is to:
1. Assess whether the TARGET fits the SENDER's ICP. Output fit_assessment.level in {strong, plausible, weak, none}.
2. Decide whether to contact now, wait for a trigger, or skip. Output contact_decision in {contact, wait_for_trigger, skip}.
3. Select exactly TWO outreach angles with meaningfully different framings:
     - One "pain_led":     leads with a problem the target likely has (per observations or ICP triggers).
     - One "trigger_led":  leads with a recent or current event from the target (hiring, funding, expansion, launch, leadership change, etc).
   Each angle has a one-sentence HYPOTHESIS and evidence_refs (the target observation_ids that support it).
4. Define persona_alignment with REASONING for each decision:
     - role_relevance: "high" | "medium" | "low" -- based on how directly the value prop maps to this role.
     - role_relevance_reason: ONE short sentence explaining WHY (cite the seniority + the value-prop mechanism).
     - preferred_framing: short phrase like "ROI / pipeline impact" (senior) or "operational mechanism" (IC).
     - preferred_framing_reason: ONE short sentence tying the framing to the role's likely buying motion.
     - avoid: list of framings that would alienate this persona.
     - avoid_reason: ONE short sentence justifying the avoid list (e.g. "VPs disengage from feature-by-feature comparisons").

Rules:
- Senior roles (VP, C-level, Founder) prefer concise business-outcome framing; avoid deep mechanism detail.
- IC / manager roles can receive operational, mechanism-specific framing.
- Every angle must reference at least one target observation_id from the input. If you cannot ground an angle in target observations, set fit_assessment.level appropriately and contact_decision="wait_for_trigger" or "skip".
- Do NOT invent target facts. If the target observations are thin, say so in fit_assessment.missing_evidence.

Output JSON:
{
  "fit_assessment": {
    "level": "strong|plausible|weak|none",
    "reasons": [...],
    "risks": [...],
    "missing_evidence": [...]
  },
  "contact_decision": "contact|wait_for_trigger|skip",
  "angles": [
    { "type": "pain_led",    "hypothesis": "...", "evidence_refs": [...] },
    { "type": "trigger_led", "hypothesis": "...", "evidence_refs": [...] }
  ],
  "persona_alignment": {
    "role_relevance": "high|medium|low",
    "role_relevance_reason": "...",
    "preferred_framing": "...",
    "preferred_framing_reason": "...",
    "avoid": [...],
    "avoid_reason": "..."
  }
}
"""


def _format_icp(icp: ICP) -> str:
    return (
        f"- target_industries: {icp.target_industries.values}\n"
        f"- size_bands: {icp.size_bands.values}\n"
        f"- likely_buyers: {icp.likely_buyers.values}\n"
        f"- common_triggers: {icp.common_triggers.values}\n"
        f"- negative_icp: {icp.negative_icp.values}\n"
    )


def _format_vp(vp: ValueProposition) -> str:
    return (
        f"- customer:  {vp.customer}\n"
        f"- pain:      {vp.pain}\n"
        f"- outcome:   {vp.outcome}\n"
        f"- mechanism: {vp.mechanism}\n"
    )


def _format_target_obs(obs: list[Observation]) -> str:
    return (
        "\n".join(
            f"- {o.observation_id} [{o.kind}] (conf={o.confidence:.2f}, val={(o.validation.value if o.validation else 'none')}): {o.text}"
            for o in obs
        )
        or "(no validated target observations)"
    )


def synthesize_strategy(
    *,
    sender_icp: ICP,
    sender_vp: ValueProposition,
    target_observations: list[Observation],
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> StrategyArtifact:
    user = (
        "SENDER ICP:\n" + _format_icp(sender_icp) + "\n"
        "SENDER VALUE PROPOSITION:\n" + _format_vp(sender_vp) + "\n"
        f"PERSONA:\n- role: {persona.role}\n- seniority: {persona.seniority.value}\n\n"
        "TARGET OBSERVATIONS:\n" + _format_target_obs(target_observations)
        + "\n\nProduce the strategy JSON now."
    )

    try:
        draft = llm.structured(
            system=_SYSTEM,
            user=user,
            schema=_StrategyDraft,
            purpose="synthesize_strategy",
            usage=usage,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("synthesize_strategy failed: %s", e)
        return StrategyArtifact(
            fit_assessment=FitAssessment(
                level=FitLevel.NONE,
                reasons=[],
                risks=[],
                missing_evidence=[f"strategy_error: {e}"],
            ),
            strategy=Strategy(
                contact_decision=ContactDecision.SKIP,
                angles=[],
                persona_alignment=PersonaAlignment(
                    role_relevance="low",
                    role_relevance_reason="",
                    preferred_framing="",
                    preferred_framing_reason="",
                    avoid=[],
                    avoid_reason="",
                ),
            ),
        )

    obs_ids = {o.observation_id for o in target_observations}

    # Drop any hallucinated evidence_refs.
    angles_clean: list[Angle] = []
    for a in draft.angles[:2]:
        refs = [r for r in a.evidence_refs if r in obs_ids]
        angles_clean.append(
            Angle(type=a.type, hypothesis=a.hypothesis.strip(), evidence_refs=refs)
        )

    # Enforce the two-angle requirement: if the LLM produced fewer, mark fit lower.
    if len(angles_clean) < 2 and draft.fit_assessment.level != FitLevel.NONE:
        log.info("strategy: only %d angles produced", len(angles_clean))

    role_rel = draft.persona_alignment.role_relevance.lower()
    if role_rel not in {"high", "medium", "low"}:
        role_rel = "medium"

    return StrategyArtifact(
        fit_assessment=FitAssessment(
            level=draft.fit_assessment.level,
            reasons=draft.fit_assessment.reasons,
            risks=draft.fit_assessment.risks,
            missing_evidence=draft.fit_assessment.missing_evidence,
        ),
        strategy=Strategy(
            contact_decision=draft.contact_decision,
            angles=angles_clean,
            persona_alignment=PersonaAlignment(
                role_relevance=role_rel,  # type: ignore[arg-type]
                role_relevance_reason=draft.persona_alignment.role_relevance_reason.strip(),
                preferred_framing=draft.persona_alignment.preferred_framing,
                preferred_framing_reason=draft.persona_alignment.preferred_framing_reason.strip(),
                avoid=draft.persona_alignment.avoid,
                avoid_reason=draft.persona_alignment.avoid_reason.strip(),
            ),
        ),
    )
