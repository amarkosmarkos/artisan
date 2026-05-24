"use client";

import * as React from "react";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Check,
  Copy,
  ShieldAlert,
  AlertTriangle,
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
import { SectionHeading } from "@/components/section-heading";
import { getAngleMeta } from "@/lib/angle-meta";
import { cn } from "@/lib/utils";
import type {
  Angle,
  Email,
  TargetResponse,
} from "@/lib/types";
import { EmailClaimsPanel } from "@/components/email-claims-panel";

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
      for (const c of e.claims ?? []) {
        for (const ref of c.evidence_refs ?? []) {
          if (ref.startsWith("obs_")) {
            ids.push(ref);
          } else if (ref.startsWith("sender:obs_")) {
            ids.push(ref.slice("sender:".length));
          }
        }
      }
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

      <div className="mb-8">
        <SectionHeading
          level="section"
          title="Fit & strategy"
          description="Assessment, persona alignment, and recommended angles before the final copy."
        />
      </div>

      <div className="grid gap-6 md:grid-cols-3">
        <Card className="md:col-span-2">
          <CardHeader>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <CardTitle className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                  Fit assessment
                </CardTitle>
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
            <CardTitle className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Persona alignment
            </CardTitle>
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
        <div className="mt-10">
          <StrategyAnglesPanel angles={angles} evidenceById={evidenceById} />
        </div>
      )}

      <div className="mt-12 space-y-6">
        <SectionHeading
          level="page"
          title="Generated emails"
          description="Final outreach copy — ready to review, copy, and send."
        />
        {emails.length > 0 ? (
          <div className="space-y-8">
            {emails.map((email) => (
              <EmailCard
                key={email.email_id}
                email={email}
                evidenceById={evidenceById}
              />
            ))}
          </div>
        ) : (
          <Card className="border-dashed">
            <CardContent className="py-10 text-center">
              <Lightbulb className="h-5 w-5 mx-auto mb-2 text-muted-foreground" />
              <p className="text-sm text-muted-foreground">
                No emails were generated. The system refused to write outbound
                copy because the target evidence was insufficient to support
                concrete claims.
              </p>
            </CardContent>
          </Card>
        )}
      </div>
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
        <CardTitle className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Strategy angles
        </CardTitle>
        <CardDescription>
          Hypotheses that drive each email. Evidence is on the right.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {angles.map((a) => {
          const meta = getAngleMeta(a.type);
          return (
            <div
              key={a.type}
              className="rounded-md border border-border/60 p-3"
            >
              <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className={cn("flex items-center gap-2 text-xs uppercase tracking-wide font-medium", meta.tone)}>
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

function EmailCard({
  email,
  evidenceById,
}: {
  email: Email;
  evidenceById: Map<string, import("@/lib/api").EvidenceRecord>;
}) {
  const angleMeta = getAngleMeta(email.angle);

  const [copied, setCopied] = React.useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(`${email.subject}\n\n${email.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  };

  return (
    <Card className="overflow-hidden border-2 border-[hsl(var(--sender))]/25 bg-gradient-to-br from-card via-card to-[hsl(var(--sender))]/5 shadow-sm">
      <CardHeader className="border-b border-border/60 bg-background/50 px-6 py-5">
        <div className="flex items-center justify-between gap-2">
          <div className={cn("flex items-center gap-2 text-xs uppercase tracking-wide font-semibold", angleMeta.tone)}>
            {angleMeta.icon}
            {angleMeta.label}
          </div>
          <Button size="sm" variant="ghost" onClick={copy}>
            <Copy className="h-3.5 w-3.5" />
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <CardTitle className="text-xl md:text-2xl mt-3 leading-snug font-semibold">
          {email.subject}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-5 px-6 py-8">
        <pre className="whitespace-pre-wrap font-sans text-base leading-relaxed text-foreground/90">
          {email.body}
        </pre>

        <EmailClaimsPanel email={email} evidenceById={evidenceById} />
      </CardContent>
    </Card>
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
