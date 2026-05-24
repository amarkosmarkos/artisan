// Mirror of backend Pydantic schemas. Kept lean -- only what the UI uses.

export type FitLevel = "strong" | "plausible" | "weak" | "none";
export type ContactDecision = "contact" | "wait_for_trigger" | "skip";
export type ClaimStatus =
  | "entailed"
  | "neutral"
  | "contradicted"
  | "unsupported"
  | "repaired";

export type StatementSupportStatus =
  | "supported"
  | "unsupported"
  | "contradicted"
  | "not_checkable"
  | "sender_context_not_verified";

export type StatementCategory =
  | "target_fact"
  | "sender_or_value_prop"
  | "generic_or_rhetorical"
  | "cta";
export type AngleType = "pain_led" | "trigger_led" | "outcome_led";
export type PlannerDecision =
  | "continue"
  | "fetch_more"
  | "web_search"
  | "proceed_low_confidence"
  | "stop";
export type Seniority =
  | "ic"
  | "manager"
  | "director"
  | "vp"
  | "c_level"
  | "founder";

export interface FieldWithEvidence {
  values: string[];
  confidence: number;
  evidence_refs: string[];
}

export interface ICP {
  target_industries: FieldWithEvidence;
  size_bands: FieldWithEvidence;
  likely_buyers: FieldWithEvidence;
  common_triggers: FieldWithEvidence;
  negative_icp: FieldWithEvidence;
}

export interface ValueProposition {
  id?: string;
  label?: string;
  customer: string;
  pain: string;
  outcome: string;
  mechanism: string;
  confidence: number;
  evidence_refs: string[];
}

export interface Observation {
  observation_id: string;
  company_id: string;
  kind: string;
  text: string;
  section_id: string;
  confidence: number;
  validation: "entailed" | "neutral" | "contradicted" | null;
  validation_score: number | null;
}

export interface FitAssessment {
  level: FitLevel;
  reasons: string[];
  risks: string[];
  missing_evidence: string[];
}

export interface PersonaAlignment {
  role_relevance: "high" | "medium" | "low";
  role_relevance_reason?: string;
  preferred_framing: string;
  preferred_framing_reason?: string;
  avoid: string[];
  avoid_reason?: string;
}

export interface Angle {
  type: AngleType;
  hypothesis: string;
  evidence_refs: string[];
}

export interface Strategy {
  contact_decision: ContactDecision;
  angles: Angle[];
  persona_alignment: PersonaAlignment;
}

export interface StrategyArtifact {
  fit_assessment: FitAssessment;
  strategy: Strategy;
  selected_value_proposition_id?: string | null;
  selected_value_proposition_label?: string;
  selection_reason?: string;
  messaging_angle?: string;
}

export interface StatementContextRef {
  ref_id: string;
  ref_type: string;
  label: string;
  snippet: string;
}

export interface VerifiedStatement {
  statement_id: string;
  text: string;
  category: StatementCategory;
  status: StatementSupportStatus;
  nli_score: number | null;
  context_refs: StatementContextRef[];
  rationale: string;
}

export interface EmailSafetyReport {
  statements: VerifiedStatement[];
  email_regenerated: boolean;
  regeneration_count: number;
  final_email_safe: boolean;
  verification_ok: boolean;
  failed_statements: string[];
}

export interface Email {
  email_id: string;
  angle: AngleType;
  subject: string;
  body: string;
  safety?: EmailSafetyReport | null;
}

export interface ClaimMapEntry {
  claim_id: string;
  email_id: string;
  angle: AngleType;
  text: string;
  category: StatementCategory;
  status: StatementSupportStatus;
  nli_score: number | null;
  citations: { url: string; snippet: string }[];
}

export interface RunMetrics {
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  pages_fetched: number;
  sections_created: number;
  observations_extracted: number;
  observations_validated: number;
  observations_rejected: number;
  compression_ratio: number;
  raw_cleaned_chars: number;
  evidence_chars_used: number;
  extracted_statements_count: number;
  supported_statements_count: number;
  unsupported_statements_count: number;
  contradicted_statements_count: number;
  not_checkable_statements_count: number;
  evidence_support_rate: number | null;
  email_regenerated: boolean;
  regeneration_count: number;
  final_email_safe: boolean;
  verification_ok: boolean;
  failed_statements: string[];
  claims_total: number;
  claims_supported: number;
  claims_unsupported: number;
  claims_contradicted: number;
  angle_overlap: number | null;
  claim_support_rate: number | null;
  unsupported_claim_rate: number | null;
  observation_validation_rate: number | null;
  planner_decisions: Array<{
    task: string;
    decision: PlannerDecision;
    reason: string;
    missing_fields?: string[];
    suggested_internal_pages?: string[];
    suggested_queries?: string[];
  }>;
  stages: Array<{ name: string; duration_ms: number; detail: Record<string, unknown> }>;
}

export interface SenderResponse {
  company_id: string;
  sender_url: string;
  icp: ICP;
  value_proposition: ValueProposition;
  value_propositions?: ValueProposition[];
  observations: Observation[];
  metrics: RunMetrics;
}

export interface PersonaInput {
  role: string;
  seniority: Seniority;
  name?: string | null;
}

export interface TargetResponse {
  target_company_id: string;
  target_url: string;
  sender_company_id: string;
  persona: PersonaInput;
  observations: Observation[];
  strategy: StrategyArtifact;
  emails: Email[];
  claim_map: ClaimMapEntry[];
  metrics: RunMetrics;
  selected_value_proposition?: ValueProposition | null;
  sender_value_propositions?: ValueProposition[];
}

export interface ProgressEvent {
  ts: number;
  id: string;
  stage: string;
  detail: Record<string, unknown>;
}

// ---------- Target discovery (post-sender suggestions) ----------

export interface DiscoveryEvidence {
  url: string;
  title: string;
  snippet: string;
}

export interface SuggestedPersona {
  title: string;
  seniority: Seniority | null;
  name: string | null;
  rationale: string;
}

export type DiscoveryConfidence = "high" | "medium" | "low";

export interface SuggestedTarget {
  company_name: string;
  domain: string;
  homepage_url: string;
  fit_rationale: string;
  matched_value_proposition_id: string | null;
  matched_value_proposition_label: string;
  confidence: DiscoveryConfidence;
  evidence: DiscoveryEvidence[];
  personas: SuggestedPersona[];
}

export type DiscoveryStatus = "ok" | "weak" | "unavailable" | "error";

export interface SuggestedTargetsResponse {
  sender_company_id: string;
  provider: string;
  queries: string[];
  suggestions: SuggestedTarget[];
  status: DiscoveryStatus;
  message: string;
}
