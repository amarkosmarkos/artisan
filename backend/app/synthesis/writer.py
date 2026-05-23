"""Email writer.

Each outreach angle gets its own LLM call. The writer sees the selected sender
value proposition, ICP, persona guidance, the single angle to write, and the
retrieved target observations. It can decline an angle if the retrieved facts
do not support a credible sales email.

Inputs:
- value proposition (sender)
- ICP summary       (sender)
- validated target observations
- strategy artifact (fit + angles + persona alignment)
- persona

Raw website text is never passed to the writer.
"""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, Field

from ..config import settings
from ..schemas import (
    Angle,
    AngleType,
    ClaimStatus,
    Email,
    EmailClaim,
    ICP,
    Observation,
    PersonaInput,
    StrategyArtifact,
    ValueProposition,
)
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


# ---------- Per-angle email writer ----------

class _ClaimDraft(BaseModel):
    text: str = Field(min_length=4, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=4)


class _EmailDraft(BaseModel):
    # Backward-compatible with previous prompt/schema. The writer no longer
    # honors refusals, but accepting these fields prevents old model behavior
    # from breaking structured parsing when it emits them.
    should_write: bool = True
    skip_reason: str = ""
    subject: str = Field(default="", max_length=180)
    body: str = Field(default="", max_length=2200)
    claims: list[_ClaimDraft] = Field(default_factory=list, max_length=6)


_WRITER_SYSTEM = """You are an expert B2B outbound sales writer.

INPUTS:
- Sender value proposition + ICP: what the sender can credibly offer.
- Persona: who the email is addressed to and how they likely think.
- Strategy angle: the intended framing for THIS one email.
- Retrieved target observations: the only target-specific facts you may use.

YOUR JOB:
Always write one high-quality, credible sales email for the given angle, even
when fit_assessment is weak/none or contact_decision is skip. Those fields are
diagnostic context, not permission to refuse. Use the retrieved target
observations to make the email specific when they help. You may connect those
facts to the sender value proposition, but never invent facts about the target,
their priorities, their pain, their tech stack, their projects, or their intent.

If the retrieved observations are thin or the fit is poor, still write the
email, but make it conservative: use softer language, avoid overstating pain,
and position the note as exploratory rather than claiming clear need.

OUTPUT (strict JSON):
{
  "subject": "...",
  "body": "...",
  "claims": [
    { "text": "...", "evidence_refs": ["obs_xxx"] }
  ]
}

SALES WRITING GUIDANCE:
- Sound like a sharp human seller, not a template. Plain text, natural tone.
- Structure the email with 2-4 short paragraphs:
  1. Open with the most relevant retrieved target fact or trigger.
  2. Explain why that fact might matter to this persona.
  3. Connect the sender's value proposition to a plausible business outcome.
  4. End with a low-friction CTA.
- Be concise: usually 90-160 words. More is not better.
- Prefer business outcomes for senior personas; use operational detail only when
  the persona is closer to execution.
- Use the strategy angle as direction, not a script. If the angle is weak,
  adapt it into a credible exploratory note instead of refusing.
- Use retrieved facts where they strengthen relevance. You do not need to use
  every observation. Skip noisy or irrelevant facts.
- Generic sender-side positioning is allowed, but target-specific statements
  must be grounded in retrieved observations.
- If no retrieved target fact is worth using, write a broader but still useful
  email based on sender value proposition + persona context. In that case,
  claims may be empty.

EVIDENCE / CLAIM RULES:
- For every target-specific factual statement you make, add one claim with the
  exact observation_id(s) that support it.
- Claim `text` should be a concise sentence or clause that appears in the body,
  or is a very close substring of a sentence in the body.
- Use only observation_ids from the retrieved observations. Never invent ids.
- Do not cite sender-side value proposition statements as claims; claims are
  for target-specific facts only.
- Never say the target "needs", "wants", "is looking for", "is struggling with",
  or "is prioritizing" something unless a retrieved observation says that.
"""


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
    return (
        f"role: {persona.role}\n"
        f"seniority: {persona.seniority.value}\n"
        f"role_relevance: {pa.role_relevance}\n"
        f"preferred_framing: {pa.preferred_framing}\n"
        f"avoid: {pa.avoid}\n"
    )


def _format_angle(angle: Angle) -> str:
    return (
        f"type: {angle.type.value}\n"
        f"hypothesis: {angle.hypothesis}\n"
        f"evidence_refs_from_strategy: {angle.evidence_refs}"
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
    target_observations: list[Observation],
    strategy: StrategyArtifact,
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> _EmailDraft | None:
    """Produce one email for one strategy angle in an independent LLM call."""
    user = (
        "SENDER VALUE PROPOSITION:\n"
        f"- label:     {sender_vp.label}\n"
        f"- customer:  {sender_vp.customer}\n"
        f"- pain:      {sender_vp.pain}\n"
        f"- outcome:   {sender_vp.outcome}\n"
        f"- mechanism: {sender_vp.mechanism}\n\n"
        f"SENDER ICP (summary): {_format_icp(sender_icp)}\n\n"
        "STRATEGY ANGLE FOR THIS EMAIL:\n"
        + _format_angle(angle)
        + "\n\nRETRIEVED TARGET OBSERVATIONS:\n"
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
) -> _EmailDraft:
    """Last-resort email when the writer model fails.

    Keeps the promise that the product always returns an email while avoiding
    any target-specific claims that would require evidence.
    """
    outcome = sender_vp.outcome.strip() or "improve business outcomes"
    mechanism = sender_vp.mechanism.strip() or "the team's approach"
    customer = sender_vp.customer.strip() or "teams"
    label = sender_vp.label.strip() or "your work"
    subject = f"Exploring {label}"
    body = (
        f"I wanted to reach out because your role as {persona.role} may touch "
        f"areas where {customer} evaluate ways to {outcome}.\n\n"
        f"The reason I thought it could be relevant is that {mechanism}. I do "
        "not want to assume this is a current priority on your side, but it "
        "may be worth comparing notes if this area is on the roadmap.\n\n"
        "Would a short conversation be useful?"
    )
    return _EmailDraft(subject=subject, body=body, claims=[])


def write_emails(
    *,
    sender_vp: ValueProposition,
    sender_icp: ICP,
    target_observations: list[Observation],
    strategy: StrategyArtifact,
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> list[Email]:
    valid_obs_ids: set[str] = {o.observation_id for o in target_observations}
    obs_lookup: dict[str, Observation] = {
        o.observation_id: o for o in target_observations
    }

    def build(angle: Angle, d: _EmailDraft) -> Email | None:
        if not d.should_write:
            log.info(
                "write_emails: overriding model refusal for angle=%s reason=%s",
                angle.type.value,
                d.skip_reason[:160],
            )
            d = _fallback_email_draft(
                angle=angle, sender_vp=sender_vp, persona=persona
            )
        if not d.subject.strip() or not d.body.strip():
            log.info(
                "write_emails: using fallback for angle=%s because subject/body was empty",
                angle.type.value,
            )
            d = _fallback_email_draft(
                angle=angle, sender_vp=sender_vp, persona=persona
            )
        email_id = f"email_{uuid.uuid4().hex[:10]}"
        claims: list[EmailClaim] = []
        for c in d.claims:
            # Keep only refs the system actually knows; reject hallucinated ids.
            refs = [r for r in c.evidence_refs if r in valid_obs_ids]
            if not refs:
                continue
            claims.append(
                EmailClaim(
                    claim_id=f"claim_{uuid.uuid4().hex[:10]}",
                    text=c.text.strip(),
                    evidence_refs=refs,
                    status=ClaimStatus.UNSUPPORTED,  # set by verifier
                )
            )

        # Safety net: if the model wrote target-specific prose but forgot valid
        # claims, recover only when an observation text visibly appears in the
        # body. If nothing matches, still keep the email: the prompt allows a
        # broader sender/persona-based note with zero target-specific claims.
        if not claims:
            body_l = d.body.lower()
            for obs in obs_lookup.values():
                obs_l = obs.text.lower().strip()
                prefix = ""
                for cut in range(min(len(obs_l), 80), 19, -10):
                    candidate = obs_l[:cut].rstrip(" .,;:")
                    if candidate and candidate in body_l:
                        prefix = candidate
                        break
                if prefix:
                    claims.append(
                        EmailClaim(
                            claim_id=f"claim_{uuid.uuid4().hex[:10]}",
                            text=prefix,
                            evidence_refs=[obs.observation_id],
                            status=ClaimStatus.UNSUPPORTED,
                        )
                    )

        return Email(
            email_id=email_id,
            angle=angle.type,
            subject=d.subject.strip(),
            body=d.body.strip(),
            claims=claims,
        )

    angles = _angles_for_writer(strategy)
    out: list[Email] = []
    for angle in angles:
        draft = _write_email_for_angle(
            angle=angle,
            sender_vp=sender_vp,
            sender_icp=sender_icp,
            target_observations=target_observations,
            strategy=strategy,
            persona=persona,
            llm=llm,
            usage=usage,
        )
        if draft is None:
            draft = _fallback_email_draft(
                angle=angle, sender_vp=sender_vp, persona=persona
            )
        email = build(angle, draft)
        if email:
            out.append(email)
    return out
