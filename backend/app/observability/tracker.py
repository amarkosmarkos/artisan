"""MLflow run tracker.

Every sender or target run opens an MLflow run and logs:
- parameters (URLs, persona, model name)
- metrics (latency, tokens, cost, pages, sections, observations, claim rates, angle overlap, compression)
- artifacts (ICP, VP, strategy, emails, claim map JSON)
- a stage timeline as a single artifact

MLflow's file tracking backend keeps everything in ``data/mlruns/`` so the
project is self-contained and no external server is required.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

import mlflow
from mlflow.tracking import MlflowClient

from ..config import settings
from ..schemas import RunMetrics

log = logging.getLogger(__name__)


_initialized = False
_experiment_id: str | None = None


def _ensure_init() -> None:
    """Idempotently set the MLflow tracking URI and resolve the experiment id.

    We intentionally do **not** touch the process-global "active run" state
    (``mlflow.start_run`` / ``mlflow.active_run``). Instead every
    ``RunTracker`` uses :class:`MlflowClient` directly with an explicit
    ``run_id`` so concurrent flows cannot collide, and a crashed flow can't
    leak its run into the next one.
    """
    global _initialized, _experiment_id
    if _initialized:
        return
    mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    client = MlflowClient()
    exp = client.get_experiment_by_name(settings.mlflow_experiment)
    if exp is None:
        _experiment_id = client.create_experiment(settings.mlflow_experiment)
    else:
        _experiment_id = exp.experiment_id
    _initialized = True


@dataclass
class StageEvent:
    name: str
    started_at: float
    duration_ms: float
    detail: dict[str, Any] = field(default_factory=dict)


class RunTracker:
    """Context manager that wraps an isolated MLflow run + metrics object.

    Uses :class:`MlflowClient` with an explicit ``run_id`` so we never touch
    MLflow's process-global active-run state. This makes the tracker:

    - safe under concurrent flows (each gets its own run_id),
    - crash-tolerant (a crashed flow can't leak into the next one),
    - fail-soft (if MLflow itself errors we keep the flow running and just
      lose tracking, instead of taking the user's request down with us).
    """

    def __init__(self, kind: str, *, run_name: str | None = None, params: dict | None = None):
        self.kind = kind
        self.run_name = run_name or f"{kind}-{int(time.time())}"
        self.params = params or {}
        self.metrics = RunMetrics()
        self._stages: list[StageEvent] = []
        self._t0: float = 0.0
        self._client: MlflowClient | None = None
        self._run_id: str | None = None

    def __enter__(self) -> "RunTracker":
        self._t0 = time.time()
        try:
            _ensure_init()
            self._client = MlflowClient()
            assert _experiment_id is not None
            run = self._client.create_run(
                experiment_id=_experiment_id,
                run_name=self.run_name,
            )
            self._run_id = run.info.run_id
            for k, v in self.params.items():
                try:
                    self._client.log_param(self._run_id, k, str(v)[:250])
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            # Tracking is best-effort: never let MLflow take down a flow.
            log.warning("mlflow run start failed; continuing without tracking: %s", e)
            self._client = None
            self._run_id = None
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.metrics.latency_ms = (time.time() - self._t0) * 1000.0
        if self._client is None or self._run_id is None:
            return
        try:
            self._log_metrics()
        except Exception:  # noqa: BLE001
            log.debug("metrics log failed", exc_info=True)
        try:
            self._client.set_terminated(
                self._run_id,
                status="FINISHED" if exc is None else "FAILED",
            )
        except Exception:  # noqa: BLE001
            pass

    # ----- public helpers -----

    def stage(self, name: str):
        return _StageCtx(self, name)

    def log_artifact_json(self, name: str, obj: Any) -> None:
        if self._client is None or self._run_id is None:
            return
        try:
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, name)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(_to_jsonable(obj), f, indent=2)
                self._client.log_artifact(self._run_id, path)
        except Exception as e:  # noqa: BLE001
            log.debug("mlflow log_artifact failed for %s: %s", name, e)

    def add_stage(self, name: str, duration_ms: float, detail: dict[str, Any]) -> None:
        ev = StageEvent(name=name, started_at=time.time(), duration_ms=duration_ms, detail=detail)
        self._stages.append(ev)
        self.metrics.stages.append(
            {
                "name": ev.name,
                "duration_ms": round(ev.duration_ms, 2),
                "detail": _to_jsonable(detail),
            }
        )

    # ----- internals -----

    def _log_metrics(self) -> None:
        m = self.metrics
        # Computed metrics
        if m.observations_extracted > 0:
            m.observation_validation_rate = round(
                m.observations_validated / m.observations_extracted, 3
            )
        if m.claims_total > 0:
            m.claim_support_rate = round(m.claims_supported / m.claims_total, 3)
            m.unsupported_claim_rate = round(m.claims_unsupported / m.claims_total, 3)
        if m.raw_cleaned_chars > 0 and m.evidence_chars_used > 0:
            m.compression_ratio = round(
                m.raw_cleaned_chars / m.evidence_chars_used, 3
            )

        assert self._client is not None and self._run_id is not None

        scalars = {
            "latency_ms": m.latency_ms,
            "tokens_in": m.tokens_in,
            "tokens_out": m.tokens_out,
            "cost_usd": m.cost_usd,
            "pages_fetched": m.pages_fetched,
            "sections_created": m.sections_created,
            "observations_extracted": m.observations_extracted,
            "observations_validated": m.observations_validated,
            "observations_rejected": m.observations_rejected,
            "compression_ratio": m.compression_ratio,
            "raw_cleaned_chars": m.raw_cleaned_chars,
            "evidence_chars_used": m.evidence_chars_used,
            "claims_total": m.claims_total,
            "claims_supported": m.claims_supported,
            "claims_unsupported": m.claims_unsupported,
            "claims_contradicted": m.claims_contradicted,
            "claims_repaired": m.claims_repaired,
            "angle_overlap": m.angle_overlap if m.angle_overlap is not None else -1.0,
            "claim_support_rate": m.claim_support_rate or 0.0,
            "unsupported_claim_rate": m.unsupported_claim_rate or 0.0,
            "observation_validation_rate": m.observation_validation_rate or 0.0,
        }
        for k, v in scalars.items():
            try:
                self._client.log_metric(self._run_id, k, float(v))
            except Exception:  # noqa: BLE001
                pass

        # Stage + planner timelines as JSON artifacts.
        try:
            with tempfile.TemporaryDirectory() as td:
                for fname, payload in (
                    ("stages.json", {"stages": m.stages}),
                    ("planner_decisions.json", {"planner_decisions": m.planner_decisions}),
                ):
                    path = os.path.join(td, fname)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(payload, f, indent=2)
                    self._client.log_artifact(self._run_id, path)
        except Exception:  # noqa: BLE001
            pass


class _StageCtx:
    def __init__(self, tracker: RunTracker, name: str) -> None:
        self.tracker = tracker
        self.name = name
        self._start = 0.0
        self.detail: dict[str, Any] = {}

    def __enter__(self) -> "_StageCtx":
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        dur = (time.time() - self._start) * 1000.0
        if exc is not None:
            self.detail["error"] = str(exc)
        self.tracker.add_stage(self.name, dur, self.detail)


def _to_jsonable(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    try:
        json.dumps(obj)
        return obj
    except (TypeError, ValueError):
        return str(obj)
