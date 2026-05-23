"use client";

import * as React from "react";
import { AlertTriangle, Sparkles } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  EvidenceList,
  ExpandableEvidence,
} from "@/components/evidence-popover";
import type {
  StrategyArtifact,
  ValueProposition,
} from "@/lib/types";
import type { EvidenceRecord } from "@/lib/api";

interface Props {
  strategy: StrategyArtifact;
  selectedVp: ValueProposition | null | undefined;
  senderVps?: ValueProposition[];
  evidenceById?: Map<string, EvidenceRecord>;
}

// Single source of truth for "which value proposition drove this strategy
// and these emails". Renders the selection_id, label, reason, messaging
// angle, and the full VP content. If the backend did not provide a
// resolved VP we surface that as a warning rather than silently hiding the
// card — the bug was that the selection was implicit and invisible.
export function SelectedValuePropositionCard({
  strategy,
  selectedVp,
  senderVps,
  evidenceById,
}: Props) {
  const selectedId = strategy.selected_value_proposition_id ?? null;
  const label =
    strategy.selected_value_proposition_label || selectedVp?.label || "";
  const reason = strategy.selection_reason || "";
  const angle = strategy.messaging_angle || "";

  const alternatives = (senderVps ?? []).filter(
    (vp) => vp.id && vp.id !== selectedId,
  );

  if (!selectedVp && !selectedId) {
    return (
      <Card className="border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/5">
        <CardHeader className="pb-2">
          <div className="flex items-center gap-2">
            <AlertTriangle className="h-4 w-4 text-[hsl(var(--warning))]" />
            <CardTitle className="text-sm">
              No value proposition was selected
            </CardTitle>
          </div>
          <CardDescription>
            The strategy did not record a selected value proposition. Emails
            below may have been generated from a fallback. Re-run outreach if
            this looks wrong.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <Card className="accent-sender bg-sender-soft">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              <Sparkles className="h-3 w-3" />
              Selected value proposition
            </div>
            <CardTitle className="mt-1 text-base">
              {label || selectedVp?.label || "Primary offering"}
            </CardTitle>
            {selectedId && (
              <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                {selectedId}
              </p>
            )}
          </div>
          {selectedVp && (
            <Badge variant="sender">
              conf {selectedVp.confidence.toFixed(2)}
            </Badge>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {reason && (
          <Section label="Why this one">
            <p className="leading-relaxed">{reason}</p>
          </Section>
        )}
        {angle && (
          <Section label="Messaging angle">
            <p className="leading-relaxed italic">&ldquo;{angle}&rdquo;</p>
          </Section>
        )}

        {selectedVp && (
          <div className="grid gap-3 sm:grid-cols-2">
            <VPField label="Customer" value={selectedVp.customer} />
            <VPField label="Pain" value={selectedVp.pain} />
            <VPField label="Outcome" value={selectedVp.outcome} />
            <VPField label="Mechanism" value={selectedVp.mechanism} />
          </div>
        )}

        {selectedVp && selectedVp.evidence_refs.length > 0 && (
          <Section label="Supporting evidence">
            <ExpandableEvidence count={selectedVp.evidence_refs.length}>
              <EvidenceList
                evidenceRefs={selectedVp.evidence_refs}
                evidenceById={evidenceById}
              />
            </ExpandableEvidence>
          </Section>
        )}

        {alternatives.length > 0 && (
          <Section label={`Other available value propositions (${alternatives.length})`}>
            <div className="flex flex-wrap gap-1.5">
              {alternatives.map((vp) => (
                <Badge
                  key={vp.id || vp.label}
                  variant="muted"
                  className="font-normal"
                  title={vp.id}
                >
                  {vp.label || "untitled"}
                </Badge>
              ))}
            </div>
          </Section>
        )}
      </CardContent>
    </Card>
  );
}

function Section({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {children}
    </div>
  );
}

function VPField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {value ? (
        <p className="mt-0.5 text-sm leading-relaxed">{value}</p>
      ) : (
        <p className="mt-0.5 text-xs italic text-muted-foreground">—</p>
      )}
    </div>
  );
}
