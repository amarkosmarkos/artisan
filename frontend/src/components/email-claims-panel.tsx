"use client";

import * as React from "react";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  XOctagon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  EvidenceCard,
  evidenceFromObservation,
} from "@/components/claim-evidence";
import type { EvidenceRecord } from "@/lib/api";
import type {
  Email,
  EmailClaim,
  StatementContextRef,
} from "@/lib/types";
import { cn } from "@/lib/utils";

type BadgeVariant =
  | "default"
  | "secondary"
  | "outline"
  | "success"
  | "warning"
  | "destructive"
  | "muted";

const SCOPE_META: Record<
  EmailClaim["scope"],
  { label: string; variant: BadgeVariant; hint: string }
> = {
  general: {
    label: "general",
    variant: "muted",
    hint: "Broad industry knowledge — no retrieval evidence required.",
  },
  sender: {
    label: "sender",
    variant: "default",
    hint: "Specific claim about the sender — must be backed by retrieval.",
  },
  target: {
    label: "target",
    variant: "default",
    hint: "Specific claim about the target — must be backed by retrieval.",
  },
};

function refKind(refId: string): string {
  if (refId.startsWith("obs_") || refId.startsWith("sender:obs_")) {
    return "observation";
  }
  if (refId.startsWith("vp:")) return "value prop";
  if (refId.startsWith("icp:")) return "ICP";
  if (refId.startsWith("strategy:")) return "strategy";
  if (refId.startsWith("persona:")) return "persona";
  if (refId.startsWith("target:")) return "target";
  if (refId.startsWith("sender:")) return "sender";
  return "ref";
}

function ConfidenceBadge({
  value,
  size = "sm",
}: {
  value: number | null | undefined;
  size?: "sm" | "xs";
}) {
  const v = typeof value === "number" ? value : null;
  if (v === null) return null;
  let variant: BadgeVariant = "muted";
  if (v >= 0.8) variant = "success";
  else if (v >= 0.5) variant = "default";
  else if (v > 0) variant = "warning";
  return (
    <Badge
      variant={variant}
      className={size === "xs" ? "text-[10px]" : "text-[10px]"}
      title="Confidence in this verdict (0.0 – 1.0)."
    >
      conf {v.toFixed(2)}
    </Badge>
  );
}

function ClaimVerdictBadge({ claim }: { claim: EmailClaim }) {
  if (claim.scope === "general") {
    return (
      <Badge
        variant="muted"
        className="text-[10px]"
        title="General-knowledge claim — no evidence check required."
      >
        general knowledge
      </Badge>
    );
  }
  if (claim.grounded === true) {
    return (
      <Badge
        variant="success"
        className="text-[10px]"
        title="Grounded by the cited retrieval evidence."
      >
        <Check className="h-3 w-3" />
        <span className="ml-0.5">grounded</span>
      </Badge>
    );
  }
  if (claim.grounded === false) {
    return (
      <Badge
        variant="destructive"
        className="text-[10px]"
        title="Cited evidence does not support this claim."
      >
        <AlertTriangle className="h-3 w-3" />
        <span className="ml-0.5">not grounded</span>
      </Badge>
    );
  }
  return (
    <Badge variant="muted" className="text-[10px]">
      pending
    </Badge>
  );
}

function ContextRefRow({ ref: ctxRef }: { ref: StatementContextRef | null | undefined }) {
  if (!ctxRef?.ref_id) {
    return (
      <div className="rounded-md border border-border/40 bg-muted/10 p-2 text-xs text-muted-foreground italic">
        Evidence snippet unavailable.
      </div>
    );
  }
  return (
    <div className="rounded-md border border-border/50 bg-muted/20 p-2.5">
      <div className="flex flex-wrap items-center gap-1.5 mb-1">
        <Badge variant="outline" className="font-mono text-[10px]">
          {refKind(ctxRef.ref_id)} · {ctxRef.ref_id}
        </Badge>
        {ctxRef.label && (
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            {ctxRef.label}
          </span>
        )}
      </div>
      <p className="text-xs leading-snug text-foreground/85">
        {ctxRef.snippet || "(no snippet)"}
      </p>
    </div>
  );
}

function ObservationRefRow({
  refId,
  record,
}: {
  refId: string;
  record: EvidenceRecord | undefined;
}) {
  if (!record) {
    return (
      <div className="rounded-md border border-border/40 bg-muted/10 p-2 text-xs text-muted-foreground">
        <span className="font-mono">{refId}</span> — section text loading…
      </div>
    );
  }
  return <EvidenceCard ev={record} compact />;
}

function ClaimRow({
  claim,
  evidenceById,
}: {
  claim: EmailClaim;
  evidenceById: Map<string, EvidenceRecord>;
}) {
  const isGeneral = claim.scope === "general";
  const canExpand = !isGeneral;
  const [open, setOpen] = React.useState(
    !isGeneral && claim.grounded === false,
  );
  const refs = claim.evidence_refs ?? [];
  const evidence = (claim.evidence ?? []).filter(
    (e): e is StatementContextRef => Boolean(e?.ref_id),
  );

  const obsRefs = refs.filter(
    (r) => r.startsWith("obs_") || r.startsWith("sender:obs_"),
  );
  const obsLookupIds = obsRefs.map((r) =>
    r.startsWith("sender:") ? r.slice("sender:".length) : r,
  );
  const nonObs = evidence.filter(
    (e) =>
      !e.ref_id.startsWith("obs_") && !e.ref_id.startsWith("sender:obs_"),
  );
  const hasExpandableEvidence = obsRefs.length > 0 || nonObs.length > 0;

  const scopeMeta = SCOPE_META[claim.scope] ?? SCOPE_META.general;
  const showFailure =
    !isGeneral && claim.grounded === false && Boolean(claim.reason);

  const body = (
    <div className="min-w-0 flex-1">
      <p className="text-sm leading-snug text-foreground/90">{claim.text}</p>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
        <ClaimVerdictBadge claim={claim} />
        <Badge
          variant={scopeMeta.variant}
          className="text-[10px]"
          title={scopeMeta.hint}
        >
          {scopeMeta.label}
        </Badge>
        {!isGeneral && <ConfidenceBadge value={claim.confidence} />}
        {!isGeneral && refs.length > 0 && (
          <Badge variant="outline" className="font-mono text-[10px]">
            {refs.length} evidence ref{refs.length === 1 ? "" : "s"}
          </Badge>
        )}
      </div>
      {isGeneral && (
        <p className="mt-1.5 text-[11px] leading-snug text-muted-foreground">
          Broad market or industry knowledge — no page-level retrieval
          evidence is required for this claim.
        </p>
      )}
      {showFailure && (
        <p className="mt-1 text-[11px] text-destructive/90">{claim.reason}</p>
      )}
    </div>
  );

  if (!canExpand) {
    return (
      <div className="rounded-md border border-border/60 bg-muted/10 px-3 py-2.5">
        {body}
      </div>
    );
  }

  return (
    <div
      className={cn(
        "rounded-md border bg-card/30",
        claim.grounded === false
          ? "border-destructive/40"
          : "border-border/60",
      )}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left"
        aria-expanded={open}
      >
        <ChevronRight
          className={cn(
            "h-4 w-4 mt-0.5 shrink-0 text-muted-foreground transition-transform",
            open && "rotate-90",
          )}
        />
        {body}
      </button>

      {open && (
        <div className="space-y-2 border-t border-border/60 px-3 py-2.5">
          {!hasExpandableEvidence && (
            <p className="text-xs italic text-muted-foreground">
              No retrieval snippets were hydrated for the cited refs. The
              writer may have cited invalid ref_ids, or the section text is
              still loading.
            </p>
          )}
          {nonObs.map((r) => (
            <ContextRefRow key={r.ref_id} ref={r} />
          ))}
          {obsRefs.map((origRef, i) => {
            const lookupId = obsLookupIds[i];
            return (
              <ObservationRefRow
                key={origRef}
                refId={origRef}
                record={evidenceById.get(lookupId)}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}

export function EmailClaimsPanel({
  email,
  evidenceById,
  compact,
}: {
  email: Email;
  evidenceById: Map<string, EvidenceRecord>;
  compact?: boolean;
}) {
  const safety = email.safety;
  const claims = email.claims ?? [];
  const ungrounded = claims.filter(
    (c) => c.scope !== "general" && c.grounded === false,
  ).length;

  return (
    <div
      className={
        compact
          ? "space-y-4 pt-2"
          : "border-t border-border/60 pt-4 space-y-4"
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Guardrail
        </p>
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
            <ConfidenceBadge value={safety.confidence} />
            {!safety.verification_ok && (
              <Badge
                variant="destructive"
                className="text-[10px]"
                title="Guardrail LLM failed; cannot confirm safety."
              >
                verifier unavailable
              </Badge>
            )}
            {safety.email_regenerated && (
              <Badge variant="outline" className="text-[10px]">
                regenerated ×{safety.regeneration_count}
              </Badge>
            )}
          </>
        ) : (
          <Badge variant="muted" className="text-[10px]">
            not run
          </Badge>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Claims used in this email
          </p>
          <Badge variant="outline" className="text-[10px] font-mono">
            {claims.length}
          </Badge>
          {ungrounded > 0 && (
            <Badge variant="warning" className="text-[10px]">
              {ungrounded} not grounded
            </Badge>
          )}
        </div>
        {claims.length === 0 ? (
          <p className="text-xs italic text-muted-foreground">
            The writer declared no claims for this email — the guardrail
            could not verify it.
          </p>
        ) : (
          <div className="space-y-2">
            {claims.map((c) => (
              <ClaimRow
                key={c.claim_id}
                claim={c}
                evidenceById={evidenceById}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// Tiny re-export so other panels can render a single observation row in
// the same style if they want.
export { evidenceFromObservation };
