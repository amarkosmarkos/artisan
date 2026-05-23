"""Dashboard endpoints.

The dashboard is the auditability surface of the system: it shows the full
evidence chain (sources -> sections -> observations -> ICP -> strategy ->
emails -> claims -> verification status). Everything is read directly from
SQLite so the API is a thin projection layer over the data the pipeline
already persisted.

Phase 2 additions:
- ``GET /companies`` accepts ``role=sender|target`` and ``q=substring`` filters.
- ``GET /companies/{id}`` returns a unified detail blob (sender or target).
- ``DELETE /companies/{id}`` cascades through every dependent table.
- ``DELETE /strategies/{target_company_id}`` and
  ``DELETE /emails/{email_id}`` for finer CRUD.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from ..db import fetchall, fetchone, tx

log = logging.getLogger(__name__)

router = APIRouter()


# ---------- Companies ----------


@router.get("/companies")
def list_companies(
    role: Literal["sender", "target"] | None = Query(default=None),
    q: str | None = Query(default=None, description="case-insensitive URL substring"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    where: list[str] = []
    params: list[Any] = []
    if role:
        where.append("role = ?")
        params.append(role)
    if q:
        where.append("LOWER(url) LIKE ?")
        params.append(f"%{q.lower()}%")
    sql = (
        "SELECT company_id, url, role, created_at FROM companies"
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY created_at DESC LIMIT ?"
    )
    params.append(limit)
    rows = fetchall(sql, tuple(params))
    return {"companies": [dict(r) for r in rows]}


@router.get("/companies/{company_id}")
def company_detail(company_id: str) -> dict:
    row = fetchone(
        "SELECT company_id, url, role, created_at FROM companies WHERE company_id = ?",
        (company_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="company not found")

    base = dict(row)

    # Counts so list/detail views can show evidence depth at a glance.
    counts = {
        "pages": _count("SELECT COUNT(*) AS c FROM pages WHERE company_id = ?", company_id),
        "sections": _count(
            "SELECT COUNT(*) AS c FROM sections WHERE company_id = ?", company_id
        ),
        "observations": _count(
            "SELECT COUNT(*) AS c FROM observations WHERE company_id = ?", company_id
        ),
    }

    detail: dict[str, Any] = {**base, "counts": counts}

    if base["role"] == "sender":
        icp_row = fetchone(
            "SELECT payload FROM icps WHERE company_id = ?", (company_id,)
        )
        vp_row = fetchone(
            "SELECT payload FROM value_props WHERE company_id = ?", (company_id,)
        )
        detail["icp"] = json.loads(icp_row["payload"]) if icp_row else None
        if vp_row:
            from ..synthesis.value_props_store import (
                parse_stored_value_props,
                primary_value_proposition,
            )

            vps = parse_stored_value_props(json.loads(vp_row["payload"]))
            detail["value_propositions"] = [
                vp.model_dump(mode="json") for vp in vps
            ]
            detail["value_proposition"] = primary_value_proposition(vps).model_dump(
                mode="json"
            )
        else:
            detail["value_proposition"] = None
            detail["value_propositions"] = []
    else:
        detail["personas"] = _target_persona_runs(company_id)

    return detail


def _resolve_selected_vp(
    sender_company_id: str | None, strategy_payload: dict
) -> tuple[dict | None, list[dict]]:
    """Look up the sender's stored VPs and resolve the strategy's selected one.

    Returns ``(selected_vp_dict_or_None, all_sender_vp_dicts)``. Used by the
    persisted-target dashboard endpoint so the UI never has to guess which
    VP drove a strategy.
    """
    from ..synthesis.value_props_store import (
        parse_stored_value_props,
        resolve_value_proposition,
    )

    if not sender_company_id:
        return None, []
    vp_row = fetchone(
        "SELECT payload FROM value_props WHERE company_id = ?",
        (sender_company_id,),
    )
    if not vp_row:
        return None, []
    vps = parse_stored_value_props(json.loads(vp_row["payload"]))
    if not vps:
        return None, []
    selected_id = (strategy_payload or {}).get("selected_value_proposition_id")
    resolved = resolve_value_proposition(vps, selected_id)
    return (
        resolved.model_dump(mode="json") if resolved else None,
        [vp.model_dump(mode="json") for vp in vps],
    )


def _target_persona_runs(target_company_id: str) -> list[dict]:
    """Group strategies+emails+claim_map per persona for a target.

    Returns one entry per persona record attached to the target. Personas
    that exist but never had outreach generated come back with
    ``strategy=None`` and ``emails=[]`` so the UI can render them as
    "ready to run".
    """
    personas = fetchall(
        "SELECT persona_id, target_company_id, name, role, seniority, created_at "
        "FROM personas WHERE target_company_id = ? ORDER BY created_at ASC",
        (target_company_id,),
    )
    # Strategies / emails are keyed by (target_company_id, persona_id) where
    # persona_id may be '' for legacy rows. Build a quick lookup:
    strat_rows = fetchall(
        "SELECT persona_id, sender_company_id, persona, payload "
        "FROM strategies WHERE target_company_id = ?",
        (target_company_id,),
    )
    strat_by_pid = {r["persona_id"]: r for r in strat_rows}

    email_rows = fetchall(
        "SELECT email_id, persona_id, angle, payload "
        "FROM emails WHERE target_company_id = ? ORDER BY angle ASC",
        (target_company_id,),
    )
    emails_by_pid: dict[str, list[dict]] = {}
    for r in email_rows:
        emails_by_pid.setdefault(r["persona_id"], []).append(
            json.loads(r["payload"])
        )

    claim_rows = fetchall(
        "SELECT cm.claim_id, cm.email_id, cm.angle, cm.text, cm.status, "
        "       cm.nli_score, cm.citations, e.persona_id "
        "FROM claim_map cm "
        "JOIN emails e ON e.email_id = cm.email_id "
        "WHERE e.target_company_id = ?",
        (target_company_id,),
    )
    claims_by_pid: dict[str, list[dict]] = {}
    for r in claim_rows:
        d = dict(r)
        d["citations"] = json.loads(d["citations"] or "[]")
        claims_by_pid.setdefault(d.pop("persona_id"), []).append(d)

    def _build_strategy_block(s_row: Any) -> dict:
        strategy_payload = json.loads(s_row["payload"])
        selected_vp, sender_vps = _resolve_selected_vp(
            s_row["sender_company_id"], strategy_payload
        )
        return {
            "sender_company_id": s_row["sender_company_id"],
            "persona": json.loads(s_row["persona"]),
            "strategy": strategy_payload,
            "selected_value_proposition": selected_vp,
            "sender_value_propositions": sender_vps,
        }

    out: list[dict] = []
    seen_pids: set[str] = set()
    for p in personas:
        pid = p["persona_id"]
        seen_pids.add(pid)
        s = strat_by_pid.get(pid)
        out.append(
            {
                "persona_id": pid,
                "name": p["name"],
                "role": p["role"],
                "seniority": p["seniority"],
                "created_at": p["created_at"],
                "strategy": _build_strategy_block(s) if s else None,
                "emails": emails_by_pid.get(pid, []),
                "claim_map": claims_by_pid.get(pid, []),
            }
        )

    # Surface legacy/orphan strategy rows that have no persona record so we
    # don't silently hide them from the UI.
    for pid, s in strat_by_pid.items():
        if pid in seen_pids:
            continue
        persona_payload = json.loads(s["persona"])
        out.append(
            {
                "persona_id": pid,
                "name": persona_payload.get("name"),
                "role": persona_payload.get("role", "—"),
                "seniority": persona_payload.get("seniority", "ic"),
                "created_at": None,
                "strategy": _build_strategy_block(s),
                "emails": emails_by_pid.get(pid, []),
                "claim_map": claims_by_pid.get(pid, []),
            }
        )
    return out


@router.delete("/companies/{company_id}")
def delete_company(company_id: str) -> dict:
    """Cascade-delete a company and every artifact derived from it.

    SQLite doesn't have ON DELETE CASCADE configured on these tables, so we
    do it explicitly inside one transaction. We also walk through emails to
    find the claim_map entries, since claim_map is keyed by email_id, not
    by company_id.
    """
    row = fetchone(
        "SELECT company_id FROM companies WHERE company_id = ?", (company_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="company not found")

    with tx() as conn:
        # Claim map: keyed by email_id, so resolve email_ids first.
        email_ids = [
            r["email_id"]
            for r in conn.execute(
                "SELECT email_id FROM emails WHERE target_company_id = ?",
                (company_id,),
            ).fetchall()
        ]
        if email_ids:
            placeholders = ",".join("?" for _ in email_ids)
            conn.execute(
                f"DELETE FROM claim_map WHERE email_id IN ({placeholders})",
                tuple(email_ids),
            )

        for sql in (
            "DELETE FROM emails WHERE target_company_id = ?",
            "DELETE FROM strategies WHERE target_company_id = ?",
            "DELETE FROM strategies WHERE sender_company_id = ?",
            "DELETE FROM icps WHERE company_id = ?",
            "DELETE FROM value_props WHERE company_id = ?",
            "DELETE FROM observations WHERE company_id = ?",
            "DELETE FROM sections WHERE company_id = ?",
            "DELETE FROM pages WHERE company_id = ?",
            "DELETE FROM personas WHERE target_company_id = ?",
            "DELETE FROM sender_targets WHERE sender_company_id = ?",
            "DELETE FROM sender_targets WHERE target_company_id = ?",
            "DELETE FROM runs WHERE company_id = ?",
            "DELETE FROM runs WHERE target_company_id = ?",
            "DELETE FROM companies WHERE company_id = ?",
        ):
            conn.execute(sql, (company_id,))

    return {"deleted": company_id}


# ---------- Sources / sections / observations ----------


@router.get("/companies/{company_id}/sources")
def company_sources(company_id: str) -> dict:
    pages = fetchall(
        "SELECT page_id, url, status_code, cleaned_chars, source, fetched_at "
        "FROM pages WHERE company_id = ? ORDER BY fetched_at",
        (company_id,),
    )
    return {"pages": [dict(r) for r in pages]}


@router.get("/companies/{company_id}/sections")
def company_sections(company_id: str) -> dict:
    rows = fetchall(
        "SELECT section_id, url, heading, length(text) AS chars, source "
        "FROM sections WHERE company_id = ? ORDER BY rowid",
        (company_id,),
    )
    return {"sections": [dict(r) for r in rows]}


@router.get("/sections/{section_id}")
def section_detail(section_id: str) -> dict:
    row = fetchone(
        "SELECT section_id, url, heading, text, char_start, char_end, source "
        "FROM sections WHERE section_id = ?",
        (section_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="section not found")
    return dict(row)


@router.get("/companies/{company_id}/observations")
def company_observations(company_id: str) -> dict:
    rows = fetchall(
        "SELECT observation_id, kind, text, section_id, confidence, validation, validation_score "
        "FROM observations WHERE company_id = ? ORDER BY rowid",
        (company_id,),
    )
    return {"observations": [dict(r) for r in rows]}


# ---------- Sender artifacts ----------


@router.get("/companies/{company_id}/icp")
def company_icp(company_id: str) -> dict:
    row = fetchone("SELECT payload FROM icps WHERE company_id = ?", (company_id,))
    if not row:
        raise HTTPException(status_code=404, detail="icp not found")
    return json.loads(row["payload"])


@router.get("/companies/{company_id}/value-proposition")
def company_vp(company_id: str) -> dict:
    row = fetchone(
        "SELECT payload FROM value_props WHERE company_id = ?", (company_id,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="value proposition not found")
    return json.loads(row["payload"])


# ---------- Target artifacts ----------


@router.get("/targets/{target_company_id}/strategy")
def target_strategy(target_company_id: str) -> dict:
    row = fetchone(
        "SELECT payload, persona, sender_company_id FROM strategies WHERE target_company_id = ?",
        (target_company_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="strategy not found")
    return {
        "sender_company_id": row["sender_company_id"],
        "persona": json.loads(row["persona"]),
        "strategy": json.loads(row["payload"]),
    }


@router.delete("/strategies/{target_company_id}")
def delete_strategy(
    target_company_id: str,
    persona_id: str | None = Query(default=None),
) -> dict:
    """Delete strategy + downstream emails + claim_map.

    When ``persona_id`` is provided we scope the delete to that persona;
    otherwise we wipe every strategy attached to the target.
    """
    where = "WHERE target_company_id = ?"
    params: list[Any] = [target_company_id]
    if persona_id is not None:
        where += " AND persona_id = ?"
        params.append(persona_id)

    rows = fetchall(f"SELECT persona_id FROM strategies {where}", tuple(params))
    if not rows:
        raise HTTPException(status_code=404, detail="strategy not found")

    with tx() as conn:
        email_ids = [
            r["email_id"]
            for r in conn.execute(
                f"SELECT email_id FROM emails {where}", tuple(params)
            ).fetchall()
        ]
        if email_ids:
            placeholders = ",".join("?" for _ in email_ids)
            conn.execute(
                f"DELETE FROM claim_map WHERE email_id IN ({placeholders})",
                tuple(email_ids),
            )
        conn.execute(f"DELETE FROM emails {where}", tuple(params))
        conn.execute(f"DELETE FROM strategies {where}", tuple(params))

    return {
        "deleted_target": target_company_id,
        "deleted_persona": persona_id,
    }


@router.get("/targets/{target_company_id}/emails")
def target_emails(target_company_id: str) -> dict:
    rows = fetchall(
        "SELECT email_id, angle, subject, body, payload FROM emails WHERE target_company_id = ?",
        (target_company_id,),
    )
    return {"emails": [json.loads(r["payload"]) for r in rows]}


@router.delete("/emails/{email_id}")
def delete_email(email_id: str) -> dict:
    row = fetchone("SELECT email_id FROM emails WHERE email_id = ?", (email_id,))
    if not row:
        raise HTTPException(status_code=404, detail="email not found")
    with tx() as conn:
        conn.execute("DELETE FROM claim_map WHERE email_id = ?", (email_id,))
        conn.execute("DELETE FROM emails WHERE email_id = ?", (email_id,))
    return {"deleted": email_id}


@router.get("/targets/{target_company_id}/claim-map")
def target_claim_map(target_company_id: str) -> dict:
    rows = fetchall(
        "SELECT cm.claim_id, cm.email_id, cm.angle, cm.text, cm.status, cm.nli_score, cm.citations "
        "FROM claim_map cm "
        "JOIN emails e ON e.email_id = cm.email_id "
        "WHERE e.target_company_id = ?",
        (target_company_id,),
    )
    out = []
    for r in rows:
        d = dict(r)
        d["citations"] = json.loads(d["citations"] or "[]")
        out.append(d)
    return {"claims": out}


# ---------- Evidence resolver ----------


@router.post("/evidence/resolve")
def resolve_evidence(payload: dict) -> dict:
    """Hydrate a list of observation IDs into full evidence records.

    Body: ``{"observation_ids": ["obs_..", "obs_.."]}``.
    Returns each observation joined with its section text + URL so the
    frontend can render an expandable claim/evidence block in one call.
    """
    ids = payload.get("observation_ids") or []
    if not isinstance(ids, list) or not ids:
        return {"evidence": {}}
    placeholders = ",".join("?" for _ in ids)
    rows = fetchall(
        f"SELECT o.observation_id, o.text, o.kind, o.confidence, o.validation, "
        f"       o.validation_score, o.section_id, s.url, s.heading, s.text AS section_text "
        f"FROM observations o "
        f"LEFT JOIN sections s ON s.section_id = o.section_id "
        f"WHERE o.observation_id IN ({placeholders})",
        tuple(ids),
    )
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        snippet = d.pop("section_text") or d["text"]
        # Trim very long sections so the UI stays readable; the full
        # section is available via /sections/{id} if needed.
        if isinstance(snippet, str) and len(snippet) > 600:
            snippet = snippet[:600].rsplit(" ", 1)[0] + "…"
        d["snippet"] = snippet
        out[d["observation_id"]] = d
    return {"evidence": out}


# ---------- helpers ----------


def _count(sql: str, *params: Any) -> int:
    row = fetchone(sql, tuple(params))
    return int(row["c"]) if row else 0
