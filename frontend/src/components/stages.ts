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
  | "strategy"
  | "write_emails"
  | "claim_extract"
  | "verify"
  | "repair"
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
  { key: "claim_extract", label: "Consolidate claims",   hint: "" },
  { key: "verify",        label: "Verify claims (NLI)",  hint: "" },
  { key: "repair",        label: "Repair (bounded)",     hint: "at most once" },
  { key: "angle_overlap", label: "Angle overlap",        hint: "diverge if too similar" },
  { key: "done",          label: "Done",                 hint: "" },
];

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
  if (stage === "icp") return "icp";
  if (stage === "vp") return "vp";
  if (stage === "strategy" || stage === "strategy_done") return "strategy";
  if (stage === "write_emails" || stage === "write_emails_done")
    return "write_emails";
  if (stage === "claim_extract" || stage === "claim_extract_done")
    return "claim_extract";
  if (stage === "verify" || stage === "verify_progress" || stage === "verify_done")
    return "verify";
  if (stage === "repair" || stage === "repair_progress" || stage === "repair_done")
    return "repair";
  if (
    stage === "angle_overlap" ||
    stage === "angle_overlap_repair" ||
    stage === "angle_overlap_done"
  )
    return "angle_overlap";
  if (stage === "done" || stage === "__done__") return "done";
  return null;
}
