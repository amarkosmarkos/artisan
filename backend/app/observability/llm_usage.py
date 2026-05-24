"""Map LLM usage accumulators into persisted run metrics."""
from __future__ import annotations

from ..schemas import LlmUsageByPurpose, RunMetrics
from ..services.llm import UsageAccumulator

# Display order follows the pipeline (roughly).
_PURPOSE_ORDER: tuple[str, ...] = (
    "extract_observations_",
    "planner_",
    "synthesize_icp",
    "synthesize_vp",
    "synthesize_strategy",
    "writer_write_email_",
    "email_guard_judge",
    "email_guard_regenerate",
    "diverge_pain_led",
    "repair_email",
    "target_discovery_",
)

_PURPOSE_LABELS: dict[str, str] = {
    "extract_observations_target": "Observation extraction (target)",
    "extract_observations_sender": "Observation extraction (sender)",
    "synthesize_icp": "ICP synthesis",
    "synthesize_vp": "Value proposition synthesis",
    "synthesize_strategy": "Strategy synthesis",
    "email_guard_judge": "Email safety verification",
    "email_guard_regenerate": "Email safety regeneration",
    "diverge_pain_led": "Angle overlap repair",
    "repair_email": "Email repair",
    "target_discovery_extract": "Target discovery (extract)",
}


def purpose_label(purpose: str) -> str:
    if purpose in _PURPOSE_LABELS:
        return _PURPOSE_LABELS[purpose]
    if purpose.startswith("writer_write_email_"):
        angle = purpose.removeprefix("writer_write_email_").replace("_", "-")
        return f"Email writer ({angle})"
    if purpose.startswith("planner_"):
        task = purpose.removeprefix("planner_").replace("_", " ")
        return f"Planner ({task})"
    return purpose.replace("_", " ").strip().title()


def _sort_key(purpose: str) -> tuple[int, str]:
    for idx, prefix in enumerate(_PURPOSE_ORDER):
        if purpose.startswith(prefix) or purpose == prefix.rstrip("_"):
            return (idx, purpose)
    return (len(_PURPOSE_ORDER), purpose)


def build_llm_usage_breakdown(usage: UsageAccumulator) -> list[LlmUsageByPurpose]:
    rows: list[LlmUsageByPurpose] = []
    for purpose, bucket in usage.by_purpose.items():
        rows.append(
            LlmUsageByPurpose(
                purpose=purpose,
                label=purpose_label(purpose),
                calls=int(bucket.get("calls") or 0),
                tokens_in=int(bucket.get("tokens_in") or 0),
                tokens_out=int(bucket.get("tokens_out") or 0),
                cost_usd=round(float(bucket.get("cost_usd") or 0.0), 6),
            )
        )
    rows.sort(key=lambda r: (_sort_key(r.purpose), -r.cost_usd))
    return rows


def apply_usage_to_metrics(usage: UsageAccumulator, metrics: RunMetrics) -> None:
    """Copy token/cost totals and per-purpose breakdown into run metrics."""
    metrics.tokens_in = usage.tokens_in
    metrics.tokens_out = usage.tokens_out
    metrics.cost_usd = round(usage.cost_usd, 6)
    metrics.llm_calls = usage.calls
    metrics.llm_usage_by_purpose = build_llm_usage_breakdown(usage)
