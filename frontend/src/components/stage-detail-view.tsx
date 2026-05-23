"use client";

import * as React from "react";
import { ExternalLink } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn, formatNumber } from "@/lib/utils";

const FIELD_LABELS: Record<string, string> = {
  pages: "Pages fetched",
  failed: "Failed URLs",
  sections: "Sections",
  observations: "Observations",
  entailed: "Entailed",
  contradicted: "Contradicted",
  neutral: "Neutral",
  unsupported: "Unsupported",
  usable: "Usable",
  claims: "Claims",
  repaired: "Repaired",
  overlap: "Angle overlap",
  overlap_after_repair: "Overlap after repair",
  decision: "Decision",
  reason: "Reason",
  url: "URL",
  explicit: "Explicit URLs",
  targets: "Target paths",
  candidates: "Candidates",
  fetched: "Fetched",
  count: "Count",
  done: "Completed",
  total: "Total",
  chunks: "Chunks",
  emails: "Emails",
  emails_done: "Emails done",
  emails_total: "Emails total",
  queries: "Queries",
  message: "Status",
  primary: "Primary VP",
  fit_level: "Fit level",
  contact_decision: "Contact decision",
  angles: "Angles",
  selected_vp_label: "Selected VP",
  selected_vp_id: "Selected VP id",
  missing_fields: "Missing fields",
  uncrawled_urls: "Uncrawled URLs",
};

function labelFor(key: string): string {
  return (
    FIELD_LABELS[key] ??
    key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
  );
}

function isUrl(value: string): boolean {
  return /^https?:\/\//i.test(value);
}

function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (!Number.isInteger(value) && Math.abs(value) <= 1) {
      return value.toFixed(3);
    }
    return formatNumber(value);
  }
  return String(value);
}

interface StageDetailViewProps {
  detail: Record<string, unknown>;
  className?: string;
}

export function StageDetailView({ detail, className }: StageDetailViewProps) {
  const entries = Object.entries(detail).filter(
    ([, v]) => v !== null && v !== undefined && v !== "",
  );
  if (entries.length === 0) return null;

  return (
    <dl
      className={cn(
        "mt-2 grid gap-2 rounded-md border border-border/40 bg-muted/20 p-3 sm:grid-cols-2",
        className,
      )}
    >
      {entries.map(([key, value]) => (
        <DetailEntry key={key} label={labelFor(key)} value={value} />
      ))}
    </dl>
  );
}

function DetailEntry({ label, value }: { label: string; value: unknown }) {
  if (Array.isArray(value)) {
    if (value.length === 0) return null;
    const primitives = value.every(
      (v) => typeof v === "string" || typeof v === "number" || typeof v === "boolean",
    );
    if (primitives) {
      return (
        <div className="sm:col-span-2">
          <dt className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
            {label}
          </dt>
          <dd className="mt-1 flex flex-wrap gap-1">
            {value.map((item, i) => {
              const text = formatScalar(item);
              if (typeof item === "string" && isUrl(item)) {
                return (
                  <a
                    key={i}
                    href={item}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex max-w-full items-center gap-1 rounded border border-border/50 bg-background/60 px-2 py-0.5 text-xs hover:border-foreground/30"
                  >
                    <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono">{prettyUrl(item)}</span>
                  </a>
                );
              }
              return (
                <Badge key={i} variant="outline" className="font-normal">
                  {text}
                </Badge>
              );
            })}
          </dd>
        </div>
      );
    }
  }

  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const nested = value as Record<string, unknown>;
    const nestedEntries = Object.entries(nested).filter(
      ([, v]) => v !== null && v !== undefined && v !== "",
    );
    if (nestedEntries.length === 0) return null;
    return (
      <div className="sm:col-span-2 rounded border border-border/30 bg-background/40 p-2">
        <dt className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </dt>
        <dd className="mt-1.5">
          <StageDetailView detail={nested} className="mt-0 border-0 bg-transparent p-0" />
        </dd>
      </div>
    );
  }

  const text = formatScalar(value);
  const url = typeof value === "string" && isUrl(value);

  return (
    <>
      <dt className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className="text-sm text-foreground/90">
        {url ? (
          <a
            href={text}
            target="_blank"
            rel="noreferrer"
            className="inline-flex max-w-full items-center gap-1 font-mono text-xs hover:underline"
          >
            <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
            <span className="truncate">{prettyUrl(text)}</span>
          </a>
        ) : keyLooksLikeReason(label) ? (
          <span className="leading-snug">{text}</span>
        ) : (
          <span className="font-mono tabular-nums">{text}</span>
        )}
      </dd>
    </>
  );
}

function keyLooksLikeReason(label: string): boolean {
  return label.toLowerCase() === "reason" || label.toLowerCase() === "status";
}

function prettyUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname);
  } catch {
    return url;
  }
}
