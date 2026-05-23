"""Email writer.

Two-pass design:

1. **Pick** (LLM): pick 2-3 specific target observation_ids per angle that
   the writer commits to citing. This forces concrete fact selection
   *before* prose, instead of letting the model paraphrase the strategy
   hypothesis as a generic opener.
2. **Write** (LLM): write each email using only the picked facts. Every
   picked observation_id MUST end up as a claim with that exact id in
   ``evidence_refs``. The claim text must substring-appear in the body
   (downstream ``claim_extract`` enforces this).

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

from ..schemas import (
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


# ---------- Pass A: pick which facts to cite ----------

class _PickedAngle(BaseModel):
    type: AngleType
    observation_ids: list[str] = Field(min_length=1, max_length=4)


class _PickedFacts(BaseModel):
    pain_led: _PickedAngle
    trigger_led: _PickedAngle


_PICKER_SYSTEM = """You select the most useful target facts for two outbound emails.

INPUT: a list of validated TARGET OBSERVATIONS (each has an observation_id, a kind, and a short text), plus the high-level strategy hypotheses for the pain-led and trigger-led angles.

TASK:
For EACH angle, pick 2-3 observation_ids that:
1. Are SPECIFIC to the target (not generic industry truisms).
2. Make the angle's hypothesis concrete (a real product launch, a real funding round, a real hiring push, an explicit pain quote, etc.).
3. Are actually present in the input (do not invent ids).

Return STRICTLY JSON in this shape:
{
  "pain_led":    { "type": "pain_led",    "observation_ids": ["obs_xxx", "obs_yyy"] },
  "trigger_led": { "type": "trigger_led", "observation_ids": ["obs_zzz", ...] }
}
"""


# ---------- Pass B: write the two emails ----------

class _ClaimDraft(BaseModel):
    text: str = Field(min_length=4, max_length=400)
    evidence_refs: list[str] = Field(min_length=1, max_length=4)


class _EmailDraft(BaseModel):
    subject: str = Field(min_length=4, max_length=180)
    body: str = Field(min_length=20, max_length=2200)
    claims: list[_ClaimDraft] = Field(min_length=1, max_length=5)


class _WriterDraft(BaseModel):
    pain_led: _EmailDraft
    trigger_led: _EmailDraft


_WRITER_SYSTEM = """You write outbound emails grounded ONLY in evidence the system has selected for you.

INPUTS:
- Sender value proposition + ICP (sender-side positioning).
- Persona (role, seniority) + persona_alignment guidance.
- For each angle (pain_led, trigger_led): a small list of "picked facts". Each fact has an observation_id, a kind, and the exact text. THESE ARE THE ONLY TARGET FACTS YOU MAY USE.

OUTPUT (strict JSON):
{
  "pain_led":    { "subject": "...", "body": "...", "claims": [ { "text": "...", "evidence_refs": [observation_id, ...] }, ... ] },
  "trigger_led": { "subject": "...", "body": "...", "claims": [...] }
}

WRITING RULES:
- Open the body with ONE specific picked fact, paraphrased into a single concrete sentence. NEVER open with "I understand that you are facing challenges" or any generic empathy preamble.
- 4-7 short sentences total. Plain text. No markdown, no emojis, no signature placeholder.
- The two emails MUST differ in opening sentence, framing, and call-to-action.
- For EVERY picked fact you used, emit one claim:
    * "text" must be a sentence that ALSO appears literally in "body" (claim text is a substring of body, lowercase-insensitive).
    * "evidence_refs" must contain the matching observation_id.
- Do not fabricate target facts beyond the picked ones. Generic sender-side statements ("we help X teams...") need no evidence_refs and should NOT appear in claims.
- Persona shaping:
    * Senior (VP / C-level / Founder): business outcome, ROI, pipeline impact. No mechanism deep-dives.
    * Mid-level / IC: operational mechanism allowed. Be specific.
- Respect persona_alignment.avoid (do not use those framings).
- End with one low-friction CTA (a question, a 15-min ask, or "happy to share notes").
"""


def _format_icp(icp: ICP) -> str:
    return (
        f"industries={icp.target_industries.values}; "
        f"sizes={icp.size_bands.values}; "
        f"buyers={icp.likely_buyers.values}; "
        f"triggers={icp.common_triggers.values}; "
        f"negative={icp.negative_icp.values}"
    )


def _format_obs_for_picker(obs: list[Observation]) -> str:
    return "\n".join(
        f"- {o.observation_id} [{o.kind}, conf={o.confidence:.2f}]: {o.text}"
        for o in obs
    ) or "(none)"


def _format_obs_for_writer(picked_ids: list[str], obs_by_id: dict[str, Observation]) -> str:
    lines = []
    for oid in picked_ids:
        o = obs_by_id.get(oid)
        if not o:
            continue
        lines.append(f"- {o.observation_id} [{o.kind}]: {o.text}")
    return "\n".join(lines) or "(none)"


def _persona_block(strategy: StrategyArtifact, persona: PersonaInput) -> str:
    pa = strategy.strategy.persona_alignment
    return (
        f"role: {persona.role}\n"
        f"seniority: {persona.seniority.value}\n"
        f"role_relevance: {pa.role_relevance}\n"
        f"preferred_framing: {pa.preferred_framing}\n"
        f"avoid: {pa.avoid}\n"
    )


def _pick_facts(
    *,
    target_observations: list[Observation],
    strategy: StrategyArtifact,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> _PickedFacts | None:
    """Pass A: pick observation_ids for each angle."""
    angle_hypos = "\n".join(
        f"- {a.type.value}: {a.hypothesis}" for a in strategy.strategy.angles
    )
    user = (
        "TARGET OBSERVATIONS:\n"
        + _format_obs_for_picker(target_observations)
        + "\n\nSTRATEGY HYPOTHESES:\n"
        + angle_hypos
        + "\n\nPick 2-3 observation_ids per angle. Return JSON now."
    )
    try:
        return llm.structured(
            system=_PICKER_SYSTEM,
            user=user,
            schema=_PickedFacts,
            purpose="writer_pick_facts",
            usage=usage,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("writer pick_facts failed: %s", e)
        return None


def _write_emails_with_picks(
    *,
    sender_vp: ValueProposition,
    sender_icp: ICP,
    obs_by_id: dict[str, Observation],
    picks: _PickedFacts,
    strategy: StrategyArtifact,
    persona: PersonaInput,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> _WriterDraft | None:
    """Pass B: produce both emails using only the picked facts."""
    user = (
        "SENDER VALUE PROPOSITION:\n"
        f"- customer:  {sender_vp.customer}\n"
        f"- pain:      {sender_vp.pain}\n"
        f"- outcome:   {sender_vp.outcome}\n"
        f"- mechanism: {sender_vp.mechanism}\n\n"
        f"SENDER ICP (summary): {_format_icp(sender_icp)}\n\n"
        "PICKED FACTS - PAIN_LED ANGLE:\n"
        + _format_obs_for_writer(picks.pain_led.observation_ids, obs_by_id)
        + "\n\nPICKED FACTS - TRIGGER_LED ANGLE:\n"
        + _format_obs_for_writer(picks.trigger_led.observation_ids, obs_by_id)
        + "\n\nFIT ASSESSMENT: "
        + strategy.fit_assessment.level.value
        + " | contact_decision: "
        + strategy.strategy.contact_decision.value
        + "\n\nPERSONA:\n"
        + _persona_block(strategy, persona)
        + "\nReturn the JSON with both emails now."
    )
    try:
        return llm.structured(
            system=_WRITER_SYSTEM,
            user=user,
            schema=_WriterDraft,
            purpose="writer_write_emails",
            usage=usage,
            temperature=0.4,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("writer write_emails failed: %s", e)
        return None


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
    # Defensive refusal: if there are no observations to cite, do NOT
    # generate a generic, ungrounded email.
    if not target_observations:
        log.info("write_emails: no target observations -> skipping email generation")
        return []

    valid_obs_ids: set[str] = {o.observation_id for o in target_observations}
    obs_lookup: dict[str, Observation] = {
        o.observation_id: o for o in target_observations
    }

    picks = _pick_facts(
        target_observations=target_observations,
        strategy=strategy,
        llm=llm,
        usage=usage,
    )
    if picks is None:
        return []

    # Drop hallucinated observation_ids before pass B sees them.
    valid_pain = [oid for oid in picks.pain_led.observation_ids if oid in valid_obs_ids]
    valid_trig = [oid for oid in picks.trigger_led.observation_ids if oid in valid_obs_ids]
    if not valid_pain or not valid_trig:
        log.warning(
            "write_emails: picker returned no valid ids (pain=%d, trigger=%d) -> skipping",
            len(valid_pain),
            len(valid_trig),
        )
        return []
    picks.pain_led.observation_ids = valid_pain
    picks.trigger_led.observation_ids = valid_trig

    draft = _write_emails_with_picks(
        sender_vp=sender_vp,
        sender_icp=sender_icp,
        obs_by_id=obs_lookup,
        picks=picks,
        strategy=strategy,
        persona=persona,
        llm=llm,
        usage=usage,
    )
    if draft is None:
        return []

    def build(angle: AngleType, d: _EmailDraft, picked_ids: list[str]) -> Email:
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

        # Safety net: if the model emitted a body but no valid claims, fall
        # back to one claim per picked fact whose text demonstrably appears
        # in the body. We take the longest leading prefix of the
        # observation text (in 10-char steps) that survives in the body.
        # Without this, a sloppy LLM response would zero out every claim
        # and trigger the "Claims (0)" UI complaint.
        if not claims:
            body_l = d.body.lower()
            for oid in picked_ids:
                obs = obs_lookup.get(oid)
                if not obs:
                    continue
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
                            evidence_refs=[oid],
                            status=ClaimStatus.UNSUPPORTED,
                        )
                    )

        return Email(
            email_id=email_id,
            angle=angle,
            subject=d.subject.strip(),
            body=d.body.strip(),
            claims=claims,
        )

    return [
        build(AngleType.PAIN_LED, draft.pain_led, picks.pain_led.observation_ids),
        build(
            AngleType.TRIGGER_LED, draft.trigger_led, picks.trigger_led.observation_ids
        ),
    ]
