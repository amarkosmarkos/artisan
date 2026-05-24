"use client";

import * as React from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { StageDetailView } from "@/components/stage-detail-view";
import {
  getCompanyObservations,
  getCompanySources,
  getRunDetail,
} from "@/lib/api";
import type { RunMetrics } from "@/lib/types";

export default function RunDetailPage() {
  const params = useParams<{ run_id: string }>();
  const runId = params?.run_id;
  const run = useQuery({
    enabled: !!runId,
    queryKey: ["run", runId],
    queryFn: () => getRunDetail(runId!),
  });

  if (!runId) return null;
  if (run.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (run.isError) {
    return (
      <p className="text-sm text-destructive">
        Failed to load run: {String(run.error)}
      </p>
    );
  }
  const data = run.data;
  if (!data) return null;

  const m = data.metrics;
  const isTarget = data.kind === "target";
  const latencyMs = effectiveLatencyMs(m);
  const validationRate = effectiveValidationRate(m);
  const claimSupportRate = isTarget ? effectiveClaimSupportRate(m) : null;
  const senderHref = data.company_id ? `/senders/${data.company_id}` : null;
  const targetHref = data.target_company_id
    ? `/targets/${data.target_company_id}`
    : null;
  // Show pages + observations of the company this run produced. For sender
  // runs that's the sender; for target runs, the target.
  const evidenceCompanyId =
    data.kind === "target" ? data.target_company_id : data.company_id;

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link href="/metrics">
          <ArrowLeft className="h-4 w-4" /> Admin
        </Link>
      </Button>

      <header
        className={`rounded-xl border border-border/60 p-5 ${
          data.kind === "target" ? "bg-target-soft" : "bg-sender-soft"
        }`}
      >
        <div className="flex items-center gap-2">
          <Badge variant={data.kind === "target" ? "target" : "sender"}>
            {data.kind}
          </Badge>
        </div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          {prettyUrl(data.target_url || data.sender_url || "—")}
        </h1>
        <p className="text-xs text-muted-foreground">
          {data.run_id} · {formatDate(data.created_at)}
        </p>
        <div className="flex gap-2 pt-3">
          {senderHref && (
            <Button variant="outline" size="sm" asChild>
              <Link href={senderHref}>
                <ExternalLink className="h-3.5 w-3.5" /> Sender
              </Link>
            </Button>
          )}
          {targetHref && (
            <Button variant="outline" size="sm" asChild>
              <Link href={targetHref}>
                <ExternalLink className="h-3.5 w-3.5" /> Target
              </Link>
            </Button>
          )}
        </div>
      </header>

      <Warnings metrics={m} />

      <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
        <Stat
          label="Latency"
          value={
            latencyMs !== null ? `${(latencyMs / 1000).toFixed(1)}s` : "—"
          }
        />
        <Stat
          label="Tokens"
          value={formatNumber((m.tokens_in ?? 0) + (m.tokens_out ?? 0))}
          sub={`${formatNumber(m.tokens_in ?? 0)} in / ${formatNumber(m.tokens_out ?? 0)} out`}
        />
        <Stat
          label="Cost"
          value={`$${(m.cost_usd ?? 0).toFixed(4)}`}
        />
        <Stat
          label="Pages"
          value={String(m.pages_fetched ?? 0)}
          sub={`${m.sections_created ?? 0} sections`}
        />
        <Stat
          label="Observations"
          value={String(m.observations_extracted ?? 0)}
          sub={`${m.observations_validated ?? 0} entailed · ${m.observations_rejected ?? 0} rejected`}
        />
        <Stat
          label="Validation rate"
          value={formatPct(validationRate)}
        />
        <Stat
          label="Evidence support"
          value={isTarget ? formatPct(claimSupportRate) : "N/A"}
          sub={
            isTarget
              ? `${statementSupported(m)}/${statementTotal(m)} supported · regen ${m.regeneration_count ?? 0} · ${m.final_email_safe === false ? "unsafe" : "safe"}`
              : "Target runs only"
          }
        />
        <Stat
          label="Angle overlap"
          value={
            !isTarget
              ? "N/A"
              : m.angle_overlap !== null
                ? (m.angle_overlap ?? 0).toFixed(2)
                : "—"
          }
          sub={isTarget ? "cosine" : "Target runs only"}
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Stages timeline</CardTitle>
          <CardDescription>
            Each pipeline node, in execution order, with its duration and the
            event detail it emitted.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <StagesTimeline stages={m.stages} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Planner decisions</CardTitle>
          <CardDescription>
            The single agentic step in the pipeline. Up to two passes per run.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <PlannerDecisions decisions={m.planner_decisions} />
        </CardContent>
      </Card>

      {evidenceCompanyId && (
        <PipelineSources companyId={evidenceCompanyId} />
      )}
    </div>
  );
}

function Warnings({ metrics }: { metrics: RunMetrics }) {
  const issues: { label: string; tone: "destructive" | "warning"; count: number }[] =
    [];
  const contradicted =
    metrics.contradicted_statements_count ?? metrics.claims_contradicted ?? 0;
  if (contradicted > 0) {
    issues.push({
      label: "Contradicted statements",
      tone: "destructive",
      count: contradicted,
    });
  }
  const unsupported =
    metrics.unsupported_statements_count ?? metrics.claims_unsupported ?? 0;
  if (unsupported > 0) {
    issues.push({
      label: "Unsupported statements",
      tone: "warning",
      count: unsupported,
    });
  }
  if (metrics.final_email_safe === false) {
    issues.push({
      label: "Email failed safety check",
      tone: "destructive",
      count: metrics.failed_statements?.length ?? 1,
    });
  }
  // Coverage-style warnings from planner.
  metrics.planner_decisions?.forEach((d) => {
    if (d.decision === "proceed_low_confidence" && d.missing_fields?.length) {
      issues.push({
        label: `Proceeded with low confidence (${d.task})`,
        tone: "warning",
        count: d.missing_fields.length,
      });
    }
  });

  if (issues.length === 0) return null;

  return (
    <Card className="border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/5">
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <AlertTriangle className="h-4 w-4 text-[hsl(var(--warning))]" />
          Warnings
        </CardTitle>
        <CardDescription>
          Things to review before treating these emails as send-ready.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="flex flex-wrap gap-2">
          {issues.map((it, i) => (
            <li key={i}>
              <Badge variant={it.tone}>
                {it.label}: {it.count}
              </Badge>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function PipelineSources({ companyId }: { companyId: string }) {
  const sources = useQuery({
    queryKey: ["company-sources", companyId],
    queryFn: () => getCompanySources(companyId),
  });
  const observations = useQuery({
    queryKey: ["company-observations", companyId],
    queryFn: () => getCompanyObservations(companyId),
  });

  const obsRows = observations.data?.observations ?? [];
  const lowConf = obsRows.filter((o) => o.confidence < 0.6).length;
  const neutral = obsRows.filter((o) => o.validation === "neutral").length;
  const contradicted = obsRows.filter(
    (o) => o.validation === "contradicted",
  ).length;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Pipeline visibility</CardTitle>
        <CardDescription>
          Pages this run fetched and a quick health snapshot of the
          observations it extracted.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <Stat
            label="Pages"
            value={String(sources.data?.pages?.length ?? "…")}
          />
          <Stat label="Observations" value={String(obsRows.length)} />
          <Stat label="Neutral" value={String(neutral)} />
          <Stat label="Contradicted" value={String(contradicted)} />
        </div>

        {lowConf > 0 && (
          <p className="rounded-md border border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/10 px-3 py-2 text-xs text-[hsl(var(--warning))]">
            {lowConf} observations carry a confidence below 0.60. They are
            kept for transparency but downstream synthesis weights them less.
          </p>
        )}

        {sources.data?.pages?.length ? (
          <details className="rounded-md border border-border/60 bg-card/40 px-3 py-2">
            <summary className="cursor-pointer text-sm font-medium">
              Retrieved pages ({sources.data.pages.length})
            </summary>
            <ul className="mt-2 space-y-1.5">
              {sources.data.pages.map((p) => (
                <li
                  key={p.page_id}
                  className="flex items-center gap-3 rounded border border-border/40 px-2 py-1.5"
                >
                  <a
                    href={p.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex flex-1 min-w-0 items-center gap-2 text-sm"
                  >
                    <ExternalLink className="h-3.5 w-3.5 text-muted-foreground" />
                    <span className="truncate font-mono text-xs">
                      {prettyUrl(p.url)}
                    </span>
                  </a>
                  <Badge variant="outline" className="font-mono">
                    {p.cleaned_chars ?? "—"} chars
                  </Badge>
                  <span className="hidden sm:inline text-[10px] text-muted-foreground uppercase">
                    {p.source}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </CardContent>
    </Card>
  );
}

function StagesTimeline({ stages }: { stages: RunMetrics["stages"] }) {
  if (!stages || stages.length === 0) {
    return <p className="text-sm text-muted-foreground">No stages recorded.</p>;
  }
  const total = stages.reduce((acc, s) => acc + (s.duration_ms || 0), 0);
  return (
    <ul className="space-y-2">
      {stages.map((s, i) => {
        const pct = total > 0 ? ((s.duration_ms || 0) / total) * 100 : 0;
        return (
          <li
            key={`${s.name}-${i}`}
            className="rounded-md border border-border/60 p-3"
          >
            <div className="flex items-center justify-between gap-3">
              <div className="font-mono text-sm">{s.name}</div>
              <div className="text-xs text-muted-foreground">
                {(s.duration_ms / 1000).toFixed(2)}s · {pct.toFixed(0)}%
              </div>
            </div>
            <div className="mt-2 h-1.5 w-full rounded bg-border/60 overflow-hidden">
              <div
                className="h-full bg-foreground/70"
                style={{ width: `${pct}%` }}
              />
            </div>
            {s.detail && Object.keys(s.detail).length > 0 && (
              <StageDetailView detail={s.detail} />
            )}
          </li>
        );
      })}
    </ul>
  );
}

function PlannerDecisions({
  decisions,
}: {
  decisions: RunMetrics["planner_decisions"];
}) {
  if (!decisions || decisions.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No planner pass recorded.
      </p>
    );
  }
  return (
    <ul className="space-y-3">
      {decisions.map((d, i) => (
        <li key={i} className="rounded-md border border-border/60 p-3">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="capitalize">
              {d.task}
            </Badge>
            <Badge
              variant={
                d.decision === "continue" ||
                d.decision === "proceed_low_confidence"
                  ? "sender"
                  : d.decision === "stop"
                    ? "destructive"
                    : "outline"
              }
            >
              {d.decision.replace(/_/g, " ")}
            </Badge>
          </div>
          {d.reason && (
            <p className="mt-2 text-sm text-foreground/90 leading-snug">
              {d.reason}
            </p>
          )}
          {d.missing_fields && d.missing_fields.length > 0 && (
            <div className="mt-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Missing fields
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {d.missing_fields.map((f) => (
                  <Badge key={f} variant="outline" className="font-normal">
                    {f}
                  </Badge>
                ))}
              </div>
            </div>
          )}
          {d.suggested_internal_pages &&
            d.suggested_internal_pages.length > 0 && (
              <div className="mt-2">
                <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                  Suggested pages
                </p>
                <div className="mt-1 flex flex-wrap gap-1">
                  {d.suggested_internal_pages.map((p) => (
                    <Badge key={p} variant="outline" className="font-mono font-normal text-xs">
                      {p}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          {d.suggested_queries && d.suggested_queries.length > 0 && (
            <div className="mt-2">
              <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                Suggested queries
              </p>
              <div className="mt-1 flex flex-wrap gap-1">
                {d.suggested_queries.map((q) => (
                  <Badge key={q} variant="outline" className="font-normal">
                    {q}
                  </Badge>
                ))}
              </div>
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="rounded-md border border-border/60 px-4 py-3">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-mono text-xl">{value}</p>
      {sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}
    </div>
  );
}

function formatPct(rate: number | null): string {
  if (rate === null) return "—";
  return `${(rate * 100).toFixed(0)}%`;
}

function effectiveLatencyMs(m: RunMetrics): number | null {
  if (m.latency_ms && m.latency_ms > 0) return m.latency_ms;
  const stageTotal = (m.stages ?? []).reduce(
    (sum, s) => sum + (s.duration_ms || 0),
    0,
  );
  return stageTotal > 0 ? stageTotal : null;
}

function effectiveValidationRate(m: RunMetrics): number | null {
  if (m.observation_validation_rate != null) {
    return m.observation_validation_rate;
  }
  const extracted = m.observations_extracted ?? 0;
  const validated = m.observations_validated ?? 0;
  return extracted > 0 && validated > 0 ? validated / extracted : null;
}

function statementTotal(m: RunMetrics): number {
  return m.extracted_statements_count ?? m.claims_total ?? 0;
}

function statementSupported(m: RunMetrics): number {
  return m.supported_statements_count ?? m.claims_supported ?? 0;
}

function effectiveClaimSupportRate(m: RunMetrics): number | null {
  if (m.evidence_support_rate != null) return m.evidence_support_rate;
  if (m.claim_support_rate != null) return m.claim_support_rate;
  const checkable =
    statementSupported(m) +
    (m.unsupported_statements_count ?? m.claims_unsupported ?? 0) +
    (m.contradicted_statements_count ?? m.claims_contradicted ?? 0);
  if (checkable > 0) return statementSupported(m) / checkable;
  const total = statementTotal(m);
  return total > 0 ? statementSupported(m) / total : null;
}

function prettyUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname);
  } catch {
    return url;
  }
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}
