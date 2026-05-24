"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { Check, Loader2, CircleDashed } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import {
  normalizeStage,
  SENDER_STAGES,
  TARGET_STAGES,
  type StageKey,
} from "./stages";
import type { ProgressEvent } from "@/lib/types";

interface Props {
  events: ProgressEvent[];
  kind: "sender" | "target";
  done: boolean;
}

type StageStatus = "pending" | "active" | "done";

// Build per-stage status + summary string from the raw event stream.
// We walk events in order so the "active" highlight follows the latest
// in-flight stage, and per-stage summary text reflects the most recent
// known counts (pages, sections, observations, batches, etc.).
function deriveStageState(events: ProgressEvent[], stages: readonly { key: StageKey }[]) {
  const status = new Map<StageKey, StageStatus>();
  const summary = new Map<StageKey, string>();
  for (const s of stages) status.set(s.key, "pending");

  let activeKey: StageKey | null = null;

  const setSummary = (key: StageKey, text: string) => summary.set(key, text);

  for (const ev of events) {
    const key = normalizeStage(ev.stage);
    if (!key) continue;
    const isTerminal = ev.stage.endsWith("_done");

    if (!isTerminal) {
      if (activeKey && activeKey !== key) status.set(activeKey, "done");
      status.set(key, "active");
      activeKey = key;
    } else {
      status.set(key, "done");
      if (activeKey === key) activeKey = null;
    }

    // Per-stage summary derivation. Pulls numbers straight from event.detail.
    const d = ev.detail || {};
    switch (ev.stage) {
      case "discover":
        setSummary("discover", "scanning");
        break;
      case "discover_done":
        setSummary("discover", `${num(d.candidates)} pages`);
        break;
      case "fetch_done":
        setSummary("discover", `${num(d.fetched)} pages fetched`);
        break;
      case "section":
        setSummary("section", `${num(d.count)} pages`);
        break;
      case "section_done":
        setSummary("section", `${num(d.sections)} sections`);
        break;
      case "extract":
        setSummary("extract", `${num(d.sections)} sections`);
        break;
      case "extract_progress":
        setSummary(
          "extract",
          `batch ${num(d.done)}/${num(d.total)}`,
        );
        break;
      case "extract_done":
        setSummary("extract", `${num(d.observations)} observations`);
        break;
      case "validate":
        setSummary("validate", `${num(d.observations)} to check`);
        break;
      case "validate_progress":
        setSummary("validate", `NLI ${num(d.done)}/${num(d.total)}`);
        break;
      case "validate_done":
        setSummary(
          "validate",
          `${num(d.entailed)} entailed · ${num(d.contradicted)} contradicted`,
        );
        break;
      case "filter_done":
        setSummary("validate", `${num(d.usable)} usable observations`);
        break;
      case "planner":
        setSummary(
          "planner",
          `gaps: ${listOf(d.missing_fields) || "checking"}`,
        );
        break;
      case "planner_done":
        setSummary(
          "planner",
          `decision: ${String(d.decision || "—")}`,
        );
        break;
      case "fetch_more":
        setSummary("fetch_more", "fetching extra pages");
        break;
      case "web_search":
        setSummary("web_search", `queries: ${num(arrLen(d.queries))}`);
        break;
      case "web_search_done":
        setSummary("web_search", `${num(d.sections)} new sections`);
        break;
      case "synthesis":
      case "synthesis_progress":
        if (typeof d.message === "string" && d.message) {
          setSummary("icp", d.message);
          setSummary("vp", d.message);
        }
        break;
      case "icp":
        setSummary(
          "icp",
          typeof d.message === "string" && d.message ? d.message : "Synthesizing ICP…",
        );
        break;
      case "icp_done":
        setSummary("icp", "ICP ready");
        break;
      case "vp":
        setSummary(
          "vp",
          typeof d.message === "string" && d.message
            ? d.message
            : "Synthesizing value proposition(s)…",
        );
        break;
      case "vp_done":
        setSummary(
          "vp",
          num(d.count) > 1
            ? `${num(d.count)} value propositions`
            : String(d.primary || "Value proposition ready"),
        );
        break;
      case "strategy":
        setSummary("strategy", `${num(d.observations)} observations`);
        break;
      case "strategy_done": {
        const vpLabel =
          (typeof d.selected_vp_label === "string" && d.selected_vp_label) ||
          (typeof d.selected_vp_id === "string" && d.selected_vp_id) ||
          "";
        const vpSuffix = vpLabel ? ` · vp: ${vpLabel}` : "";
        setSummary(
          "strategy",
          `${String(d.fit_level || "—")} · ${String(d.contact_decision || "—")} · ${num(d.angles)} angles${vpSuffix}`,
        );
        break;
      }
      case "write_emails":
        setSummary("write_emails", "drafting pain + trigger");
        break;
      case "write_emails_done":
        setSummary("write_emails", `${num(d.emails)} emails drafted`);
        break;
      case "email_guard":
        setSummary("email_guard", `checking ${num(d.emails)} email(s)`);
        break;
      case "email_guard_progress":
        setSummary(
          "email_guard",
          `${num(d.emails_done)}/${num(d.emails_total)} emails`,
        );
        break;
      case "email_guard_done":
        setSummary(
          "email_guard",
          `${num(d.supported)}/${num(d.extracted)} supported · ${
            d.unsafe ? "unsafe" : "safe"
          }`,
        );
        break;
      case "angle_overlap":
        setSummary("angle_overlap", "measuring");
        break;
      case "angle_overlap_repair":
        setSummary(
          "angle_overlap",
          `diverging (was ${fmtFloat(d.overlap)})`,
        );
        break;
      case "angle_overlap_done":
        setSummary(
          "angle_overlap",
          d.repaired
            ? `overlap ${fmtFloat(d.overlap)} (after repair)`
            : `overlap ${fmtFloat(d.overlap)}`,
        );
        break;
      default:
        break;
    }
  }
  return { status, summary, activeKey };
}

function num(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "string") {
    const n = parseInt(v, 10);
    return isNaN(n) ? 0 : n;
  }
  return 0;
}

function arrLen(v: unknown): number {
  return Array.isArray(v) ? v.length : 0;
}

function listOf(v: unknown): string {
  if (!Array.isArray(v) || v.length === 0) return "";
  return v.slice(0, 3).join(", ") + (v.length > 3 ? "…" : "");
}

function fmtFloat(v: unknown): string {
  if (typeof v === "number") return v.toFixed(2);
  return "—";
}

export function ProgressStream({ events, kind, done }: Props) {
  const stages = kind === "sender" ? SENDER_STAGES : TARGET_STAGES;
  const { status, summary, activeKey } = React.useMemo(
    () => deriveStageState(events, stages),
    [events, stages],
  );

  // When the run finishes, mark every still-pending stage as done so the
  // list reads as fully completed.
  const finalStatus = React.useMemo(() => {
    if (!done) return status;
    const m = new Map(status);
    for (const s of stages) if (m.get(s.key) !== "done") m.set(s.key, "done");
    return m;
  }, [done, status, stages]);

  const startedAt = events[0]?.ts;
  const lastTs = events[events.length - 1]?.ts;
  const elapsedSec =
    startedAt && lastTs ? Math.max(0, Math.round((lastTs - startedAt) / 1000)) : 0;

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-6">
        <div className="flex items-center justify-between mb-4">
          <div className="text-xs font-mono text-muted-foreground">
            {done ? "completed" : activeKey ? "running" : "starting"}
          </div>
          <div className="text-xs font-mono text-muted-foreground tabular-nums">
            {elapsedSec}s
          </div>
        </div>

        <ol className="flex flex-col">
          {stages.map((s) => {
            const st = finalStatus.get(s.key) ?? "pending";
            const detail = summary.get(s.key);
            return (
              <li
                key={s.key}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 transition-colors",
                  st === "active" && "bg-accent/40",
                )}
              >
                <StageIcon state={st} />
                <div className="flex flex-1 items-baseline justify-between gap-3 min-w-0">
                  <span
                    className={cn(
                      "text-sm transition-colors",
                      st === "pending" && "text-muted-foreground",
                      st === "active" && "font-medium",
                      st === "done" && "text-foreground",
                    )}
                  >
                    {s.label}
                  </span>
                  {detail && (
                    <motion.span
                      key={`${s.key}-${detail}`}
                      initial={{ opacity: 0, y: 2 }}
                      animate={{ opacity: 1, y: 0 }}
                      transition={{ duration: 0.15 }}
                      className={cn(
                        "text-xs font-mono tabular-nums truncate",
                        st === "active"
                          ? "text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {detail}
                    </motion.span>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

function StageIcon({ state }: { state: StageStatus }) {
  if (state === "done")
    return (
      <div className="flex h-5 w-5 items-center justify-center rounded-full bg-foreground text-background shrink-0">
        <Check className="h-3 w-3" strokeWidth={2.5} />
      </div>
    );
  if (state === "active")
    return <Loader2 className="h-5 w-5 animate-spin text-foreground shrink-0" />;
  return <CircleDashed className="h-5 w-5 text-muted-foreground/60 shrink-0" />;
}
