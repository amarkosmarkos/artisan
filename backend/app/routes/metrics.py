"""Technical / admin metrics dashboard.

Reads from the ``runs`` table that the orchestrator populates on every
successful flow execution. Each row contains the full ``RunMetrics``
payload as JSON, so downstream pages can render latency, tokens, cost,
pages fetched, claim support rate, angle overlap, planner decisions, and
the per-stage timeline without re-running anything.

This is the pipeline-operator view. The product/customer-facing pages
(``/senders/[id]``, ``/targets/[id]``) deliberately don't surface this
detail.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..db import fetchall, fetchone

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("/runs")
def list_runs(
    kind: Literal["sender", "target"] | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    where: list[str] = []
    params: list[Any] = []
    if kind:
        where.append("r.kind = ?")
        params.append(kind)
    sql = (
        "SELECT r.run_id, r.kind, r.company_id, r.target_company_id, r.metrics, r.created_at, "
        "       sc.url AS sender_url, tc.url AS target_url "
        "FROM runs r "
        "LEFT JOIN companies sc ON sc.company_id = r.company_id "
        "LEFT JOIN companies tc ON tc.company_id = r.target_company_id "
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY r.created_at DESC LIMIT ?"
    )
    params.append(limit)
    rows = fetchall(sql, tuple(params))
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        m = _enrich_metrics(
            json.loads(d.pop("metrics") or "{}"),
            kind=d["kind"],
            company_id=d.get("company_id"),
            target_company_id=d.get("target_company_id"),
        )
        d["summary"] = _summary_metrics(m)
        out.append(d)
    return {"runs": out}


@router.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    row = fetchone(
        "SELECT r.run_id, r.kind, r.company_id, r.target_company_id, r.metrics, r.created_at, "
        "       sc.url AS sender_url, tc.url AS target_url "
        "FROM runs r "
        "LEFT JOIN companies sc ON sc.company_id = r.company_id "
        "LEFT JOIN companies tc ON tc.company_id = r.target_company_id "
        "WHERE r.run_id = ?",
        (run_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="run not found")
    d = dict(row)
    m = _enrich_metrics(
        json.loads(d.pop("metrics") or "{}"),
        kind=d["kind"],
        company_id=d.get("company_id"),
        target_company_id=d.get("target_company_id"),
    )
    d["metrics"] = m
    d["summary"] = _summary_metrics(m)
    return d


@router.get("/runs-summary")
def runs_summary() -> dict:
    """Aggregate KPIs across all stored runs (used by the metrics dashboard header)."""
    rows = fetchall("SELECT kind, metrics FROM runs")
    total = len(rows)
    by_kind: dict[str, int] = {"sender": 0, "target": 0}
    tokens_in = tokens_out = 0
    cost_usd = 0.0
    pages = obs = 0
    extracted = supported = 0
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        m = json.loads(r["metrics"] or "{}")
        tokens_in += int(m.get("tokens_in") or 0)
        tokens_out += int(m.get("tokens_out") or 0)
        cost_usd += float(m.get("cost_usd") or 0.0)
        pages += int(m.get("pages_fetched") or 0)
        obs += int(m.get("observations_extracted") or 0)
        extracted += int(
            m.get("extracted_statements_count") or m.get("claims_total") or 0
        )
        supported += int(
            m.get("supported_statements_count") or m.get("claims_supported") or 0
        )
    return {
        "total_runs": total,
        "by_kind": by_kind,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": round(cost_usd, 4),
        "pages_fetched": pages,
        "observations_extracted": obs,
        "extracted_statements_count": extracted,
        "supported_statements_count": supported,
        "evidence_support_rate": (
            round(supported / extracted, 3) if extracted else None
        ),
        "claims_total": extracted,
        "claims_supported": supported,
        "claim_support_rate": (
            round(supported / extracted, 3) if extracted else None
        ),
    }


def _enrich_metrics(
    m: dict,
    *,
    kind: str,
    company_id: str | None,
    target_company_id: str | None,
) -> dict:
    """Fill derived / missing fields for runs persisted before finalize_metrics."""
    out = dict(m)
    stages = out.get("stages") or []

    latency_ms = float(out.get("latency_ms") or 0.0)
    if latency_ms <= 0 and stages:
        stage_total = sum(float(s.get("duration_ms") or 0.0) for s in stages)
        if stage_total > 0:
            out["latency_ms"] = round(stage_total, 2)

    obs_company_id = target_company_id if kind == "target" else company_id
    obs_ext = int(out.get("observations_extracted") or 0)
    obs_val = int(out.get("observations_validated") or 0)
    obs_rej = int(out.get("observations_rejected") or 0)

    if obs_val == 0 and obs_ext > 0 and obs_company_id:
        row = fetchone(
            "SELECT "
            "SUM(CASE WHEN validation = 'entailed' THEN 1 ELSE 0 END) AS entailed, "
            "SUM(CASE WHEN validation = 'contradicted' THEN 1 ELSE 0 END) AS contradicted "
            "FROM observations WHERE company_id = ?",
            (obs_company_id,),
        )
        if row and row["entailed"]:
            obs_val = int(row["entailed"])
            obs_rej = int(row["contradicted"] or 0)
            out["observations_validated"] = obs_val
            out["observations_rejected"] = obs_rej

    if out.get("observation_validation_rate") is None and obs_ext > 0 and obs_val > 0:
        out["observation_validation_rate"] = round(obs_val / obs_ext, 3)

    claims_total = int(out.get("claims_total") or 0)
    claims_supported = int(out.get("claims_supported") or 0)
    if out.get("claim_support_rate") is None and claims_total > 0:
        out["claim_support_rate"] = round(claims_supported / claims_total, 3)

    return out


def _summary_metrics(m: dict) -> dict:
    """Compact projection of RunMetrics for list views."""
    return {
        "latency_ms": m.get("latency_ms"),
        "tokens_in": m.get("tokens_in"),
        "tokens_out": m.get("tokens_out"),
        "cost_usd": m.get("cost_usd"),
        "pages_fetched": m.get("pages_fetched"),
        "sections_created": m.get("sections_created"),
        "observations_extracted": m.get("observations_extracted"),
        "observations_validated": m.get("observations_validated"),
        "observations_rejected": m.get("observations_rejected"),
        "observation_validation_rate": m.get("observation_validation_rate"),
        "extracted_statements_count": m.get("extracted_statements_count"),
        "supported_statements_count": m.get("supported_statements_count"),
        "evidence_support_rate": m.get("evidence_support_rate"),
        "final_email_safe": m.get("final_email_safe"),
        "claims_total": m.get("claims_total"),
        "claims_supported": m.get("claims_supported"),
        "claim_support_rate": m.get("claim_support_rate"),
        "angle_overlap": m.get("angle_overlap"),
        "stages": len(m.get("stages") or []),
    }
