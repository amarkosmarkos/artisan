"""Typed shared state for the LangGraph state machines.

Each node receives the full ``FlowState`` and returns a partial dict of
keys to update. LangGraph merges the partial update into the state.

Mutable accessory objects (``usage``, ``metrics``, ``tracker``) are stored
as references; nodes mutate them in place rather than returning new
copies. That keeps the state dict small and the timeline coherent across
nodes.
"""
from __future__ import annotations

from typing import Any, Callable, Optional, TypedDict

from ..observability.tracker import RunTracker
from ..schemas import (
    Email,
    ICP,
    Observation,
    PersonaInput,
    PlannerOutput,
    RunMetrics,
    StrategyArtifact,
    ValueProposition,
)
from ..services.llm import UsageAccumulator


class FlowState(TypedDict, total=False):
    # ---- Inputs ----
    task: str                        # "sender" | "target"
    homepage_url: str
    company_id: str
    sender_company_id: Optional[str]
    sender_icp: Optional[ICP]
    sender_vp: Optional[ValueProposition]
    sender_vps: list[ValueProposition]
    persona: Optional[PersonaInput]
    # Optional persona scoping for target runs.
    persona_id: Optional[str]

    # ---- Evidence substrate ----
    sections_by_id: dict[str, dict]  # section_id -> section dict
    observations: list[Observation]
    raw_cleaned_chars: int
    evidence_chars: int
    pages_fetched: int
    failed_sources: list[str]
    discovered_urls: list[str]   # all internal links seen during crawl
    crawled_urls: list[str]      # URLs we successfully extracted markdown from

    # ---- Planner ----
    planner_output: Optional[PlannerOutput]
    planner_attempts: int            # how many times the Planner has run
    fetch_more_done: bool
    web_search_done: bool

    # ---- Sender outputs ----
    icp: Optional[ICP]
    value_proposition: Optional[ValueProposition]
    value_propositions: list[ValueProposition]

    # ---- Target outputs ----
    strategy: Optional[StrategyArtifact]
    emails: list[Email]
    repair_done: bool

    # ---- Services & observability ----
    progress: Callable[[str, dict[str, Any]], None]
    usage: UsageAccumulator
    tracker: RunTracker
    metrics: RunMetrics


def make_initial_state(
    *,
    task: str,
    homepage_url: str,
    company_id: str,
    progress: Callable[[str, dict[str, Any]], None],
    usage: UsageAccumulator,
    tracker: RunTracker,
    sender_company_id: Optional[str] = None,
    sender_icp: Optional[ICP] = None,
    sender_vp: Optional[ValueProposition] = None,
    sender_vps: Optional[list[ValueProposition]] = None,
    persona: Optional[PersonaInput] = None,
    persona_id: Optional[str] = None,
) -> FlowState:
    return FlowState(  # type: ignore[typeddict-item]
        task=task,
        homepage_url=homepage_url,
        company_id=company_id,
        sender_company_id=sender_company_id,
        sender_icp=sender_icp,
        sender_vp=sender_vp,
        sender_vps=sender_vps or ([sender_vp] if sender_vp else []),
        persona=persona,
        persona_id=persona_id,
        sections_by_id={},
        observations=[],
        raw_cleaned_chars=0,
        evidence_chars=0,
        pages_fetched=0,
        failed_sources=[],
        discovered_urls=[],
        crawled_urls=[],
        planner_output=None,
        planner_attempts=0,
        fetch_more_done=False,
        web_search_done=False,
        icp=None,
        value_proposition=None,
        value_propositions=[],
        strategy=None,
        emails=[],
        repair_done=False,
        progress=progress,
        usage=usage,
        tracker=tracker,
        metrics=tracker.metrics,
    )
