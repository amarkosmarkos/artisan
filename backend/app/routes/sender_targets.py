"""Sender → targets and target → personas CRUD.

A sender's outbound effort is, by definition, the set of targets it pursues.
We don't model "campaigns" as a separate entity: a sender IS the campaign.
This module exposes:

- ``GET    /senders/{sender_company_id}/targets`` — targets owned by a sender
- ``POST   /senders/{sender_company_id}/targets`` — register a target URL and
  associate it to the sender (idempotent on URL).
- ``DELETE /senders/{sender_company_id}/targets/{target_company_id}`` —
  remove the association (does NOT delete the target's evidence).
- ``GET    /companies/{target_company_id}/personas`` — personas attached to
  a target. A persona is a *recipient*: role + seniority + optional name.
- ``POST   /companies/{target_company_id}/personas`` — create a persona.
- ``DELETE /personas/{persona_id}`` — delete a persona.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import fetchall, fetchone, tx
from ..schemas import Seniority

log = logging.getLogger(__name__)

router = APIRouter()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------- Schemas ----------


class AddTargetRequest(BaseModel):
    target_url: str = Field(min_length=1, max_length=500)


class CreatePersonaRequest(BaseModel):
    role: str = Field(min_length=1, max_length=200)
    seniority: Seniority
    name: str | None = None


# ---------- Sender → targets ----------


@router.get("/senders/{sender_company_id}/targets")
def list_sender_targets(sender_company_id: str) -> dict:
    sender = fetchone(
        "SELECT company_id, role FROM companies WHERE company_id = ?",
        (sender_company_id,),
    )
    if not sender:
        raise HTTPException(status_code=404, detail="sender not found")
    if sender["role"] != "sender":
        raise HTTPException(
            status_code=400,
            detail=f"company {sender_company_id} is registered as a {sender['role']}",
        )

    rows = fetchall(
        "SELECT c.company_id, c.url, c.created_at, st.added_at "
        "FROM sender_targets st "
        "JOIN companies c ON c.company_id = st.target_company_id "
        "WHERE st.sender_company_id = ? "
        "ORDER BY st.added_at DESC",
        (sender_company_id,),
    )
    return {"targets": [dict(r) for r in rows]}


@router.post("/senders/{sender_company_id}/targets")
def add_sender_target(sender_company_id: str, req: AddTargetRequest) -> dict:
    sender = fetchone(
        "SELECT company_id, role FROM companies WHERE company_id = ?",
        (sender_company_id,),
    )
    if not sender:
        raise HTTPException(status_code=404, detail="sender not found")
    if sender["role"] != "sender":
        raise HTTPException(
            status_code=400,
            detail=f"company {sender_company_id} is registered as a {sender['role']}",
        )

    url = req.target_url.strip()
    # Reuse a target row if the URL is already known.
    existing = fetchone(
        "SELECT company_id FROM companies WHERE url = ? AND role = 'target'",
        (url,),
    )
    if existing:
        target_company_id = existing["company_id"]
        created = False
    else:
        target_company_id = _new_id("co")
        with tx() as conn:
            conn.execute(
                "INSERT INTO companies (company_id, url, role, created_at) VALUES (?,?,?,?)",
                (target_company_id, url, "target", _now()),
            )
        created = True

    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO sender_targets "
            "(sender_company_id, target_company_id, added_at) VALUES (?,?,?)",
            (sender_company_id, target_company_id, _now()),
        )

    return {
        "sender_company_id": sender_company_id,
        "target_company_id": target_company_id,
        "target_url": url,
        "company_created": created,
    }


@router.delete("/senders/{sender_company_id}/targets/{target_company_id}")
def remove_sender_target(sender_company_id: str, target_company_id: str) -> dict:
    with tx() as conn:
        cur = conn.execute(
            "DELETE FROM sender_targets WHERE sender_company_id = ? AND target_company_id = ?",
            (sender_company_id, target_company_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="association not found")
    return {
        "sender_company_id": sender_company_id,
        "target_company_id": target_company_id,
    }


# ---------- Personas ----------


@router.get("/companies/{target_company_id}/personas")
def list_personas(target_company_id: str) -> dict:
    rows = fetchall(
        "SELECT persona_id, target_company_id, name, role, seniority, created_at "
        "FROM personas WHERE target_company_id = ? ORDER BY created_at DESC",
        (target_company_id,),
    )
    return {"personas": [dict(r) for r in rows]}


@router.post("/companies/{target_company_id}/personas")
def create_persona(target_company_id: str, req: CreatePersonaRequest) -> dict:
    row = fetchone(
        "SELECT company_id, role FROM companies WHERE company_id = ?",
        (target_company_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="target company not found")
    if row["role"] != "target":
        raise HTTPException(
            status_code=400,
            detail=f"company {target_company_id} is registered as a {row['role']}, not a target",
        )

    persona_id = _new_id("pers")
    created_at = _now()
    with tx() as conn:
        conn.execute(
            "INSERT INTO personas (persona_id, target_company_id, name, role, seniority, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (
                persona_id,
                target_company_id,
                req.name,
                req.role.strip(),
                req.seniority.value,
                created_at,
            ),
        )
    return {
        "persona_id": persona_id,
        "target_company_id": target_company_id,
        "name": req.name,
        "role": req.role.strip(),
        "seniority": req.seniority.value,
        "created_at": created_at,
    }


@router.delete("/personas/{persona_id}")
def delete_persona(persona_id: str) -> dict:
    """Delete a persona and cascade through its strategies / emails / claims."""
    row = fetchone(
        "SELECT persona_id FROM personas WHERE persona_id = ?",
        (persona_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="persona not found")
    with tx() as conn:
        email_ids = [
            r["email_id"]
            for r in conn.execute(
                "SELECT email_id FROM emails WHERE persona_id = ?",
                (persona_id,),
            ).fetchall()
        ]
        if email_ids:
            placeholders = ",".join("?" for _ in email_ids)
            conn.execute(
                f"DELETE FROM claim_map WHERE email_id IN ({placeholders})",
                tuple(email_ids),
            )
        conn.execute("DELETE FROM emails WHERE persona_id = ?", (persona_id,))
        conn.execute("DELETE FROM strategies WHERE persona_id = ?", (persona_id,))
        conn.execute("DELETE FROM personas WHERE persona_id = ?", (persona_id,))
    return {"deleted": persona_id}
