"""Email safety guardrail (cheap, focused, traceable).

The writer outputs the email body + a list of ``EmailClaim`` objects.
Each claim already carries:

  - ``text``: a near-verbatim snippet from the body;
  - ``scope``: ``general`` (no evidence needed) / ``sender`` / ``target``;
  - ``evidence_refs``: ref_ids from the CONTEXT INDEX the writer cited;
  - ``evidence``: the hydrated snippets for those refs.

The guardrail makes ONE LLM call per email. It receives nothing but the
email body and the writer's declared claims with their cited evidence.
For each claim it returns ``grounded: bool`` + ``confidence: float`` +
``reason``. The email is safe iff every sender/target claim is grounded.

There is no independent claim extraction here. There is no re-reading of
the full briefing. The guardrail's only job is: does the writer's cited
evidence actually support the claim it cites?
"""
from __future__ import annotations

import logging
from statistics import mean
from typing import Literal

from pydantic import BaseModel, Field

from ..db import fetchall
from ..schemas import (
    Email,
    EmailClaim,
    EmailSafetyReport,
    Observation,
    StatementContextRef,
)
from ..services.llm import LLMClient, UsageAccumulator
from .context_index import ContextBundle
from . import writer as writer_mod

log = logging.getLogger(__name__)


MAX_REGENERATIONS = 1


# Backwards-compatible alias.
GuardContext = ContextBundle


# ---------- LLM judge schema ----------


class _ClaimVerdict(BaseModel):
    claim_id: str
    grounded: bool
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    reason: str = Field(default="", max_length=300)


class _JudgeResult(BaseModel):
    claims: list[_ClaimVerdict] = Field(default_factory=list)


_SYSTEM_JUDGE = """You are a strict fact-checking guardrail for B2B sales emails.

You will be given:
- EMAIL BODY (subject + body).
- A list of CLAIMS the writer says it used in the email. Each claim has:
    * claim_id
    * text (a snippet from the email body)
    * scope: "general" / "sender" / "target"
    * evidence: 0+ retrieval snippets the writer cited for that claim.

YOUR JOB:
For each claim return one verdict object with the SAME claim_id:
  - grounded (bool):
      * scope=general — return grounded=true unless the claim text is in
        fact a specific sender/target assertion in disguise (then false).
      * scope=sender or scope=target — return grounded=true ONLY when the
        provided ``evidence`` snippets materially support the claim
        (paraphrase / direct support / clear implication). Return
        grounded=false when the evidence is empty, off-topic, or
        contradicts the claim.
  - confidence (0.0–1.0): how confident you are in the verdict.
  - reason: ONE short sentence explaining the verdict. Mandatory when
    grounded=false; optional when true.

Do not invent new claims. Do not re-read or guess at outside knowledge.
The cited evidence is the only premise you have.

OUTPUT (strict JSON):
{
  "claims": [
    { "claim_id": "...", "grounded": true | false, "confidence": 0.0, "reason": "..." }
  ]
}
"""


_REGEN_SYSTEM = """You rewrite a B2B outbound sales email so every factual claim is either
backed by the provided CONTEXT INDEX or softened to a general,
non-company-specific statement.

Rules:
- Output subject, body, AND claims_used (same schema as the writer).
- Open with "Dear <role> of/at <company>," — never "Hi there,".
- Close with: Markos Artisan (no "Best regards").
- claims_used MUST contain at least one claim, with the same scope rules
  as the writer (general / sender / target). Cite only ref_ids that exist
  in CONTEXT INDEX.
"""


# ---------- DB helpers ----------


def load_observations_for_company(company_id: str) -> list[Observation]:
    rows = fetchall(
        "SELECT observation_id, company_id, section_id, kind, text, confidence, "
        "validation, validation_score "
        "FROM observations WHERE company_id = ? ORDER BY rowid",
        (company_id,),
    )
    out: list[Observation] = []
    for row in rows:
        try:
            out.append(
                Observation(
                    observation_id=str(row["observation_id"]),
                    company_id=str(row["company_id"]),
                    section_id=str(row["section_id"]),
                    kind=str(row["kind"]),
                    text=str(row["text"]),
                    confidence=float(row["confidence"]),
                    validation=row["validation"],
                    validation_score=row["validation_score"],
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------- Judge ----------


def _format_evidence_for_judge(evidence: list[StatementContextRef]) -> str:
    if not evidence:
        return "    (no evidence cited)"
    lines: list[str] = []
    for ref in evidence:
        lines.append(
            f"    - [{ref.ref_id}] ({ref.ref_type}) {ref.label}: {ref.snippet}"
        )
    return "\n".join(lines)


def _format_claims_for_judge(claims: list[EmailClaim]) -> str:
    if not claims:
        return "(no claims declared)"
    parts: list[str] = []
    for c in claims:
        parts.append(
            f"- claim_id: {c.claim_id}\n"
            f"  scope: {c.scope}\n"
            f"  text: {c.text}\n"
            f"  evidence:\n"
            f"{_format_evidence_for_judge(c.evidence)}"
        )
    return "\n".join(parts)


def _judge_email(
    email: Email,
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> _JudgeResult | None:
    if not email.claims:
        log.warning(
            "email_guard: email=%s has no declared claims; nothing to verify",
            email.email_id,
        )
        return _JudgeResult(claims=[])

    user = (
        f"EMAIL SUBJECT: {email.subject}\n"
        f"EMAIL BODY:\n{email.body}\n\n"
        "CLAIMS TO VERIFY:\n"
        + _format_claims_for_judge(email.claims)
        + "\n\nReturn one verdict per claim_id."
    )
    try:
        return llm.structured(
            system=_SYSTEM_JUDGE,
            user=user,
            schema=_JudgeResult,
            purpose="email_guard_judge",
            usage=usage,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        log.exception(
            "email_guard judge failed email=%s: %s", email.email_id, e
        )
        return None


def _apply_verdicts(
    claims: list[EmailClaim], result: _JudgeResult
) -> list[EmailClaim]:
    """Merge per-claim verdicts back onto the original claim list."""
    by_id = {v.claim_id: v for v in result.claims}
    out: list[EmailClaim] = []
    for c in claims:
        v = by_id.get(c.claim_id)
        if v is None:
            # The judge silently dropped this claim — treat as not grounded
            # for sender/target, accepted-by-default for general.
            grounded = c.scope == "general"
            out.append(
                c.model_copy(
                    update={
                        "grounded": grounded,
                        "confidence": 0.0,
                        "reason": "" if grounded else "judge skipped this claim",
                    }
                )
            )
            continue

        grounded = bool(v.grounded)
        # Hard rule: a sender/target claim with zero evidence cannot be
        # grounded, even if the LLM said so. Trust the deterministic check
        # over the judge's optimism.
        if c.scope in ("sender", "target") and not c.evidence:
            grounded = False

        out.append(
            c.model_copy(
                update={
                    "grounded": grounded,
                    "confidence": float(v.confidence),
                    "reason": v.reason.strip(),
                }
            )
        )
    return out


def _aggregate_verdict(claims: list[EmailClaim]) -> tuple[bool, float]:
    """Return (is_safe, email_confidence) from per-claim verdicts."""
    is_safe = True
    confs: list[float] = []
    for c in claims:
        if c.confidence is not None:
            confs.append(float(c.confidence))
        if c.scope in ("sender", "target") and c.grounded is False:
            is_safe = False
    avg = round(mean(confs), 3) if confs else 0.0
    return is_safe, avg


def verify_email(
    email: Email,
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> Email:
    """Judge one email. Returns the email with per-claim verdicts attached."""
    if not email.claims:
        report = EmailSafetyReport(
            is_safe=False,
            confidence=0.0,
            verification_ok=True,
        )
        log.warning(
            "email_guard: email=%s judged unsafe (no declared claims)",
            email.email_id,
        )
        return email.model_copy(update={"safety": report})

    result = _judge_email(email, llm=llm, usage=usage)
    if result is None:
        report = EmailSafetyReport(
            is_safe=False,
            confidence=0.0,
            verification_ok=False,
        )
        return email.model_copy(update={"safety": report})

    judged_claims = _apply_verdicts(email.claims, result)
    is_safe, conf = _aggregate_verdict(judged_claims)
    report = EmailSafetyReport(
        is_safe=is_safe,
        confidence=conf,
        verification_ok=True,
    )
    return email.model_copy(
        update={"claims": judged_claims, "safety": report}
    )


# ---------- Regeneration (single pass on unsafe) ----------


class _RegeneratedEmail(BaseModel):
    subject: str = Field(max_length=180)
    body: str = Field(max_length=2200)
    claims_used: list[writer_mod.ClaimUsedDraft] = Field(min_length=1, max_length=8)


def regenerate_email(
    email: Email,
    *,
    ctx: GuardContext,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> Email:
    """Rewrite an unsafe email once, then re-coerce its claims."""
    from .context_index import build_context_index

    context_doc, ref_index = build_context_index(ctx)
    failing = [
        c
        for c in email.claims
        if c.scope in ("sender", "target") and c.grounded is False
    ]
    failure_lines = "\n".join(
        f"  - [{c.claim_id}] ({c.scope}) {c.text}"
        + (f" — reason: {c.reason}" if c.reason else "")
        for c in failing
    ) or "  (none specified — soften any unverifiable claim)"
    user = (
        f"ORIGINAL SUBJECT: {email.subject}\n\n"
        f"ORIGINAL BODY:\n{email.body}\n\n"
        f"CLAIMS THAT FAILED VERIFICATION:\n{failure_lines}\n\n"
        f"{context_doc}\n\n"
        "Rewrite the full email and declare claims_used. Every sender/target "
        "claim MUST cite ref_ids that exist in CONTEXT INDEX."
    )
    try:
        draft = llm.structured(
            system=_REGEN_SYSTEM,
            user=user,
            schema=_RegeneratedEmail,
            purpose="email_guard_regenerate",
            usage=usage,
            temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(
            "email_guard regenerate failed email=%s: %s", email.email_id, e
        )
        return email

    new_claims = writer_mod.coerce_email_claims(draft.claims_used, ref_index)
    if not new_claims:
        log.warning(
            "email_guard regenerate email=%s returned no valid claims; keeping original",
            email.email_id,
        )
        new_claims = list(email.claims)
    return email.model_copy(
        update={
            "subject": draft.subject.strip() or email.subject,
            "body": draft.body.strip() or email.body,
            "claims": new_claims,
        }
    )


def guard_email(
    email: Email,
    *,
    ctx: GuardContext,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> Email:
    """Judge once. If unsafe, regenerate once and judge again. Then accept."""
    judged = verify_email(email, llm=llm, usage=usage)
    safety = judged.safety
    if safety and safety.is_safe:
        return judged
    if safety and not safety.verification_ok:
        # Verifier unavailable; do not regenerate (we can't tell if it would help).
        return judged

    # Regenerate ONCE.
    rewritten = regenerate_email(judged, ctx=ctx, llm=llm, usage=usage)
    rejudged = verify_email(rewritten, llm=llm, usage=usage)
    new_safety = rejudged.safety or EmailSafetyReport(
        is_safe=False, confidence=0.0, verification_ok=False
    )
    return rejudged.model_copy(
        update={
            "safety": new_safety.model_copy(
                update={
                    "email_regenerated": True,
                    "regeneration_count": 1,
                }
            )
        }
    )


# ---------- Analytics ----------


def accumulate_safety_metrics(
    emails: list[Email],
) -> dict[str, int | bool | float | None]:
    """Aggregate guardrail outcomes across emails for ``RunMetrics``."""
    declared_total = 0
    claims_total = 0
    unsupported_total = 0
    emails_safe = 0
    emails_total = 0
    confidences: list[float] = []
    email_regenerated = False
    regeneration_count = 0
    final_email_safe = True
    verification_ok = True

    for email in emails:
        emails_total += 1
        declared_total += len(email.claims)
        claims_total += len(email.claims)
        for c in email.claims:
            if c.scope in ("sender", "target") and c.grounded is False:
                unsupported_total += 1
        safety = email.safety
        if not safety:
            final_email_safe = False
            verification_ok = False
            continue
        if safety.email_regenerated:
            email_regenerated = True
        regeneration_count += safety.regeneration_count
        if not safety.verification_ok:
            verification_ok = False
        if safety.is_safe:
            emails_safe += 1
        else:
            final_email_safe = False
        confidences.append(safety.confidence)

    avg_conf = round(mean(confidences), 3) if confidences else None
    return {
        "declared_claims_count": declared_total,
        "email_claims_count": claims_total,
        "unsupported_claims_count": unsupported_total,
        "safety_confidence_avg": avg_conf,
        "email_regenerated": email_regenerated,
        "regeneration_count": regeneration_count,
        "emails_safe_count": emails_safe,
        "emails_total": emails_total,
        "final_email_safe": final_email_safe,
        "verification_ok": verification_ok,
    }


select_sender_evidence_for_verifier = writer_mod.select_sender_evidence
