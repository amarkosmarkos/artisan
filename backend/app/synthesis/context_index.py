"""Shared CONTEXT INDEX for writer + guardrail.

Both modules need to refer to the same slice of workflow context with the
same ref_id namespace:

  - the writer cites these ref_ids when it declares the claims it used;
  - the guardrail resolves the same ref_ids when verifying those claims.

A single canonical builder avoids drift between the two callers.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..pipeline import validate as obs_validate
from ..schemas import (
    ICP,
    Observation,
    PersonaInput,
    StatementContextRef,
    StrategyArtifact,
    ValueProposition,
)


@dataclass
class ContextBundle:
    """The slice of workflow context that the writer is allowed to cite.

    Used by both the writer (input briefing) and the email guardrail
    (verification of declared claims).
    """

    target_observations: list[Observation] = field(default_factory=list)
    sender_observations: list[Observation] = field(default_factory=list)
    sender_evidence: list[Observation] = field(default_factory=list)
    sender_icp: ICP | None = None
    sender_vp: ValueProposition | None = None
    strategy: StrategyArtifact | None = None
    persona: PersonaInput | None = None
    target_company_name: str = ""


_MAX_CONTEXT_CHARS = 18000


def _icp_field_lines(icp: ICP) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key, fld in (
        ("icp:industries", icp.target_industries),
        ("icp:sizes", icp.size_bands),
        ("icp:buyers", icp.likely_buyers),
        ("icp:triggers", icp.common_triggers),
        ("icp:negative", icp.negative_icp),
    ):
        if fld.values:
            rows.append((key, "; ".join(fld.values)))
    return rows


def _safe_target_observations(bundle: ContextBundle) -> list[Observation]:
    try:
        return obs_validate.filter_for_synthesis(bundle.target_observations)
    except Exception:  # noqa: BLE001
        return list(bundle.target_observations)


def build_context_index(
    bundle: ContextBundle,
) -> tuple[str, dict[str, StatementContextRef]]:
    """Build a numbered context document and ref lookup.

    Only sources the writer actually consumed go in (VP, strategy, sender
    evidence subset, ICP, filtered target observations, persona, identity).
    The returned document is plain text safe to put into an LLM prompt and
    the returned dict maps ``ref_id -> StatementContextRef`` for lookups
    after the LLM call returns cited ids.
    """
    refs: dict[str, StatementContextRef] = {}
    lines: list[str] = ["CONTEXT INDEX:"]
    total_chars = len(lines[0])

    def add(ref_id: str, ref_type: str, label: str, snippet: str) -> None:
        nonlocal total_chars
        snippet = (snippet or "").strip()
        if not snippet:
            return
        snippet = snippet[:400]
        line = f"[{ref_id}] ({ref_type}) {label}: {snippet}"
        if total_chars + len(line) + 1 > _MAX_CONTEXT_CHARS:
            return
        refs[ref_id] = StatementContextRef(
            ref_id=ref_id,
            ref_type=ref_type,
            label=label,
            snippet=snippet,
        )
        lines.append(line)
        total_chars += len(line) + 1

    if bundle.sender_vp:
        vp = bundle.sender_vp
        vp_key = vp.id or "primary"
        for ref_id, label, val in (
            (f"vp:{vp_key}:label", "VP label", vp.label),
            (f"vp:{vp_key}:customer", "VP customer", vp.customer),
            (f"vp:{vp_key}:pain", "VP pain", vp.pain),
            (f"vp:{vp_key}:outcome", "VP outcome", vp.outcome),
            (f"vp:{vp_key}:mechanism", "VP mechanism", vp.mechanism),
        ):
            add(ref_id, "value_prop", label, val)

    if bundle.strategy:
        fa = bundle.strategy.fit_assessment
        add("strategy:fit", "strategy", "fit level", fa.level.value)
        if fa.reasons:
            add(
                "strategy:fit_reasons",
                "strategy",
                "fit reasons",
                "; ".join(fa.reasons),
            )
        if bundle.strategy.messaging_angle:
            add(
                "strategy:messaging_angle",
                "strategy",
                "messaging angle",
                bundle.strategy.messaging_angle,
            )
        if bundle.strategy.selection_reason:
            add(
                "strategy:selection_reason",
                "strategy",
                "VP selection",
                bundle.strategy.selection_reason,
            )
        for angle in bundle.strategy.strategy.angles:
            add(
                f"strategy:angle:{angle.type.value}",
                "strategy",
                f"angle {angle.type.value}",
                angle.hypothesis,
            )

    for obs in bundle.sender_evidence:
        add(
            f"sender:{obs.observation_id}",
            "observation",
            f"sender {obs.kind}",
            obs.text,
        )

    if bundle.sender_icp:
        for ref_id, text in _icp_field_lines(bundle.sender_icp):
            add(ref_id, "icp", ref_id.replace("icp:", ""), text)

    for obs in _safe_target_observations(bundle):
        add(
            obs.observation_id,
            "observation",
            f"target {obs.kind}",
            obs.text,
        )

    if bundle.persona:
        add("persona:role", "persona", "role", bundle.persona.role)
        add(
            "persona:seniority",
            "persona",
            "seniority",
            bundle.persona.seniority.value,
        )
    if bundle.target_company_name:
        add("target:name", "target", "target company name", bundle.target_company_name)

    return "\n".join(lines), refs
