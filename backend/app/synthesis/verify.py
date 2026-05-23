"""Claim verification + bounded repair loop.

For each claim in each email:
1. Find the supporting sections via the cited observation_ids.
2. Run NLI(premise=section_text, hypothesis=claim_text).
3. Tag the claim: ENTAILED / NEUTRAL / CONTRADICTED / UNSUPPORTED.

If verification fails (CONTRADICTED or UNSUPPORTED) we run a single
bounded REPAIR pass: ask the LLM to either rewrite the claim using only
supported evidence, or drop it. We do NOT loop indefinitely; one repair
attempt per email at most.

The output is a list of ClaimMapEntry rows -- one per claim -- carrying
the final status, the NLI score, and the citations (URL + snippet).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field

from ..schemas import (
    ClaimMapEntry,
    ClaimStatus,
    Email,
    EmailClaim,
    NliLabel,
    Observation,
)
from ..services.llm import LLMClient, UsageAccumulator
from ..services.nli import NliValidator

log = logging.getLogger(__name__)


@dataclass
class VerificationContext:
    observations: dict[str, Observation]
    sections: dict[str, dict]


# ---------- Repair I/O ----------

class _RepairedClaim(BaseModel):
    claim_id: str
    action: str = Field(pattern="^(rewrite|drop)$")
    rewritten_text: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)


class _RepairResult(BaseModel):
    claims: list[_RepairedClaim] = Field(default_factory=list)
    new_body: str | None = None


_SYSTEM_REPAIR = """You repair unsupported or contradicted claims in an outbound email.

For each unsupported claim, choose ONE action:
- "rewrite": replace the claim with a version that is supported by the provided observations. Cite the supporting observation_ids.
- "drop":    remove the claim entirely from the email body.

Also return the new email body with the repairs applied. Do not introduce new facts that lack evidence. Keep the email length and tone unchanged.

Output JSON:
{
  "claims": [ { "claim_id": "...", "action": "rewrite|drop", "rewritten_text": "...", "evidence_refs": [...] } ],
  "new_body": "the full updated email body"
}
"""


def _evidence_for_claim(
    claim: EmailClaim, ctx: VerificationContext
) -> list[tuple[Observation, dict]]:
    """Return (observation, section) pairs that back this claim."""
    out: list[tuple[Observation, dict]] = []
    for ref in claim.evidence_refs:
        obs = ctx.observations.get(ref)
        if not obs:
            continue
        sec = ctx.sections.get(obs.section_id)
        if not sec:
            continue
        out.append((obs, sec))
    return out


def verify_email(
    email: Email,
    ctx: VerificationContext,
    *,
    nli: NliValidator,
) -> list[EmailClaim]:
    """Mark each claim with ENTAILED / NEUTRAL / CONTRADICTED / UNSUPPORTED."""
    pairs: list[tuple[str, str]] = []
    idx: list[int] = []
    out_claims = [c.model_copy() for c in email.claims]

    for i, claim in enumerate(out_claims):
        evidence = _evidence_for_claim(claim, ctx)
        if not evidence:
            claim.status = ClaimStatus.UNSUPPORTED
            continue
        # Use the longest supporting section as the premise.
        premise = max((s["text"] for _, s in evidence), key=len)
        pairs.append((premise, claim.text))
        idx.append(i)

    if pairs:
        results = nli.score_pairs(pairs)
        for i, res in zip(idx, results, strict=False):
            out_claims[i].nli_score = res.score
            if res.label == NliLabel.ENTAILED:
                out_claims[i].status = ClaimStatus.ENTAILED
            elif res.label == NliLabel.CONTRADICTED:
                out_claims[i].status = ClaimStatus.CONTRADICTED
            else:
                out_claims[i].status = ClaimStatus.NEUTRAL

    return out_claims


def needs_repair(claims: list[EmailClaim]) -> bool:
    return any(
        c.status in (ClaimStatus.CONTRADICTED, ClaimStatus.UNSUPPORTED)
        for c in claims
    )


def repair_email(
    email: Email,
    ctx: VerificationContext,
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
    nli: NliValidator,
) -> Email:
    """One bounded repair loop. Rewrites unsupported claims, then re-verifies."""
    bad = [
        c for c in email.claims
        if c.status in (ClaimStatus.CONTRADICTED, ClaimStatus.UNSUPPORTED)
    ]
    if not bad:
        return email

    available_obs = "\n".join(
        f"- {o.observation_id} [{o.kind}]: {o.text}"
        for o in ctx.observations.values()
    )
    bad_block = "\n".join(
        f"- claim_id={c.claim_id} status={c.status.value} text=\"{c.text}\" current_refs={c.evidence_refs}"
        for c in bad
    )
    user = (
        f"Original email body:\n{email.body}\n\n"
        f"Unsupported / contradicted claims:\n{bad_block}\n\n"
        f"All available observations (use only these for citations):\n{available_obs}\n\n"
        "Return the repair JSON now."
    )

    try:
        repair = llm.structured(
            system=_SYSTEM_REPAIR,
            user=user,
            schema=_RepairResult,
            purpose="repair_email",
            usage=usage,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("repair_email failed: %s", e)
        return email

    by_id = {c.claim_id: c for c in email.claims}
    obs_ids = set(ctx.observations.keys())
    repaired_claims: list[EmailClaim] = []
    dropped_ids: set[str] = set()

    for rep in repair.claims:
        original = by_id.get(rep.claim_id)
        if not original:
            continue
        if rep.action == "drop":
            dropped_ids.add(rep.claim_id)
            continue
        if rep.action == "rewrite" and rep.rewritten_text:
            refs = [r for r in rep.evidence_refs if r in obs_ids]
            repaired_claims.append(
                original.model_copy(
                    update={
                        "text": rep.rewritten_text.strip(),
                        "evidence_refs": refs,
                        "repaired_text": rep.rewritten_text.strip(),
                        "status": ClaimStatus.UNSUPPORTED,  # re-verified below
                    }
                )
            )

    # Build the final claim list: keep entailed/neutral claims, plus rewrites, minus drops.
    final_claims: list[EmailClaim] = []
    for c in email.claims:
        if c.claim_id in dropped_ids:
            continue
        if any(r.claim_id == c.claim_id for r in repaired_claims):
            continue
        final_claims.append(c)
    final_claims.extend(repaired_claims)

    new_body = repair.new_body.strip() if repair.new_body else email.body
    updated = email.model_copy(update={"body": new_body, "claims": final_claims})

    # Re-verify just the rewritten claims.
    if repaired_claims:
        pairs: list[tuple[str, str]] = []
        idx: list[int] = []
        for i, c in enumerate(updated.claims):
            if c.repaired_text is None:
                continue
            evidence = _evidence_for_claim(c, ctx)
            if not evidence:
                c.status = ClaimStatus.UNSUPPORTED
                continue
            premise = max((s["text"] for _, s in evidence), key=len)
            pairs.append((premise, c.text))
            idx.append(i)
        if pairs:
            results = nli.score_pairs(pairs)
            for i, res in zip(idx, results, strict=False):
                updated.claims[i].nli_score = res.score
                if res.label == NliLabel.ENTAILED:
                    updated.claims[i].status = ClaimStatus.REPAIRED
                elif res.label == NliLabel.CONTRADICTED:
                    updated.claims[i].status = ClaimStatus.CONTRADICTED
                else:
                    updated.claims[i].status = ClaimStatus.NEUTRAL

    return updated


def build_claim_map(
    emails: list[Email], ctx: VerificationContext
) -> list[ClaimMapEntry]:
    entries: list[ClaimMapEntry] = []
    for email in emails:
        for c in email.claims:
            citations: list[dict[str, str]] = []
            for obs, sec in _evidence_for_claim(c, ctx):
                snippet = sec["text"]
                if len(snippet) > 280:
                    snippet = snippet[:280].rstrip() + "..."
                citations.append({"url": sec["url"], "snippet": snippet})
            entries.append(
                ClaimMapEntry(
                    claim_id=c.claim_id,
                    email_id=email.email_id,
                    angle=email.angle,
                    text=c.text,
                    status=c.status,
                    nli_score=c.nli_score,
                    citations=citations,
                )
            )
    return entries
