"""Post-sender target discovery.

After a sender flow completes we have an ICP + value propositions. This
module turns that profile into up to 3 concrete candidate companies the
user could pursue, using OpenAI's ``web_search`` tool as the only
external signal source.

Design constraints (matched to the product spec):
- Suggest up to 3 target companies.
- For each, suggest up to 2 personas/roles. Names are only filled in
  when the public web evidence clearly names someone in that role at
  that company; otherwise we keep it role-only.
- Discovery NEVER persists targets or triggers downstream analysis. The
  caller persists targets by calling the existing
  ``POST /senders/{id}/targets`` endpoint with the chosen URL.
- Discovery is best-effort: when the search tool is unavailable or the
  matches are weak, we return a structured status so the UI can render
  a clean empty/error state.

The flow is two LLM calls:
1. Free-form ``responses.create`` with ``web_search`` tool to gather
   recent, cited evidence about companies fitting the ICP/VP.
2. ``LLMClient.structured`` to parse that evidence into typed
   ``SuggestedTarget`` objects.

Splitting the calls keeps the structured-output schema clean (instructor
+ tools_strict cannot easily compose with the ``web_search`` tool today)
and lets us audit the raw search text in logs.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from ..config import settings
from ..db import fetchall, fetchone
from ..schemas import (
    ICP,
    DiscoveryEvidence,
    Seniority,
    SuggestedPersona,
    SuggestedTarget,
    SuggestedTargetsResponse,
    ValueProposition,
)
from ..services.llm import LLMClient, UsageAccumulator
from ..services.web_search import run_web_search
from .value_props_store import parse_stored_value_props

log = logging.getLogger(__name__)


_BLOCKED_DOMAINS = (
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "google.com",
    "bing.com",
    "wikipedia.org",
    "youtube.com",
    "medium.com",
    "reddit.com",
)


# Maximum number of search-tool citations we forward to the structured
# extractor. Keeps the prompt bounded regardless of how chatty the web
# search response is.
_MAX_EVIDENCE_PER_CALL = 30


# ---------- DB helpers ----------


def _load_sender_context(
    sender_company_id: str,
) -> tuple[str, ICP, list[ValueProposition]] | None:
    """Load (url, ICP, value_propositions) for a sender or return None."""
    row = fetchone(
        "SELECT company_id, url, role FROM companies WHERE company_id = ?",
        (sender_company_id,),
    )
    if not row or row["role"] != "sender":
        return None
    icp_row = fetchone(
        "SELECT payload FROM icps WHERE company_id = ?", (sender_company_id,)
    )
    vp_row = fetchone(
        "SELECT payload FROM value_props WHERE company_id = ?",
        (sender_company_id,),
    )
    if not icp_row or not vp_row:
        return None
    icp = ICP.model_validate(json.loads(icp_row["payload"]))
    vps = parse_stored_value_props(json.loads(vp_row["payload"]))
    if not vps:
        return None
    return (row["url"] or "", icp, vps)


def _existing_target_domains(sender_company_id: str) -> set[str]:
    """Domains the sender has already added so we don't suggest duplicates."""
    rows = fetchall(
        "SELECT c.url FROM sender_targets st "
        "JOIN companies c ON c.company_id = st.target_company_id "
        "WHERE st.sender_company_id = ?",
        (sender_company_id,),
    )
    out: set[str] = set()
    for r in rows:
        d = _domain_of(r["url"])
        if d:
            out.add(d)
    return out


# ---------- Domain utilities ----------


def _domain_of(url: str) -> str:
    if not url:
        return ""
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _sender_domain(sender_url: str) -> str:
    return _domain_of(sender_url)


def _is_useful_evidence_url(url: str) -> bool:
    d = _domain_of(url)
    if not d:
        return False
    return not any(d == b or d.endswith("." + b) for b in _BLOCKED_DOMAINS)


def _is_candidate_company_domain(url: str, *, exclude: Iterable[str]) -> bool:
    """A candidate domain is one that could host a company homepage."""
    d = _domain_of(url)
    if not d:
        return False
    if any(d == b or d.endswith("." + b) for b in _BLOCKED_DOMAINS):
        return False
    return d not in set(exclude)


# ---------- Prompt construction ----------


def _summarize_icp(icp: ICP) -> str:
    parts: list[str] = []
    for label, field in [
        ("Target industries", icp.target_industries),
        ("Size bands", icp.size_bands),
        ("Likely buyers", icp.likely_buyers),
        ("Common triggers", icp.common_triggers),
        ("Negative ICP", icp.negative_icp),
    ]:
        if field.values:
            parts.append(f"- {label}: {', '.join(field.values)}")
    return "\n".join(parts) or "- (no structured ICP fields)"


def _summarize_vps(vps: list[ValueProposition]) -> str:
    lines: list[str] = []
    for vp in vps[:4]:
        head = vp.label or "Value proposition"
        lines.append(
            f"- [{vp.id or '-'}] {head}\n"
            f"    customer:  {vp.customer}\n"
            f"    pain:      {vp.pain}\n"
            f"    outcome:   {vp.outcome}\n"
            f"    mechanism: {vp.mechanism}"
        )
    return "\n".join(lines) or "- (none)"


_DISCOVERY_INSTRUCTION = """You are an outbound discovery assistant.

You are given a SENDER's Ideal Customer Profile (ICP) and Value Propositions.
Use the web_search tool to identify up to 3 REAL companies that plausibly
match the ICP and could benefit from one of the value propositions.

You MUST:
- Only return companies you can support with a citation from your search.
- Cite the homepage or an authoritative source for each company.
- Prefer companies that show RECENT public signals (funding, hiring,
  product launches, expansion, leadership changes) that align with the
  ICP's common triggers.
- For each company, propose up to 2 buyer ROLES or TITLES that would
  plausibly own the pain. Use role/title only. Do NOT invent named
  people. Only mention a person by name if the search clearly identifies
  that named person currently in that role at that company, with a
  citation.

You MUST NOT:
- Invent companies or claim a match without a citation.
- Suggest the sender itself, its parent/subsidiary, a known competitor,
  or any company whose domain you cannot verify.
- Include LinkedIn, X/Twitter, Facebook, or Maps results as the primary
  citation for a company.

Output a concise natural-language briefing organized as:

Company N: <Name> (<homepage_url>)
- Why it fits: <1-2 sentences referencing the ICP / VP it matches>
- Matched value proposition: <vp_id or the label you saw, or "none">
- Confidence: high | medium | low
- Source(s): <one or two short bullets, each ending with a citation>
- Suggested roles:
    * <role or title> - <why this role>
    * <role or title> - <why this role>

If you cannot find 3 well-sourced matches, return fewer. If you find
none, state that plainly."""


def _build_discovery_prompt(
    sender_url: str, icp: ICP, vps: list[ValueProposition]
) -> str:
    return (
        f"SENDER homepage: {sender_url or '(unknown)'}\n\n"
        f"SENDER ICP:\n{_summarize_icp(icp)}\n\n"
        f"SENDER VALUE PROPOSITIONS (use the [id] when referring back):\n"
        f"{_summarize_vps(vps)}\n\n"
        "Now find up to 3 companies that fit and propose roles to contact."
    )


# ---------- Web search call ----------


@dataclass
class _RawSearchResult:
    raw_text: str
    citations: list[DiscoveryEvidence]


def _run_web_search(
    llm: LLMClient,  # noqa: ARG001 — kept for call-site symmetry with extract pass
    sender_url: str,
    icp: ICP,
    vps: list[ValueProposition],
) -> tuple[_RawSearchResult | None, str]:
    """Responses API + ``web_search`` via ``services.web_search``.

    Returns ``(result, error_detail)``. ``error_detail`` is non-empty when
    the Responses/web_search call failed (wrong API route, blocked tool,
    unsupported deployment, etc.).
    """
    user_prompt = _build_discovery_prompt(sender_url, icp, vps)
    outcome = run_web_search(
        instructions=_DISCOVERY_INSTRUCTION,
        input=user_prompt,
        tool_choice="required",
    )
    if not outcome.ok:
        return None, outcome.error

    citations: list[DiscoveryEvidence] = []
    for c in outcome.citations[:_MAX_EVIDENCE_PER_CALL]:
        if not _is_useful_evidence_url(c.url):
            continue
        citations.append(
            DiscoveryEvidence(url=c.url, title=c.title, snippet=c.snippet)
        )

    log.info(
        "discovery web_search ok (%s): text_chars=%d citations=%d",
        outcome.client_used,
        len(outcome.raw_text),
        len(citations),
    )
    return _RawSearchResult(raw_text=outcome.raw_text, citations=citations), ""


# ---------- Structured extraction ----------


class _PersonaDraft(BaseModel):
    title: str = ""
    seniority: str | None = None
    name: str | None = None
    rationale: str = ""


class _TargetDraft(BaseModel):
    company_name: str = ""
    homepage_url: str = ""
    fit_rationale: str = ""
    matched_value_proposition_id: str | None = None
    matched_value_proposition_label: str = ""
    confidence: str = "medium"
    evidence_urls: list[str] = Field(default_factory=list)
    personas: list[_PersonaDraft] = Field(default_factory=list)


class _DiscoveryDraft(BaseModel):
    suggestions: list[_TargetDraft] = Field(default_factory=list)


_EXTRACT_SYSTEM = """You convert an outbound discovery briefing into a
strict JSON list of suggested targets. You do NOT add facts; you only
extract what is explicitly in the BRIEFING text or supported by the
CITATIONS provided alongside it.

Hard rules:
- Return at most 3 suggestions.
- Each suggestion must have homepage_url present in the BRIEFING and a
  matching citation in CITATIONS. Drop suggestions that lack a citation.
- evidence_urls must be a subset of CITATIONS urls.
- For each suggestion include 0, 1, or up to 2 personas. Each persona is
  a role/title (e.g. "VP of Engineering"). Only set a persona name when
  the briefing names a specific public person currently in that role at
  that company.
- confidence is one of "high", "medium", "low".
- If the briefing does not name a value proposition, set
  matched_value_proposition_id to null and the label to an empty string.
- Seniority, when set, is one of: ic, manager, director, vp, c_level,
  founder. If unsure, leave it null.
"""


def _format_citations_for_extract(evidence: list[DiscoveryEvidence]) -> str:
    if not evidence:
        return "(no citations)"
    lines: list[str] = []
    for i, e in enumerate(evidence, 1):
        title = e.title or "(untitled)"
        snippet = (e.snippet or "").replace("\n", " ").strip()
        lines.append(f"{i}. {title}\n   url: {e.url}\n   snippet: {snippet[:280]}")
    return "\n".join(lines)


def _extract_structured(
    llm: LLMClient,
    usage: UsageAccumulator,
    raw: _RawSearchResult,
) -> _DiscoveryDraft:
    user = (
        "BRIEFING:\n"
        f"{raw.raw_text or '(empty)'}\n\n"
        "CITATIONS (allowed evidence URLs):\n"
        f"{_format_citations_for_extract(raw.citations)}\n\n"
        "Extract up to 3 well-sourced suggestions as JSON."
    )
    return llm.structured(
        system=_EXTRACT_SYSTEM,
        user=user,
        schema=_DiscoveryDraft,
        purpose="target_discovery_extract",
        usage=usage,
    )


# ---------- Post-processing ----------


_CONFIDENCE_ALIASES = {
    "high": "high",
    "h": "high",
    "strong": "high",
    "medium": "medium",
    "med": "medium",
    "m": "medium",
    "plausible": "medium",
    "low": "low",
    "l": "low",
    "weak": "low",
}


def _normalize_confidence(value: str) -> str:
    return _CONFIDENCE_ALIASES.get((value or "").strip().lower(), "medium")


_SENIORITY_VALUES = {s.value for s in Seniority}


def _normalize_seniority(value: str | None) -> Seniority | None:
    if not value:
        return None
    v = value.strip().lower().replace("-", "_").replace(" ", "_")
    if v in {"clevel", "c_level", "executive"}:
        v = "c_level"
    if v in _SENIORITY_VALUES:
        return Seniority(v)
    return None


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url):
        url = "https://" + url.lstrip("/")
    return url


def _vp_label(
    vp_id: str | None, sender_vps: list[ValueProposition], fallback: str
) -> tuple[str | None, str]:
    """Resolve a draft VP id to a known sender VP. We accept either the
    real ``vp.id`` or the ``vp.label`` (the LLM sometimes echoes one in
    place of the other). Returns the resolved (id, label) or (None, fallback).
    """
    if vp_id:
        for vp in sender_vps:
            if vp.id and vp.id == vp_id:
                return vp.id, vp.label or fallback
            if vp.label and vp.label.strip().lower() == vp_id.strip().lower():
                return vp.id or None, vp.label
    if fallback:
        for vp in sender_vps:
            if vp.label and vp.label.strip().lower() == fallback.strip().lower():
                return vp.id or None, vp.label
    return None, fallback or ""


def _suggestion_from_draft(
    draft: _TargetDraft,
    *,
    citations_by_url: dict[str, DiscoveryEvidence],
    sender_vps: list[ValueProposition],
    excluded_domains: set[str],
    accepted_domains: set[str],
) -> SuggestedTarget | None:
    name = (draft.company_name or "").strip()
    url = _normalize_url(draft.homepage_url)
    if not name or not url:
        return None
    domain = _domain_of(url)
    if not domain or domain in excluded_domains or domain in accepted_domains:
        return None
    if not _is_candidate_company_domain(url, exclude=excluded_domains):
        return None

    # Evidence: keep only urls present in citations to enforce grounding.
    evidence: list[DiscoveryEvidence] = []
    seen_ev: set[str] = set()
    for ev_url in draft.evidence_urls:
        ev_url = (ev_url or "").strip()
        if not ev_url or ev_url in seen_ev:
            continue
        if ev_url in citations_by_url:
            evidence.append(citations_by_url[ev_url])
            seen_ev.add(ev_url)
    # As a fallback, if the homepage itself is a citation, attach it.
    if not evidence and url in citations_by_url:
        evidence.append(citations_by_url[url])
    if not evidence:
        return None

    vp_id, vp_label = _vp_label(
        draft.matched_value_proposition_id,
        sender_vps,
        draft.matched_value_proposition_label or "",
    )

    personas: list[SuggestedPersona] = []
    for p in draft.personas[:2]:
        title = (p.title or "").strip()
        if not title:
            continue
        personas.append(
            SuggestedPersona(
                title=title,
                seniority=_normalize_seniority(p.seniority),
                name=(p.name or "").strip() or None,
                rationale=(p.rationale or "").strip(),
            )
        )

    return SuggestedTarget(
        company_name=name,
        domain=domain,
        homepage_url=url,
        fit_rationale=(draft.fit_rationale or "").strip(),
        matched_value_proposition_id=vp_id,
        matched_value_proposition_label=vp_label,
        confidence=_normalize_confidence(draft.confidence),  # type: ignore[arg-type]
        evidence=evidence,
        personas=personas,
    )


# ---------- Public entry point ----------


def discover_targets(
    sender_company_id: str,
    *,
    llm: LLMClient,
    max_targets: int = 3,
) -> SuggestedTargetsResponse:
    """Run the two-pass discovery for a sender and return a response.

    The function never raises for ordinary failures (missing artifacts,
    web search unavailable, weak matches). It always returns a typed
    response with a ``status`` field so the UI can render the right
    state.
    """
    ctx = _load_sender_context(sender_company_id)
    if not ctx:
        return SuggestedTargetsResponse(
            sender_company_id=sender_company_id,
            status="unavailable",
            message="Sender ICP / value proposition not available yet.",
            provider="openai_web_search",
        )
    sender_url, icp, vps = ctx

    usage = UsageAccumulator()

    raw, search_error = _run_web_search(llm, sender_url, icp, vps)
    if raw is None:
        hint = (
            "Azure web search uses the Responses API at "
            "{endpoint}/openai/v1/ (not the chat api-version). "
            "Ensure your deployment supports web search (e.g. gpt-4o, gpt-4.1) "
            "and that the tool is enabled on your Foundry resource."
        ).format(endpoint=(settings.azure_openai_endpoint or "").rstrip("/"))
        detail = search_error[:500] if search_error else "unknown error"
        return SuggestedTargetsResponse(
            sender_company_id=sender_company_id,
            status="unavailable",
            message=(
                f"Web search failed: {detail}. {hint} "
                "You can still add targets manually below."
            ),
            provider="azure_web_search",
        )

    if not raw.citations:
        return SuggestedTargetsResponse(
            sender_company_id=sender_company_id,
            status="weak",
            message="No public matches were strong enough to suggest.",
            provider="openai_web_search",
        )

    try:
        draft = _extract_structured(llm, usage, raw)
    except Exception as e:  # noqa: BLE001
        log.warning("discovery structured extract failed: %s", e)
        return SuggestedTargetsResponse(
            sender_company_id=sender_company_id,
            status="error",
            message="Could not parse discovery results.",
            provider="openai_web_search",
        )

    excluded = _existing_target_domains(sender_company_id)
    sender_d = _sender_domain(sender_url)
    if sender_d:
        excluded.add(sender_d)

    citations_by_url = {ev.url: ev for ev in raw.citations}
    accepted: set[str] = set()
    suggestions: list[SuggestedTarget] = []
    for d in draft.suggestions:
        item = _suggestion_from_draft(
            d,
            citations_by_url=citations_by_url,
            sender_vps=vps,
            excluded_domains=excluded,
            accepted_domains=accepted,
        )
        if item is None:
            continue
        suggestions.append(item)
        accepted.add(item.domain)
        if len(suggestions) >= max_targets:
            break

    if not suggestions:
        return SuggestedTargetsResponse(
            sender_company_id=sender_company_id,
            status="weak",
            message="No grounded matches met the discovery quality bar.",
            provider="openai_web_search",
        )

    return SuggestedTargetsResponse(
        sender_company_id=sender_company_id,
        provider="openai_web_search",
        suggestions=suggestions,
        status="ok",
    )
