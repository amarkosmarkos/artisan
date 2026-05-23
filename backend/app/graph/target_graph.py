"""Target flow LangGraph.

```
START -> target_crawl -> target_extract -> target_validate -> planner
       ├── (fetch_more)  -> target_crawl (explicit URLs; max once)
       ├── (web_search)  -> external_enrichment -> strategy
       ├── (continue|low_conf) -> strategy
       └── (stop)        -> END

   strategy -> writer -> claim_extract -> claim_verify
       ├── needs repair -> repair (max once) -> analytics -> END
       └── ok          -> analytics -> END
```
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from ..schemas import (
    ClaimMapEntry,
    ContactDecision,
    Email,
    FitAssessment,
    FitLevel,
    Observation,
    PersonaAlignment,
    Strategy,
    StrategyArtifact,
    TargetResponse,
    ValueProposition,
)
from ..synthesis.value_props_store import resolve_value_proposition
from ..services.embed import Embedder
from ..services.external import ExternalSignalProvider
from ..services.llm import LLMClient
from ..services.nli import NliValidator
from . import nodes
from .state import FlowState

log = logging.getLogger(__name__)


def build_target_graph(
    *,
    llm: LLMClient,
    nli: NliValidator,
    embedder: Embedder,
    external: ExternalSignalProvider,
):
    g = StateGraph(FlowState)

    g.add_node("target_crawl", nodes.make_crawl_node())
    g.add_node("target_fetch_more", nodes.make_crawl_node(fetch_more=True))
    g.add_node(
        "target_extract", nodes.make_extract_node(llm=llm, nli=nli, task="target")
    )
    g.add_node("target_validate", nodes.make_validate_node(nli=nli))
    g.add_node("planner", nodes.make_planner_node(llm=llm, task="target_eval"))
    g.add_node(
        "external_enrichment",
        nodes.make_external_enrich_node(llm=llm, provider=external, nli=nli),
    )
    # NOTE: LangGraph forbids node names that collide with state keys.
    # Our FlowState has a `strategy` key, so the node must be named differently.
    g.add_node("synthesize_strategy", nodes.make_strategy_node(llm=llm))
    g.add_node("write_emails", nodes.make_writer_node(llm=llm))
    g.add_node("extract_claims", nodes.make_claim_extract_node())
    g.add_node("verify_claims", nodes.make_claim_verify_node(nli=nli))
    g.add_node("repair_claims", nodes.make_repair_node(llm=llm, nli=nli))
    g.add_node("compute_analytics", nodes.make_analytics_node(embedder=embedder, llm=llm, nli=nli))

    g.add_edge(START, "target_crawl")
    g.add_edge("target_crawl", "target_extract")
    g.add_edge("target_extract", "target_validate")
    g.add_edge("target_validate", "planner")

    g.add_conditional_edges(
        "planner",
        nodes.route_planner_target,
        {
            "fetch_more": "target_fetch_more",
            "web_search": "external_enrichment",
            "continue": "synthesize_strategy",
            "stop": END,
        },
    )
    g.add_edge("target_fetch_more", "target_extract")
    g.add_edge("external_enrichment", "synthesize_strategy")

    g.add_edge("synthesize_strategy", "write_emails")
    g.add_edge("write_emails", "extract_claims")
    g.add_edge("extract_claims", "verify_claims")

    g.add_conditional_edges(
        "verify_claims",
        nodes.route_repair,
        {"repair": "repair_claims", "skip": "compute_analytics"},
    )
    g.add_edge("repair_claims", "compute_analytics")
    g.add_edge("compute_analytics", END)

    return g.compile()


async def run_target_graph(
    *,
    initial_state: FlowState,
    llm: LLMClient,
    nli: NliValidator,
    embedder: Embedder,
    external: ExternalSignalProvider,
) -> TargetResponse:
    graph = build_target_graph(llm=llm, nli=nli, embedder=embedder, external=external)
    final: FlowState = await graph.ainvoke(initial_state)  # type: ignore[assignment]
    observations: list[Observation] = final.get("observations") or []
    emails: list[Email] = final.get("emails") or []
    claim_map: list[ClaimMapEntry] = final.get("claim_map") or []
    strategy = final.get("strategy")
    persona = final.get("persona")
    tracker = final["tracker"]
    assert persona is not None, "target flow requires a persona"

    # Defensive refusal: if the Planner stopped early, surface that instead
    # of inventing a strategy.
    if strategy is None:
        strategy = StrategyArtifact(
            fit_assessment=FitAssessment(
                level=FitLevel.NONE,
                reasons=[],
                risks=[],
                missing_evidence=["planner_stopped_insufficient_evidence"],
            ),
            strategy=Strategy(
                contact_decision=ContactDecision.SKIP,
                angles=[],
                persona_alignment=PersonaAlignment(
                    role_relevance="low",
                    role_relevance_reason="",
                    preferred_framing="",
                    preferred_framing_reason="",
                    avoid=[],
                    avoid_reason="",
                ),
            ),
        )

    sender_vps: list[ValueProposition] = final.get("sender_vps") or []
    selected_vp: ValueProposition | None = None
    if sender_vps:
        selected_vp = resolve_value_proposition(
            sender_vps, strategy.selected_value_proposition_id
        )

    return TargetResponse(
        target_company_id=final["company_id"],
        target_url=final["homepage_url"],
        sender_company_id=final.get("sender_company_id") or "",
        persona=persona,
        observations=observations,
        strategy=strategy,
        emails=emails,
        claim_map=claim_map,
        metrics=tracker.metrics,
        selected_value_proposition=selected_vp,
        sender_value_propositions=sender_vps,
    )
