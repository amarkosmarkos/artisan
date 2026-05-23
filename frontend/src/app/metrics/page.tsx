"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  getRunsSummary,
  listRuns,
  type RunRow,
} from "@/lib/api";

export default function MetricsPage() {
  const aggregate = useQuery({
    queryKey: ["runs-summary"],
    queryFn: getRunsSummary,
  });
  const runs = useQuery({
    queryKey: ["runs"],
    queryFn: () => listRuns({ limit: 200 }),
  });

  const a = aggregate.data;
  const rows: RunRow[] = runs.data?.runs ?? [];

  return (
    <div className="space-y-8">
      <header className="rounded-xl border border-border/60 bg-sender-soft p-5">
        <div className="flex items-center gap-2">
          <Activity className="h-4 w-4 text-[hsl(var(--sender))]" />
          <Badge variant="sender">technical</Badge>
        </div>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          Metrics &amp; pipeline
        </h1>
        <p className="mt-1 text-sm text-muted-foreground max-w-2xl">
          Operator view: latency, tokens, cost, claim support, planner
          decisions, and per-stage timing for every flow that has finished.
          MLflow tracking is also available at{" "}
          <a
            href="http://localhost:5000"
            target="_blank"
            rel="noreferrer"
            className="underline"
          >
            localhost:5000
          </a>
          .
        </p>
      </header>

      <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
        <Stat
          tone="sender"
          label="Total runs"
          value={a ? String(a.total_runs) : "…"}
          sub={
            a
              ? `${a.by_kind.sender} sender · ${a.by_kind.target} target`
              : undefined
          }
        />
        <Stat
          tone="default"
          label="Tokens"
          value={a ? formatNumber(a.tokens_in + a.tokens_out) : "…"}
          sub={
            a
              ? `${formatNumber(a.tokens_in)} in / ${formatNumber(a.tokens_out)} out`
              : undefined
          }
        />
        <Stat
          tone="persona"
          label="Cost"
          value={a ? `$${a.cost_usd.toFixed(2)}` : "…"}
          sub="Cumulative"
        />
        <Stat
          tone="target"
          label="Claim support"
          value={
            a && a.claim_support_rate !== null
              ? `${(a.claim_support_rate * 100).toFixed(0)}%`
              : "–"
          }
          sub={
            a
              ? `${a.claims_supported}/${a.claims_total} claims`
              : undefined
          }
        />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Recent runs</CardTitle>
          <CardDescription>
            Click a row for the full stages timeline + planner decisions.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {runs.isLoading && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {!runs.isLoading && rows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No runs persisted yet. Start a sender or target flow.
            </p>
          )}
          {rows.length > 0 && (
            <ul className="divide-y divide-border/60">
              {rows.map((r) => (
                <li key={r.run_id}>
                  <Link
                    href={`/metrics/${r.run_id}`}
                    className="flex items-center justify-between gap-3 py-3 hover:bg-accent/30 -mx-2 px-2 rounded"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="capitalize">
                          {r.kind}
                        </Badge>
                        <span className="truncate text-sm font-medium">
                          {prettyUrl(r.target_url || r.sender_url || "—")}
                        </span>
                      </div>
                      <p className="mt-0.5 flex items-center gap-3 text-xs text-muted-foreground">
                        <span>{formatDate(r.created_at)}</span>
                        <span>·</span>
                        <span>
                          {r.summary.observations_extracted ?? 0} obs
                        </span>
                        {r.kind === "target" && (
                          <>
                            <span>·</span>
                            <span>
                              {r.summary.claims_supported ?? 0}/
                              {r.summary.claims_total ?? 0} claims
                            </span>
                          </>
                        )}
                      </p>
                    </div>
                    <div className="hidden sm:grid grid-cols-3 gap-3 text-right text-xs text-muted-foreground">
                      <Mini
                        label="Latency"
                        value={
                          r.summary.latency_ms
                            ? `${(r.summary.latency_ms / 1000).toFixed(1)}s`
                            : "—"
                        }
                      />
                      <Mini
                        label="Tokens"
                        value={formatNumber(
                          (r.summary.tokens_in ?? 0) +
                            (r.summary.tokens_out ?? 0),
                        )}
                      />
                      <Mini
                        label="Cost"
                        value={
                          r.summary.cost_usd
                            ? `$${r.summary.cost_usd.toFixed(3)}`
                            : "$0"
                        }
                      />
                    </div>
                    <ChevronRight className="h-4 w-4 text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "sender" | "target" | "persona" | "default";
}) {
  const toneClass =
    tone === "sender"
      ? "border-[hsl(var(--sender))]/40 bg-sender-soft"
      : tone === "target"
        ? "border-[hsl(var(--target))]/40 bg-target-soft"
        : tone === "persona"
          ? "border-[hsl(var(--persona))]/40 bg-persona-soft"
          : "border-border/60";
  return (
    <div className={`rounded-md border px-4 py-3 ${toneClass}`}>
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-mono text-xl">{value}</p>
      {sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}
    </div>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wide">{label}</p>
      <p className="font-mono text-foreground">{value}</p>
    </div>
  );
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
