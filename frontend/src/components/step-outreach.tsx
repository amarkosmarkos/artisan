"use client";

import * as React from "react";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Check,
  Copy,
  Flame,
  ShieldAlert,
  TrendingUp,
  XOctagon,
  AlertTriangle,
  Wrench,
  Lightbulb,
} from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useEvidenceLookup } from "@/components/claim-evidence";
import { EvidenceList, ExpandableEvidence } from "./evidence-popover";
import { SelectedValuePropositionCard } from "./selected-vp-card";
import { cn } from "@/lib/utils";
import type {
  Angle,
  ClaimStatus,
  Email,
  TargetResponse,
} from "@/lib/types";

interface Props {
  result: TargetResponse;
  onBack: () => void;
  onShowAnalytics: () => void;
  onNewTarget?: () => void;
}

type BadgeVariant =
  | "default"
  | "secondary"
  | "outline"
  | "success"
  | "warning"
  | "destructive"
  | "muted";

const FIT_VARIANT: Record<string, BadgeVariant> = {
  strong: "success",
  plausible: "default",
  weak: "warning",
  none: "destructive",
};

const DECISION_LABEL: Record<string, string> = {
  contact: "Contact",
  wait_for_trigger: "Wait for trigger",
  skip: "Skip",
};

const DECISION_VARIANT: Record<string, BadgeVariant> = {
  contact: "default",
  wait_for_trigger: "warning",
  skip: "destructive",
};

export function StepOutreach({
  result,
  onBack,
  onShowAnalytics,
  onNewTarget,
}: Props) {
  const evidenceIds = React.useMemo(() => {
    const ids = result.observations.map((o) => o.observation_id);
    for (const a of result.strategy.strategy.angles) {
      ids.push(...a.evidence_refs);
    }
    for (const e of result.emails) {
      for (const c of e.claims) ids.push(...c.evidence_refs);
    }
    if (result.selected_value_proposition) {
      ids.push(...result.selected_value_proposition.evidence_refs);
    }
    return ids;
  }, [result]);
  const evidenceById = useEvidenceLookup(evidenceIds);

  const { strategy, emails } = result;
  const fit = strategy.fit_assessment;
  const personaAlignment = strategy.strategy.persona_alignment;
  const angles = strategy.strategy.angles;

  return (
    <motion.div
      key="outreach"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -16 }}
      transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
      className="w-full max-w-6xl mx-auto"
    >
      <div className="mb-6 flex items-center justify-between gap-3">
        <Button variant="ghost" size="sm" onClick={onBack}>
          <ArrowLeft className="h-4 w-4" /> ICP
        </Button>
        <div className="flex gap-2">
          {onNewTarget && (
            <Button variant="default" size="sm" onClick={onNewTarget}>
              <ArrowRight className="h-4 w-4" /> Add another target
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={onShowAnalytics}>
            <BarChart3 className="h-4 w-4" /> Analytics
          </Button>
        </div>
      </div>

      <div className="mb-6">
        <SelectedValuePropositionCard
          strategy={strategy}
          selectedVp={result.selected_value_proposition ?? null}
          senderVps={result.sender_value_propositions}
          evidenceById={evidenceById}
        />
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <CardTitle className="text-base">Fit assessment</CardTitle>
                <CardDescription>
                  {result.persona.role} ({result.persona.seniority})
                </CardDescription>
              </div>
              <div className="flex items-center gap-2">
                <Badge variant={FIT_VARIANT[fit.level] ?? "default"}>
                  {fit.level}
                </Badge>
                <Badge
                  variant={
                    DECISION_VARIANT[strategy.strategy.contact_decision] ?? "default"
                  }
                >
                  {DECISION_LABEL[strategy.strategy.contact_decision]}
                </Badge>
              </div>
            </div>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            {fit.reasons.length > 0 && (
              <Bullets
                title="Reasons"
                icon={<Check className="h-3.5 w-3.5 text-[hsl(var(--success))]" />}
                items={fit.reasons}
              />
            )}
            {fit.risks.length > 0 && (
              <Bullets
                title="Risks"
                icon={<ShieldAlert className="h-3.5 w-3.5 text-[hsl(var(--warning))]" />}
                items={fit.risks}
              />
            )}
            {fit.missing_evidence.length > 0 && (
              <Bullets
                title="Missing evidence"
                icon={<AlertTriangle className="h-3.5 w-3.5 text-muted-foreground" />}
                items={fit.missing_evidence}
              />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Persona alignment</CardTitle>
            <CardDescription>How the angle is framed</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-sm">
            <PersonaRow
              label="Role relevance"
              reason={personaAlignment.role_relevance_reason}
            >
              <Badge variant="outline" className="capitalize">
                {personaAlignment.role_relevance}
              </Badge>
            </PersonaRow>
            <PersonaRow
              label="Preferred framing"
              reason={personaAlignment.preferred_framing_reason}
            >
              <span className="text-foreground/90 text-right">
                {personaAlignment.preferred_framing || "—"}
              </span>
            </PersonaRow>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Avoid
              </p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {personaAlignment.avoid.length > 0 ? (
                  personaAlignment.avoid.map((a) => (
                    <Badge key={a} variant="muted" className="font-normal">
                      {a}
                    </Badge>
                  ))
                ) : (
                  <span className="text-xs italic text-muted-foreground">—</span>
                )}
              </div>
              {personaAlignment.avoid_reason && (
                <p className="mt-1.5 text-xs text-muted-foreground leading-snug">
                  {personaAlignment.avoid_reason}
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {angles.length > 0 && (
        <div className="mt-6">
          <StrategyAnglesPanel angles={angles} evidenceById={evidenceById} />
        </div>
      )}

      <div className="mt-6 grid gap-6 md:grid-cols-2">
        {emails.map((email) => (
          <EmailCard
            key={email.email_id}
            email={email}
            evidenceById={evidenceById}
          />
        ))}
      </div>

      {emails.length === 0 && (
        <Card className="mt-6 border-dashed">
          <CardContent className="py-8 text-center">
            <Lightbulb className="h-5 w-5 mx-auto mb-2 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              No emails were generated. The system refused to write outbound
              copy because the target evidence was insufficient to support
              concrete claims.
            </p>
          </CardContent>
        </Card>
      )}
    </motion.div>
  );
}

function StrategyAnglesPanel({
  angles,
  evidenceById,
}: {
  angles: Angle[];
  evidenceById: Map<string, import("@/lib/api").EvidenceRecord>;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Strategy angles</CardTitle>
        <CardDescription>
          Hypotheses that drive each email. Evidence is on the right.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {angles.map((a) => {
          const meta = ANGLE_META[a.type] ?? ANGLE_META.pain_led;
          return (
            <div
              key={a.type}
              className="rounded-md border border-border/60 p-3"
            >
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
                  {meta.icon}
                  {meta.label}
                </div>
                <ExpandableEvidence count={a.evidence_refs.length}>
                  <EvidenceList
                    evidenceRefs={a.evidence_refs}
                    evidenceById={evidenceById}
                  />
                </ExpandableEvidence>
              </div>
              <p className="mt-2 text-sm italic text-foreground/90 leading-snug">
                &ldquo;{a.hypothesis}&rdquo;
              </p>
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

const ANGLE_META: Record<
  string,
  { label: string; icon: React.ReactNode }
> = {
  pain_led:    { label: "Pain-led",    icon: <Flame className="h-3.5 w-3.5" /> },
  trigger_led: { label: "Trigger-led", icon: <TrendingUp className="h-3.5 w-3.5" /> },
  outcome_led: { label: "Outcome-led", icon: <Lightbulb className="h-3.5 w-3.5" /> },
};

function EmailCard({
  email,
  evidenceById,
}: {
  email: Email;
  evidenceById: Map<string, import("@/lib/api").EvidenceRecord>;
}) {
  const angleMeta = ANGLE_META[email.angle] ?? ANGLE_META.pain_led;

  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(`${email.subject}\n\n${email.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b border-border/60 pb-4">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
            {angleMeta.icon}
            {angleMeta.label}
          </div>
          <Button size="sm" variant="ghost" onClick={copy}>
            <Copy className="h-3.5 w-3.5" />
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <CardTitle className="text-base mt-2 leading-snug">
          {email.subject}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 pt-5">
        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-foreground/90">
          {email.body}
        </pre>

        <div className="border-t border-border/60 pt-4">
          <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-2">
            Claims ({email.claims.length})
          </p>
          {email.claims.length === 0 ? (
            <p className="text-xs italic text-muted-foreground">
              No grounded claims. The writer fell back to a generic body.
            </p>
          ) : (
            <ul className="space-y-2.5">
              {email.claims.map((c) => (
                <li
                  key={c.claim_id}
                  className="rounded-md border border-border/60 p-2.5"
                >
                  <div className="flex items-start gap-2">
                    <ClaimStatusBadge status={c.status} score={c.nli_score} />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm leading-snug text-foreground/90">{c.text}</p>
                      {c.repaired_text && (
                        <p className="mt-1 text-xs text-muted-foreground italic">
                          <Wrench className="inline h-3 w-3 mr-1" />
                          repaired from previous version
                        </p>
                      )}
                      <div className="mt-1.5">
                        <ExpandableEvidence count={c.evidence_refs.length}>
                          <EvidenceList
                            evidenceRefs={c.evidence_refs}
                            evidenceById={evidenceById}
                          />
                        </ExpandableEvidence>
                      </div>
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

type ClaimVariant =
  | "default"
  | "secondary"
  | "outline"
  | "success"
  | "warning"
  | "destructive"
  | "muted";

const CLAIM_STATUS_META: Record<
  ClaimStatus,
  {
    label: string;
    variant: ClaimVariant;
    icon: React.ReactNode;
    explain: string;
  }
> = {
  entailed: {
    label: "supported",
    variant: "success",
    icon: <Check className="h-3 w-3" />,
    explain:
      "NLI entailed: the cited evidence directly supports this claim.",
  },
  repaired: {
    label: "repaired",
    variant: "default",
    icon: <Wrench className="h-3 w-3" />,
    explain:
      "Original draft was unsupported; the writer rewrote the sentence to match available evidence.",
  },
  neutral: {
    label: "neutral",
    variant: "muted",
    icon: <AlertTriangle className="h-3 w-3" />,
    explain:
      "NLI neutral: the evidence neither implies nor contradicts the claim. Treat as weak support.",
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
      "NLI contradicted: the evidence directly contradicts the claim. Do not send as-is.",
  },
};

function ClaimStatusBadge({
  status,
  score,
}: {
  status: ClaimStatus;
  score: number | null;
}) {
  const m = CLAIM_STATUS_META[status];
  return (
    <Badge variant={m.variant} className="shrink-0" title={m.explain}>
      {m.icon}
      <span className="ml-0.5">{m.label}</span>
      {score !== null && (
        <span className="ml-1 font-mono text-[10px] opacity-70">
          {score.toFixed(2)}
        </span>
      )}
    </Badge>
  );
}

function PersonaRow({
  label,
  reason,
  children,
}: {
  label: string;
  reason?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        {children}
      </div>
      {reason && (
        <p className="mt-1 text-xs text-muted-foreground leading-snug">
          {reason}
        </p>
      )}
    </div>
  );
}

function Bullets({
  title,
  icon,
  items,
}: {
  title: string;
  icon: React.ReactNode;
  items: string[];
}) {
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground mb-1.5">
        {title}
      </p>
      <ul className={cn("space-y-1.5")}>
        {items.map((r, i) => (
          <li key={i} className="flex items-start gap-2">
            <span className="mt-0.5">{icon}</span>
            <span className="leading-snug">{r}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
