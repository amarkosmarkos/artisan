// Stage metadata: friendly labels and ordering for the live execution timeline.

export type StageKey =
  | "discover"
  | "fetch"
  | "clean"
  | "section"
  | "extract"
  | "validate"
  | "planner"
  | "fetch_more"
  | "web_search"
  | "icp"
  | "vp"
  | "target_discovery"
  | "strategy"
  | "write_emails"
  | "email_guard"
  | "angle_overlap"
  | "done";

export interface StageDef {
  key: StageKey;
  label: string;
  hint: string;
}

export const SENDER_STAGES: StageDef[] = [
  { key: "discover",   label: "Crawl",                  hint: "BFS crawl + Markdown extraction" },
  { key: "section",    label: "Section deterministically", hint: "headings + paragraph chunks" },
  { key: "extract",    label: "Extract observations",   hint: "LLM cites section_ids" },
  { key: "validate",   label: "Validate (NLI)",         hint: "selective NLI on inferred kinds" },
  { key: "planner",    label: "Planner review",         hint: "coverage check + bounded follow-up" },
  { key: "icp",        label: "Synthesize ICP",         hint: "industries / sizes / buyers / triggers" },
  { key: "vp",         label: "Value proposition",      hint: "customer / pain / outcome / mechanism" },
  { key: "target_discovery", label: "Discover targets", hint: "web search for ICP-fit companies + roles" },
  { key: "done",       label: "Done",                   hint: "" },
];

export const TARGET_STAGES: StageDef[] = [
  { key: "discover",      label: "Crawl",                hint: "BFS crawl + Markdown extraction" },
  { key: "section",       label: "Section",              hint: "deterministic provenance" },
  { key: "extract",       label: "Extract observations", hint: "" },
  { key: "validate",      label: "Validate (NLI)",       hint: "" },
  { key: "planner",       label: "Planner review",       hint: "agentic decision point" },
  { key: "web_search",    label: "External enrichment",  hint: "bounded, planner-gated" },
  { key: "strategy",      label: "Strategy artifact",    hint: "fit + persona + 2 angles" },
  { key: "write_emails",  label: "Write emails",         hint: "pain-led + trigger-led" },
  { key: "email_guard",   label: "Guardrail",            hint: "extract claims from email; link retrieval evidence; is_safe + confidence" },
  { key: "angle_overlap", label: "Angle overlap",        hint: "diverge if too similar" },
  { key: "done",          label: "Done",                 hint: "" },
];

/** Friendly labels for persisted Admin stage timelines (tracker stage names). */
export const STAGE_LABELS: Record<string, string> = {
  crawl: "Crawl",
  section: "Section",
  extract: "Extract observations",
  validate: "Validate (NLI)",
  planner: "Planner review",
  fetch_more: "Fetch more pages",
  web_search: "External enrichment",
  icp: "Synthesize ICP",
  vp: "Value proposition",
  target_discovery: "Discover targets",
  strategy: "Strategy artifact",
  write_emails: "Write emails",
  email_guard: "Guardrail",
  angle_overlap: "Angle overlap",
};

export function stageLabel(name: string): string {
  return STAGE_LABELS[name] ?? name.replace(/_/g, " ");
}

// Map raw event stage names to the canonical UI stage key.
export function normalizeStage(stage: string): StageKey | null {
  if (stage === "discover" || stage === "discover_done" || stage === "fetch_done")
    return "discover";
  if (stage === "section" || stage === "section_done") return "section";
  if (
    stage === "extract" ||
    stage === "extract_progress" ||
    stage === "extract_done" ||
    stage === "extract_ws"
  )
    return "extract";
  if (
    stage === "validate" ||
    stage === "validate_progress" ||
    stage === "validate_done" ||
    stage === "filter_done"
  )
    return "validate";
  if (stage === "planner" || stage === "planner_done" || stage === "icp_pass1")
    return "planner";
  if (stage === "fetch_more") return "fetch_more";
  if (stage === "web_search" || stage === "web_search_done") return "web_search";
  if (
    stage === "synthesis" ||
    stage === "synthesis_progress" ||
    stage === "icp_done" ||
    stage === "vp_done"
  )
    return "icp";
  if (stage === "icp") return "icp";
  if (stage === "vp") return "vp";
  if (stage === "target_discovery" || stage === "target_discovery_done")
    return "target_discovery";
  if (stage === "strategy" || stage === "strategy_done") return "strategy";
  if (stage === "write_emails" || stage === "write_emails_done")
    return "write_emails";
  if (
    stage === "email_guard" ||
    stage === "email_guard_progress" ||
    stage === "email_guard_done"
  )
    return "email_guard";
  if (
    stage === "angle_overlap" ||
    stage === "angle_overlap_repair" ||
    stage === "angle_overlap_done"
  )
    return "angle_overlap";
  if (stage === "done" || stage === "__done__") return "done";
  return null;
}
