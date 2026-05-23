"""The single agentic Planner step.

After the first observation pass we ask the LLM: do we have enough
evidence to synthesize, or do we need more? This is the *only* open-ended
decision point in the pipeline. Everything else is deterministic.

The planner sees:
- the current set of validated observations (kind + text + section)
- which target fields are still missing
- per-field evidence counts and confidence
- which source URLs failed or returned thin content
- the active task: ``sender_icp`` or ``target_eval``

The planner outputs a typed decision; the orchestrator acts on it.
"""
from __future__ import annotations

import logging

from ..schemas import (
    Observation,
    PlannerDecision,
    PlannerInput,
    PlannerOutput,
)
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


_SYSTEM = """You are the Planner for an evidence-first outbound research pipeline.

You decide whether the system has enough public evidence to proceed to
synthesis, or whether it should fetch more. You do NOT generate content,
ICPs, or emails. You output a single typed decision.

Decisions:
- "continue": evidence is sufficient; proceed to synthesis.
- "fetch_more": ask the system to fetch additional internal pages from the same site.
- "web_search": ask the system to run bounded public web search (only for target evaluation; do not use for sender ICP unless website is genuinely too thin).
- "proceed_low_confidence": evidence is thin but further fetching is unlikely to help; proceed with explicit low confidence.
- "stop": evidence is so thin that downstream synthesis would be ungrounded.

Rules:
- Prefer "continue" when each critical field has at least 2 supporting observations.
- Prefer "fetch_more" ONLY when critical fields have 0-1 observations AND the
  "Uncrawled discovered URLs" list below is non-empty.
- If uncrawled discovered URLs is empty, do NOT choose fetch_more — choose
  "continue" or "proceed_low_confidence" instead.
- Prefer "web_search" only for target evaluation when current website coverage is fine but external triggers (news, hiring, funding) are missing.
- Prefer "proceed_low_confidence" when missing evidence is unlikely to be public.
- Suggested queries must be specific (company name + signal).
- For suggested_internal_pages: pick ONLY exact URLs from the "Uncrawled discovered URLs" list. Never invent paths.

Return JSON matching: {
  "decision": "continue|fetch_more|web_search|proceed_low_confidence|stop",
  "reason": "...",
  "missing_fields": [...],
  "suggested_queries": [...],
  "suggested_internal_pages": [...]
}
"""


def _format_observations(obs: list[Observation]) -> str:
    if not obs:
        return "(none)"
    by_kind: dict[str, list[str]] = {}
    for o in obs:
        by_kind.setdefault(o.kind, []).append(o.text)
    lines: list[str] = []
    for kind, texts in by_kind.items():
        lines.append(f"- {kind} ({len(texts)}):")
        for t in texts[:6]:
            lines.append(f"    * {t}")
    return "\n".join(lines)


def run_planner(
    inp: PlannerInput,
    *,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> PlannerOutput:
    uncrawled = inp.uncrawled_discovered_urls[:40]
    user = (
        f"Task: {inp.task}\n\n"
        f"Validated observations (by kind):\n{_format_observations(inp.observations)}\n\n"
        f"Missing fields: {inp.missing_fields}\n"
        f"Evidence counts per field: {inp.evidence_counts}\n"
        f"Confidence per field: {inp.field_confidence}\n"
        f"Failed sources: {inp.failed_sources[:6]}\n\n"
        f"Uncrawled discovered URLs ({len(uncrawled)}):\n"
        + ("\n".join(f"  - {u}" for u in uncrawled) if uncrawled else "  (none — do not choose fetch_more)\n")
    )
    try:
        out = llm.structured(
            system=_SYSTEM,
            user=user,
            schema=PlannerOutput,
            purpose=f"planner_{inp.task}",
            usage=usage,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        log.warning("planner failed, defaulting to proceed_low_confidence: %s", e)
        # Defensive refusal: a planner error never blocks the pipeline -- we
        # surface the low-confidence path rather than silently retrying.
        return PlannerOutput(
            decision=PlannerDecision.PROCEED_LOW_CONFIDENCE,
            reason=f"planner_error: {e}",
            missing_fields=inp.missing_fields,
        )

    # Safety: never produce more than a handful of new fetches.
    allowed = set(uncrawled)
    out.suggested_internal_pages = [
        u for u in out.suggested_internal_pages if u in allowed
    ][:5]
    out.suggested_queries = out.suggested_queries[:3]

    # Hard guard: fetch_more requires real uncrawled URLs.
    if out.decision == PlannerDecision.FETCH_MORE and not uncrawled:
        out = PlannerOutput(
            decision=PlannerDecision.PROCEED_LOW_CONFIDENCE,
            reason=(
                (out.reason or "")
                + " [overridden: no uncrawled internal URLs remain]"
            ).strip(),
            missing_fields=out.missing_fields,
            suggested_queries=out.suggested_queries,
            suggested_internal_pages=[],
        )
    log.info(
        "planner decision=%s reason=%s missing=%s",
        out.decision.value, out.reason[:80], out.missing_fields,
    )
    return out
