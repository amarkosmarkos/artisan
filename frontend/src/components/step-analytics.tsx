"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { ArrowLeft, Check, XOctagon, AlertTriangle } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatCost, formatDuration, formatNumber } from "@/lib/utils";
import type { TargetResponse } from "@/lib/types";

interface Props {
  result: TargetResponse;
  onBack: () => void;
}

export function StepAnalytics({ result, onBack }: Props) {
  const m = result.metrics;
  const safeRate =
    (m.emails_total ?? 0) > 0
      ? (m.emails_safe_count ?? 0) / (m.emails_total ?? 0)
      : null;
  const stats: { label: string; value: string; hint?: string }[] = [
    { label: "Latency", value: formatDuration(m.latency_ms) },
    {
      label: "Tokens (in / out)",
      value: `${formatNumber(m.tokens_in)} / ${formatNumber(m.tokens_out)}`,
    },
    { label: "Cost", value: formatCost(m.cost_usd) },
    { label: "Pages fetched", value: String(m.pages_fetched) },
    { label: "Sections processed", value: String(m.sections_created) },
    { label: "Observations extracted", value: String(m.observations_extracted) },
    {
      label: "Validation pass rate",
      value: pct(m.observation_validation_rate),
      hint: `${m.observations_validated} of ${m.observations_extracted} entailed`,
    },
    {
      label: "Evidence compression",
      value: m.compression_ratio ? `${m.compression_ratio.toFixed(2)}x` : "—",
      hint: `${formatNumber(m.raw_cleaned_chars)} → ${formatNumber(m.evidence_chars_used)} chars`,
    },
    {
      label: "Claims in emails",
      value: String(m.email_claims_count ?? 0),
      hint: `${m.unsupported_claims_count ?? 0} unsupported`,
    },
    {
      label: "Emails safe",
      value:
        (m.emails_total ?? 0) === 0
          ? "—"
          : `${m.emails_safe_count ?? 0} / ${m.emails_total ?? 0}`,
      hint: safeRate !== null ? pct(safeRate) : undefined,
    },
    {
      label: "Guardrail confidence",
      value:
        m.safety_confidence_avg == null
          ? "—"
          : m.safety_confidence_avg.toFixed(2),
      hint: "Average across all guardrail calls.",
    },
    {
      label: "Angle overlap",
      value: m.angle_overlap == null ? "—" : m.angle_overlap.toFixed(3),
      hint:
        m.angle_overlap != null && m.angle_overlap > 0.78
          ? "repaired"
          : undefined,
    },
  ];

  return (
    <motion.div
      key="analytics"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -16 }}
      transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
      className="w-full max-w-6xl mx-auto"
    >
      <div className="mb-6 flex items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> Outreach
        </Button>
      </div>

      <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-4">
        {stats.map((s) => (
          <Card key={s.label}>
            <CardContent className="py-5">
              <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {s.label}
              </p>
              <p className="mt-1 text-2xl font-semibold tabular-nums">{s.value}</p>
              {s.hint && (
                <p className="mt-0.5 text-xs text-muted-foreground">{s.hint}</p>
              )}
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-8 grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Pipeline timeline</CardTitle>
            <CardDescription>What ran, in order, with duration.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1.5">
              {m.stages.map((s, i) => (
                <li
                  key={`${s.name}-${i}`}
                  className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2 text-sm"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="font-mono text-xs text-muted-foreground w-6 text-right shrink-0">
                      {i + 1}
                    </span>
                    <span className="truncate">{s.name}</span>
                  </div>
                  <span className="font-mono text-xs tabular-nums text-muted-foreground">
                    {formatDuration(s.duration_ms)}
                  </span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Planner decisions</CardTitle>
            <CardDescription>
              The agentic step is bounded to one explicit Planner call per task.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {m.planner_decisions.map((p, i) => (
              <div key={i} className="rounded-md border border-border/60 p-3">
                <div className="flex items-center justify-between gap-3">
                  <Badge variant="outline" className="capitalize">
                    {p.task}
                  </Badge>
                  <Badge variant="muted">{p.decision}</Badge>
                </div>
                <p className="mt-2 text-sm text-foreground/90">{p.reason}</p>
                {p.suggested_internal_pages && p.suggested_internal_pages.length > 0 && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    suggested pages: {p.suggested_internal_pages.join(", ")}
                  </p>
                )}
                {p.suggested_queries && p.suggested_queries.length > 0 && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    suggested queries: {p.suggested_queries.join(" · ")}
                  </p>
                )}
              </div>
            ))}
            {m.planner_decisions.length === 0 && (
              <p className="text-sm text-muted-foreground">No planner decisions recorded.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mt-8 grid gap-6">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Guardrail per email</CardTitle>
            <CardDescription>
              Claims the guardrail found in each email, linked to retrieval
              evidence. General-knowledge claims skip verification.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {result.emails.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No emails were generated.
              </p>
            )}
            {result.emails.map((e) => {
              const safety = e.safety;
              const claims = e.claims ?? [];
              const ungrounded = claims.filter(
                (c) => c.scope !== "general" && c.grounded === false,
              ).length;
              return (
                <div
                  key={e.email_id}
                  className="rounded-md border border-border/60 p-3 space-y-2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="capitalize">
                      {e.angle.replace("_", "-")}
                    </Badge>
                    {safety ? (
                      <>
                        <Badge
                          variant={safety.is_safe ? "success" : "destructive"}
                          className="text-[10px]"
                        >
                          {safety.is_safe ? (
                            <Check className="h-3 w-3" />
                          ) : (
                            <XOctagon className="h-3 w-3" />
                          )}
                          <span className="ml-0.5">
                            {safety.is_safe ? "safe" : "unsafe"}
                          </span>
                        </Badge>
                        <Badge
                          variant={
                            (safety.confidence ?? 0) >= 0.8
                              ? "success"
                              : (safety.confidence ?? 0) >= 0.5
                                ? "default"
                                : (safety.confidence ?? 0) > 0
                                  ? "warning"
                                  : "muted"
                          }
                          className="text-[10px]"
                        >
                          conf {(safety.confidence ?? 0).toFixed(2)}
                        </Badge>
                        {!safety.verification_ok && (
                          <Badge variant="destructive" className="text-[10px]">
                            verifier unavailable
                          </Badge>
                        )}
                      </>
                    ) : (
                      <Badge variant="muted" className="text-[10px]">
                        not run
                      </Badge>
                    )}
                    <Badge variant="outline" className="text-[10px] font-mono">
                      {claims.length} claims
                    </Badge>
                    {ungrounded > 0 && (
                      <Badge variant="warning" className="text-[10px]">
                        <AlertTriangle className="h-3 w-3" />
                        <span className="ml-0.5">{ungrounded} not grounded</span>
                      </Badge>
                    )}
                  </div>
                  <p className="text-sm font-medium text-foreground/90 line-clamp-1">
                    {e.subject}
                  </p>
                </div>
              );
            })}
          </CardContent>
        </Card>
      </div>
    </motion.div>
  );
}

function pct(v: number | null) {
  if (v === null) return "—";
  return `${Math.round(v * 100)}%`;
}
