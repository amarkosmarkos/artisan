"""Select the most relevant sections before LLM extraction.

The crawl + sectioning stages can produce hundreds of sections per target.
Extraction cost scales linearly with section count, so we rank sections by
overlap with the active outreach context (value proposition, persona, ICP)
and keep only the top-N for extraction.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from ..schemas import ICP, PersonaInput, ValueProposition

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# URL-path hints reused from crawl scoring (subset of commercial pages).
_URL_HINTS: tuple[str, ...] = (
    "about",
    "product",
    "platform",
    "solution",
    "pricing",
    "customer",
    "industry",
    "team",
    "career",
    "news",
)


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


def _context_tokens(
    *,
    sender_vp: ValueProposition | None,
    persona: PersonaInput | None,
    sender_icp: ICP | None,
) -> set[str]:
    parts: list[str] = []
    if sender_vp:
        parts.extend(
            [
                sender_vp.label,
                sender_vp.customer,
                sender_vp.pain,
                sender_vp.outcome,
                sender_vp.mechanism,
            ]
        )
    if persona:
        parts.append(persona.role)
    if sender_icp:
        for field in (
            sender_icp.target_industries,
            sender_icp.likely_buyers,
            sender_icp.common_triggers,
        ):
            parts.extend(field.values)
    blob = " ".join(p for p in parts if p).lower()
    return _tokens(blob)


def _score_section(section: dict, context: set[str]) -> float:
    heading = section.get("heading") or ""
    text = section.get("text") or ""
    body_tokens = _tokens(f"{heading} {text}")
    overlap = len(body_tokens & context)
    score = float(overlap)

    url = section.get("url") or ""
    path = urlparse(url).path.lower()
    if not path or path == "/":
        score += 3.0
    for hint in _URL_HINTS:
        if hint in path:
            score += 0.75

    # Prefer sections with substantive content over tiny nav stubs.
    score += min(len(text) / 800.0, 1.5)
    return score


def select_sections_for_extraction(
    sections: list[dict],
    *,
    max_sections: int,
    sender_vp: ValueProposition | None = None,
    persona: PersonaInput | None = None,
    sender_icp: ICP | None = None,
) -> list[dict]:
    """Return up to ``max_sections`` sections ranked by outreach relevance."""
    if max_sections <= 0 or len(sections) <= max_sections:
        return sections

    context = _context_tokens(
        sender_vp=sender_vp, persona=persona, sender_icp=sender_icp
    )
    ranked = sorted(
        sections,
        key=lambda s: _score_section(s, context),
        reverse=True,
    )
    return ranked[:max_sections]
