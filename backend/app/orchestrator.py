"""Thin orchestrator.

The orchestrator's only job now is to:

1. Wire up singletons (LLM, NLI, embedder, external signal provider).
2. Open an MLflow run via ``RunTracker``.
3. Build the initial ``FlowState`` and hand it to the LangGraph state
   machine for the chosen flow.

All pipeline logic lives in ``app.graph`` and the existing
``app.pipeline`` / ``app.synthesis`` modules. This keeps the public
interface from the routes layer stable while the internals are a tidy
state machine.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Callable

from .db import fetchone, tx
from .graph import run_sender_graph, run_target_graph
from .graph.state import make_initial_state
from .observability.tracker import RunTracker
from .schemas import (
    ICP,
    PersonaInput,
    SenderResponse,
    TargetResponse,
    ValueProposition,
)
from .services.embed import get_embedder
from .services.external import get_external_provider
from .services.llm import UsageAccumulator, get_llm
from .services.nli import get_nli

log = logging.getLogger(__name__)


ProgressFn = Callable[[str, dict], None]


def _noop(stage: str, detail: dict) -> None:  # noqa: ARG001
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_company(company_id: str, url: str, role: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO companies (company_id, url, role, created_at) VALUES (?,?,?,?)",
            (company_id, url, role, _now()),
        )


def _existing_or_new_target(url: str, sender_company_id: str) -> str:
    """Reuse a target row when (url, role='target') already exists.

    If the URL was previously added to this sender (via the UI's "Add target"
    action), we reuse that company_id; otherwise we mint a new one and
    persist it. The caller is responsible for inserting the sender↔target
    association.
    """
    row = fetchone(
        "SELECT c.company_id "
        "FROM companies c "
        "LEFT JOIN sender_targets st "
        "  ON st.target_company_id = c.company_id "
        "  AND st.sender_company_id = ? "
        "WHERE c.url = ? AND c.role = 'target' "
        "ORDER BY (st.added_at IS NULL), c.created_at DESC "
        "LIMIT 1",
        (sender_company_id, url),
    )
    if row:
        return row["company_id"]
    company_id = f"co_{uuid.uuid4().hex[:10]}"
    _persist_company(company_id, url, "target")
    return company_id


def _associate_sender_target(sender_company_id: str, target_company_id: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sender_targets "
            "(sender_company_id, target_company_id, added_at) VALUES (?,?,?)",
            (sender_company_id, target_company_id, _now()),
        )


def _existing_or_new_persona(
    target_company_id: str, persona: PersonaInput
) -> str:
    """Reuse a persona row matching this target + role + seniority.

    Inline persona payloads from the workflow page are auto-promoted to
    persistent persona rows so every run is grouped under a persona in
    the target detail UI.
    """
    role = (persona.role or "").strip()
    seniority = persona.seniority.value
    row = fetchone(
        "SELECT persona_id FROM personas "
        "WHERE target_company_id = ? AND role = ? AND seniority = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (target_company_id, role, seniority),
    )
    if row:
        return row["persona_id"]
    persona_id = f"pers_{uuid.uuid4().hex[:10]}"
    with tx() as conn:
        conn.execute(
            "INSERT INTO personas "
            "(persona_id, target_company_id, name, role, seniority, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (persona_id, target_company_id, None, role, seniority, _now()),
        )
    return persona_id


def _persist_run_metrics(
    *,
    kind: str,
    company_id: str | None,
    target_company_id: str | None,
    metrics_json: str,
) -> str:
    """Snapshot a finished run's metrics into the runs table.

    The technical / admin dashboard reads from here so the user has a
    durable list of past pipeline executions even after process restarts.
    """
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    with tx() as conn:
        conn.execute(
            "INSERT INTO runs (run_id, kind, company_id, target_company_id, metrics, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, kind, company_id, target_company_id, metrics_json, _now()),
        )
    return run_id


def _load_sender_artifacts(
    sender_company_id: str,
) -> tuple[ICP, list[ValueProposition], ValueProposition] | None:
    from .synthesis.value_props_store import (
        parse_stored_value_props,
        primary_value_proposition,
    )

    icp_row = fetchone(
        "SELECT payload FROM icps WHERE company_id = ?", (sender_company_id,)
    )
    vp_row = fetchone(
        "SELECT payload FROM value_props WHERE company_id = ?", (sender_company_id,)
    )
    if not icp_row or not vp_row:
        return None
    vps = parse_stored_value_props(json.loads(vp_row["payload"]))
    if not vps:
        return None
    return (
        ICP.model_validate(json.loads(icp_row["payload"])),
        vps,
        primary_value_proposition(vps),
    )


async def run_sender_flow_async(
    homepage_url: str,
    *,
    progress: ProgressFn = _noop,
) -> SenderResponse:
    llm = get_llm()
    nli = get_nli()
    usage = UsageAccumulator()
    company_id = f"co_{uuid.uuid4().hex[:10]}"
    _persist_company(company_id, homepage_url, "sender")

    with RunTracker(
        "sender", run_name=f"sender:{homepage_url}", params={"url": homepage_url}
    ) as tracker:
        state = make_initial_state(
            task="sender",
            homepage_url=homepage_url,
            company_id=company_id,
            progress=progress,
            usage=usage,
            tracker=tracker,
        )
        response = await run_sender_graph(initial_state=state, llm=llm, nli=nli)
        _persist_run_metrics(
            kind="sender",
            company_id=response.company_id,
            target_company_id=None,
            metrics_json=response.metrics.model_dump_json(),
        )
        progress("done", {
            "company_id": response.company_id,
            "observations": len(response.observations),
            "icp_fields": {
                "industries": len(response.icp.target_industries.values),
                "size_bands": len(response.icp.size_bands.values),
                "buyers": len(response.icp.likely_buyers.values),
                "triggers": len(response.icp.common_triggers.values),
                "negative": len(response.icp.negative_icp.values),
            },
        })
        return response


async def run_target_flow_async(
    *,
    sender_company_id: str,
    target_url: str,
    persona: PersonaInput,
    progress: ProgressFn = _noop,
    persona_id: str | None = None,
) -> TargetResponse:
    sender_pair = _load_sender_artifacts(sender_company_id)
    if not sender_pair:
        raise ValueError(
            f"unknown sender_company_id={sender_company_id}; run sender flow first"
        )
    sender_icp, sender_vps, sender_vp = sender_pair

    llm = get_llm()
    nli = get_nli()
    embedder = get_embedder()
    external = get_external_provider(llm)
    usage = UsageAccumulator()

    # Reuse a target row if the same URL was already added to this sender.
    # This makes "research the same target again" a re-run, not a duplicate.
    target_company_id = _existing_or_new_target(target_url, sender_company_id)
    _associate_sender_target(sender_company_id, target_company_id)

    # Always link the run to a persona record so the UI can group results
    # by persona. If the caller didn't pass an explicit persona_id we
    # promote the inline persona to a row (deduplicated by role+seniority).
    if not persona_id:
        persona_id = _existing_or_new_persona(target_company_id, persona)

    with RunTracker(
        "target",
        run_name=f"target:{target_url}",
        params={
            "target_url": target_url,
            "sender_company_id": sender_company_id,
            "role": persona.role,
            "seniority": persona.seniority.value,
            "external_provider": external.name,
        },
    ) as tracker:
        state = make_initial_state(
            task="target",
            homepage_url=target_url,
            company_id=target_company_id,
            progress=progress,
            usage=usage,
            tracker=tracker,
            sender_company_id=sender_company_id,
            sender_icp=sender_icp,
            sender_vp=sender_vp,
            sender_vps=sender_vps,
            persona=persona,
            persona_id=persona_id,
        )
        response = await run_target_graph(
            initial_state=state,
            llm=llm,
            nli=nli,
            embedder=embedder,
            external=external,
        )
        _persist_run_metrics(
            kind="target",
            company_id=sender_company_id,
            target_company_id=response.target_company_id,
            metrics_json=response.metrics.model_dump_json(),
        )
        progress("done", {
            "target_company_id": response.target_company_id,
            "claims_total": tracker.metrics.claims_total,
            "claims_supported": tracker.metrics.claims_supported,
            "angle_overlap": tracker.metrics.angle_overlap,
        })
        return response
