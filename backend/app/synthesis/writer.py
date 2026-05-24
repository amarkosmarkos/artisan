"""Email writer.

Each outreach angle gets its own LLM call. The writer sees the selected
sender value proposition, ICP, persona guidance, the single angle to
write, the retrieved target observations, and the CONTEXT INDEX of
ref_ids it is allowed to cite.

The writer's output is:

  - subject + body (the send-ready email);
  - ``claims``: every factual claim used in the email, each with:
      * scope: ``general`` / ``sender`` / ``target`` — declared by the writer
      * evidence_refs: ref_ids from the CONTEXT INDEX (none for ``general``)

The pipeline immediately hydrates ``evidence_refs`` into ``evidence``
snippets from the same retrieval, so the guardrail receives the claim
together with the exact text the writer cited. The guardrail only judges
whether each claim is grounded by its own cited evidence — no
re-reading of the full briefing.
"""
from __future__ import annotations

import logging
import uuid

from pydantic import BaseModel, Field
from typing import Literal

from ..config import settings
from ..schemas import (
    Angle,
    AngleType,
    Email,
    EmailClaim,
    ICP,
    Observation,
    PersonaInput,
    StatementContextRef,
    StrategyArtifact,
    ValueProposition,
)
from ..services.llm import LLMClient, UsageAccumulator
from .context_index import ContextBundle, build_context_index

log = logging.getLogger(__name__)


class ClaimUsedDraft(BaseModel):
    text: str = Field(min_length=4, max_length=500)
    scope: Literal["general", "sender", "target"] = "general"
    evidence_refs: list[str] = Field(default_factory=list, max_length=8)


class _EmailDraft(BaseModel):
    should_write: bool = True
    skip_reason: str = ""
    subject: str = Field(default="", max_length=180)
    body: str = Field(default="", max_length=2200)
    # Hard requirement: the guardrail verifies ONLY these claims.
    claims_used: list[ClaimUsedDraft] = Field(min_length=1, max_length=8)


_WRITER_SYSTEM = """You are an expert B2B outbound sales writer.

INPUTS YOU RECEIVE:
- TARGET COMPANY NAME: the recipient's company.
- RECIPIENT NAME: optional; do not use for the greeting.
- SENDER VALUE PROPOSITION (label, customer, pain, outcome, mechanism).
- SENDER EVIDENCE: real facts about the sender's product, customers, results,
  or mechanism.
- STRATEGY ANGLE FOR THIS EMAIL: the sales angle (pain_led / trigger_led /
  etc), its hypothesis, and the target observations that ground it.
- ADDITIONAL TARGET OBSERVATIONS: extra facts about the target.
- PERSONA: seniority, role, and messaging guidance for the recipient.
- MESSAGING ANGLE / SELECTED VP REASON: why this angle and VP were chosen.
- CONTEXT INDEX: numbered [ref_id] entries with snippets. This is the
  COMPLETE set of premises you may cite when declaring claims.

YOUR JOB:
Write ONE send-ready sales email AND declare every factual claim you used.
The guardrail will check each claim against the evidence YOU cited — not
the full briefing — so be honest about what each claim relies on.

The output must be complete plain text — no placeholders, no brackets.

GREETING + SIGN-OFF (HARD):
- Open with "Dear <role> of/at <company>," using PERSONA.role and
  TARGET COMPANY NAME. Never "Hi <name>," and never "Hi there,".
- Close with a blank line, then sign exactly: Markos Artisan.

BANNED LANGUAGE (or close variants):
"AI solutions", "align with your objectives", "enhance operational
efficiency", "explore this further", "I hope this message finds you well",
"I would be happy to share more", "as you expand your workforce" (unless
hiring is the explicit core angle), "Let me know your thoughts".

EVIDENCE DISCIPLINE:
- Target-specific or sender-specific facts MUST be backed by a ref_id
  that exists in CONTEXT INDEX. Do not invent ref_ids. Do not invent
  facts about the target or the sender that aren't in the context.
- A general market-level statement (no company-specific facts) is allowed
  with zero refs — declare it with scope=general.

DECLARED CLAIMS (HARD — this is the only thing the guardrail sees):
For every factual sentence in the email body (excluding greeting,
sign-off, and pure CTA lines) add an entry to ``claims_used`` with:
  - text: a verbatim or near-verbatim snippet from the body
  - scope:
      * "general"  — broad industry/market knowledge that does NOT name
        the target or make a specific sender claim. evidence_refs MUST be empty.
      * "sender"   — a specific assertion about the sender company, its
        product, customers, results, or capability. evidence_refs MUST contain
        at least one ref_id of type ``value_prop`` or sender ``observation``.
      * "target"   — a specific assertion about the recipient company, its
        situation, plans, stack, hiring, etc. evidence_refs MUST contain at
        least one target ``observation`` ref_id.
  - evidence_refs: 1–3 ref_ids from CONTEXT INDEX that materially support
    the claim. For scope=general, leave empty.

If you cannot find ref_ids that materially support a sender/target claim,
DO NOT write that claim — soften the sentence to scope=general instead.

OUTPUT (strict JSON — claims_used is REQUIRED, minimum 1 claim):
{
  "subject": "...",
  "body": "...",
  "claims_used": [
    {
      "text": "<near-verbatim snippet from body>",
      "scope": "general" | "sender" | "target",
      "evidence_refs": ["<ref_id from CONTEXT INDEX>", ...]
    }
  ]
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


def select_sender_evidence(
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


_select_sender_evidence = select_sender_evidence


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


def coerce_email_claims(
    raw: list[ClaimUsedDraft],
    ref_index: dict[str, StatementContextRef],
) -> list[EmailClaim]:
    """Validate writer-declared claims and hydrate the cited evidence.

    - Drops ref_ids not present in the CONTEXT INDEX (writer hallucination).
    - Downgrades scope=sender/target with zero valid refs to scope=general,
      because such a claim cannot be verified and the writer SHOULDN'T have
      stated it as company-specific.
    - Populates ``evidence`` with the exact ``StatementContextRef`` snippets
      that came out of the context index. This is what the guardrail will
      receive — no other context is needed at judgement time.
    """
    out: list[EmailClaim] = []
    for c in raw:
        text = (c.text or "").strip()
        if not text:
            continue

        refs: list[str] = []
        snippets: list[StatementContextRef] = []
        seen: set[str] = set()
        for r in c.evidence_refs or []:
            ref_id = (r or "").strip()
            if not ref_id or ref_id in seen:
                continue
            ctx = ref_index.get(ref_id)
            if ctx is None:
                continue
            seen.add(ref_id)
            refs.append(ref_id)
            snippets.append(ctx)

        scope = c.scope
        if scope in ("sender", "target") and not refs:
            log.warning(
                "writer: downgraded %s claim with no valid refs to general: %s",
                scope,
                text[:80],
            )
            scope = "general"

        out.append(
            EmailClaim(
                claim_id=f"claim_{uuid.uuid4().hex[:10]}",
                text=text,
                scope=scope,
                evidence_refs=refs,
                evidence=snippets,
            )
        )
    return out


# Old name kept so callers in email_guard.py don't have to change in lockstep.
coerce_declared_claims = coerce_email_claims


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
    context_doc: str,
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
        + "\n"
        + context_doc
        + "\n\nReturn the JSON for this one email now."
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
    role = (persona.role or "").strip() or "Decision Maker"
    greeting = f"Dear {role} at {company}," if company else f"Dear {role},"
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
        "teardown first?\n\n"
        "Markos Artisan"
    )
    claim_text = (
        f"The reason I thought it could be relevant is that {mechanism}."
    )
    return _EmailDraft(
        subject=subject,
        body=body,
        claims_used=[
            ClaimUsedDraft(
                text=claim_text,
                scope="sender",
                evidence_refs=[f"vp:{sender_vp.id or 'primary'}:mechanism"],
            )
        ],
    )


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
    sender_evidence = select_sender_evidence(
        sender_vp, sender_observations or []
    )

    bundle = ContextBundle(
        target_observations=target_observations,
        sender_observations=sender_observations or [],
        sender_evidence=sender_evidence,
        sender_icp=sender_icp,
        sender_vp=sender_vp,
        strategy=strategy,
        persona=persona,
        target_company_name=target_company_name,
    )
    context_doc, ref_index = build_context_index(bundle)

    def build(angle: Angle, d: _EmailDraft) -> Email:
        if not d.should_write or not d.subject.strip() or not d.body.strip():
            log.info(
                "write_emails: using fallback for angle=%s "
                "(should_write=%s, has_subject=%s, has_body=%s)",
                angle.type.value,
                d.should_write,
                bool(d.subject.strip()),
                bool(d.body.strip()),
            )
            d = _fallback_email_draft(
                angle=angle,
                sender_vp=sender_vp,
                persona=persona,
                target_company_name=target_company_name,
            )
        claims = coerce_email_claims(d.claims_used, ref_index)
        if not claims:
            log.warning(
                "write_emails: angle=%s produced zero claims after coercion "
                "(raw claims_used=%d)",
                angle.type.value,
                len(d.claims_used or []),
            )
        return Email(
            email_id=f"email_{uuid.uuid4().hex[:10]}",
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
            target_company_name=target_company_name,
            target_observations=target_observations,
            sender_evidence=sender_evidence,
            strategy=strategy,
            persona=persona,
            context_doc=context_doc,
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
        out.append(build(angle, draft))
    return out
