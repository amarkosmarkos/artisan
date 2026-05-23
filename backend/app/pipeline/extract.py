"""LLM-driven observation extraction.

Given a list of deterministic sections, ask an LLM to extract short,
single-fact observations relevant to commercial positioning. Every
observation MUST reference the section_id it came from. We do not let the
model invent free-form citations: we present a small batch of sections
with their IDs, and require the model to echo the ID it used.

Sections are batched so prompts stay within budget. Each observation has:
- ``kind`` (industry / customer / pricing / trigger / buyer / capability / risk / ...)
- ``text`` (one short sentence)
- ``section_id`` (must exist in the batch)
- ``confidence`` (0..1)
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, Field

from ..config import settings
from ..schemas import Observation
from ..services.llm import LLMClient, UsageAccumulator

log = logging.getLogger(__name__)


# Allowed observation kinds. We use a small closed enum so downstream
# field-mapping is deterministic.
ObservationKind = Literal[
    "industry",
    "customer",
    "buyer_role",
    "use_case",
    "pricing",
    "pain_point",
    "value_prop",
    "trigger",
    "size_band",
    "negative_icp",
    "hiring",
    "funding",
    "leadership",
    "expansion",
    "tech_stack",
    "capability",
    "geography",
    "other",
]


class _ExtractedObservation(BaseModel):
    kind: ObservationKind
    text: str = Field(min_length=4, max_length=320)
    section_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class _ExtractionResult(BaseModel):
    observations: list[_ExtractedObservation] = Field(default_factory=list)


class BatchProgressCallback(Protocol):
    def __call__(self, batch_obs: list[Observation], done: int, total: int) -> None: ...


_SYSTEM_SENDER = """You extract commercially-relevant observations from website sections of a company that is selling products, services, platforms, systems, or solutions.

For each section, write ONE-SENTENCE observations capturing:
- target industries / verticals served
- customer segments and sizes (SMB, mid-market, enterprise, public sector, large industrial buyers, operators, etc.)
- buyer roles (titles, departments) explicitly named
- use cases and workflows the product addresses
- pricing model, plans, tiers
- pain points the company claims to solve
- the value proposition (customer + pain + outcome + mechanism)
- the company's broad business lines or product/service categories
- triggers that lead a customer to buy (events, changes, problems)
- explicit non-customers / negative ICP (e.g. "not for individuals")
- capabilities, integrations, tech footprint

Rules:
- Use ONLY information present in the provided sections.
- Every observation MUST cite the exact section_id from the input.
- If a section has nothing relevant, return no observations for it.
- Each observation is ONE clean fact, not a paragraph.
- confidence reflects how unambiguous the section makes the claim.

Return JSON: { "observations": [ { "kind": "...", "text": "...", "section_id": "...", "confidence": 0.0-1.0 }, ... ] }
"""


_SYSTEM_TARGET = """You extract commercially-relevant observations about a TARGET company that someone may want to sell *to*.

For each section, write ONE-SENTENCE observations capturing:
- what the target company does / sells
- the industry, market, geography it operates in
- company size, headcount range, growth signals
- customer segments (who they serve)
- public buyers / decision makers mentioned (titles, names with titles)
- pain points or operational friction implied by the content
- triggers / current events: hiring spikes, expansion, launches, funding, leadership changes
- tech footprint, integrations, platforms in use
- pricing if visible

Rules:
- Use ONLY information present in the provided sections.
- Every observation MUST cite the exact section_id from the input.
- Do NOT speculate. If unclear, do not extract.
- Each observation is ONE clean fact.
- confidence reflects how unambiguous the section makes the claim.

Return JSON: { "observations": [ { "kind": "...", "text": "...", "section_id": "...", "confidence": 0.0-1.0 }, ... ] }
"""


def _format_sections(sections: list[dict]) -> str:
    lines: list[str] = []
    for s in sections:
        heading = s.get("heading") or "(no heading)"
        text = s["text"][:1400]  # trim oversized sections for the prompt budget
        lines.append(f"---\nsection_id: {s['section_id']}\nurl: {s['url']}\nheading: {heading}\n\n{text}\n")
    return "\n".join(lines)


async def extract_observations(
    sections: list[dict],
    *,
    company_id: str,
    llm: LLMClient,
    usage: UsageAccumulator,
    task: Literal["sender", "target"],
    batch_size: int | None = None,
    concurrency: int | None = None,
    on_batch_done: Callable[[int, int], None] | None = None,
    on_batch_observations: BatchProgressCallback | None = None,
) -> list[Observation]:
    """Extract observations from sections in parallel batches.

    Batches are independent (each batch is a self-contained LLM call against
    a small slice of sections), so we fan them out under a bounded semaphore.
    ``on_batch_done(done, total)`` is called after each batch finishes so the
    UI can display live progress instead of waiting for the whole stage.
    """
    if not sections:
        return []
    system = _SYSTEM_SENDER if task == "sender" else _SYSTEM_TARGET
    section_index = {s["section_id"]: s for s in sections}

    bsize = batch_size or settings.extract_batch_size
    conc = concurrency or settings.extract_concurrency
    batches: list[list[dict]] = [
        sections[i : i + bsize] for i in range(0, len(sections), bsize)
    ]
    total = len(batches)
    sem = asyncio.Semaphore(max(1, conc))
    done_counter = 0
    done_lock = asyncio.Lock()

    def _materialize(result: _ExtractionResult) -> list[Observation]:
        out: list[Observation] = []
        for ex in result.observations:
            if ex.section_id not in section_index:
                # Reject hallucinated section IDs.
                log.debug(
                    "extract: dropping observation with unknown section_id %s",
                    ex.section_id,
                )
                continue
            out.append(
                Observation(
                    observation_id=f"obs_{uuid.uuid4().hex[:12]}",
                    company_id=company_id,
                    kind=ex.kind,
                    text=ex.text.strip(),
                    section_id=ex.section_id,
                    confidence=ex.confidence,
                )
            )
        return out

    async def _run_batch(idx: int, batch: list[dict]) -> list[Observation]:
        nonlocal done_counter
        user = (
            "Extract observations from the following sections. "
            "Cite the exact section_id for each observation.\n\n"
            + _format_sections(batch)
        )
        async with sem:
            try:
                # llm.structured is sync (Instructor + sync AzureOpenAI). Push
                # it onto a worker thread so we can run batches concurrently.
                result = await asyncio.to_thread(
                    llm.structured,
                    system=system,
                    user=user,
                    schema=_ExtractionResult,
                    purpose=f"extract_observations_{task}",
                    usage=usage,
                )
                obs = _materialize(result)
            except Exception as e:  # noqa: BLE001
                log.warning("extract: batch %d failed: %s", idx, e)
                obs = []
        async with done_lock:
            done_counter += 1
            if on_batch_observations:
                try:
                    on_batch_observations(obs, done_counter, total)
                except Exception:  # noqa: BLE001
                    log.debug(
                        "extract: on_batch_observations raised; ignoring",
                        exc_info=True,
                    )
            elif on_batch_done:
                try:
                    on_batch_done(done_counter, total)
                except Exception:  # noqa: BLE001
                    log.debug("extract: on_batch_done raised; ignoring", exc_info=True)
        return obs

    results = await asyncio.gather(
        *[_run_batch(i, b) for i, b in enumerate(batches)]
    )
    all_obs: list[Observation] = [o for batch_obs in results for o in batch_obs]
    log.info(
        "extract: %d observations from %d sections (task=%s, batches=%d, conc=%d)",
        len(all_obs),
        len(sections),
        task,
        total,
        conc,
    )
    return all_obs
