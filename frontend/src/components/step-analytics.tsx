"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { ArrowLeft, ExternalLink } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, formatCost, formatDuration, formatNumber } from "@/lib/utils";
import type { TargetResponse } from "@/lib/types";

interface Props {
  result: TargetResponse;
  onBack: () => void;
}

export function StepAnalytics({ result, onBack }: Props) {
  const m = result.metrics;
  const stats: { label: string; value: string; hint?: string }[] = [
    { label: "Latency", value: formatDuration(m.latency_ms) },
    { label: "Tokens (in / out)", value: `${formatNumber(m.tokens_in)} / ${formatNumber(m.tokens_out)}` },
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
    { label: "Claims used", value: String(m.claims_total) },
    { label: "Unsupported claims", value: String(m.claims_unsupported) },
    {
      label: "Angle overlap",
      value: m.angle_overlap === null ? "—" : m.angle_overlap.toFixed(3),
      hint: m.angle_overlap !== null && m.angle_overlap > 0.78 ? "repaired" : undefined,
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

      <div className="mt-8">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Evidence chain</CardTitle>
            <CardDescription>
              Observation → strategy angle → email claim → verification status.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="rounded-md border border-border/60">
              <table className="w-full text-sm">
                <thead className="text-xs uppercase tracking-wide text-muted-foreground">
                  <tr className="border-b border-border/60">
                    <th className="text-left font-medium px-3 py-2">Angle</th>
                    <th className="text-left font-medium px-3 py-2">Claim</th>
                    <th className="text-left font-medium px-3 py-2">Status</th>
                    <th className="text-left font-medium px-3 py-2">NLI</th>
                    <th className="text-left font-medium px-3 py-2">Citations</th>
                  </tr>
                </thead>
                <tbody>
                  {result.claim_map.map((c) => (
                    <tr key={c.claim_id} className="border-b border-border/30 last:border-0">
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {c.angle.replace("_", "-")}
                      </td>
                      <td className="px-3 py-2 max-w-md">
                        <span className="line-clamp-2">{c.text}</span>
                      </td>
                      <td className="px-3 py-2">
                        <span
                          className={cn(
                            "text-xs",
                            c.status === "entailed" && "text-[hsl(var(--success))]",
                            c.status === "repaired" && "text-foreground",
                            c.status === "contradicted" && "text-destructive",
                            c.status === "unsupported" && "text-[hsl(var(--warning))]",
                            c.status === "neutral" && "text-muted-foreground"
                          )}
                        >
                          {c.status}
                        </span>
                      </td>
                      <td className="px-3 py-2 text-xs font-mono tabular-nums text-muted-foreground">
                        {c.nli_score === null ? "—" : c.nli_score.toFixed(2)}
                      </td>
                      <td className="px-3 py-2">
                        <ul className="flex flex-col gap-1">
                          {c.citations.map((cit, i) => (
                            <li key={i}>
                              <a
                                href={cit.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                              >
                                <ExternalLink className="h-3 w-3" />
                                <span className="truncate max-w-[180px]">
                                  {safeHost(cit.url)}
                                </span>
                              </a>
                            </li>
                          ))}
                        </ul>
                      </td>
                    </tr>
                  ))}
                  {result.claim_map.length === 0 && (
                    <tr>
                      <td colSpan={5} className="px-3 py-4 text-sm text-muted-foreground text-center">
                        No claims recorded.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
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

function safeHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
