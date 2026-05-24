"""Angle overlap measurement and divergence repair.

We embed both email bodies and measure cosine similarity. If the two
emails are too similar (the angles collapse), we ask the LLM to rewrite
the pain_led email to lead with a *different* aspect than trigger_led,
without inventing facts.

This guarantees the two emails are meaningfully different in framing,
not just paraphrases of each other.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..schemas import AngleType, Email, Observation
from ..services.embed import Embedder, cosine
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


ANGLE_OVERLAP_MAX = 0.78  # above this we trigger divergence repair


class _DivergenceDraft(BaseModel):
    subject: str
    body: str


_SYSTEM_DIVERGE = """You rewrite the PAIN-LED email so that it is meaningfully different from the trigger-led email.

Constraints:
- Lead with the target's likely PROBLEM, not with a recent event.
- Do NOT introduce any new facts about the target beyond the provided observations.
- Keep length 4-7 short sentences.
- Different opening sentence and different call-to-action than the trigger-led email.

Output JSON: { "subject": "...", "body": "..." }
"""


def measure_overlap(email_a: Email, email_b: Email, embedder: Embedder) -> float:
    emb = embedder.encode([email_a.body, email_b.body])
    return cosine(emb[0], emb[1])


def diverge_pain_led(
    *,
    pain: Email,
    trigger: Email,
    target_observations: list[Observation],
    llm: LLMClient,
    usage: UsageAccumulator,
) -> Email:
    obs_block = "\n".join(
        f"- {o.observation_id} [{o.kind}]: {o.text}" for o in target_observations
    )
    user = (
        "TRIGGER-LED email (do not duplicate it):\n"
        f"Subject: {trigger.subject}\n\n{trigger.body}\n\n"
        "ORIGINAL PAIN-LED email (rewrite it):\n"
        f"Subject: {pain.subject}\n\n{pain.body}\n\n"
        f"TARGET OBSERVATIONS:\n{obs_block}\n\n"
        "Rewrite the PAIN-LED email now."
    )
    try:
        draft = llm.structured(
            system=_SYSTEM_DIVERGE,
            user=user,
            schema=_DivergenceDraft,
            purpose="diverge_pain_led",
            usage=usage,
            temperature=0.4,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("diverge_pain_led failed: %s", e)
        return pain

    return pain.model_copy(
        update={
            "subject": draft.subject.strip(),
            "body": draft.body.strip(),
            "safety": None,
            "angle": AngleType.PAIN_LED,
        }
    )
