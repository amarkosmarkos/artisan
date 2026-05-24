"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  ExternalLink,
  FileText,
  Loader2,
  Wrench,
  XOctagon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import {
  getSection,
  resolveEvidence,
  type EvidenceRecord,
  type ObservationRow,
} from "@/lib/api";
import type { ClaimStatus, StatementSupportStatus } from "@/lib/types";

type VerificationStatus = ClaimStatus | StatementSupportStatus;

const STATUS_META: Record<
  VerificationStatus,
  {
    label: string;
    variant: "success" | "default" | "muted" | "warning" | "destructive";
    icon: React.ReactNode;
    explain: string;
  }
> = {
  entailed: {
    label: "supported",
    variant: "success",
    icon: <Check className="h-3 w-3" />,
    explain: "NLI entailed: cited evidence directly supports this claim.",
  },
  repaired: {
    label: "repaired",
    variant: "default",
    icon: <Wrench className="h-3 w-3" />,
    explain:
      "Original draft was unsupported; rewritten to match available evidence.",
  },
  neutral: {
    label: "neutral",
    variant: "muted",
    icon: <AlertTriangle className="h-3 w-3" />,
    explain:
      "NLI neutral: evidence neither implies nor contradicts the claim. Weak support.",
  },
  unsupported: {
    label: "unsupported",
    variant: "warning",
    icon: <AlertTriangle className="h-3 w-3" />,
    explain:
      "No grounded evidence was found for this claim. Verify before sending.",
  },
  contradicted: {
    label: "contradicted",
    variant: "destructive",
    icon: <XOctagon className="h-3 w-3" />,
    explain:
      "NLI contradicted: evidence directly contradicts the claim. Do not send as-is.",
  },
  supported: {
    label: "supported",
    variant: "success",
    icon: <Check className="h-3 w-3" />,
    explain: "Supported by available workflow context.",
  },
  not_checkable: {
    label: "not checkable",
    variant: "muted",
    icon: <AlertTriangle className="h-3 w-3" />,
    explain: "Non-factual or rhetorical; not verified as a fact claim.",
  },
  sender_context_not_verified: {
    label: "sender context not verified",
    variant: "muted",
    icon: <AlertTriangle className="h-3 w-3" />,
    explain:
      "Sender / value-prop positioning; no sender context available to verify against. Not a safety failure.",
  },
};

/** Build a partial evidence record before `/evidence/resolve` returns. */
export function evidenceFromObservation(row: ObservationRow): EvidenceRecord {
  return {
    observation_id: row.observation_id,
    text: row.text,
    kind: row.kind,
    confidence: row.confidence,
    validation: row.validation,
    validation_score: row.validation_score,
    section_id: row.section_id,
    url: null,
    heading: null,
    snippet: "",
  };
}

/**
 * Hook that hydrates observation IDs into full evidence records (claim +
 * section snippet, URL, NLI) in a single round-trip.
 */
export function useEvidenceLookup(
  observationIds: string[],
): Map<string, EvidenceRecord> {
  const ids = React.useMemo(
    () => Array.from(new Set(observationIds.filter(Boolean))).sort(),
    [observationIds],
  );
  const key = ids.join(",");
  const q = useQuery({
    enabled: ids.length > 0,
    queryKey: ["evidence", key],
    queryFn: () => resolveEvidence(ids),
    staleTime: 60_000,
  });
  return React.useMemo(() => {
    const out = new Map<string, EvidenceRecord>();
    if (q.data?.evidence) {
      for (const [id, rec] of Object.entries(q.data.evidence)) {
        out.set(id, rec);
      }
    }
    return out;
  }, [q.data]);
}

/**
 * Generic claim row with collapsible evidence panel. Works for both
 * email claims (with NLI status + score) and "anchor" claims like ICP /
 * VP / strategy bullets that just reference observation IDs.
 */
export function ClaimEvidence({
  claim,
  evidenceIds,
  status,
  score,
  evidence,
  defaultOpen = false,
  tone = "claim",
}: {
  claim: string;
  evidenceIds: string[];
  status?: VerificationStatus;
  score?: number | null;
  evidence: Map<string, EvidenceRecord>;
  defaultOpen?: boolean;
  tone?: "claim" | "evidence";
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const records = evidenceIds
    .map((id) => evidence.get(id))
    .filter((r): r is EvidenceRecord => Boolean(r));

  const has = records.length > 0;
  const meta = status ? STATUS_META[status] : null;

  return (
    <div
      className={cn(
        "rounded-lg border border-border/60 transition-colors",
        tone === "claim" ? "bg-claim" : "bg-evidence",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left"
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 mt-0.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-snug text-foreground/90">{claim}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {meta && (
              <Badge variant={meta.variant} title={meta.explain}>
                {meta.icon}
                <span className="ml-0.5">{meta.label}</span>
                {typeof score === "number" && (
                  <span className="ml-1 font-mono text-[10px] opacity-70">
                    {score.toFixed(2)}
                  </span>
                )}
              </Badge>
            )}
            <Badge variant="outline" className="font-mono">
              {evidenceIds.length} evidence ref
              {evidenceIds.length === 1 ? "" : "s"}
            </Badge>
          </div>
        </div>
      </button>

      {open && (
        <div className="space-y-2 border-t border-border/60 px-3 py-2.5">
          {!has && (
            <p className="text-xs text-muted-foreground">
              No evidence rows resolved. The claim references{" "}
              {evidenceIds.length} observation
              {evidenceIds.length === 1 ? "" : "s"} that may have been deleted
              or is loading.
            </p>
          )}
          {records.map((r) => (
            <EvidenceCard key={r.observation_id} ev={r} defaultOpen />
          ))}
        </div>
      )}
    </div>
  );
}

/** Observation row wired to a pre-fetched evidence map (or a loading stub). */
export function ObservationEvidenceCard({
  row,
  evidence,
  compact,
}: {
  row: ObservationRow;
  evidence?: EvidenceRecord;
  compact?: boolean;
}) {
  const ev = evidence ?? evidenceFromObservation(row);
  return <EvidenceCard ev={ev} compact={compact} />;
}

/**
 * Expandable observation: extracted claim on the header, real page text
 * when expanded.
 */
export function EvidenceCard({
  ev,
  compact,
  defaultOpen = false,
}: {
  ev: EvidenceRecord;
  compact?: boolean;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  const sectionId = ev.section_id;
  const snippetLooksTruncated =
    Boolean(ev.snippet) && ev.snippet.endsWith("…");

  const fullSection = useQuery({
    queryKey: ["section", sectionId],
    queryFn: () => getSection(sectionId!),
    enabled: open && Boolean(sectionId) && snippetLooksTruncated,
    staleTime: 300_000,
  });

  const sourceText =
    fullSection.data?.text ??
    (ev.snippet?.trim() ? ev.snippet : null);
  const sourcePending =
    open && snippetLooksTruncated && fullSection.isLoading;

  return (
    <div
      className={cn(
        "rounded-md border border-border/40 bg-background/50",
        compact ? "text-xs" : "text-sm",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "flex w-full items-start gap-2 text-left",
          compact ? "px-2 py-2" : "px-2.5 py-2.5",
        )}
      >
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 mt-0.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        <div className="min-w-0 flex-1">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Extracted claim
          </p>
          <p
            className={cn(
              "leading-snug text-foreground/90",
              compact ? "text-xs" : "text-sm",
            )}
          >
            {ev.text}
          </p>
          <EvidenceMeta ev={ev} compact={compact} className="mt-1.5" />
        </div>
      </button>

      {open && (
        <div
          className={cn(
            "border-t border-border/40",
            compact ? "px-2 pb-2" : "px-2.5 pb-2.5",
          )}
        >
          <div className="mt-2 rounded-md border border-border/50 bg-muted/30 p-2.5">
            <div className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1.5">
              <FileText className="h-3 w-3" />
              Source on page
            </div>
            {sourcePending ? (
              <p className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="h-3 w-3 animate-spin" />
                Loading section text…
              </p>
            ) : sourceText ? (
              <p
                className={cn(
                  "whitespace-pre-wrap leading-relaxed text-foreground/85",
                  compact ? "text-xs" : "text-sm",
                )}
              >
                {sourceText}
              </p>
            ) : (
              <p className="text-xs text-muted-foreground italic">
                Section text not available yet.
              </p>
            )}
            {ev.heading && (
              <p className="mt-2 text-[11px] text-muted-foreground italic">
                under &ldquo;{ev.heading}&rdquo;
              </p>
            )}
            {ev.url && (
              <a
                href={ev.url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-xs text-foreground/70 hover:text-foreground"
              >
                <ExternalLink className="h-3 w-3" />
                <span className="truncate max-w-[280px]">
                  {prettyUrl(ev.url)}
                </span>
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function EvidenceMeta({
  ev,
  compact,
  className,
}: {
  ev: EvidenceRecord;
  compact?: boolean;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-1.5 uppercase tracking-wide text-muted-foreground",
        compact ? "text-[9px]" : "text-[10px]",
        className,
      )}
    >
      <Badge variant="outline" className="font-normal normal-case">
        {ev.kind}
      </Badge>
      <span>
        conf{" "}
        <span className="font-mono text-foreground/80">
          {ev.confidence.toFixed(2)}
        </span>
      </span>
      {ev.validation && (
        <span
          title={
            ev.validation === "entailed"
              ? "NLI entailed: section text supports this observation."
              : ev.validation === "contradicted"
                ? "NLI contradicted: section text contradicts this observation."
                : "NLI neutral: weak evidence."
          }
          className={cn(
            "cursor-help underline decoration-dotted underline-offset-2",
            ev.validation === "entailed" && "text-[hsl(var(--success))]",
            ev.validation === "contradicted" && "text-destructive",
          )}
        >
          nli {ev.validation}
          {ev.validation_score !== null && (
            <span className="ml-1 font-mono">
              {ev.validation_score?.toFixed(2)}
            </span>
          )}
        </span>
      )}
      {ev.url && !compact && (
        <a
          href={ev.url}
          target="_blank"
          rel="noreferrer"
          className="normal-case inline-flex items-center gap-1 text-foreground/70 hover:text-foreground"
          onClick={(e) => e.stopPropagation()}
        >
          <ExternalLink className="h-3 w-3" />
          <span className="truncate max-w-[200px]">{prettyUrl(ev.url)}</span>
        </a>
      )}
    </div>
  );
}

function prettyUrl(url: string): string {
  try {
    const u = new URL(url);
    return (
      u.hostname.replace(/^www\./, "") +
      (u.pathname === "/" ? "" : u.pathname)
    );
  } catch {
    return url;
  }
}
