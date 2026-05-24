"""Graph nodes.

Each node is a function ``(state) -> partial_state``. Nodes are closures
over the LLM client, NLI validator, embedder, and external signal
provider, all of which are singletons per process.

We deliberately keep business logic in the existing modules
(``pipeline/``, ``synthesis/``) and treat each node as a thin orchestrator
that calls them. This is a refactor, not a rewrite.
"""
from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from ..config import settings
from ..db import dumps, tx
from ..pipeline import crawl, extract, planner, section_select, validate
from ..schemas import (
    AngleType,
    Email,
    ICP,
    Observation,
    PlannerDecision,
    PlannerInput,
    PlannerOutput,
    ValueProposition,
)
from ..services.embed import Embedder
from ..services.external import (
    ExternalSignalProvider,
    hits_to_sections,
)
from ..services.llm import LLMClient
from ..services.nli import NliValidator
from ..synthesis import (
    email_guard as guard_mod,
    overlap as overlap_mod,
    sender as sender_synth,
    strategy as strategy_synth,
    value_props_store,
    writer as writer_mod,
)
from .state import FlowState

log = logging.getLogger(__name__)


_TARGET_CORE_SIGNAL_KINDS = ["industry", "size_band", "trigger"]
_TARGET_OPTIONAL_SIGNAL_KINDS = ["hiring", "funding", "expansion", "leadership"]
_OPTIONAL_SIGNAL_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hiring": ("hiring", "recruit", "headcount", "talent", "workforce", "open role"),
    "funding": ("funding", "raise", "investment", "series ", "capital"),
    "expansion": ("expansion", "expand", "new market", "geographic", "office opening"),
    "leadership": ("leadership", "executive", "ceo", "cto", "cfo", "appoint"),
}


def _target_context_text(state: FlowState) -> str:
    parts: list[str] = []
    vp = state.get("sender_vp")
    if vp:
        parts.extend([vp.label, vp.customer, vp.pain, vp.outcome, vp.mechanism])
    persona = state.get("persona")
    if persona:
        parts.append(persona.role)
    icp = state.get("sender_icp")
    if icp:
        for field in (icp.target_industries, icp.likely_buyers, icp.common_triggers):
            parts.extend(field.values)
    return " ".join(p for p in parts if p).lower()


def _essential_target_signal_kinds(state: FlowState) -> list[str]:
    """Signal kinds the planner should treat as required for this run."""
    kinds = list(_TARGET_CORE_SIGNAL_KINDS)
    blob = _target_context_text(state)
    for kind in _TARGET_OPTIONAL_SIGNAL_KINDS:
        keywords = _OPTIONAL_SIGNAL_KEYWORDS.get(kind, ())
        if any(kw in blob for kw in keywords):
            kinds.append(kind)
    return kinds


# ---------- Helpers ----------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist_pages_and_sections(
    company_id: str, pages: list[crawl.PageResult], sections_by_url: dict[str, list[dict]]
) -> None:
    with tx() as conn:
        for p in pages:
            page_id = crawl.new_page_id()
            md_len = len(p.markdown or "")
            conn.execute(
                "INSERT OR IGNORE INTO pages (page_id, company_id, url, status_code, "
                "content_hash, cleaned_chars, fetched_at, source) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (page_id, company_id, p.url, p.status_code, p.content_hash,
                 md_len, _now(), "website"),
            )
            for s in sections_by_url.get(p.url, []):
                conn.execute(
                    "INSERT OR REPLACE INTO sections (section_id, company_id, page_id, "
                    "url, heading, text, char_start, char_end, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        s["section_id"], company_id, page_id, s["url"],
                        s.get("heading"), s["text"], s.get("char_start"),
                        s.get("char_end"), s.get("source", "website"),
                    ),
                )


def _persist_search_sections(company_id: str, sections: list[dict]) -> None:
    if not sections:
        return
    page_id = crawl.new_page_id()
    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO pages (page_id, company_id, url, status_code, "
            "content_hash, cleaned_chars, fetched_at, source) VALUES (?,?,?,?,?,?,?,?)",
            (page_id, company_id, "search://web", 200, "",
             sum(len(s["text"]) for s in sections), _now(), "web_search"),
        )
        for s in sections:
            conn.execute(
                "INSERT OR REPLACE INTO sections (section_id, company_id, page_id, url, "
                "heading, text, char_start, char_end, source) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    s["section_id"], company_id, page_id, s["url"],
                    s.get("heading"), s["text"], s.get("char_start"),
                    s.get("char_end"), "web_search",
                ),
            )


def _persist_observations(observations: list[Observation]) -> None:
    if not observations:
        return
    with tx() as conn:
        for o in observations:
            conn.execute(
                "INSERT OR REPLACE INTO observations (observation_id, company_id, "
                "section_id, kind, text, confidence, validation, validation_score) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    o.observation_id, o.company_id, o.section_id, o.kind, o.text,
                    o.confidence, o.validation.value if o.validation else None,
                    o.validation_score,
                ),
            )


def _kind_counts(observations: list[Observation]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for o in observations:
        counts[o.kind] = counts.get(o.kind, 0) + 1
    return counts


def _emit(state: FlowState, stage: str, detail: dict | None = None) -> None:
    state["progress"](stage, detail or {})


# ---------- Crawl + section nodes ----------


def make_crawl_node(*, fetch_more: bool = False) -> Callable[[FlowState], dict]:
    """Build a crawl node.

    If ``fetch_more`` is True the node crawls ONLY the URLs the Planner
    suggested in ``planner_output.suggested_internal_pages``. Otherwise it
    does a fresh deep crawl from ``homepage_url``.
    """

    async def node(state: FlowState) -> dict:
        company_id = state["company_id"]
        homepage = state["homepage_url"]
        tracker = state["tracker"]
        flow_task = state.get("task") or "sender"
        if flow_task == "target":
            crawl_pages = settings.target_crawl_max_pages
            crawl_depth = settings.target_crawl_max_depth
        else:
            crawl_pages = settings.crawl_max_pages
            crawl_depth = settings.crawl_max_depth
        usable_explicit: list[str] | None = None
        if fetch_more:
            planner_out = state.get("planner_output")
            if planner_out:
                usable_explicit = crawl.pick_fetch_more_urls(
                    homepage=homepage,
                    discovered=state.get("discovered_urls") or [],
                    crawled=state.get("crawled_urls") or [],
                    planner_suggestions=planner_out.suggested_internal_pages,
                    missing_fields=planner_out.missing_fields,
                    limit=settings.fetch_more_max_pages,
                )
                if not usable_explicit:
                    log.info(
                        "fetch_more: no uncrawled discovered URLs remain "
                        "(planner suggested %d invented paths)",
                        len(planner_out.suggested_internal_pages),
                    )

        with tracker.stage("crawl") as st:
            _emit(state, "discover", {
                "url": homepage,
                "explicit": bool(usable_explicit),
                "targets": len(usable_explicit or []),
            })
            out = await crawl.crawl_company(
                homepage,
                max_pages=settings.fetch_more_max_pages if fetch_more else crawl_pages,
                max_depth=crawl_depth,
                explicit_urls=usable_explicit,
            )
            st.detail["pages"] = len(out.pages)
            st.detail["failed"] = len(out.failed_urls)
            _emit(state, "discover_done", {"candidates": len(out.pages)})
            _emit(state, "fetch_done", {"fetched": len(out.pages)})

        # Section + persist
        with tracker.stage("section") as st:
            _emit(state, "section", {"count": len(out.pages)})
            sections_by_url: dict[str, list[dict]] = {}
            new_sections: dict[str, dict] = {}
            raw_chars = 0
            for p in out.pages:
                secs = crawl.section_page(p.url, p.markdown)
                sections_by_url[p.url] = secs
                for s in secs:
                    new_sections[s["section_id"]] = s
                raw_chars += len(p.markdown or "")
            st.detail["sections"] = len(new_sections)
            _emit(state, "section_done", {"sections": len(new_sections)})

        _persist_pages_and_sections(company_id, out.pages, sections_by_url)

        # Merge into the prior section index (fetch_more preserves the original sections).
        sections_by_id = dict(state.get("sections_by_id") or {})
        sections_by_id.update(new_sections)

        # Reflect crawl progress in MLflow metrics.
        tracker.metrics.pages_fetched += len(out.pages)
        tracker.metrics.sections_created = len(sections_by_id)
        tracker.metrics.raw_cleaned_chars = state.get("raw_cleaned_chars", 0) + raw_chars

        discovered_set = set(state.get("discovered_urls") or [])
        discovered_set.update(out.discovered_urls)
        crawled_set = set(state.get("crawled_urls") or [])
        for p in out.pages:
            crawled_set.add(p.url)

        return {
            "sections_by_id": sections_by_id,
            "pages_fetched": state.get("pages_fetched", 0) + len(out.pages),
            "raw_cleaned_chars": state.get("raw_cleaned_chars", 0) + raw_chars,
            "failed_sources": list(state.get("failed_sources") or []) + out.failed_urls,
            "discovered_urls": sorted(discovered_set),
            "crawled_urls": sorted(crawled_set),
            # One fetch_more attempt per run (success or not) so the planner
            # cannot loop on invented 404 paths.
            "fetch_more_done": state.get("fetch_more_done", False) or fetch_more,
        }

    return node


def _normalize(homepage: str, path: str) -> str:
    from urllib.parse import urljoin
    if path.startswith(("http://", "https://")):
        return path
    return urljoin(homepage, path)


# ---------- Extract / validate ----------


def make_extract_node(
    *, llm: LLMClient, nli: NliValidator, task: str
) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        company_id = state["company_id"]
        sections_by_id = state.get("sections_by_id") or {}

        # Only extract from sections we haven't already processed.
        already_section_ids = {o.section_id for o in (state.get("observations") or [])}
        new_sections = [
            s for sid, s in sections_by_id.items() if sid not in already_section_ids
        ]
        batch_size = settings.extract_batch_size
        concurrency = settings.extract_concurrency
        max_sections = (
            settings.target_max_sections_for_extraction if task == "target" else 0
        )
        total_before_cap = len(new_sections)
        if max_sections > 0 and len(new_sections) > max_sections:
            new_sections = section_select.select_sections_for_extraction(
                new_sections,
                max_sections=max_sections,
                sender_vp=state.get("sender_vp"),
                persona=state.get("persona"),
                sender_icp=state.get("sender_icp"),
            )
        with tracker.stage("extract") as st:
            _emit(
                state,
                "extract",
                {
                    "sections": len(new_sections),
                    "total_sections": total_before_cap,
                    "capped": total_before_cap > len(new_sections),
                },
            )

            # Overlap NLI validation with remaining LLM batches: while later
            # batches extract, earlier batches can validate on CPU.
            nli_lock = threading.Lock()
            validate_tasks: list[asyncio.Task[list[Observation]]] = []

            def _validate_batch_sync(batch_obs: list[Observation]) -> list[Observation]:
                with nli_lock:
                    validated, _ = validate.validate_observations(
                        batch_obs, sections_by_id, nli=nli
                    )
                return validated

            def _on_batch(batch_obs: list[Observation], done: int, total: int) -> None:
                _emit(
                    state,
                    "extract_progress",
                    {"done": done, "total": total, "observations": len(batch_obs)},
                )
                if batch_obs:
                    validate_tasks.append(
                        asyncio.create_task(
                            asyncio.to_thread(_validate_batch_sync, batch_obs)
                        )
                    )

            new_obs = await extract.extract_observations(
                new_sections,
                company_id=company_id,
                llm=llm,
                usage=usage,
                task="sender" if task == "sender" else "target",
                batch_size=batch_size,
                concurrency=concurrency,
                on_batch_observations=_on_batch,
            )

            if validate_tasks:
                batch_results = await asyncio.gather(*validate_tasks)
                validated_by_id = {
                    o.observation_id: o
                    for batch in batch_results
                    for o in batch
                }
                new_obs = [
                    validated_by_id.get(o.observation_id, o) for o in new_obs
                ]

            # Persist as soon as observations are extracted + inline-validated.
            # The downstream validate_node only runs NLI on the leftover
            # un-validated tail, so if we waited for it the (typical) case
            # where every observation was already validated here would silently
            # never write anything to the DB -- breaking later evidence lookups.
            _persist_observations(new_obs)

            st.detail["observations"] = len(new_obs)
            tracker.metrics.observations_extracted += len(new_obs)
            _emit(state, "extract_done", {"observations": len(new_obs)})

        return {
            "observations": (state.get("observations") or []) + new_obs,
        }

    return node


def make_validate_node(*, nli: NliValidator) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        sections_by_id = state.get("sections_by_id") or {}
        observations = state.get("observations") or []

        # Only re-validate observations that haven't been touched yet.
        to_validate = [o for o in observations if o.validation is None]
        already = [o for o in observations if o.validation is not None]

        with tracker.stage("validate") as st:
            # Up-front forecast so the UI shows a target for the progress bar
            # before the first NLI chunk completes.
            chunks_forecast = (
                math.ceil(len(to_validate) / 32) if to_validate else 0
            )
            _emit(
                state,
                "validate",
                {
                    "observations": len(to_validate),
                    "chunks": chunks_forecast,
                },
            )

            # NLI is sync CPU work; running it inline blocks the asyncio loop
            # and keeps SSE events from flushing. Push it onto a worker
            # thread. ProgressChannel.emit is already thread-safe via
            # asyncio.run_coroutine_threadsafe, so the callback can fire
            # directly from the worker thread.
            def _on_chunk(done: int, total: int) -> None:
                _emit(state, "validate_progress", {"done": done, "total": total})

            if to_validate:
                validated, _counts = await asyncio.to_thread(
                    validate.validate_observations,
                    to_validate,
                    sections_by_id,
                    nli=nli,
                    on_chunk_done=_on_chunk,
                )
            else:
                validated = []

            merged = already + validated
            tallies = validate.validation_tallies(merged)
            # Inline validation during extract marks observations before this
            # node runs; tally the full merged set so persisted metrics match.
            tracker.metrics.observations_validated = tallies["entailed"]
            tracker.metrics.observations_rejected = tallies["contradicted"]
            st.detail.update(tallies)
            _emit(state, "validate_done", tallies)

        # Persist the full set (already + newly validated). INSERT OR REPLACE
        # is idempotent, so re-persisting `already` is cheap and guarantees
        # every validated observation is in the DB for later evidence lookups
        # even if `to_validate` was empty (the common case when the extract
        # node already inline-validated everything).
        _persist_observations(merged)

        # Update evidence-chars metric.
        evidence_chars = sum(
            len(sections_by_id[o.section_id]["text"])
            for o in merged
            if o.section_id in sections_by_id and o.validation is not None
        )
        tracker.metrics.evidence_chars_used = evidence_chars

        usable = validate.filter_for_synthesis(merged)
        _emit(state, "filter_done", {"usable": len(usable)})
        return {"observations": merged}

    return node


# ---------- Sender synthesis ----------


def make_sender_synthesize_node(*, llm: LLMClient) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        observations = state.get("observations") or []
        usable = validate.filter_for_synthesis(observations)

        _emit(
            state,
            "synthesis",
            {
                "message": f"Evaluating {len(usable)} extracted observations…",
                "step": "evaluate",
                "usable": len(usable),
            },
        )

        _emit(
            state,
            "synthesis_progress",
            {"message": "Synthesizing ICP…", "step": "icp"},
        )
        _emit(state, "icp", {"message": "Synthesizing ICP…"})

        _emit(
            state,
            "synthesis_progress",
            {"message": "Synthesizing value proposition(s)…", "step": "vp"},
        )
        _emit(state, "vp", {"message": "Synthesizing value proposition(s)…"})

        async def _timed_icp() -> tuple[ICP, float]:
            t0 = time.perf_counter()
            result = await asyncio.to_thread(
                sender_synth.synthesize_icp, usable, llm=llm, usage=usage
            )
            return result, (time.perf_counter() - t0) * 1000.0

        async def _timed_vps() -> tuple[list[ValueProposition], float]:
            t0 = time.perf_counter()
            result = await asyncio.to_thread(
                sender_synth.synthesize_value_propositions,
                usable,
                llm=llm,
                usage=usage,
            )
            return result, (time.perf_counter() - t0) * 1000.0

        (icp, icp_ms), (value_propositions, vp_ms) = await asyncio.gather(
            _timed_icp(), _timed_vps()
        )

        _emit(
            state,
            "synthesis_progress",
            {
                "message": "Preparing recommendation summary…",
                "step": "summary",
                "value_propositions": len(value_propositions),
            },
        )

        vp = value_props_store.primary_value_proposition(value_propositions)

        tracker.add_stage(
            "icp",
            icp_ms,
            {
                "fields": 5,
                "industries": len(icp.target_industries.values),
                "buyers": len(icp.likely_buyers.values),
            },
        )
        tracker.add_stage(
            "vp",
            vp_ms,
            {
                "count": len(value_propositions),
                "primary": vp.label or "Primary",
            },
        )

        _emit(
            state,
            "synthesis_progress",
            {"message": "Finalizing synthesis…", "step": "finalize"},
        )
        _emit(state, "icp_done", {"fields": 5})
        _emit(
            state,
            "vp_done",
            {"count": len(value_propositions), "primary": vp.label or "Primary"},
        )

        # Persist
        vp_payload = value_props_store.serialize_value_props(value_propositions)
        with tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO icps (company_id, payload) VALUES (?, ?)",
                (state["company_id"], dumps(icp.model_dump(mode="json"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO value_props (company_id, payload) VALUES (?, ?)",
                (state["company_id"], dumps(vp_payload)),
            )

        tracker.log_artifact_json("icp.json", icp.model_dump(mode="json"))
        tracker.log_artifact_json("value_propositions.json", vp_payload)
        tracker.log_artifact_json(
            "observations.json",
            [o.model_dump(mode="json") for o in observations],
        )
        return {
            "icp": icp,
            "value_proposition": vp,
            "value_propositions": value_propositions,
        }

    return node


# ---------- Planner ----------


def make_planner_node(*, llm: LLMClient, task: str) -> Callable[[FlowState], dict]:
    """Single agentic decision point. Bounded -- max 2 invocations per flow."""

    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        attempts = state.get("planner_attempts", 0)
        observations = state.get("observations") or []
        usable = validate.filter_for_synthesis(observations)

        # Hard cap on planner invocations.
        if attempts >= 2:
            forced = PlannerOutput(
                decision=PlannerDecision.CONTINUE,
                reason="planner_attempts_exhausted",
            )
            tracker.metrics.planner_decisions.append(
                {"task": task, "decision": forced.decision.value, "reason": forced.reason}
            )
            _emit(state, "planner_done",
                  {"decision": forced.decision.value, "reason": forced.reason})
            return {"planner_output": forced, "planner_attempts": attempts + 1}

        discovered = state.get("discovered_urls") or []
        crawled = set(state.get("crawled_urls") or [])
        uncrawled = [u for u in discovered if u not in crawled][:40]

        if task == "sender_icp":
            # Synthesize a draft ICP just to compute gaps.
            draft = sender_synth.synthesize_icp(usable, llm=llm, usage=usage)
            missing, counts, confs = sender_synth.compute_field_gaps(draft)
            # Empty ICP fields with plenty of observations usually means the
            # evidence is there but synthesis didn't map it — not a crawl gap.
            if len(usable) >= 30 and (state.get("pages_fetched") or 0) >= 5:
                if not uncrawled:
                    missing = []
        else:
            essential = _essential_target_signal_kinds(state)
            missing = [
                k for k in essential if _kind_counts(usable).get(k, 0) == 0
            ]
            counts = _kind_counts(usable)
            confs = {}

        with tracker.stage("planner") as st:
            _emit(state, "planner", {
                "missing_fields": missing,
                "uncrawled_urls": len(uncrawled),
            })
            out = planner.run_planner(
                PlannerInput(
                    task=task,
                    observations=usable,
                    missing_fields=missing,
                    evidence_counts=counts,
                    field_confidence=confs,
                    failed_sources=state.get("failed_sources") or [],
                    uncrawled_discovered_urls=uncrawled,
                ),
                llm=llm,
                usage=usage,
            )
            st.detail.update(
                {"decision": out.decision.value, "reason": out.reason}
            )
            tracker.metrics.planner_decisions.append(
                {
                    "task": task,
                    "decision": out.decision.value,
                    "reason": out.reason,
                    "missing_fields": out.missing_fields,
                    "suggested_internal_pages": out.suggested_internal_pages,
                    "suggested_queries": out.suggested_queries,
                }
            )
            _emit(state, "planner_done",
                  {"decision": out.decision.value, "reason": out.reason})

        return {"planner_output": out, "planner_attempts": attempts + 1}

    return node


def route_planner_sender(state: FlowState) -> str:
    """Sender flow router. Web search is *not* allowed in the sender flow."""
    p = state.get("planner_output")
    if p is None:
        return "continue"
    d = p.decision
    if (
        d == PlannerDecision.FETCH_MORE
        and not state.get("fetch_more_done")
        and _has_fetch_more_targets(state, p)
    ):
        return "fetch_more"
    if d == PlannerDecision.STOP:
        return "stop"
    # web_search and proceed_low_confidence both proceed to synthesis for sender.
    return "continue"


def _has_fetch_more_targets(state: FlowState, p: PlannerOutput) -> bool:
    """True when fetch_more can actually retrieve new real pages."""
    if state.get("fetch_more_done"):
        return False
    discovered = set(state.get("discovered_urls") or [])
    crawled = set(state.get("crawled_urls") or [])
    uncrawled = discovered - crawled
    if uncrawled:
        return True
    # Allow if planner picked URLs that exist in discovered (post-filter).
    return any(u in discovered for u in p.suggested_internal_pages)


def route_planner_target(state: FlowState) -> str:
    p = state.get("planner_output")
    if p is None:
        return "continue"
    d = p.decision
    if d == PlannerDecision.WEB_SEARCH and not state.get("web_search_done") and p.suggested_queries:
        return "web_search"
    if d == PlannerDecision.STOP:
        return "stop"
    return "continue"


# ---------- External enrichment (target only) ----------


def make_external_enrich_node(
    *, llm: LLMClient, provider: ExternalSignalProvider, nli: NliValidator,
) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        company_id = state["company_id"]
        planner_out = state.get("planner_output")
        queries = planner_out.suggested_queries if planner_out else []

        with tracker.stage("web_search") as st:
            _emit(state, "web_search", {"queries": queries, "provider": provider.name})
            search_queries = queries[:3]

            async def _search_one(q: str) -> list[dict]:
                hits = await asyncio.to_thread(
                    provider.search, q, settings.external_signal_max_results
                )
                return hits_to_sections(hits, company_id)

            search_results = await asyncio.gather(
                *[_search_one(q) for q in search_queries]
            )
            ws_sections: list[dict] = []
            for batch in search_results:
                ws_sections.extend(batch)
            st.detail["sections"] = len(ws_sections)
            _emit(state, "web_search_done",
                  {"sections": len(ws_sections), "provider": provider.name})

        if not ws_sections:
            return {"web_search_done": True}

        _persist_search_sections(company_id, ws_sections)
        sections_by_id = dict(state.get("sections_by_id") or {})
        for s in ws_sections:
            sections_by_id[s["section_id"]] = s

        _emit(state, "extract_ws", {"sections": len(ws_sections)})

        nli_lock = threading.Lock()
        validate_tasks: list[asyncio.Task[list[Observation]]] = []

        def _validate_ws_batch(batch_obs: list[Observation]) -> list[Observation]:
            with nli_lock:
                validated, _ = validate.validate_observations(
                    batch_obs, sections_by_id, nli=nli
                )
            return validated

        def _on_ws_batch(batch_obs: list[Observation], done: int, total: int) -> None:
            _emit(
                state,
                "extract_progress",
                {
                    "done": done,
                    "total": total,
                    "scope": "web_search",
                    "observations": len(batch_obs),
                },
            )
            if batch_obs:
                validate_tasks.append(
                    asyncio.create_task(
                        asyncio.to_thread(_validate_ws_batch, batch_obs)
                    )
                )

        new_obs = await extract.extract_observations(
            ws_sections,
            company_id=company_id,
            llm=llm,
            usage=usage,
            task="target",
            on_batch_observations=_on_ws_batch,
        )
        if validate_tasks:
            batch_results = await asyncio.gather(*validate_tasks)
            validated_by_id = {
                o.observation_id: o
                for batch in batch_results
                for o in batch
            }
            new_obs = [validated_by_id.get(o.observation_id, o) for o in new_obs]
        remaining = [o for o in new_obs if o.validation is None]
        if remaining:
            extra_validated, _counts = validate.validate_observations(
                remaining, sections_by_id, nli=nli
            )
            extra_by_id = {o.observation_id: o for o in extra_validated}
            new_obs = [extra_by_id.get(o.observation_id, o) for o in new_obs]
        _persist_observations(new_obs)
        all_obs = (state.get("observations") or []) + new_obs
        tallies = validate.validation_tallies(all_obs)
        tracker.metrics.observations_extracted += len(new_obs)
        tracker.metrics.observations_validated = tallies["entailed"]
        tracker.metrics.observations_rejected = tallies["contradicted"]

        return {
            "sections_by_id": sections_by_id,
            "observations": all_obs,
            "web_search_done": True,
        }

    return node


# ---------- Strategy + writer + claims + verify + repair ----------


def make_strategy_node(*, llm: LLMClient) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        observations = state.get("observations") or []
        usable = validate.filter_for_synthesis(observations)
        sender_icp = state.get("sender_icp")
        sender_vps = state.get("sender_vps") or []
        sender_vp = state.get("sender_vp")
        persona = state.get("persona")
        assert sender_icp and sender_vp and persona, "target flow requires sender artifacts + persona"
        if not sender_vps:
            sender_vps = [sender_vp]

        with tracker.stage("strategy"):
            _emit(state, "strategy", {"observations": len(usable)})
            strategy = strategy_synth.synthesize_strategy(
                sender_icp=sender_icp,
                sender_vps=sender_vps,
                target_observations=usable,
                persona=persona,
                llm=llm,
                usage=usage,
            )
            _emit(
                state,
                "strategy_done",
                {
                    "fit_level": strategy.fit_assessment.level.value,
                    "contact_decision": strategy.strategy.contact_decision.value,
                    "angles": len(strategy.strategy.angles),
                    "selected_vp_id": strategy.selected_value_proposition_id,
                    "selected_vp_label": strategy.selected_value_proposition_label,
                },
            )
            log.info(
                "strategy_done: vp_id=%r label=%r fit=%s decision=%s",
                strategy.selected_value_proposition_id,
                strategy.selected_value_proposition_label,
                strategy.fit_assessment.level.value,
                strategy.strategy.contact_decision.value,
            )
        tracker.log_artifact_json("strategy.json", strategy.model_dump(mode="json"))
        return {"strategy": strategy}

    return node


def make_writer_node(*, llm: LLMClient) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        observations = state.get("observations") or []
        usable = validate.filter_for_synthesis(observations)
        sender_icp = state["sender_icp"]
        sender_vps = state.get("sender_vps") or []
        sender_vp = state["sender_vp"]
        strategy = state["strategy"]
        persona = state["persona"]
        assert sender_icp and sender_vp and strategy and persona

        # Safeguard: emails MUST be driven by the VP the strategy selected.
        # If the strategy did not select one (only legal when the sender has a
        # single VP), keep the primary. Otherwise resolve explicitly.
        if sender_vps:
            if not strategy.selected_value_proposition_id:
                log.warning(
                    "writer: strategy has no selected_value_proposition_id; "
                    "using primary VP (%r) as fallback",
                    sender_vp.id,
                )
            else:
                resolved = value_props_store.resolve_value_proposition(
                    sender_vps, strategy.selected_value_proposition_id
                )
                if resolved.id != strategy.selected_value_proposition_id:
                    log.warning(
                        "writer: selected vp_id=%r not found in sender_vps; "
                        "falling back to primary",
                        strategy.selected_value_proposition_id,
                    )
                sender_vp = resolved
                log.info(
                    "writer: using vp_id=%r label=%r for emails",
                    sender_vp.id,
                    sender_vp.label,
                )

        sender_observations: list[Observation] = []
        sender_company_id = state.get("sender_company_id") or ""
        if sender_company_id:
            try:
                sender_observations = guard_mod.load_observations_for_company(
                    sender_company_id
                )
            except Exception:  # noqa: BLE001
                log.exception(
                    "writer: failed to load sender observations for %s",
                    sender_company_id,
                )

        target_company_name = _company_name_from_url(state.get("homepage_url") or "")

        with tracker.stage("write_emails"):
            _emit(state, "write_emails", {"angles": ["pain_led", "trigger_led"]})
            emails = writer_mod.write_emails(
                sender_vp=sender_vp,
                sender_icp=sender_icp,
                target_observations=usable,
                sender_observations=sender_observations,
                target_company_name=target_company_name,
                strategy=strategy,
                persona=persona,
                llm=llm,
                usage=usage,
            )
            _emit(
                state,
                "write_emails_done",
                {"emails": len(emails)},
            )
        # Persist the resolved VP into state so the email_guard verifier
        # checks each statement against the exact VP that wrote the email.
        return {"emails": emails, "sender_vp": sender_vp}

    return node


def _company_name_from_url(url: str) -> str:
    if not url:
        return ""
    try:
        from urllib.parse import urlparse

        host = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return ""
    label = host.split(".")[0]
    return label.capitalize() if label else ""


def _guard_context_from_state(state: FlowState) -> guard_mod.GuardContext:
    sender_id = state.get("sender_company_id") or ""
    sender_obs: list[Observation] = []
    if sender_id:
        sender_obs = guard_mod.load_observations_for_company(sender_id)
    sender_vp = state.get("sender_vp")
    sender_evidence: list[Observation] = []
    if sender_vp:
        sender_evidence = guard_mod.select_sender_evidence_for_verifier(
            sender_vp, sender_obs
        )
    return guard_mod.GuardContext(
        target_observations=state.get("observations") or [],
        sender_observations=sender_obs,
        sender_evidence=sender_evidence,
        sender_icp=state.get("sender_icp"),
        sender_vp=sender_vp,
        strategy=state.get("strategy"),
        persona=state.get("persona"),
        target_company_name=_company_name_from_url(
            state.get("homepage_url") or ""
        ),
    )


def _apply_safety_tallies(metrics, tallies: dict) -> None:
    """Copy the guardrail aggregates from ``accumulate_safety_metrics`` into ``RunMetrics``."""
    metrics.declared_claims_count = int(tallies["declared_claims_count"])
    metrics.email_claims_count = int(tallies["email_claims_count"])
    metrics.unsupported_claims_count = int(tallies["unsupported_claims_count"])
    avg_conf = tallies["safety_confidence_avg"]
    metrics.safety_confidence_avg = (
        float(avg_conf) if avg_conf is not None else None
    )
    metrics.email_regenerated = bool(tallies["email_regenerated"])
    metrics.regeneration_count = int(tallies["regeneration_count"])
    metrics.emails_safe_count = int(tallies["emails_safe_count"])
    metrics.emails_total = int(tallies["emails_total"])
    metrics.final_email_safe = bool(tallies["final_email_safe"])
    metrics.verification_ok = bool(tallies["verification_ok"])


def make_email_guard_node(
    *, llm: LLMClient, nli: NliValidator, embedder: Embedder,
) -> Callable[[FlowState], dict]:
    """Independent post-generation verification on final email bodies."""
    # ``nli`` and ``embedder`` are kept for graph-wiring compatibility; the
    # LLM-as-judge verifier does not use them.
    del nli, embedder

    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        emails = state.get("emails") or []
        ctx = _guard_context_from_state(state)

        with tracker.stage("email_guard") as st:
            _emit(state, "email_guard", {"emails": len(emails)})
            guarded: list[Email] = []
            for i, email in enumerate(emails, start=1):
                guarded.append(
                    guard_mod.guard_email(
                        email, ctx=ctx, llm=llm, usage=usage
                    )
                )
                _emit(
                    state,
                    "email_guard_progress",
                    {"emails_done": i, "emails_total": len(emails)},
                )

            tallies = guard_mod.accumulate_safety_metrics(guarded)
            st.detail.update(tallies)
            _emit(
                state,
                "email_guard_done",
                {
                    "declared": tallies["declared_claims_count"],
                    "claims": tallies["email_claims_count"],
                    "unsupported": tallies["unsupported_claims_count"],
                    "safe_emails": tallies["emails_safe_count"],
                    "total_emails": tallies["emails_total"],
                    "confidence_avg": tallies["safety_confidence_avg"],
                    "unsafe": not tallies["final_email_safe"],
                    "verifier_ok": tallies["verification_ok"],
                },
            )
            _apply_safety_tallies(tracker.metrics, tallies)

        return {"emails": guarded}

    return node


# ---------- Analytics + persist (target) ----------


def make_analytics_node(
    *, embedder: Embedder, llm: LLMClient, nli: NliValidator,
) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        observations = state.get("observations") or []
        emails = list(state.get("emails") or [])
        guard_ctx = _guard_context_from_state(state)

        # Angle overlap + optional divergence repair.
        if len(emails) == 2:
            with tracker.stage("angle_overlap") as st:
                _emit(state, "angle_overlap", {"emails": 2})
                sim = overlap_mod.measure_overlap(emails[0], emails[1], embedder)
                tracker.metrics.angle_overlap = round(sim, 3)
                st.detail["overlap"] = sim
                if sim > overlap_mod.ANGLE_OVERLAP_MAX:
                    _emit(state, "angle_overlap_repair", {"overlap": sim})
                    pain_idx = next(
                        (i for i, e in enumerate(emails) if e.angle == AngleType.PAIN_LED), None
                    )
                    trig_idx = next(
                        (i for i, e in enumerate(emails) if e.angle == AngleType.TRIGGER_LED), None
                    )
                    if pain_idx is not None and trig_idx is not None:
                        usable = validate.filter_for_synthesis(observations)
                        repaired = overlap_mod.diverge_pain_led(
                            pain=emails[pain_idx],
                            trigger=emails[trig_idx],
                            target_observations=usable,
                            llm=llm,
                            usage=usage,
                        )
                        repaired = guard_mod.guard_email(
                            repaired,
                            ctx=guard_ctx,
                            llm=llm,
                            usage=usage,
                        )
                        emails[pain_idx] = repaired
                        tallies = guard_mod.accumulate_safety_metrics(emails)
                        _apply_safety_tallies(tracker.metrics, tallies)
                        sim2 = overlap_mod.measure_overlap(
                            emails[0], emails[1], embedder
                        )
                        tracker.metrics.angle_overlap = round(sim2, 3)
                        st.detail["overlap_after_repair"] = sim2
                        _emit(
                            state,
                            "angle_overlap_done",
                            {"overlap": sim2, "repaired": True},
                        )
                    else:
                        _emit(
                            state,
                            "angle_overlap_done",
                            {"overlap": sim, "repaired": False},
                        )
                else:
                    _emit(
                        state,
                        "angle_overlap_done",
                        {"overlap": sim, "repaired": False},
                    )

        tallies = guard_mod.accumulate_safety_metrics(emails)
        _apply_safety_tallies(tracker.metrics, tallies)

        # Persist scoped to (target_company_id, persona_id). Re-running for
        # the same persona overwrites; running for a different persona keeps
        # both rows so the UI can show emails grouped by persona.
        target_company_id = state["company_id"]
        sender_company_id = state.get("sender_company_id") or ""
        persona_id = state.get("persona_id") or ""
        persona = state["persona"]
        strategy = state["strategy"]
        assert persona is not None and strategy is not None
        with tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO strategies "
                "(target_company_id, persona_id, sender_company_id, persona, payload) "
                "VALUES (?,?,?,?,?)",
                (
                    target_company_id, persona_id, sender_company_id,
                    dumps(persona.model_dump(mode="json")),
                    dumps(strategy.model_dump(mode="json")),
                ),
            )
            conn.execute(
                "DELETE FROM emails WHERE target_company_id = ? AND persona_id = ?",
                (target_company_id, persona_id),
            )
            for e in emails:
                conn.execute(
                    "INSERT INTO emails "
                    "(email_id, target_company_id, persona_id, angle, subject, body, payload) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        e.email_id, target_company_id, persona_id, e.angle.value,
                        e.subject, e.body, dumps(e.model_dump(mode="json")),
                    ),
                )

        tracker.log_artifact_json(
            "emails.json", [e.model_dump(mode="json") for e in emails]
        )
        return {"emails": emails}

    return node
