"""Email writer.

Each outreach angle gets its own LLM call. The writer sees the selected sender
value proposition, ICP, persona guidance, the single angle to write, and the
retrieved target observations.

Safety verification runs later in ``email_guard`` on the final subject/body.
The writer must not emit claims or evidence refs.
"""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, Field

from ..config import settings
from ..schemas import (
    Angle,
    AngleType,
    Email,
    ICP,
    Observation,
    PersonaInput,
    StrategyArtifact,
    ValueProposition,
)
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


class _EmailDraft(BaseModel):
    should_write: bool = True
    skip_reason: str = ""
    subject: str = Field(default="", max_length=180)
    body: str = Field(default="", max_length=2200)


_WRITER_SYSTEM = """You are an expert B2B outbound sales writer.

INPUTS YOU RECEIVE:
- TARGET COMPANY NAME: the recipient's company.
- RECIPIENT NAME: optional first name for the greeting.
- SENDER VALUE PROPOSITION (label, customer, pain, outcome, mechanism).
- SENDER EVIDENCE: real facts about the sender's product, customers, results,
  or mechanism.
- STRATEGY ANGLE FOR THIS EMAIL: the sales angle (pain_led / trigger_led /
  etc), its hypothesis, and the target observations that ground it.
- ADDITIONAL TARGET OBSERVATIONS: extra facts about the target.
- PERSONA: seniority, role, and messaging guidance for the recipient.
- MESSAGING ANGLE / SELECTED VP REASON: why this angle and VP were chosen.

YOUR JOB:
Write ONE send-ready sales email built around a clear commercial story.
The output must be complete plain text — no placeholders, no brackets, no
fields left for the sender to fill in.
Do this even when fit_assessment is weak or contact_decision is skip; those
are diagnostic, not permission to refuse.

REASONING ORDER (follow before writing):
1. IDENTIFY THE SALES ANGLE — the main reason this target might care now.
   Read STRATEGY ANGLE, MESSAGING ANGLE, and SELECTED VP REASON. The angle is
   a concrete commercial problem or opportunity, not a generic category.
   Examples: reducing inference cost, improving deployment efficiency,
   lowering compute footprint, compressing large models, scaling AI systems
   more economically, improving model serving economics.
2. CONNECT THE VALUE PROPOSITION TO THAT ANGLE — explain why the selected
   VP resolves or advances that specific problem for this target. Do NOT
   describe the sender company in the abstract. Show the implication for
   the target's business or technical situation.
3. SELECT ONLY THE STRONGEST SUPPORTING CONTEXT — pick one or two facts
   (target observation, sender evidence, persona detail) that best prove the
   angle. Ignore everything else, even if it appears in the input.
4. BUILD ONE COHERENT NARRATIVE:
   (a) target context or market reality
   (b) why that creates a relevant business/technical problem
   (c) how the selected value proposition maps to that problem
   (d) simple, low-friction CTA

The email must persuade, not inform. Every sentence must advance the story.
Never list loosely connected facts.

HIRING AS TRIGGER (RESTRICTED):
Do NOT use hiring, headcount growth, or "as you expand your workforce" unless
hiring is genuinely the core sales angle AND directly tied to the selected VP
(e.g. team growth, implementation capacity, organizational scaling).
For technical AI infrastructure value propositions, prefer stronger angles:
compute cost, inference efficiency, deployment complexity, latency, scale,
model performance, serving economics.
If a hiring observation exists but a stronger technical or economic angle is
available, use the stronger angle and ignore the hiring signal.

BANNED / STRONGLY DISCOURAGED LANGUAGE:
Never use these or close variants:
- "AI solutions"
- "align with your objectives" / "align with your goals"
- "enhance operational efficiency"
- "explore this further"
- "organizations looking to implement AI effectively"
- "I hope this message finds you well"
- "I would be happy to share more"
- "Would a short conversation be useful?"
- "Let me know your thoughts"
- "as you expand your workforce" (unless hiring is the core angle)
Write in plain, direct language. No corporate filler. No exaggerated claims.

NO PLACEHOLDERS (HARD):
The email must be ready to send as-is. Never output bracket placeholders such
as [First Name], [Target Company], [Your Name], [Your Title], [Your Contact
Information], or any similar token.
- If RECIPIENT NAME is present, open with "Hi <name>,".
- If RECIPIENT NAME is empty or "(none)", open with exactly "Hi,".
- If TARGET COMPANY NAME is known, refer to the company by that name.
- If a value is unknown, omit it naturally — do not substitute a placeholder.

EVIDENCE DISCIPLINE (HARD):
- Target-specific factual claims must come from provided target observations.
  Never invent the target's needs, plans, tech stack, customers, hiring, or
  intent.
- Sender-specific facts (numbers, named methods, customers, outcomes) must
  come from SENDER EVIDENCE. Do not invent metrics or capabilities.
- If evidence is weak, use a general but relevant market-level statement
  instead of inventing specificity.

PREFERRED STRUCTURE:
Subject: name the specific commercial angle — not a generic benefit.

Opening (1-2 sentences):
One strong target-relevant context or market reality tied to the sales angle.

Middle (1-2 sentences):
Connect the selected value proposition to a concrete implication for the
target. Use sender evidence only where it sharpens credibility.

CTA (1 sentence):
Ask for a low-friction next step — a short call, a reply, or a forward.

WRITING STYLE:
- Plain text. 90-130 words. 2-4 short paragraphs.
- Commercial clarity over completeness. Sharp, not safe-and-vague.
- Senior personas (VP, C-level, Founder): lead with business outcome
  (cost, revenue, speed, risk). Mechanism only as a brief hook.
- IC / Manager / Director: operational detail and mechanism are fair game.

OPTIMIZE FOR:
commercial clarity, coherent story, selected sales angle, selected value
proposition, evidence-grounded personalization, readiness to send.

OUTPUT (strict JSON):
{
  "subject": "...",
  "body": "..."
}
"""


_MAX_SENDER_EVIDENCE_ITEMS = 12


def _format_icp(icp: ICP) -> str:
    return (
        f"industries={icp.target_industries.values}; "
        f"sizes={icp.size_bands.values}; "
        f"buyers={icp.likely_buyers.values}; "
        f"triggers={icp.common_triggers.values}; "
        f"negative={icp.negative_icp.values}"
    )


def _format_obs_for_writer(obs: list[Observation]) -> str:
    return "\n".join(
        f"- {o.observation_id} [{o.kind}, conf={o.confidence:.2f}]: {o.text}"
        for o in obs
    ) or "(none)"


def _persona_block(strategy: StrategyArtifact, persona: PersonaInput) -> str:
    pa = strategy.strategy.persona_alignment
    name = (persona.name or "").strip() or "(none)"
    return (
        f"name: {name}\n"
        f"role: {persona.role}\n"
        f"seniority: {persona.seniority.value}\n"
        f"role_relevance: {pa.role_relevance}\n"
        f"preferred_framing: {pa.preferred_framing}\n"
        f"avoid: {pa.avoid}\n"
    )


def _format_angle(
    angle: Angle, observations_by_id: dict[str, Observation]
) -> str:
    grounding_lines = []
    for ref in angle.evidence_refs:
        ob = observations_by_id.get(ref)
        if ob:
            grounding_lines.append(f"  - {ref} [{ob.kind}]: {ob.text}")
    grounding = (
        "\n".join(grounding_lines)
        if grounding_lines
        else "  (no concrete observation tied to this angle; keep tone exploratory)"
    )
    return (
        f"type: {angle.type.value}\n"
        f"hypothesis: {angle.hypothesis}\n"
        "angle_grounding_observations:\n"
        f"{grounding}"
    )


def _select_sender_evidence(
    sender_vp: ValueProposition,
    sender_observations: list[Observation],
) -> list[Observation]:
    """Surface the sender observations the writer actually needs.

    Priority order:
      1. Observations explicitly cited by the selected VP.
      2. Other sender observations, by descending confidence, capped at
         ``_MAX_SENDER_EVIDENCE_ITEMS``.
    """
    by_id = {o.observation_id: o for o in sender_observations}
    pinned: list[Observation] = []
    seen: set[str] = set()
    for ref in sender_vp.evidence_refs:
        ob = by_id.get(ref)
        if ob and ob.observation_id not in seen:
            pinned.append(ob)
            seen.add(ob.observation_id)
    remaining = [
        o for o in sender_observations if o.observation_id not in seen
    ]
    remaining.sort(key=lambda o: o.confidence, reverse=True)
    extra = remaining[: max(0, _MAX_SENDER_EVIDENCE_ITEMS - len(pinned))]
    return pinned + extra


def _format_sender_evidence(obs: list[Observation]) -> str:
    if not obs:
        return "(no sender evidence available)"
    return "\n".join(
        f"- {o.observation_id} [{o.kind}, conf={o.confidence:.2f}]: {o.text}"
        for o in obs
    )


def _angles_for_writer(strategy: StrategyArtifact) -> list[Angle]:
    """Return at least two angles, with fallbacks so writing never stops."""
    angles = list(strategy.strategy.angles)
    seen = {angle.type for angle in angles}
    fallbacks = [
        Angle(
            type=AngleType.PAIN_LED,
            hypothesis=(
                "Write a conservative exploratory email that connects the "
                "sender value proposition to a possible business challenge for "
                "this persona without claiming a confirmed pain."
            ),
            evidence_refs=[],
        ),
        Angle(
            type=AngleType.TRIGGER_LED,
            hypothesis=(
                "Write a conservative exploratory email using any retrieved "
                "target signal if available; otherwise keep the trigger broad "
                "and avoid inventing urgency."
            ),
            evidence_refs=[],
        ),
    ]
    for fallback in fallbacks:
        if fallback.type not in seen:
            angles.append(fallback)
            seen.add(fallback.type)
    return angles[:2]


def _write_email_for_angle(
    *,
    angle: Angle,
    sender_vp: ValueProposition,
    sender_icp: ICP,
    target_company_name: str,
    target_observations: list[Observation],
    sender_evidence: list[Observation],
    strategy: StrategyArtifact,
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> _EmailDraft | None:
    observations_by_id = {o.observation_id: o for o in target_observations}
    company_name = (target_company_name or "").strip() or "(unknown)"
    recipient_name = (persona.name or "").strip() or "(none)"
    user = (
        f"TARGET COMPANY NAME: {company_name}\n"
        f"RECIPIENT NAME: {recipient_name}\n\n"
        "SENDER VALUE PROPOSITION:\n"
        f"- label:     {sender_vp.label}\n"
        f"- customer:  {sender_vp.customer}\n"
        f"- pain:      {sender_vp.pain}\n"
        f"- outcome:   {sender_vp.outcome}\n"
        f"- mechanism: {sender_vp.mechanism}\n\n"
        "SENDER EVIDENCE (use for specific sender claims):\n"
        + _format_sender_evidence(sender_evidence)
        + f"\n\nSENDER ICP (summary): {_format_icp(sender_icp)}\n\n"
        "STRATEGY ANGLE FOR THIS EMAIL:\n"
        + _format_angle(angle, observations_by_id)
        + "\n\nADDITIONAL TARGET OBSERVATIONS:\n"
        + _format_obs_for_writer(target_observations)
        + "\n\nFIT ASSESSMENT: "
        + strategy.fit_assessment.level.value
        + " | contact_decision: "
        + strategy.strategy.contact_decision.value
        + "\nSELECTED VP REASON: "
        + (strategy.selection_reason or "")
        + "\nMESSAGING ANGLE: "
        + (strategy.messaging_angle or "")
        + "\n\nPERSONA:\n"
        + _persona_block(strategy, persona)
        + "\nReturn the JSON for this one email now."
    )
    try:
        return llm.structured(
            system=_WRITER_SYSTEM,
            user=user,
            schema=_EmailDraft,
            purpose=f"writer_write_email_{angle.type.value}",
            usage=usage,
            model=settings.writer_llm_model or None,
            temperature=settings.writer_llm_temperature,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("writer write_email angle=%s failed: %s", angle.type.value, e)
        return None


def _fallback_email_draft(
    *,
    angle: Angle,
    sender_vp: ValueProposition,
    persona: PersonaInput,
    target_company_name: str,
) -> _EmailDraft:
    outcome = sender_vp.outcome.strip() or "improve business outcomes"
    mechanism = sender_vp.mechanism.strip() or "the team's approach"
    customer = sender_vp.customer.strip() or "teams"
    label = sender_vp.label.strip() or "your work"
    company = (target_company_name or "").strip()
    greeting_name = (persona.name or "").strip()
    greeting = f"Hi {greeting_name}," if greeting_name else "Hi,"
    company_phrase = f"the team at {company}" if company else "your team"
    subject = (
        f"Exploring {label} with {company}".strip()
        if company
        else f"Exploring {label}"
    )
    body = (
        f"{greeting}\n\n"
        f"I wanted to reach out because your role as {persona.role} at "
        f"{company_phrase} may touch areas where {customer} evaluate ways "
        f"to {outcome}.\n\n"
        f"The reason I thought it could be relevant is that {mechanism}. I "
        "do not want to assume this is a current priority on your side, "
        "but it may be worth comparing notes if this area is on the "
        "roadmap.\n\n"
        "Worth a 15-minute call next week, or should I send a 1-page "
        "teardown first?"
    )
    return _EmailDraft(subject=subject, body=body)


def write_emails(
    *,
    sender_vp: ValueProposition,
    sender_icp: ICP,
    target_observations: list[Observation],
    sender_observations: list[Observation] | None = None,
    target_company_name: str = "",
    strategy: StrategyArtifact,
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> list[Email]:
    sender_evidence = _select_sender_evidence(
        sender_vp, sender_observations or []
    )
    def build(angle: Angle, d: _EmailDraft) -> Email | None:
        if not d.should_write:
            log.info(
                "write_emails: overriding model refusal for angle=%s reason=%s",
                angle.type.value,
                d.skip_reason[:160],
            )
            d = _fallback_email_draft(
                angle=angle,
                sender_vp=sender_vp,
                persona=persona,
                target_company_name=target_company_name,
            )
        if not d.subject.strip() or not d.body.strip():
            log.info(
                "write_emails: using fallback for angle=%s because subject/body was empty",
                angle.type.value,
            )
            d = _fallback_email_draft(
                angle=angle,
                sender_vp=sender_vp,
                persona=persona,
                target_company_name=target_company_name,
            )
        return Email(
            email_id=f"email_{uuid.uuid4().hex[:10]}",
            angle=angle.type,
            subject=d.subject.strip(),
            body=d.body.strip(),
        )

    angles = _angles_for_writer(strategy)
    out: list[Email] = []
    for angle in angles:
        draft = _write_email_for_angle(
            angle=angle,
            sender_vp=sender_vp,
            sender_icp=sender_icp,
            target_company_name=target_company_name,
            target_observations=target_observations,
            sender_evidence=sender_evidence,
            strategy=strategy,
            persona=persona,
            llm=llm,
            usage=usage,
        )
        if draft is None:
            draft = _fallback_email_draft(
                angle=angle,
                sender_vp=sender_vp,
                persona=persona,
                target_company_name=target_company_name,
            )
        email = build(angle, draft)
        if email:
            out.append(email)
    return out
