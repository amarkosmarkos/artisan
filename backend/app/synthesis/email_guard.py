"""Email safety guard (single-pass LLM judge).

The writer produces subject + body only. This module runs ONE LLM call per
email that simultaneously:

  1. extracts the most important factual / checkable statements,
  2. classifies each statement into one of four categories
     (``target_fact``, ``sender_or_value_prop``, ``generic_or_rhetorical``,
     ``cta``),
  3. judges each statement against the same briefing the writer received.

Safety contract (deliberately narrow):

  - Only ``target_fact`` statements can mark an email unsafe.
  - ``sender_or_value_prop`` claims are checked softly against sender
    context; missing sender refs yields ``sender_context_not_verified`` —
    never a failure.
  - ``generic_or_rhetorical`` and ``cta`` are ``not_checkable`` and never
    cause failures.

Regeneration only fires when at least one ``target_fact`` is unsupported
or contradicted, capped at one pass.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..db import fetchall
from ..pipeline import validate as obs_validate
from ..schemas import (
    Angle,
    ClaimMapEntry,
    Email,
    EmailSafetyReport,
    ICP,
    Observation,
    PersonaInput,
    StatementCategory,
    StatementContextRef,
    StatementSupportStatus,
    StrategyArtifact,
    ValueProposition,
    VerifiedStatement,
)
from ..services.llm import LLMClient, UsageAccumulator
from . import writer as writer_mod

log = logging.getLogger(__name__)

# Hard limits keep one judge call cheap and snappy.
MAX_REGENERATIONS = 1
_MAX_STATEMENTS = 6
_MAX_CONTEXT_CHARS = 18000

_VALID_STATUSES = {s.value for s in StatementSupportStatus}
_VALID_CATEGORIES = {c.value for c in StatementCategory}


@dataclass
class GuardContext:
    """The slice of workflow context the writer actually consumed."""

    target_observations: list[Observation] = field(default_factory=list)
    sender_observations: list[Observation] = field(default_factory=list)
    sender_evidence: list[Observation] = field(default_factory=list)
    sender_icp: ICP | None = None
    sender_vp: ValueProposition | None = None
    strategy: StrategyArtifact | None = None
    persona: PersonaInput | None = None
    target_company_name: str = ""


# ---------- LLM schema ----------


class _JudgedStatement(BaseModel):
    text: str = Field(min_length=4, max_length=500)
    category: str
    status: str
    rationale: str = ""
    context_refs: list[str] = Field(default_factory=list)


class _JudgeResult(BaseModel):
    statements: list[_JudgedStatement] = Field(
        default_factory=list, max_length=_MAX_STATEMENTS
    )


class _RegeneratedEmail(BaseModel):
    subject: str = Field(max_length=180)
    body: str = Field(max_length=2200)


# ---------- System prompts ----------


_SYSTEM_JUDGE = f"""You are a fact-checking judge for B2B outbound sales emails.

You will be given:
- WRITER BRIEFING: the exact briefing the writer received (target company,
  recipient name, sender value proposition, sender evidence, ICP, strategy
  angle with grounding observations, additional target observations,
  persona).
- EMAIL UNDER REVIEW: subject + body.
- CONTEXT INDEX: numbered [ref_id] entries with snippets. This is the
  complete set of premises you may cite.

YOUR JOB:
Pick at most {_MAX_STATEMENTS} factual / checkable statements from the
email body. For each, return its CATEGORY, STATUS, a short RATIONALE, and
optional CONTEXT_REFS.

CATEGORIES:
- target_fact: an assertion about the recipient (target) company — its
  facts, events, capabilities, hires, customers, tech stack, intent, or
  inferred pain. Example: "Anthropic focuses on scalable oversight."
- sender_or_value_prop: an assertion about the SENDER, its product, its
  customers, its mechanism, or its outcomes. Example: "At Multiverse
  Computing, we specialize in AI model compression."
- generic_or_rhetorical: vague commercial language with no concrete fact
  ("this could improve operational efficiency", "every exception matters",
  "outcomes that matter at scale").
- cta: greeting, sign-off, meeting-ask, or "let me know if…" phrasing.

PICK CRITERIA — prefer in this order, return at most {_MAX_STATEMENTS}:
1. target_fact statements (always include them all if there are any).
2. sender_or_value_prop statements with a specific, concrete claim.
3. generic_or_rhetorical and cta statements ONLY if you have room left.
   Skip them if you already have {_MAX_STATEMENTS} more important entries.

STATUS VALUES:
- "supported": at least one CONTEXT INDEX entry materially supports the
  statement. Paraphrases, near-paraphrases, and reasonable summarizations
  COUNT AS SUPPORTED.
- "contradicted": at least one CONTEXT INDEX entry materially disputes
  the statement. Always wins over supported.
- "unsupported": the statement asserts a checkable fact that NO CONTEXT
  INDEX entry materially supports or contradicts.
- "sender_context_not_verified": ONLY for sender_or_value_prop statements
  when no CONTEXT INDEX entry materially supports them. Use this INSTEAD
  of "unsupported" for sender claims. Treat it as informational, not as
  a failure.
- "not_checkable": for generic_or_rhetorical and cta. Always.

HARD RULES:
- target_fact statements MUST be one of: supported, unsupported,
  contradicted. Never "sender_context_not_verified".
- sender_or_value_prop statements MUST be one of: supported, contradicted,
  sender_context_not_verified. Never "unsupported".
- generic_or_rhetorical and cta MUST be "not_checkable".
- "supported" and "contradicted" require at least one valid CONTEXT INDEX
  ref_id in context_refs. Every other status must have empty context_refs.
- Only cite ref_ids that appear in CONTEXT INDEX. Never invent ref_ids.
- Each `text` must be a verbatim or near-verbatim quote of the email body.

OUTPUT (strict JSON):
{{
  "statements": [
    {{
      "text": "...",
      "category": "target_fact|sender_or_value_prop|generic_or_rhetorical|cta",
      "status": "supported|unsupported|contradicted|sender_context_not_verified|not_checkable",
      "rationale": "one short sentence",
      "context_refs": ["vp:xxx:outcome"]
    }}
  ]
}}
"""


_SYSTEM_REGENERATE = """You rewrite a B2B outbound sales email so it stays natural and preserves intent,
but removes or softens every unsupported or contradicted TARGET-SPECIFIC factual
statement.

Rules:
- Output only subject and body (plain text body).
- Do NOT patch individual sentences in isolation; produce one coherent email.
- Only include target-specific facts that appear in the provided context.
- Sender value proposition framing is allowed when not contradicting context.
- Do not invent new target facts, pain, intent, or urgency.
- If target-specific context is thin, write a conservative exploratory note.
- Keep similar length and tone to the original.

Return JSON: { "subject": "...", "body": "..." }
"""


# ---------- DB helpers ----------


def load_observations_for_company(company_id: str) -> list[Observation]:
    rows = fetchall(
        "SELECT observation_id, company_id, section_id, kind, text, confidence, "
        "validation, validation_score "
        "FROM observations WHERE company_id = ? ORDER BY rowid",
        (company_id,),
    )
    out: list[Observation] = []
    for row in rows:
        try:
            out.append(
                Observation(
                    observation_id=str(row["observation_id"]),
                    company_id=str(row["company_id"]),
                    section_id=str(row["section_id"]),
                    kind=str(row["kind"]),
                    text=str(row["text"]),
                    confidence=float(row["confidence"]),
                    validation=row["validation"],
                    validation_score=row["validation_score"],
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return out


# ---------- Context index ----------


def _icp_field_lines(icp: ICP) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for key, fld in (
        ("icp:industries", icp.target_industries),
        ("icp:sizes", icp.size_bands),
        ("icp:buyers", icp.likely_buyers),
        ("icp:triggers", icp.common_triggers),
        ("icp:negative", icp.negative_icp),
    ):
        if fld.values:
            rows.append((key, "; ".join(fld.values)))
    return rows


def _safe_target_observations(ctx: GuardContext) -> list[Observation]:
    try:
        return obs_validate.filter_for_synthesis(ctx.target_observations)
    except Exception:  # noqa: BLE001
        return list(ctx.target_observations)


def build_context_index(ctx: GuardContext) -> tuple[str, dict[str, StatementContextRef]]:
    """Build a numbered context document and ref lookup for verification.

    Only sources the writer actually consumed go in (VP, strategy, sender
    evidence subset, ICP, filtered target observations, persona, identity).
    No sections. No full sender-obs bag.
    """
    refs: dict[str, StatementContextRef] = {}
    lines: list[str] = ["CONTEXT INDEX:"]
    total_chars = len(lines[0])

    def add(ref_id: str, ref_type: str, label: str, snippet: str) -> None:
        nonlocal total_chars
        snippet = snippet.strip()
        if not snippet:
            return
        snippet = snippet[:400]
        line = f"[{ref_id}] ({ref_type}) {label}: {snippet}"
        if total_chars + len(line) + 1 > _MAX_CONTEXT_CHARS:
            return
        refs[ref_id] = StatementContextRef(
            ref_id=ref_id,
            ref_type=ref_type,
            label=label,
            snippet=snippet,
        )
        lines.append(line)
        total_chars += len(line) + 1

    if ctx.sender_vp:
        vp = ctx.sender_vp
        vp_key = vp.id or "primary"
        for ref_id, label, val in (
            (f"vp:{vp_key}:label", "VP label", vp.label),
            (f"vp:{vp_key}:customer", "VP customer", vp.customer),
            (f"vp:{vp_key}:pain", "VP pain", vp.pain),
            (f"vp:{vp_key}:outcome", "VP outcome", vp.outcome),
            (f"vp:{vp_key}:mechanism", "VP mechanism", vp.mechanism),
        ):
            add(ref_id, "value_prop", label, val)

    if ctx.strategy:
        fa = ctx.strategy.fit_assessment
        add("strategy:fit", "strategy", "fit level", fa.level.value)
        if fa.reasons:
            add("strategy:fit_reasons", "strategy", "fit reasons", "; ".join(fa.reasons))
        if ctx.strategy.messaging_angle:
            add(
                "strategy:messaging_angle",
                "strategy",
                "messaging angle",
                ctx.strategy.messaging_angle,
            )
        if ctx.strategy.selection_reason:
            add(
                "strategy:selection_reason",
                "strategy",
                "VP selection",
                ctx.strategy.selection_reason,
            )
        for angle in ctx.strategy.strategy.angles:
            add(
                f"strategy:angle:{angle.type.value}",
                "strategy",
                f"angle {angle.type.value}",
                angle.hypothesis,
            )

    for obs in ctx.sender_evidence:
        add(
            f"sender:{obs.observation_id}",
            "observation",
            f"sender {obs.kind}",
            obs.text,
        )

    if ctx.sender_icp:
        for ref_id, text in _icp_field_lines(ctx.sender_icp):
            add(ref_id, "icp", ref_id.replace("icp:", ""), text)

    for obs in _safe_target_observations(ctx):
        add(
            obs.observation_id,
            "observation",
            f"target {obs.kind}",
            obs.text,
        )

    if ctx.persona:
        add("persona:role", "persona", "role", ctx.persona.role)
        add("persona:seniority", "persona", "seniority", ctx.persona.seniority.value)
    if ctx.target_company_name:
        add("target:name", "target", "target company name", ctx.target_company_name)

    return "\n".join(lines), refs


# ---------- Briefing renderer (same view as writer) ----------


def _angle_for_email(
    email: Email, strategy: StrategyArtifact | None
) -> Angle | None:
    if not strategy:
        return None
    for angle in strategy.strategy.angles:
        if angle.type == email.angle:
            return angle
    return None


def _format_writer_briefing(email: Email, *, ctx: GuardContext) -> str:
    company = (ctx.target_company_name or "").strip() or "(unknown)"
    recipient = (
        (ctx.persona.name or "").strip()
        if ctx.persona and ctx.persona.name
        else ""
    ) or "(none)"
    vp = ctx.sender_vp or ValueProposition()
    icp = ctx.sender_icp or ICP()
    angle = _angle_for_email(email, ctx.strategy)
    angle_block = ""
    if angle:
        obs_by_id = {o.observation_id: o for o in _safe_target_observations(ctx)}
        grounding = "\n".join(
            f"    - {ref} [{obs_by_id[ref].kind}]: {obs_by_id[ref].text}"
            for ref in angle.evidence_refs
            if ref in obs_by_id
        ) or "    (none)"
        angle_block = (
            "STRATEGY ANGLE FOR THIS EMAIL:\n"
            f"- type: {angle.type.value}\n"
            f"- hypothesis: {angle.hypothesis}\n"
            f"- angle_grounding_observations:\n{grounding}\n"
        )

    persona_block = ""
    if ctx.persona:
        persona_block = (
            "PERSONA:\n"
            f"- name: {recipient}\n"
            f"- role: {ctx.persona.role}\n"
            f"- seniority: {ctx.persona.seniority.value}\n"
        )

    sender_ev_lines = "\n".join(
        f"- {o.observation_id} [{o.kind}, conf={o.confidence:.2f}]: {o.text}"
        for o in ctx.sender_evidence
    ) or "(no sender evidence available)"

    target_obs_lines = "\n".join(
        f"- {o.observation_id} [{o.kind}, conf={o.confidence:.2f}]: {o.text}"
        for o in _safe_target_observations(ctx)
    ) or "(none)"

    icp_line = (
        f"industries={icp.target_industries.values}; "
        f"sizes={icp.size_bands.values}; "
        f"buyers={icp.likely_buyers.values}; "
        f"triggers={icp.common_triggers.values}"
    )

    return (
        f"TARGET COMPANY NAME: {company}\n"
        f"RECIPIENT NAME: {recipient}\n\n"
        "SENDER VALUE PROPOSITION:\n"
        f"- label:     {vp.label}\n"
        f"- customer:  {vp.customer}\n"
        f"- pain:      {vp.pain}\n"
        f"- outcome:   {vp.outcome}\n"
        f"- mechanism: {vp.mechanism}\n\n"
        f"SENDER EVIDENCE:\n{sender_ev_lines}\n\n"
        f"SENDER ICP: {icp_line}\n\n"
        f"{angle_block}\n"
        f"ADDITIONAL TARGET OBSERVATIONS:\n{target_obs_lines}\n\n"
        f"{persona_block}"
    )


# ---------- Judge ----------


def _coerce_category(raw: str) -> StatementCategory:
    norm = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if norm in _VALID_CATEGORIES:
        return StatementCategory(norm)
    return StatementCategory.GENERIC_OR_RHETORICAL


def _coerce_status(raw: str) -> StatementSupportStatus | None:
    norm = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    if norm in _VALID_STATUSES:
        return StatementSupportStatus(norm)
    return None


def _coerce_refs(
    refs: list[str], ref_index: dict[str, StatementContextRef]
) -> list[StatementContextRef]:
    seen: set[str] = set()
    out: list[StatementContextRef] = []
    for r in refs or []:
        if not isinstance(r, str):
            continue
        ref_id = r.strip()
        if not ref_id or ref_id in seen:
            continue
        ref = ref_index.get(ref_id)
        if ref is None:
            continue
        seen.add(ref_id)
        out.append(ref)
    return out


def _reconcile(
    *,
    category: StatementCategory,
    status: StatementSupportStatus | None,
    refs: list[StatementContextRef],
) -> tuple[StatementSupportStatus, list[StatementContextRef], str | None]:
    """Apply the safety contract to a judge row.

    Returns the cleaned ``(status, refs, override_rationale)``. The
    override rationale is set when we had to correct the judge's output.
    """
    if category == StatementCategory.CTA:
        return StatementSupportStatus.NOT_CHECKABLE, [], None
    if category == StatementCategory.GENERIC_OR_RHETORICAL:
        return StatementSupportStatus.NOT_CHECKABLE, [], None

    if category == StatementCategory.SENDER_OR_VALUE_PROP:
        # Sender claims may only be supported, contradicted, or
        # explicitly "sender_context_not_verified".
        if status == StatementSupportStatus.SUPPORTED and refs:
            return StatementSupportStatus.SUPPORTED, refs, None
        if status == StatementSupportStatus.CONTRADICTED and refs:
            return StatementSupportStatus.CONTRADICTED, refs, None
        # No valid evidence: it's sender positioning, that's fine.
        return (
            StatementSupportStatus.SENDER_CONTEXT_NOT_VERIFIED,
            [],
            "Sender / value-prop positioning; no sender context found to verify against.",
        )

    # category == TARGET_FACT
    if status == StatementSupportStatus.SUPPORTED and refs:
        return StatementSupportStatus.SUPPORTED, refs, None
    if status == StatementSupportStatus.CONTRADICTED and refs:
        return StatementSupportStatus.CONTRADICTED, refs, None
    # Anything else for a target_fact (including "sender_context_not_verified",
    # which is invalid here) → unsupported.
    if status == StatementSupportStatus.SUPPORTED and not refs:
        return (
            StatementSupportStatus.UNSUPPORTED,
            [],
            "Judge marked supported but cited no valid CONTEXT INDEX ref.",
        )
    return StatementSupportStatus.UNSUPPORTED, [], None


def _judge_email(
    email: Email,
    *,
    ctx: GuardContext,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> tuple[list[VerifiedStatement], bool]:
    """Single LLM call: extract + classify + judge.

    Returns ``(statements, verification_ok)``. ``verification_ok`` is
    False only when the judge call itself failed.
    """
    context_doc, ref_index = build_context_index(ctx)
    briefing = _format_writer_briefing(email, ctx=ctx)
    user = (
        f"WRITER BRIEFING:\n{briefing}\n\n"
        "EMAIL UNDER REVIEW:\n"
        f"Subject: {email.subject}\n"
        f"Body:\n{email.body}\n\n"
        f"{context_doc}\n\n"
        f"Pick at most {_MAX_STATEMENTS} factual / checkable statements "
        "and judge each."
    )
    try:
        result = llm.structured(
            system=_SYSTEM_JUDGE,
            user=user,
            schema=_JudgeResult,
            purpose="email_guard_judge",
            usage=usage,
            temperature=0.0,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("email_guard judge failed email=%s: %s", email.email_id, e)
        return [], False

    verified: list[VerifiedStatement] = []
    for row in result.statements[:_MAX_STATEMENTS]:
        text = row.text.strip()
        if not text:
            continue
        category = _coerce_category(row.category)
        raw_status = _coerce_status(row.status)
        refs = _coerce_refs(row.context_refs, ref_index)
        status, refs, override = _reconcile(
            category=category, status=raw_status, refs=refs
        )
        rationale = override or (row.rationale or "").strip() or "(no rationale)"
        verified.append(
            VerifiedStatement(
                statement_id=f"stmt_{uuid.uuid4().hex[:10]}",
                text=text,
                category=category,
                status=status,
                context_refs=refs,
                rationale=rationale,
            )
        )

    return verified, True


# ---------- Reporting ----------


def _failed_target_facts(statements: list[VerifiedStatement]) -> list[str]:
    """The ONLY thing that can mark an email unsafe."""
    return [
        s.text
        for s in statements
        if s.category == StatementCategory.TARGET_FACT
        and s.status
        in (
            StatementSupportStatus.UNSUPPORTED,
            StatementSupportStatus.CONTRADICTED,
        )
    ]


def _build_safety_report(
    statements: list[VerifiedStatement],
    *,
    email_regenerated: bool,
    regeneration_count: int,
    verification_ok: bool,
) -> EmailSafetyReport:
    failed = _failed_target_facts(statements)
    final_email_safe = verification_ok and not failed
    return EmailSafetyReport(
        statements=statements,
        email_regenerated=email_regenerated,
        regeneration_count=regeneration_count,
        final_email_safe=final_email_safe,
        failed_statements=failed,
        verification_ok=verification_ok,
    )


def verify_email_body(
    email: Email,
    *,
    ctx: GuardContext,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> EmailSafetyReport:
    statements, verification_ok = _judge_email(
        email, ctx=ctx, llm=llm, usage=usage
    )
    return _build_safety_report(
        statements,
        email_regenerated=False,
        regeneration_count=0,
        verification_ok=verification_ok,
    )


# ---------- Regeneration (target_fact failures only) ----------


def regenerate_email(
    email: Email,
    *,
    failed: list[str],
    ctx: GuardContext,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> Email:
    context_doc, _ = build_context_index(ctx)
    failed_block = "\n".join(f"- {t}" for t in failed) or "(none)"
    briefing = _format_writer_briefing(email, ctx=ctx)
    user = (
        f"WRITER BRIEFING:\n{briefing}\n\n"
        f"ORIGINAL SUBJECT: {email.subject}\n\n"
        f"ORIGINAL BODY:\n{email.body}\n\n"
        f"TARGET-SPECIFIC STATEMENTS THAT FAILED VERIFICATION "
        f"(must not reappear as facts):\n{failed_block}\n\n"
        f"{context_doc}\n\n"
        "Rewrite the full email now."
    )
    try:
        draft = llm.structured(
            system=_SYSTEM_REGENERATE,
            user=user,
            schema=_RegeneratedEmail,
            purpose="email_guard_regenerate",
            usage=usage,
            temperature=0.2,
        )
        return email.model_copy(
            update={
                "subject": draft.subject.strip() or email.subject,
                "body": draft.body.strip() or email.body,
            }
        )
    except Exception as e:  # noqa: BLE001
        log.warning("email_guard regenerate failed email=%s: %s", email.email_id, e)
        return email


def _attach_report(
    email: Email,
    report: EmailSafetyReport,
    *,
    regenerated: bool,
    regen_count: int,
) -> Email:
    updated = report.model_copy(
        update={
            "email_regenerated": regenerated,
            "regeneration_count": regen_count,
        }
    )
    return email.model_copy(update={"safety": updated})


def guard_email(
    email: Email,
    *,
    ctx: GuardContext,
    llm: LLMClient,
    usage: UsageAccumulator,
) -> Email:
    """Judge once. Regenerate at most once and ONLY if a target_fact failed."""
    regen_count = 0
    regenerated = False
    current = email

    for attempt in range(MAX_REGENERATIONS + 1):
        report = verify_email_body(current, ctx=ctx, llm=llm, usage=usage)

        if not report.verification_ok:
            log.warning(
                "email_guard judge unavailable for email=%s; marking unsafe",
                current.email_id,
            )
            return _attach_report(
                current, report, regenerated=regenerated, regen_count=regen_count
            )

        if report.final_email_safe:
            return _attach_report(
                current, report, regenerated=regenerated, regen_count=regen_count
            )

        # final_email_safe is False only because of target_fact failures
        # (sender / generic / cta cannot mark the email unsafe).
        if attempt >= MAX_REGENERATIONS:
            return _attach_report(
                current, report, regenerated=regenerated, regen_count=regen_count
            )

        current = regenerate_email(
            current,
            failed=report.failed_statements,
            ctx=ctx,
            llm=llm,
            usage=usage,
        )
        regenerated = True
        regen_count += 1

    return current


# ---------- Analytics ----------


def build_statement_map(emails: list[Email]) -> list[ClaimMapEntry]:
    entries: list[ClaimMapEntry] = []
    for email in emails:
        safety = email.safety
        if not safety:
            continue
        for stmt in safety.statements:
            citations: list[dict[str, str]] = []
            for ref in stmt.context_refs:
                url = ref.label if ref.label.startswith("http") else ""
                citations.append(
                    {
                        "url": url,
                        "snippet": ref.snippet[:280] if ref.snippet else ref.label,
                    }
                )
            entries.append(
                ClaimMapEntry(
                    claim_id=stmt.statement_id,
                    email_id=email.email_id,
                    angle=email.angle,
                    text=stmt.text,
                    category=stmt.category,
                    status=stmt.status,
                    nli_score=stmt.nli_score,
                    citations=citations,
                )
            )
    return entries


def accumulate_statement_metrics(
    emails: list[Email],
) -> dict[str, int | bool | list[str]]:
    """Aggregate statement verification tallies across emails.

    ``final_email_safe`` only reflects ``target_fact`` outcomes — sender
    positioning and rhetoric never fail an email.
    """
    extracted = supported = unsupported = contradicted = not_checkable = 0
    sender_unverified = 0
    regeneration_count = 0
    email_regenerated = False
    final_email_safe = True
    verification_ok = True
    failed: list[str] = []

    for email in emails:
        safety = email.safety
        if not safety:
            continue
        if safety.email_regenerated:
            email_regenerated = True
        regeneration_count += safety.regeneration_count
        if not safety.final_email_safe:
            final_email_safe = False
        if not safety.verification_ok:
            verification_ok = False
        failed.extend(safety.failed_statements)
        for stmt in safety.statements:
            extracted += 1
            if stmt.status == StatementSupportStatus.SUPPORTED:
                supported += 1
            elif stmt.status == StatementSupportStatus.UNSUPPORTED:
                unsupported += 1
            elif stmt.status == StatementSupportStatus.CONTRADICTED:
                contradicted += 1
            elif stmt.status == StatementSupportStatus.SENDER_CONTEXT_NOT_VERIFIED:
                sender_unverified += 1
            else:
                not_checkable += 1

    return {
        "extracted_statements_count": extracted,
        "supported_statements_count": supported,
        "unsupported_statements_count": unsupported,
        "contradicted_statements_count": contradicted,
        "not_checkable_statements_count": not_checkable + sender_unverified,
        "email_regenerated": email_regenerated,
        "regeneration_count": regeneration_count,
        "final_email_safe": final_email_safe,
        "verification_ok": verification_ok,
        "failed_statements": failed,
    }


# Re-export the writer's curated sender-evidence helper so the email_guard
# node can reproduce the writer's exact subset without touching writer
# internals.
select_sender_evidence_for_verifier = writer_mod._select_sender_evidence
