"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { ArrowRight, Loader2, Target } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  evidenceFromObservation,
  useEvidenceLookup,
} from "@/components/claim-evidence";
import { ExpandableEvidence, EvidenceList } from "./evidence-popover";
import { SuggestedTargetsPanel } from "./suggested-targets-panel";
import { SectionHeading } from "@/components/section-heading";
import type {
  FieldWithEvidence,
  ICP,
  PersonaInput,
  Seniority,
  SenderResponse,
  ValueProposition,
} from "@/lib/types";

interface Props {
  sender: SenderResponse;
  onContinue: (input: { target_url: string; persona: PersonaInput }) => void;
  running: boolean;
}

export function StepIcp({ sender, onContinue, running }: Props) {
  const [targetUrl, setTargetUrl] = React.useState("");
  const [role, setRole] = React.useState("VP of Sales");
  const [seniority, setSeniority] = React.useState<Seniority>("vp");
  const [name, setName] = React.useState("");

  const evidenceIds = React.useMemo(
    () => collectSenderEvidenceIds(sender),
    [sender],
  );
  const resolvedEvidenceById = useEvidenceLookup(evidenceIds);
  const evidenceById = React.useMemo(() => {
    const seeded = new Map(
      sender.observations.map((o) => [
        o.observation_id,
        evidenceFromObservation(o),
      ]),
    );
    for (const [id, ev] of resolvedEvidenceById) {
      seeded.set(id, ev);
    }
    return seeded;
  }, [sender.observations, resolvedEvidenceById]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetUrl.trim() || running) return;
    const trimmedName = name.trim();
    onContinue({
      target_url: targetUrl.trim(),
      persona: {
        role,
        seniority,
        ...(trimmedName ? { name: trimmedName } : {}),
      },
    });
  };

  return (
    <motion.div
      key="icp"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -16 }}
      transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
      className="w-full max-w-6xl mx-auto"
    >
      <div className="mb-8">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Target className="h-4 w-4" />
          <span>Sender research complete</span>
          <Badge variant="muted" className="ml-2">
            {sender.observations.length} observations
          </Badge>
        </div>
        <h2 className="mt-2 text-3xl font-semibold tracking-tight">
          {hostnameOf(sender.sender_url)}
        </h2>
      </div>

      <div className="space-y-8">
        <ICPCard icp={sender.icp} evidenceById={evidenceById} />
        <div className="space-y-4">
          <SectionHeading
            level="section"
            title={
              vpsForDisplay(sender).length > 1
                ? "Value propositions"
                : "Value proposition"
            }
            description="Distinct offerings inferred from sender evidence."
          />
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {vpsForDisplay(sender).map((vp, i) => (
              <VPCard
                key={vp.id || `vp-${i}`}
                vp={vp}
                evidenceById={evidenceById}
                showLabel={
                  (sender.value_propositions?.length ?? 0) > 1 ||
                  Boolean(vp.label)
                }
              />
            ))}
          </div>
          {vpsForDisplay(sender).length === 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Value Proposition</CardTitle>
                <CardDescription>
                  No value proposition was synthesized from the sender
                  evidence.
                </CardDescription>
              </CardHeader>
            </Card>
          )}
        </div>
      </div>

      <SuggestedTargetsPanel
        senderCompanyId={sender.company_id}
        initialDiscovery={sender.suggested_targets ?? null}
        onGenerateOutreach={onContinue}
        onPrefillEvaluate={({ target_url, persona }) => {
          setTargetUrl(target_url);
          setRole(persona.role);
          setSeniority(persona.seniority);
          setName((persona.name ?? "").trim());
        }}
        running={running}
      />

      <form
        onSubmit={submit}
        className="mt-10 rounded-xl border border-border bg-card/40 p-6"
      >
        <h3 className="text-lg font-medium">Evaluate a target</h3>
        <p className="text-sm text-muted-foreground">
          We&apos;ll fit-check the target against this ICP and draft two grounded outreach
          angles for the persona below.
        </p>
        <div className="mt-6 grid gap-4 md:grid-cols-[1fr,160px,200px,160px,auto]">
          <Input
            placeholder="https://target-company.com"
            value={targetUrl}
            onChange={(e) => setTargetUrl(e.target.value)}
            disabled={running}
          />
          <Input
            placeholder="Recipient name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={running}
          />
          <Input
            placeholder="Recipient role"
            value={role}
            onChange={(e) => setRole(e.target.value)}
            disabled={running}
          />
          <Select
            value={seniority}
            onValueChange={(v) => setSeniority(v as Seniority)}
            disabled={running}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="ic">IC</SelectItem>
              <SelectItem value="manager">Manager</SelectItem>
              <SelectItem value="director">Director</SelectItem>
              <SelectItem value="vp">VP</SelectItem>
              <SelectItem value="c_level">C-Level</SelectItem>
              <SelectItem value="founder">Founder</SelectItem>
            </SelectContent>
          </Select>
          <Button type="submit" disabled={running || !targetUrl.trim()}>
            {running ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Generating
              </>
            ) : (
              <>
                Generate Outreach <ArrowRight className="h-4 w-4" />
              </>
            )}
          </Button>
        </div>
      </form>
    </motion.div>
  );
}

function vpsForDisplay(sender: SenderResponse): ValueProposition[] {
  if (sender.value_propositions && sender.value_propositions.length > 0) {
    return sender.value_propositions.filter(
      (vp): vp is ValueProposition => Boolean(vp),
    );
  }
  return sender.value_proposition ? [sender.value_proposition] : [];
}

function collectSenderEvidenceIds(sender: SenderResponse): string[] {
  const ids: string[] = [];
  for (const vp of vpsForDisplay(sender)) {
    ids.push(...(vp.evidence_refs ?? []));
  }
  if (sender.icp) {
    const fields = [
      sender.icp.target_industries,
      sender.icp.size_bands,
      sender.icp.likely_buyers,
      sender.icp.common_triggers,
      sender.icp.negative_icp,
    ];
    for (const f of fields) ids.push(...(f?.evidence_refs ?? []));
  }
  return ids;
}

function ICPCard({
  icp,
  evidenceById,
}: {
  icp: ICP;
  evidenceById: Map<string, import("@/lib/api").EvidenceRecord>;
}) {
  const fields: { key: string; label: string; field: FieldWithEvidence }[] = [
    { key: "target_industries", label: "Target industries", field: icp.target_industries },
    { key: "size_bands",        label: "Size bands",        field: icp.size_bands },
    { key: "likely_buyers",     label: "Likely buyers",     field: icp.likely_buyers },
    { key: "common_triggers",   label: "Common triggers",   field: icp.common_triggers },
    { key: "negative_icp",      label: "Negative ICP",      field: icp.negative_icp },
  ];
  return (
    <Card className="accent-sender bg-sender-soft">
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">Ideal Customer Profile</CardTitle>
        <CardDescription>Structured, evidence-grounded</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
        {fields.map(({ key, label, field }) => (
          <div key={key} className="rounded-lg border border-border/50 bg-background/40 p-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                {label}
              </p>
              <ConfidenceBar confidence={field.confidence} />
            </div>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {field.values.length > 0 ? (
                field.values.map((v) => (
                  <Badge key={v} variant="secondary" className="font-normal">
                    {v}
                  </Badge>
                ))
              ) : (
                <span className="text-xs italic text-muted-foreground">
                  no evidence found
                </span>
              )}
            </div>
            <div className="mt-1.5">
              <ExpandableEvidence count={field.evidence_refs.length}>
                <EvidenceList
                  evidenceRefs={field.evidence_refs}
                  evidenceById={evidenceById}
                />
              </ExpandableEvidence>
            </div>
          </div>
        ))}
        </div>
      </CardContent>
    </Card>
  );
}

function VPCard({
  vp,
  evidenceById,
  showLabel = false,
}: {
  vp: ValueProposition;
  evidenceById: Map<string, import("@/lib/api").EvidenceRecord>;
  showLabel?: boolean;
}) {
  const fields: { label: string; value: string }[] = [
    { label: "Customer",  value: vp.customer },
    { label: "Pain",      value: vp.pain },
    { label: "Outcome",   value: vp.outcome },
    { label: "Mechanism", value: vp.mechanism },
  ];
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-base">
          {showLabel && vp.label ? vp.label : "Value Proposition"}
        </CardTitle>
        <CardDescription>
          <span className="flex items-center gap-2">
            <ConfidenceBar confidence={vp.confidence} />
            <span>
              backed by {vp.evidence_refs.length} observation
              {vp.evidence_refs.length === 1 ? "" : "s"}
            </span>
          </span>
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {fields.map(({ label, value }) => (
          <div key={label}>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {label}
            </p>
            {value ? (
              <p className="mt-1 text-sm leading-relaxed">{value}</p>
            ) : (
              <p className="mt-1 text-xs italic text-muted-foreground">
                no evidence found
              </p>
            )}
          </div>
        ))}
        <div>
          <ExpandableEvidence count={vp.evidence_refs.length}>
            <EvidenceList
              evidenceRefs={vp.evidence_refs}
              evidenceById={evidenceById}
            />
          </ExpandableEvidence>
        </div>
      </CardContent>
    </Card>
  );
}

function ConfidenceBar({ confidence }: { confidence: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(confidence * 100)));
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1 w-16 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full bg-foreground/70"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
        {pct}%
      </span>
    </div>
  );
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
