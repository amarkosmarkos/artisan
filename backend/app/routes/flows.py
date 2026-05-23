"""HTTP routes for the sender and target flows, plus SSE progress streams.

Run lifecycle:

- ``POST /{kind}/start`` schedules a background task and returns a ``run_id``.
- ``GET  /{kind}/{run_id}/stream`` opens an SSE feed of progress events.
- ``GET  /{kind}/{run_id}/result`` resolves the final artifact.
- ``GET  /{kind}/{run_id}/status`` reports whether the run is still in flight.

We keep the in-memory ``_pending`` entry alive for ``RUN_RETENTION_SECONDS``
after completion so a browser reload can still call ``/result`` and
hydrate the UI. After the TTL the entry is reaped by a small background
task. Long-term persistence (DB-backed run lookup) is added later in
Phase 2.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from sse_starlette.sse import EventSourceResponse

from .. import orchestrator
from ..progress import ProgressChannel
from ..schemas import (
    SenderRequest,
    SenderResponse,
    TargetRequest,
    TargetResponse,
)

log = logging.getLogger(__name__)

router = APIRouter()


RUN_RETENTION_SECONDS = 60 * 10  # keep finished runs queryable for 10 minutes
_REAP_INTERVAL_SECONDS = 60


RunState = Literal["running", "done", "error", "unknown"]


@dataclass
class RunEntry:
    kind: Literal["sender", "target"]
    channel: ProgressChannel
    future: asyncio.Future
    started_at: float
    finished_at: float | None = None  # set when the future resolves


# In-memory map of run_id -> RunEntry. Keyed by run_id, *not* by kind, so
# we can reject /sender lookups for /target runs and vice versa.
_pending: dict[str, RunEntry] = {}


def _new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _entry(run_id: str, kind: Literal["sender", "target"]) -> RunEntry:
    e = _pending.get(run_id)
    if e is None or e.kind != kind:
        raise HTTPException(status_code=404, detail="unknown run_id")
    return e


def _mark_finished(run_id: str) -> None:
    e = _pending.get(run_id)
    if e is not None:
        e.finished_at = time.time()


async def _reap_loop() -> None:
    """Background task that drops expired RunEntry rows."""
    while True:
        await asyncio.sleep(_REAP_INTERVAL_SECONDS)
        now = time.time()
        expired = [
            rid
            for rid, e in _pending.items()
            if e.finished_at is not None
            and (now - e.finished_at) > RUN_RETENTION_SECONDS
        ]
        for rid in expired:
            _pending.pop(rid, None)
        if expired:
            log.debug("reaped %d expired run entries", len(expired))


_reaper_task: asyncio.Task | None = None


def _ensure_reaper() -> None:
    global _reaper_task
    if _reaper_task is None or _reaper_task.done():
        loop = asyncio.get_running_loop()
        _reaper_task = loop.create_task(_reap_loop())


# ---------- Sender ----------


@router.post("/sender", response_model=SenderResponse)
async def sender_flow(req: SenderRequest) -> SenderResponse:
    if not req.sender_url.strip():
        raise HTTPException(status_code=400, detail="sender_url is required")
    channel = ProgressChannel()
    channel.bind(asyncio.get_running_loop())
    try:
        result = await orchestrator.run_sender_flow_async(
            req.sender_url.strip(), progress=channel.emit
        )
    except Exception as e:
        log.exception("sender flow failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@router.post("/sender/start")
async def sender_flow_start(req: SenderRequest) -> dict[str, str]:
    """Start a sender flow asynchronously and return a run_id for SSE streaming."""
    if not req.sender_url.strip():
        raise HTTPException(status_code=400, detail="sender_url is required")
    _ensure_reaper()
    run_id = _new_run_id()
    channel = ProgressChannel()
    loop = asyncio.get_running_loop()
    channel.bind(loop)

    async def _go() -> SenderResponse:
        try:
            res = await orchestrator.run_sender_flow_async(
                req.sender_url.strip(), progress=channel.emit
            )
            channel.emit("__done__", {"ok": True})
            _mark_finished(run_id)
            return res
        except Exception as e:  # noqa: BLE001
            log.exception("sender flow failed")
            channel.emit("__error__", {"error": str(e)})
            _mark_finished(run_id)
            raise

    fut = asyncio.create_task(_go())
    _pending[run_id] = RunEntry(
        kind="sender", channel=channel, future=fut, started_at=time.time()
    )
    return {"run_id": run_id}


@router.get("/sender/{run_id}/stream")
async def sender_flow_stream(run_id: str):
    entry = _entry(run_id, "sender")
    return EventSourceResponse(entry.channel.stream())


@router.get("/sender/{run_id}/result", response_model=SenderResponse)
async def sender_flow_result(run_id: str) -> SenderResponse:
    entry = _entry(run_id, "sender")
    try:
        result: SenderResponse = await entry.future
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@router.get("/sender/{run_id}/status")
async def sender_flow_status(run_id: str) -> dict[str, Any]:
    return _status_payload(run_id, "sender")


# ---------- Target ----------


@router.post("/target", response_model=TargetResponse)
async def target_flow(req: TargetRequest) -> TargetResponse:
    if not req.target_url.strip():
        raise HTTPException(status_code=400, detail="target_url is required")
    if not req.sender_company_id.strip():
        raise HTTPException(status_code=400, detail="sender_company_id is required")
    channel = ProgressChannel()
    channel.bind(asyncio.get_running_loop())
    try:
        result = await orchestrator.run_target_flow_async(
            sender_company_id=req.sender_company_id,
            target_url=req.target_url.strip(),
            persona=req.persona,
            progress=channel.emit,
            persona_id=req.persona_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("target flow failed")
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@router.post("/target/start")
async def target_flow_start(req: TargetRequest) -> dict[str, str]:
    if not req.target_url.strip():
        raise HTTPException(status_code=400, detail="target_url is required")
    if not req.sender_company_id.strip():
        raise HTTPException(status_code=400, detail="sender_company_id is required")
    _ensure_reaper()
    run_id = _new_run_id()
    channel = ProgressChannel()
    loop = asyncio.get_running_loop()
    channel.bind(loop)

    async def _go() -> TargetResponse:
        try:
            res = await orchestrator.run_target_flow_async(
                sender_company_id=req.sender_company_id,
                target_url=req.target_url.strip(),
                persona=req.persona,
                progress=channel.emit,
                persona_id=req.persona_id,
            )
            channel.emit("__done__", {"ok": True})
            _mark_finished(run_id)
            return res
        except Exception as e:  # noqa: BLE001
            log.exception("target flow failed")
            channel.emit("__error__", {"error": str(e)})
            _mark_finished(run_id)
            raise

    fut = asyncio.create_task(_go())
    _pending[run_id] = RunEntry(
        kind="target", channel=channel, future=fut, started_at=time.time()
    )
    return {"run_id": run_id}


@router.get("/target/{run_id}/stream")
async def target_flow_stream(run_id: str):
    entry = _entry(run_id, "target")
    return EventSourceResponse(entry.channel.stream())


@router.get("/target/{run_id}/result", response_model=TargetResponse)
async def target_flow_result(run_id: str) -> TargetResponse:
    entry = _entry(run_id, "target")
    try:
        result: TargetResponse = await entry.future
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return result


@router.get("/target/{run_id}/status")
async def target_flow_status(run_id: str) -> dict[str, Any]:
    return _status_payload(run_id, "target")


# ---------- Status helper ----------


def _status_payload(
    run_id: str, kind: Literal["sender", "target"]
) -> dict[str, Any]:
    e = _pending.get(run_id)
    if e is None or e.kind != kind:
        return {"state": "unknown", "run_id": run_id, "kind": kind}
    state: RunState
    if e.future.done():
        state = "error" if e.future.exception() is not None else "done"
    else:
        state = "running"
    return {
        "state": state,
        "run_id": run_id,
        "kind": kind,
        "started_at": e.started_at,
        "finished_at": e.finished_at,
    }
