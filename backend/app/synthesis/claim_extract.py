"""Claim consolidation node.

The writer emits emails *with* claims (it has the best context to know
what it asserted). This node:

1. Drops claims that don't appear in the email body (the writer sometimes
   over-lists). A claim is "in the body" if any half of its text (split
   on the first comma/period) shows up as a substring.
2. Deduplicates claims with identical text.
3. Strips evidence_refs that don't point to known observation_ids.

This is intentionally deterministic. No LLM is called here. The LangGraph
graph treats this as a separate node so claim verification has a clean
input contract.
"""
from __future__ import annotations

import logging
from typing import Iterable

from ..schemas import Email, EmailClaim

log = logging.getLogger(__name__)


def _appears_in_body(claim_text: str, body: str) -> bool:
    if not claim_text or not body:
        return False
    body_l = body.lower()
    text_l = claim_text.lower().strip(" .!?\"'")
    if text_l in body_l:
        return True
    # Substring of first half (often the LLM rewrites the second clause):
    head = text_l.split(",", 1)[0].split(". ", 1)[0]
    if len(head) >= 12 and head in body_l:
        return True
    return False


def consolidate_email_claims(
    email: Email,
    *,
    known_observation_ids: Iterable[str],
) -> Email:
    obs_ids = set(known_observation_ids)
    seen_text: set[str] = set()
    cleaned: list[EmailClaim] = []
    for c in email.claims:
        text = c.text.strip()
        if not text:
            continue
        norm = text.lower()
        if norm in seen_text:
            continue
        # If the claim text isn't traceable in the email body, drop it -- the
        # writer hallucinated a claim it never actually wrote.
        if not _appears_in_body(text, email.body):
            log.debug("claim_extract: dropping orphan claim %s", c.claim_id)
            continue
        refs = [r for r in c.evidence_refs if r in obs_ids]
        cleaned.append(c.model_copy(update={"text": text, "evidence_refs": refs}))
        seen_text.add(norm)
    return email.model_copy(update={"claims": cleaned})


def consolidate(
    emails: list[Email],
    *,
    known_observation_ids: Iterable[str],
) -> list[Email]:
    obs_ids = list(known_observation_ids)
    return [consolidate_email_claims(e, known_observation_ids=obs_ids) for e in emails]
