"""Sender flow LangGraph.

```
START -> sender_crawl -> sender_extract -> sender_validate -> planner
       └── (fetch_more) -> sender_crawl (with explicit_urls; max once)
       └── (continue|low_conf) -> sender_synthesize -> END
       └── (stop) -> END
```
"""
from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph

from ..schemas import (
    ICP,
    Observation,
    SenderResponse,
    ValueProposition,
)
from ..services.llm import LLMClient
from ..services.nli import NliValidator
from . import nodes
from .state import FlowState

log = logging.getLogger(__name__)


def build_sender_graph(*, llm: LLMClient, nli: NliValidator):
    sg = StateGraph(FlowState)

    sg.add_node("sender_crawl", nodes.make_crawl_node())
    sg.add_node("sender_fetch_more", nodes.make_crawl_node(fetch_more=True))
    sg.add_node("sender_extract", nodes.make_extract_node(llm=llm, task="sender"))
    sg.add_node("sender_validate", nodes.make_validate_node(nli=nli))
    sg.add_node("planner", nodes.make_planner_node(llm=llm, task="sender_icp"))
    sg.add_node("sender_synthesize", nodes.make_sender_synthesize_node(llm=llm))

    sg.add_edge(START, "sender_crawl")
    sg.add_edge("sender_crawl", "sender_extract")
    sg.add_edge("sender_extract", "sender_validate")
    sg.add_edge("sender_validate", "planner")

    sg.add_conditional_edges(
        "planner",
        nodes.route_planner_sender,
        {
            "fetch_more": "sender_fetch_more",
            "continue": "sender_synthesize",
            "stop": END,
        },
    )
    sg.add_edge("sender_fetch_more", "sender_extract")
    sg.add_edge("sender_synthesize", END)

    return sg.compile()


async def run_sender_graph(
    *,
    initial_state: FlowState,
    llm: LLMClient,
    nli: NliValidator,
) -> SenderResponse:
    graph = build_sender_graph(llm=llm, nli=nli)
    final: FlowState = await graph.ainvoke(initial_state)  # type: ignore[assignment]
    observations: list[Observation] = final.get("observations") or []
    icp: ICP = final.get("icp") or ICP()
    vp: ValueProposition = final.get("value_proposition") or ValueProposition()
    tracker = final["tracker"]
    tracker.metrics.tokens_in = final["usage"].tokens_in
    tracker.metrics.tokens_out = final["usage"].tokens_out
    tracker.metrics.cost_usd = round(final["usage"].cost_usd, 4)

    return SenderResponse(
        company_id=final["company_id"],
        sender_url=final["homepage_url"],
        icp=icp,
        value_proposition=vp,
        observations=observations,
        metrics=tracker.metrics,
    )
