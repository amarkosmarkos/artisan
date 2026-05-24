import type {
  ICP,
  PersonaInput,
  ProgressEvent,
  SenderResponse,
  SuggestedTargetsResponse,
  TargetResponse,
  ValueProposition,
  StrategyArtifact,
  Email,
  ClaimMapEntry,
} from "./types";

const API_BASE = "/api/v1";

// ---------- Dashboard / browse types ----------

export interface CompanyRow {
  company_id: string;
  url: string;
  role: "sender" | "target";
  created_at: string;
}

export interface CompanyCounts {
  pages: number;
  sections: number;
  observations: number;
}

export interface SenderDetail extends CompanyRow {
  counts: CompanyCounts;
  icp: ICP | null;
  value_proposition: ValueProposition | null;
  value_propositions?: ValueProposition[];
}

export interface PersonaRunRow {
  persona_id: string;
  name: string | null;
  role: string;
  seniority: string;
  created_at: string | null;
  strategy: {
    sender_company_id: string;
    persona: PersonaInput;
    strategy: StrategyArtifact;
    selected_value_proposition?: ValueProposition | null;
    sender_value_propositions?: ValueProposition[];
  } | null;
  emails: Email[];
  claim_map: ClaimMapEntry[];
}

export interface TargetDetail extends CompanyRow {
  counts: CompanyCounts;
  personas: PersonaRunRow[];
}

export interface EvidenceRecord {
  observation_id: string;
  text: string;
  kind: string;
  confidence: number;
  validation: string | null;
  validation_score: number | null;
  section_id: string | null;
  url: string | null;
  heading: string | null;
  snippet: string;
}

export type CompanyDetail = SenderDetail | TargetDetail;

export interface PageRow {
  page_id: string;
  url: string;
  status_code: number | null;
  cleaned_chars: number | null;
  source: string;
  fetched_at: string;
}

export interface SectionRow {
  section_id: string;
  url: string;
  heading: string | null;
  chars: number;
  source: string;
}

export interface ObservationRow {
  observation_id: string;
  kind: string;
  text: string;
  section_id: string;
  confidence: number;
  validation: string | null;
  validation_score: number | null;
}

export type RunState = "running" | "done" | "error" | "unknown";

export interface RunStatus {
  state: RunState;
  run_id: string;
  kind: "sender" | "target";
  started_at?: number;
  finished_at?: number | null;
}

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
  }
  if (res.status === 204) return undefined as unknown as T;
  return (await res.json()) as T;
}

// ---------- Dashboard / browse ----------

export async function listCompanies(opts?: {
  role?: "sender" | "target";
  q?: string;
  limit?: number;
}): Promise<{ companies: CompanyRow[] }> {
  const params = new URLSearchParams();
  if (opts?.role) params.set("role", opts.role);
  if (opts?.q) params.set("q", opts.q);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return jsonFetch(`/companies${qs ? `?${qs}` : ""}`);
}

export async function getCompanyDetail(
  companyId: string,
): Promise<CompanyDetail> {
  return jsonFetch(`/companies/${companyId}`);
}

export async function deleteCompany(companyId: string): Promise<void> {
  await jsonFetch(`/companies/${companyId}`, { method: "DELETE" });
}

export async function getCompanySources(
  companyId: string,
): Promise<{ pages: PageRow[] }> {
  return jsonFetch(`/companies/${companyId}/sources`);
}

export async function getCompanySections(
  companyId: string,
): Promise<{ sections: SectionRow[] }> {
  return jsonFetch(`/companies/${companyId}/sections`);
}

export interface SectionDetail {
  section_id: string;
  url: string;
  heading: string | null;
  text: string;
  char_start: number | null;
  char_end: number | null;
  source: string;
}

export async function getSection(
  sectionId: string,
): Promise<SectionDetail> {
  return jsonFetch(`/sections/${sectionId}`);
}

export async function getCompanyObservations(
  companyId: string,
): Promise<{ observations: ObservationRow[] }> {
  return jsonFetch(`/companies/${companyId}/observations`);
}

export async function getTargetClaimMap(
  targetCompanyId: string,
): Promise<{ claims: ClaimMapEntry[] }> {
  return jsonFetch(`/targets/${targetCompanyId}/claim-map`);
}

export async function deleteStrategy(
  targetCompanyId: string,
  personaId?: string,
): Promise<void> {
  const qs = personaId
    ? `?persona_id=${encodeURIComponent(personaId)}`
    : "";
  await jsonFetch(`/strategies/${targetCompanyId}${qs}`, { method: "DELETE" });
}

export async function deleteEmail(emailId: string): Promise<void> {
  await jsonFetch(`/emails/${emailId}`, { method: "DELETE" });
}

export async function resolveEvidence(
  observationIds: string[],
): Promise<{ evidence: Record<string, EvidenceRecord> }> {
  if (observationIds.length === 0) return { evidence: {} };
  return jsonFetch(`/evidence/resolve`, {
    method: "POST",
    body: JSON.stringify({ observation_ids: observationIds }),
  });
}

// ---------- Sender targets + personas ----------

export interface SenderTargetRow {
  company_id: string;
  url: string;
  created_at: string;
  added_at: string;
}

export interface PersonaRow {
  persona_id: string;
  target_company_id: string;
  name: string | null;
  role: string;
  seniority: string;
  created_at: string;
}

export async function listSenderTargets(
  senderCompanyId: string,
): Promise<{ targets: SenderTargetRow[] }> {
  return jsonFetch(`/senders/${senderCompanyId}/targets`);
}

export async function addSenderTarget(
  senderCompanyId: string,
  target_url: string,
): Promise<{
  sender_company_id: string;
  target_company_id: string;
  target_url: string;
  company_created: boolean;
}> {
  return jsonFetch(`/senders/${senderCompanyId}/targets`, {
    method: "POST",
    body: JSON.stringify({ target_url }),
  });
}

export async function removeSenderTarget(
  senderCompanyId: string,
  targetCompanyId: string,
): Promise<void> {
  await jsonFetch(`/senders/${senderCompanyId}/targets/${targetCompanyId}`, {
    method: "DELETE",
  });
}

export async function discoverSenderTargets(
  senderCompanyId: string,
): Promise<SuggestedTargetsResponse> {
  return jsonFetch(`/senders/${senderCompanyId}/discover-targets`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function listPersonas(
  targetCompanyId: string,
): Promise<{ personas: PersonaRow[] }> {
  return jsonFetch(`/companies/${targetCompanyId}/personas`);
}

export async function createPersona(
  targetCompanyId: string,
  input: { role: string; seniority: string; name?: string },
): Promise<PersonaRow> {
  return jsonFetch(`/companies/${targetCompanyId}/personas`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function deletePersona(personaId: string): Promise<void> {
  await jsonFetch(`/personas/${personaId}`, { method: "DELETE" });
}

// ---------- Metrics (runs) ----------

export interface RunSummary {
  latency_ms: number | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  pages_fetched: number | null;
  sections_created: number | null;
  observations_extracted: number | null;
  observations_validated: number | null;
  observations_rejected: number | null;
  claims_total: number | null;
  claims_supported: number | null;
  claim_support_rate: number | null;
  angle_overlap: number | null;
  stages: number | null;
}

export interface RunRow {
  run_id: string;
  kind: "sender" | "target";
  company_id: string | null;
  target_company_id: string | null;
  sender_url: string | null;
  target_url: string | null;
  created_at: string;
  summary: RunSummary;
}

export interface RunDetail extends RunRow {
  metrics: import("./types").RunMetrics;
}

export interface RunsAggregate {
  total_runs: number;
  by_kind: { sender: number; target: number };
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  pages_fetched: number;
  observations_extracted: number;
  claims_total: number;
  claims_supported: number;
  claim_support_rate: number | null;
}

export async function listRuns(opts?: {
  kind?: "sender" | "target";
  limit?: number;
}): Promise<{ runs: RunRow[] }> {
  const params = new URLSearchParams();
  if (opts?.kind) params.set("kind", opts.kind);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  return jsonFetch(`/runs${qs ? `?${qs}` : ""}`);
}

export async function getRunDetail(runId: string): Promise<RunDetail> {
  return jsonFetch(`/runs/${runId}`);
}

export async function getRunsSummary(): Promise<RunsAggregate> {
  return jsonFetch(`/runs-summary`);
}

// ---------- Run lifecycle ----------

export async function startSender(senderUrl: string): Promise<{ run_id: string }> {
  return jsonFetch("/sender/start", {
    method: "POST",
    body: JSON.stringify({ sender_url: senderUrl }),
  });
}

export async function getSenderResult(runId: string): Promise<SenderResponse> {
  return jsonFetch(`/sender/${runId}/result`);
}

export async function getSenderStatus(runId: string): Promise<RunStatus> {
  return jsonFetch(`/sender/${runId}/status`);
}

export async function startTarget(input: {
  sender_company_id: string;
  target_url: string;
  persona: PersonaInput;
  persona_id?: string;
}): Promise<{ run_id: string }> {
  return jsonFetch("/target/start", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getTargetResult(runId: string): Promise<TargetResponse> {
  return jsonFetch(`/target/${runId}/result`);
}

export async function getTargetStatus(runId: string): Promise<RunStatus> {
  return jsonFetch(`/target/${runId}/status`);
}

/**
 * Subscribe to an SSE progress stream. Returns an unsubscribe function.
 */
export function streamProgress(
  kind: "sender" | "target",
  runId: string,
  onEvent: (e: ProgressEvent) => void,
  onDone: () => void,
  onError: (msg: string) => void
): () => void {
  const url = `${API_BASE}/${kind}/${runId}/stream`;
  const es = new EventSource(url);

  const handle = (ev: MessageEvent) => {
    try {
      const data = JSON.parse(ev.data) as ProgressEvent;
      onEvent(data);
      if (data.stage === "__done__") {
        es.close();
        onDone();
      } else if (data.stage === "__error__") {
        es.close();
        onError(String((data.detail as { error?: string })?.error || "unknown error"));
      }
    } catch {
      // ignore malformed payloads
    }
  };

  // sse-starlette uses named events; we listen to all of them by hooking onmessage
  // AND adding specific listeners for our terminal events.
  es.onmessage = handle;
  es.addEventListener("__done__", handle as EventListener);
  es.addEventListener("__error__", handle as EventListener);
  es.onerror = () => {
    es.close();
    onError("stream closed");
  };

  // We also have to listen to every stage event (sse-starlette emits one event-type per stage).
  // The cleanest approach is to wrap the default handler so any named event flows through.
  const originalAdd = es.addEventListener.bind(es);
  const knownStages = [
    "discover", "discover_done", "fetch", "fetch_done", "clean", "clean_done",
    "section", "section_done", "extract", "extract_progress", "extract_done",
    "validate", "validate_progress", "validate_done", "filter_done",
    "icp_pass1", "planner", "planner_done", "fetch_more", "icp", "vp",
    "strategy", "strategy_done",
    "write_emails", "write_emails_done",
    "email_guard", "email_guard_progress", "email_guard_done",
    "angle_overlap", "angle_overlap_repair", "angle_overlap_done",
    "web_search", "web_search_done",
    "extract_ws", "done",
  ];
  for (const stage of knownStages) {
    originalAdd(stage, handle as EventListener);
  }

  return () => es.close();
}
