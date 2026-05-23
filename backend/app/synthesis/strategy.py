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
    selected_value_proposition_id: str = ""
    selected_value_proposition_label: str = ""
    selection_reason: str = ""
    messaging_angle: str = ""
    angles: list[_AngleDraft] = Field(default_factory=list)
    persona_alignment: _PersonaDraft = Field(default_factory=_PersonaDraft)


_SYSTEM = """You produce a single STRATEGY ARTIFACT for outbound, combining fit assessment and strategy.

You are given:
  - The SENDER's ICP and one or more value propositions (already synthesized).
  - The TARGET company's validated observations (each with an observation_id you may cite).
  - The recipient PERSONA (role + seniority).

Your job is to:
0. SELECT VALUE PROPOSITION (mandatory, even if only one VP is provided):
   - VALUE PROPOSITION 1 is the sender's general company-level fallback VP.
   - Pick a narrower VP only when the target observations and persona clearly
     match that VP's customer/pain/outcome/mechanism.
   - If no narrower VP is clearly better, select VALUE PROPOSITION 1.
   - Output its exact id in `selected_value_proposition_id` (copy it from the input verbatim).
   - Output its label in `selected_value_proposition_label` (copy it from the input verbatim).
   - Output `selection_reason`: ONE short sentence explaining WHY this VP fits this target+persona
     (tie it to specific target observations or ICP overlap; do not be generic).
   - Output `messaging_angle`: ONE short sentence describing the high-level outreach angle implied by
     the selected VP for this target. This is the angle that should shape both emails.
1. Assess whether the TARGET fits the SELECTED VALUE PROPOSITION first. Use
   the SENDER ICP as supporting context, not as a hard exclusion gate. Output
   fit_assessment.level in {strong, plausible, weak, none}.
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
- Do not select a narrow feature/initiative VP just because the target belongs
  to the same broad industry. The target must show a concrete need, trigger,
  customer segment, or persona fit for that narrower VP.
- When a target broadly matches the sender but no specific VP is clearly
  supported, use VALUE PROPOSITION 1 and keep the messaging broad.
- Fit is about the selected VP, not every sender business line. Do NOT mark a
  target as "none" because it lacks evidence for unrelated sender lines
  (e.g. government procurement, military operations, space systems) when the
  selected VP is general/company-wide or commercial aviation.
- Commercial airlines, aircraft operators, airports, aerospace suppliers, and
  aviation service providers are related to aerospace/commercial aviation. Do
  not say an airline is misaligned with aerospace merely because it is not a
  defense/government buyer.
- Use "none" only when there is no credible relationship to the selected VP or
  the target is explicitly in negative_icp. If the target broadly matches the
  selected VP's customer/industry but lacks a strong trigger, prefer
  "plausible" or "weak" plus missing_evidence.
- Every angle must reference at least one target observation_id from the input. If you cannot ground an angle in target observations, set fit_assessment.level appropriately and contact_decision="wait_for_trigger" or "skip".
- Do NOT invent target facts. If the target observations are thin, say so in fit_assessment.missing_evidence.

Output JSON:
{
  "selected_value_proposition_id": "vp_...",
  "selected_value_proposition_label": "...",
  "selection_reason": "...",
  "messaging_angle": "...",
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
    label = f" ({vp.label})" if vp.label else ""
    return (
        f"- id: {vp.id or '(none)'}{label}\n"
        f"- customer:  {vp.customer}\n"
        f"- pain:      {vp.pain}\n"
        f"- outcome:   {vp.outcome}\n"
        f"- mechanism: {vp.mechanism}\n"
    )


def _format_vps(vps: list[ValueProposition]) -> str:
    if not vps:
        return "(no sender value propositions)"
    if len(vps) == 1:
        return "VALUE PROPOSITION 1 (GENERAL COMPANY FALLBACK):\n" + _format_vp(vps[0])
    return "\n".join(
        f"VALUE PROPOSITION {i + 1}{' (GENERAL COMPANY FALLBACK)' if i == 0 else ''}:\n{_format_vp(vp)}"
        for i, vp in enumerate(vps)
    )


def _format_target_obs(obs: list[Observation]) -> str:
    return (
        "\n".join(
            f"- {o.observation_id} [{o.kind}] (conf={o.confidence:.2f}, val={(o.validation.value if o.validation else 'none')}): {o.text}"
            for o in obs
        )
        or "(no validated target observations)"
    )


def _tokens_for_match(*parts: str) -> set[str]:
    text = " ".join(p for p in parts if p).lower()
    return {tok for tok in text.replace("/", " ").replace("-", " ").split() if tok}


def _target_mentions_any(observations: list[Observation], needles: set[str]) -> bool:
    haystack = " ".join(o.text.lower() for o in observations)
    return any(n in haystack for n in needles)


def _soften_obvious_cross_line_false_negative(
    *,
    draft_fit: FitLevel,
    reasons: list[str],
    missing_evidence: list[str],
    selected_vp: ValueProposition | None,
    target_observations: list[Observation],
) -> tuple[FitLevel, list[str], list[str]]:
    """Correct common LLM false negatives for multi-line companies.

    Example: marking an airline as "not aligned with aerospace" because it is
    not a defense/government buyer, while the selected VP is general or
    commercial aviation.
    """
    if draft_fit != FitLevel.NONE or selected_vp is None:
        return draft_fit, reasons, missing_evidence

    vp_tokens = _tokens_for_match(
        selected_vp.label,
        selected_vp.customer,
        selected_vp.pain,
        selected_vp.outcome,
        selected_vp.mechanism,
    )
    aviation_vp = bool(
        vp_tokens
        & {
            "airline",
            "airlines",
            "aviation",
            "aircraft",
            "aerospace",
            "commercial",
            "operator",
            "operators",
        }
    )
    aviation_target = _target_mentions_any(
        target_observations,
        {"airline", "airlines", "aviation", "aircraft", "airport", "flight"},
    )
    if not (aviation_vp and aviation_target):
        return draft_fit, reasons, missing_evidence

    corrected_reason = (
        "Target has aviation/airline evidence that broadly matches the selected "
        "aviation or company-level value proposition; unrelated defense/government "
        "requirements should not make fit 'none'."
    )
    cleaned_missing = [
        m
        for m in missing_evidence
        if not any(
            word in m.lower()
            for word in ("government", "military", "defense", "defence", "procurement")
        )
    ]
    return FitLevel.PLAUSIBLE, [corrected_reason, *reasons], cleaned_missing


def synthesize_strategy(
    *,
    sender_icp: ICP,
    sender_vps: list[ValueProposition],
    target_observations: list[Observation],
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> StrategyArtifact:
    if not sender_vps:
        sender_vps = [ValueProposition()]
    user = (
        "SENDER ICP:\n" + _format_icp(sender_icp) + "\n"
        "SENDER VALUE PROPOSITION(S):\n" + _format_vps(sender_vps) + "\n"
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

    vp_by_id = {vp.id: vp for vp in sender_vps if vp.id}
    selected_vp_id = draft.selected_value_proposition_id.strip()
    if selected_vp_id not in vp_by_id:
        # LLM picked an unknown id (or returned nothing). Fall back EXPLICITLY
        # to the first VP and log the fallback so the bug cannot hide.
        fallback_id = sender_vps[0].id if sender_vps else None
        log.warning(
            "strategy: LLM selected unknown vp_id=%r; falling back to %r",
            selected_vp_id,
            fallback_id,
        )
        selected_vp_id = fallback_id or ""

    selected_vp = vp_by_id.get(selected_vp_id)
    # Prefer the label echoed by the LLM, but if it does not match the picked
    # VP, fall back to the VP's actual label. This prevents silent mismatch.
    label_echo = draft.selected_value_proposition_label.strip()
    if selected_vp and (not label_echo or label_echo != selected_vp.label):
        label_echo = selected_vp.label

    fit_level, fit_reasons, missing_evidence = _soften_obvious_cross_line_false_negative(
        draft_fit=draft.fit_assessment.level,
        reasons=draft.fit_assessment.reasons,
        missing_evidence=draft.fit_assessment.missing_evidence,
        selected_vp=selected_vp,
        target_observations=target_observations,
    )
    contact_decision = draft.contact_decision
    selection_reason = draft.selection_reason.strip()
    if draft.fit_assessment.level == FitLevel.NONE and fit_level != FitLevel.NONE:
        if contact_decision == ContactDecision.SKIP:
            contact_decision = ContactDecision.WAIT_FOR_TRIGGER
        if "does not align" in selection_reason.lower():
            selection_reason = (
                "Target has airline/aviation evidence, so the general company "
                "value proposition is the safest broad framing."
            )

    log.info(
        "strategy: selected vp_id=%r label=%r reason=%r",
        selected_vp_id,
        label_echo,
        selection_reason[:120],
    )

    return StrategyArtifact(
        fit_assessment=FitAssessment(
            level=fit_level,
            reasons=fit_reasons,
            risks=draft.fit_assessment.risks,
            missing_evidence=missing_evidence,
        ),
        strategy=Strategy(
            contact_decision=contact_decision,
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
        selected_value_proposition_id=selected_vp_id or None,
        selected_value_proposition_label=label_echo,
        selection_reason=selection_reason,
        messaging_angle=draft.messaging_angle.strip(),
    )
