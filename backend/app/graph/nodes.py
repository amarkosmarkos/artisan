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
from datetime import datetime, timezone
from typing import Callable

from ..config import settings
from ..db import dumps, tx
from ..pipeline import crawl, extract, planner, validate
from ..schemas import (
    AngleType,
    ClaimStatus,
    Email,
    Observation,
    PlannerDecision,
    PlannerInput,
    PlannerOutput,
)
from ..services.embed import Embedder
from ..services.external import (
    ExternalSignalProvider,
    hits_to_sections,
)
from ..services.llm import LLMClient
from ..services.nli import NliValidator
from ..synthesis import (
    claim_extract,
    overlap as overlap_mod,
    sender as sender_synth,
    strategy as strategy_synth,
    verify as verify_mod,
    writer as writer_mod,
)
from .state import FlowState

log = logging.getLogger(__name__)


_TARGET_SIGNAL_KINDS = [
    "industry", "size_band", "hiring", "funding", "expansion", "leadership", "trigger",
]


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
                    limit=settings.crawl_max_pages,
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
                max_pages=settings.crawl_max_pages,
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


def make_extract_node(*, llm: LLMClient, task: str) -> Callable[[FlowState], dict]:
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
        with tracker.stage("extract") as st:
            _emit(state, "extract", {"sections": len(new_sections)})

            def _on_batch(done: int, total: int) -> None:
                _emit(state, "extract_progress", {"done": done, "total": total})

            new_obs = await extract.extract_observations(
                new_sections,
                company_id=company_id,
                llm=llm,
                usage=usage,
                task="sender" if task == "sender" else "target",
                on_batch_done=_on_batch,
            )
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

            validated, counts = await asyncio.to_thread(
                validate.validate_observations,
                to_validate,
                sections_by_id,
                nli=nli,
                on_chunk_done=_on_chunk,
            )
            tracker.metrics.observations_validated += counts.get("entailed", 0)
            tracker.metrics.observations_rejected += counts.get("contradicted", 0)
            st.detail.update(counts)
            _emit(state, "validate_done", counts)

        _persist_observations(validated)
        merged = already + validated

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

        with tracker.stage("synthesize_icp"):
            _emit(state, "icp", {})
            icp = sender_synth.synthesize_icp(usable, llm=llm, usage=usage)

        with tracker.stage("synthesize_vp"):
            _emit(state, "vp", {})
            vp = sender_synth.synthesize_value_proposition(
                usable, llm=llm, usage=usage
            )

        # Persist
        with tx() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO icps (company_id, payload) VALUES (?, ?)",
                (state["company_id"], dumps(icp.model_dump(mode="json"))),
            )
            conn.execute(
                "INSERT OR REPLACE INTO value_props (company_id, payload) VALUES (?, ?)",
                (state["company_id"], dumps(vp.model_dump(mode="json"))),
            )

        tracker.log_artifact_json("icp.json", icp.model_dump(mode="json"))
        tracker.log_artifact_json("value_proposition.json", vp.model_dump(mode="json"))
        tracker.log_artifact_json(
            "observations.json",
            [o.model_dump(mode="json") for o in observations],
        )
        return {"icp": icp, "value_proposition": vp}

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
            missing = [
                k for k in _TARGET_SIGNAL_KINDS if _kind_counts(usable).get(k, 0) == 0
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
    if (
        d == PlannerDecision.FETCH_MORE
        and not state.get("fetch_more_done")
        and _has_fetch_more_targets(state, p)
    ):
        return "fetch_more"
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
            ws_sections: list[dict] = []
            for q in queries[:3]:
                hits = provider.search(q, settings.external_signal_max_results)
                ws_sections.extend(hits_to_sections(hits, company_id))
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

        def _on_ws_batch(done: int, total: int) -> None:
            _emit(state, "extract_progress", {"done": done, "total": total, "scope": "web_search"})

        new_obs = await extract.extract_observations(
            ws_sections,
            company_id=company_id,
            llm=llm,
            usage=usage,
            task="target",
            on_batch_done=_on_ws_batch,
        )
        validated, counts = validate.validate_observations(
            new_obs, sections_by_id, nli=nli
        )
        _persist_observations(validated)
        tracker.metrics.observations_extracted += len(new_obs)
        tracker.metrics.observations_validated += counts.get("entailed", 0)
        tracker.metrics.observations_rejected += counts.get("contradicted", 0)

        return {
            "sections_by_id": sections_by_id,
            "observations": (state.get("observations") or []) + validated,
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
        sender_vp = state.get("sender_vp")
        persona = state.get("persona")
        assert sender_icp and sender_vp and persona, "target flow requires sender artifacts + persona"

        with tracker.stage("strategy"):
            _emit(state, "strategy", {"observations": len(usable)})
            strategy = strategy_synth.synthesize_strategy(
                sender_icp=sender_icp,
                sender_vp=sender_vp,
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
                },
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
        sender_vp = state["sender_vp"]
        strategy = state["strategy"]
        persona = state["persona"]
        assert sender_icp and sender_vp and strategy and persona

        with tracker.stage("write_emails"):
            _emit(state, "write_emails", {"angles": ["pain_led", "trigger_led"]})
            emails = writer_mod.write_emails(
                sender_vp=sender_vp,
                sender_icp=sender_icp,
                target_observations=usable,
                strategy=strategy,
                persona=persona,
                llm=llm,
                usage=usage,
            )
            _emit(
                state,
                "write_emails_done",
                {
                    "emails": len(emails),
                    "claims": sum(len(e.claims) for e in emails),
                },
            )
        return {"emails": emails}

    return node


def make_claim_extract_node() -> Callable[[FlowState], dict]:
    """Deterministic claim consolidation -- no LLM call."""

    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        observations = state.get("observations") or []
        emails = state.get("emails") or []
        with tracker.stage("claim_extract") as st:
            _emit(state, "claim_extract", {"emails": len(emails)})
            consolidated = claim_extract.consolidate(
                emails,
                known_observation_ids=[o.observation_id for o in observations],
            )
            total_claims = sum(len(e.claims) for e in consolidated)
            st.detail["claims"] = total_claims
            _emit(
                state,
                "claim_extract_done",
                {"emails": len(consolidated), "claims": total_claims},
            )
        return {"emails": consolidated}

    return node


def make_claim_verify_node(*, nli: NliValidator) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        observations = state.get("observations") or []
        emails = state.get("emails") or []
        sections_by_id = state.get("sections_by_id") or {}
        ctx = verify_mod.VerificationContext(
            observations={o.observation_id: o for o in observations},
            sections=sections_by_id,
        )
        with tracker.stage("claim_verify") as st:
            total_claims = sum(len(e.claims) for e in emails)
            _emit(
                state,
                "verify",
                {"emails": len(emails), "claims": total_claims},
            )
            updated: list[Email] = []
            done_claims = 0
            for i, e in enumerate(emails, start=1):
                claims = verify_mod.verify_email(e, ctx, nli=nli)
                updated.append(e.model_copy(update={"claims": claims}))
                done_claims += len(claims)
                _emit(
                    state,
                    "verify_progress",
                    {
                        "done": done_claims,
                        "total": total_claims,
                        "emails_done": i,
                        "emails_total": len(emails),
                    },
                )
            tallies = {
                "entailed": 0, "neutral": 0,
                "contradicted": 0, "unsupported": 0,
            }
            for e in updated:
                for c in e.claims:
                    if c.status == ClaimStatus.ENTAILED:
                        tallies["entailed"] += 1
                    elif c.status == ClaimStatus.CONTRADICTED:
                        tallies["contradicted"] += 1
                    elif c.status == ClaimStatus.UNSUPPORTED:
                        tallies["unsupported"] += 1
                    else:
                        tallies["neutral"] += 1
            st.detail.update(tallies)
            st.detail["unsupported"] = (
                tallies["unsupported"] + tallies["contradicted"]
            )
            _emit(state, "verify_done", tallies)
        return {"emails": updated}

    return node


def route_repair(state: FlowState) -> str:
    if state.get("repair_done"):
        return "skip"
    emails = state.get("emails") or []
    for e in emails:
        if verify_mod.needs_repair(e.claims):
            return "repair"
    return "skip"


def make_repair_node(*, llm: LLMClient, nli: NliValidator) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        observations = state.get("observations") or []
        emails = state.get("emails") or []
        sections_by_id = state.get("sections_by_id") or {}
        ctx = verify_mod.VerificationContext(
            observations={o.observation_id: o for o in observations},
            sections=sections_by_id,
        )
        with tracker.stage("repair") as st:
            to_repair = sum(1 for e in emails if verify_mod.needs_repair(e.claims))
            _emit(state, "repair", {"emails_to_repair": to_repair})
            repaired: list[Email] = []
            done = 0
            for e in emails:
                if verify_mod.needs_repair(e.claims):
                    repaired.append(
                        verify_mod.repair_email(e, ctx, llm=llm, usage=usage, nli=nli)
                    )
                    done += 1
                    _emit(
                        state,
                        "repair_progress",
                        {"done": done, "total": to_repair},
                    )
                else:
                    repaired.append(e)
            repaired_count = sum(
                1 for e in repaired for c in e.claims
                if c.status == ClaimStatus.REPAIRED
            )
            st.detail["repaired"] = repaired_count
            _emit(state, "repair_done", {"repaired_claims": repaired_count})
        return {"emails": repaired, "repair_done": True}

    return node


# ---------- Analytics + persist (target) ----------


def make_analytics_node(
    *, embedder: Embedder, llm: LLMClient, nli: NliValidator,
) -> Callable[[FlowState], dict]:
    async def node(state: FlowState) -> dict:
        tracker = state["tracker"]
        usage = state["usage"]
        observations = state.get("observations") or []
        emails = state.get("emails") or []
        sections_by_id = state.get("sections_by_id") or {}
        ctx = verify_mod.VerificationContext(
            observations={o.observation_id: o for o in observations},
            sections=sections_by_id,
        )

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
                        repaired = repaired.model_copy(
                            update={"claims": verify_mod.verify_email(repaired, ctx, nli=nli)}
                        )
                        emails[pain_idx] = repaired
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

        # Claim map + tallies.
        claim_map = verify_mod.build_claim_map(emails, ctx)
        for c in claim_map:
            tracker.metrics.claims_total += 1
            if c.status == ClaimStatus.ENTAILED:
                tracker.metrics.claims_supported += 1
            elif c.status == ClaimStatus.UNSUPPORTED:
                tracker.metrics.claims_unsupported += 1
            elif c.status == ClaimStatus.CONTRADICTED:
                tracker.metrics.claims_contradicted += 1
            elif c.status == ClaimStatus.REPAIRED:
                tracker.metrics.claims_repaired += 1
                tracker.metrics.claims_supported += 1

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
                conn.execute("DELETE FROM claim_map WHERE email_id = ?", (e.email_id,))
            for c in claim_map:
                conn.execute(
                    "INSERT OR REPLACE INTO claim_map (claim_id, email_id, angle, text, status, nli_score, citations) VALUES (?,?,?,?,?,?,?)",
                    (
                        c.claim_id, c.email_id, c.angle.value, c.text,
                        c.status.value, c.nli_score, dumps(list(c.citations)),
                    ),
                )

        tracker.log_artifact_json(
            "emails.json", [e.model_dump(mode="json") for e in emails]
        )
        tracker.log_artifact_json(
            "claim_map.json", [c.model_dump(mode="json") for c in claim_map]
        )
        # Totals
        tracker.metrics.tokens_in = usage.tokens_in
        tracker.metrics.tokens_out = usage.tokens_out
        tracker.metrics.cost_usd = round(usage.cost_usd, 4)
        return {"emails": emails, "claim_map": claim_map}

    return node
