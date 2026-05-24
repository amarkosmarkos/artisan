"""Pydantic schemas and enums for the evidence pipeline.

These are the value spaces of the system: every commercial decision flows
through one of these typed objects. The LLM is *never* the source of truth
for citations: it only references deterministic ``section_id`` values that
the backend created and persisted.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------- Enums ----------

class FitLevel(str, Enum):
    STRONG = "strong"
    PLAUSIBLE = "plausible"
    WEAK = "weak"
    NONE = "none"


class ContactDecision(str, Enum):
    CONTACT = "contact"
    WAIT_FOR_TRIGGER = "wait_for_trigger"
    SKIP = "skip"


class AngleType(str, Enum):
    PAIN_LED = "pain_led"
    TRIGGER_LED = "trigger_led"
    OUTCOME_LED = "outcome_led"


class PlannerDecision(str, Enum):
    CONTINUE = "continue"
    FETCH_MORE = "fetch_more"
    WEB_SEARCH = "web_search"
    PROCEED_LOW_CONFIDENCE = "proceed_low_confidence"
    STOP = "stop"


class Seniority(str, Enum):
    IC = "ic"
    MANAGER = "manager"
    DIRECTOR = "director"
    VP = "vp"
    C_LEVEL = "c_level"
    FOUNDER = "founder"


class NliLabel(str, Enum):
    ENTAILED = "entailed"
    NEUTRAL = "neutral"
    CONTRADICTED = "contradicted"


# ---------- Evidence primitives ----------

class SectionRef(BaseModel):
    section_id: str
    url: str
    heading: str | None = None


class Section(BaseModel):
    section_id: str
    company_id: str
    url: str
    heading: str | None = None
    text: str
    char_start: int | None = None
    char_end: int | None = None
    source: Literal["website", "web_search"] = "website"


class Observation(BaseModel):
    observation_id: str
    company_id: str
    kind: str  # e.g. "industry", "customer", "trigger", "pricing", "hiring"
    text: str
    section_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    validation: NliLabel | None = None
    validation_score: float | None = None


# ---------- Sender artifacts ----------

class FieldWithEvidence(BaseModel):
    """A structured field whose value is grounded in observation evidence."""
    values: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)  # observation_id list


class ICP(BaseModel):
    target_industries: FieldWithEvidence = Field(default_factory=FieldWithEvidence)
    size_bands: FieldWithEvidence = Field(default_factory=FieldWithEvidence)
    likely_buyers: FieldWithEvidence = Field(default_factory=FieldWithEvidence)
    common_triggers: FieldWithEvidence = Field(default_factory=FieldWithEvidence)
    negative_icp: FieldWithEvidence = Field(default_factory=FieldWithEvidence)


class ValueProposition(BaseModel):
    id: str = ""
    label: str = ""  # e.g. "Commercial aviation", "Defense"
    customer: str = ""
    pain: str = ""
    outcome: str = ""
    mechanism: str = ""
    confidence: float = 0.0
    evidence_refs: list[str] = Field(default_factory=list)


# ---------- Planner ----------

class PlannerInput(BaseModel):
    task: Literal["sender_icp", "target_eval"]
    observations: list[Observation]
    missing_fields: list[str]
    evidence_counts: dict[str, int]
    field_confidence: dict[str, float]
    failed_sources: list[str] = Field(default_factory=list)
    # Real internal links discovered during crawl but not yet fetched.
    # The planner MUST pick from this list for fetch_more (no invented paths).
    uncrawled_discovered_urls: list[str] = Field(default_factory=list)


class PlannerOutput(BaseModel):
    decision: PlannerDecision
    reason: str
    missing_fields: list[str] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    suggested_internal_pages: list[str] = Field(default_factory=list)


# ---------- Strategy artifact ----------

class FitAssessment(BaseModel):
    level: FitLevel
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class PersonaAlignment(BaseModel):
    role_relevance: Literal["high", "medium", "low"]
    role_relevance_reason: str = ""
    preferred_framing: str
    preferred_framing_reason: str = ""
    avoid: list[str] = Field(default_factory=list)
    avoid_reason: str = ""


class Angle(BaseModel):
    type: AngleType
    hypothesis: str
    evidence_refs: list[str] = Field(default_factory=list)  # observation_ids


class Strategy(BaseModel):
    contact_decision: ContactDecision
    angles: list[Angle]
    persona_alignment: PersonaAlignment


class StrategyArtifact(BaseModel):
    fit_assessment: FitAssessment
    strategy: Strategy
    # Best-matching sender value proposition for this target/persona (if multiple exist).
    selected_value_proposition_id: str | None = None
    # Echo of the chosen VP's label so the UI can render the selection without
    # a second lookup. Resolved server-side; the LLM also fills it.
    selected_value_proposition_label: str = ""
    # Short justification of WHY this VP was picked for this target+persona.
    selection_reason: str = ""
    # One-sentence high-level messaging angle anchored to the selected VP.
    # Distinct from the per-angle hypotheses inside Strategy.angles.
    messaging_angle: str = ""


# ---------- Email + safety verification ----------


class StatementContextRef(BaseModel):
    """A slice of workflow context that can be cited as evidence.

    Both the writer (when declaring its claims) and the guardrail (when
    looking up cited refs) use the same ref_id namespace.
    """

    ref_id: str
    ref_type: str  # observation, value_prop, icp, strategy, persona, target
    label: str = ""
    snippet: str = ""


class EmailClaim(BaseModel):
    """A factual claim used in an outbound email, end-to-end traceable.

    The writer **declares** the claim with its scope and the CONTEXT INDEX
    refs that back it (``evidence_refs``). The pipeline immediately hydrates
    those refs into ``evidence`` snippets from the same retrieval that
    produced the email. The guardrail then sets ``grounded`` / ``confidence``
    based ONLY on the claim text + its cited evidence — no re-reading the
    whole briefing, no independent extraction.
    """

    claim_id: str
    text: str
    # general = broad market/industry knowledge; no evidence check required.
    # sender / target = specific assertion that MUST be backed by retrieval.
    scope: Literal["general", "sender", "target"] = "general"
    evidence_refs: list[str] = Field(default_factory=list)
    evidence: list[StatementContextRef] = Field(default_factory=list)
    # Guardrail verdict (None = not yet judged).
    grounded: bool | None = None
    confidence: float | None = None
    # Free-form short reason from the judge; surfaced in the UI for unsafe
    # claims so the operator understands the failure mode.
    reason: str = ""


class EmailSafetyReport(BaseModel):
    """Aggregate guardrail verdict for an email.

    Per-claim verdicts live on ``Email.claims[i].grounded`` /
    ``Email.claims[i].confidence``. This object only carries the email-level
    roll-up so analytics and the UI can show a single safe / unsafe state.
    """

    is_safe: bool = True
    confidence: float = 0.0
    verification_ok: bool = True
    email_regenerated: bool = False
    regeneration_count: int = 0


class Email(BaseModel):
    email_id: str
    angle: AngleType
    subject: str
    body: str
    # Claims the writer declared (and the guardrail verified). Single
    # source of truth for the UI; never two parallel lists.
    claims: list[EmailClaim] = Field(default_factory=list)
    safety: EmailSafetyReport | None = None


# ---------- Persona input ----------

class PersonaInput(BaseModel):
    role: str
    seniority: Seniority
    # Optional recipient name. When empty, the writer must open with a plain
    # "Hi," — never a placeholder like "Hi [name]".
    name: str | None = None


# ---------- Run summary ----------

class LlmUsageByPurpose(BaseModel):
    """One bucket of LLM usage grouped by call purpose (e.g. extraction, writer)."""

    purpose: str
    label: str
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0


class RunMetrics(BaseModel):
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    llm_calls: int = 0
    llm_usage_by_purpose: list[LlmUsageByPurpose] = Field(default_factory=list)
    pages_fetched: int = 0
    sections_created: int = 0
    observations_extracted: int = 0
    observations_validated: int = 0
    observations_rejected: int = 0
    compression_ratio: float = 0.0
    raw_cleaned_chars: int = 0
    evidence_chars_used: int = 0
    # Email-level safety roll-up across all generated emails.
    declared_claims_count: int = 0
    email_claims_count: int = 0
    unsupported_claims_count: int = 0
    safety_confidence_avg: float | None = None
    email_regenerated: bool = False
    regeneration_count: int = 0
    emails_safe_count: int = 0
    emails_total: int = 0
    final_email_safe: bool = True
    verification_ok: bool = True
    angle_overlap: float | None = None
    observation_validation_rate: float | None = None
    planner_decisions: list[dict[str, Any]] = Field(default_factory=list)
    stages: list[dict[str, Any]] = Field(default_factory=list)


# ---------- API request/response ----------

class SenderRequest(BaseModel):
    sender_url: str


class SenderResponse(BaseModel):
    company_id: str
    sender_url: str
    icp: ICP
    value_proposition: ValueProposition  # primary / highest-confidence VP
    value_propositions: list[ValueProposition] = Field(default_factory=list)
    observations: list[Observation]
    metrics: RunMetrics
    # Populated automatically after sender synthesis when target discovery runs.
    suggested_targets: SuggestedTargetsResponse | None = None


class TargetRequest(BaseModel):
    sender_company_id: str
    target_url: str
    persona: PersonaInput
    # Optional: when present, the run is associated to a stored persona
    # (so the produced strategy/emails are linked back to that persona row
    # in the UI). If absent, the inline ``persona`` payload is used and
    # the run is treated as a one-shot (no DB-side persona link).
    persona_id: str | None = None


class TargetResponse(BaseModel):
    target_company_id: str
    target_url: str
    sender_company_id: str
    persona: PersonaInput
    observations: list[Observation]
    strategy: StrategyArtifact
    emails: list[Email]
    metrics: RunMetrics
    # Resolved value proposition used to drive this target's strategy + emails.
    # When multiple VPs exist on the sender, this is the one the strategy
    # selected. Always populated when sender VPs exist so the frontend never
    # needs to guess.
    selected_value_proposition: ValueProposition | None = None
    # All sender value propositions in scope at the time of this run, so the
    # UI can show "alternatives" alongside the selected one.
    sender_value_propositions: list[ValueProposition] = Field(default_factory=list)


# ---------- Target discovery (post-sender suggestions) ----------

class DiscoveryEvidence(BaseModel):
    """A web-search citation used to justify a suggested target."""

    url: str
    title: str = ""
    snippet: str = ""


class SuggestedPersona(BaseModel):
    """A role/title hypothesis for outreach. Names are only set when a
    well-sourced public reference exists; otherwise we keep it role-only.
    """

    title: str  # role or title, e.g. "VP of Engineering"
    seniority: Seniority
    name: str | None = None  # only when clearly public + well sourced
    rationale: str = ""


class SuggestedTarget(BaseModel):
    company_name: str
    domain: str  # canonical apex domain, e.g. "acme.com"
    homepage_url: str
    fit_rationale: str  # 1-2 sentences explaining why it fits the ICP/VP
    matched_value_proposition_id: str | None = None
    matched_value_proposition_label: str = ""
    confidence: Literal["high", "medium", "low"] = "medium"
    evidence: list[DiscoveryEvidence] = Field(default_factory=list)
    personas: list[SuggestedPersona] = Field(default_factory=list)


class SuggestedTargetsResponse(BaseModel):
    """Result of running OpenAI web search-backed target discovery for a
    sender. ``status`` distinguishes between healthy results, weak / no
    matches, and an unavailable provider so the UI can render a clean
    empty/error state without inferring from an empty list."""

    sender_company_id: str
    provider: str = ""
    queries: list[str] = Field(default_factory=list)
    suggestions: list[SuggestedTarget] = Field(default_factory=list)
    status: Literal["ok", "weak", "unavailable", "error"] = "ok"
    message: str = ""
