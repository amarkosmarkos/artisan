"use client";

import * as React from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Building2,
  ExternalLink,
  Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  ClaimEvidence,
  ObservationEvidenceCard,
  useEvidenceLookup,
} from "@/components/claim-evidence";
import { SenderTargetsPanel } from "@/components/sender-targets-panel";
import {
  deleteCompany,
  getCompanyDetail,
  getCompanyObservations,
  getCompanySources,
  type ObservationRow,
  type PageRow,
  type SenderDetail,
} from "@/lib/api";
import type { FieldWithEvidence, ICP, ValueProposition } from "@/lib/types";
import { cn } from "@/lib/utils";

const ICP_FIELDS: Array<{
  key: keyof ICP;
  label: string;
  description: string;
}> = [
  {
    key: "target_industries",
    label: "Target industries",
    description: "Industries where this product is most relevant.",
  },
  {
    key: "size_bands",
    label: "Company size bands",
    description: "Headcount or revenue ranges typical for the ICP.",
  },
  {
    key: "likely_buyers",
    label: "Likely buyers",
    description: "Roles/personas most likely to evaluate or purchase.",
  },
  {
    key: "common_triggers",
    label: "Common triggers",
    description: "Events that create urgency or need.",
  },
  {
    key: "negative_icp",
    label: "Negative ICP",
    description: "Segments to deprioritize or exclude.",
  },
];

// Observation kinds we surface as first-class buckets on the sender page.
// Anything not in this list shows up under "Other observations".
const FEATURED_KINDS: Array<{ kind: string; label: string; tone: string }> = [
  { kind: "pain", label: "Pain points", tone: "warning" },
  { kind: "trigger", label: "Triggers", tone: "default" },
  { kind: "proof_point", label: "Proof points", tone: "success" },
  { kind: "differentiator", label: "Differentiators", tone: "sender" },
  { kind: "customer", label: "Customers / use cases", tone: "default" },
];

export default function SenderDetailPage() {
  const params = useParams<{ company_id: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const companyId = params?.company_id;

  const detail = useQuery({
    enabled: !!companyId,
    queryKey: ["company", companyId],
    queryFn: () => getCompanyDetail(companyId!),
  });

  const sources = useQuery({
    enabled: !!companyId,
    queryKey: ["company-sources", companyId],
    queryFn: () => getCompanySources(companyId!),
  });

  const observations = useQuery({
    enabled: !!companyId,
    queryKey: ["company-observations", companyId],
    queryFn: () => getCompanyObservations(companyId!),
  });

  const remove = useMutation({
    mutationFn: () => deleteCompany(companyId!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: ["sidebar-senders"] });
      router.push("/");
    },
  });

  if (!companyId) return null;
  if (detail.isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }
  if (detail.isError) {
    return (
      <p className="text-sm text-destructive">
        Failed to load: {String(detail.error)}
      </p>
    );
  }

  const data = detail.data as SenderDetail | undefined;
  if (!data) return null;
  if (data.role !== "sender") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Not a sender</CardTitle>
          <CardDescription>
            This company is registered as a {data.role}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link href={`/targets/${data.company_id}`}>
              Open as target
            </Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const obsRows = observations.data?.observations ?? [];
  const vps =
    data.value_propositions?.length
      ? data.value_propositions
      : data.value_proposition
        ? [data.value_proposition]
        : [];
  const evidenceIdsForArtifacts = collectArtifactEvidenceIds(data.icp, vps);
  return (
    <SenderBody
      data={data}
      sources={sources.data?.pages ?? []}
      obsRows={obsRows}
      sourcesLoading={sources.isLoading}
      observationsLoading={observations.isLoading}
      evidenceIdsForArtifacts={evidenceIdsForArtifacts}
      onDelete={() => {
        if (
          confirm(
            "Delete this sender and all its evidence, ICP, VP, targets and emails?",
          )
        ) {
          remove.mutate();
        }
      }}
      deleting={remove.isPending}
    />
  );
}

function SenderBody({
  data,
  sources,
  obsRows,
  sourcesLoading,
  observationsLoading,
  evidenceIdsForArtifacts,
  onDelete,
  deleting,
}: {
  data: SenderDetail;
  sources: PageRow[];
  obsRows: ObservationRow[];
  sourcesLoading: boolean;
  observationsLoading: boolean;
  evidenceIdsForArtifacts: string[];
  onDelete: () => void;
  deleting: boolean;
}) {
  const allEvidenceIds = React.useMemo(() => {
    const s = new Set(evidenceIdsForArtifacts);
    obsRows.forEach((o) => s.add(o.observation_id));
    return Array.from(s);
  }, [evidenceIdsForArtifacts, obsRows]);
  const evidence = useEvidenceLookup(allEvidenceIds);
  const vps =
    data.value_propositions?.length
      ? data.value_propositions
      : data.value_proposition
        ? [data.value_proposition]
        : [];

  // Group all observations by kind for the bucketed display.
  const grouped = React.useMemo(() => {
    const m = new Map<string, ObservationRow[]>();
    obsRows.forEach((o) => {
      const arr = m.get(o.kind) ?? [];
      arr.push(o);
      m.set(o.kind, arr);
    });
    return m;
  }, [obsRows]);

  const featuredKinds = new Set(FEATURED_KINDS.map((f) => f.kind));
  const otherGroups = Array.from(grouped.entries()).filter(
    ([k]) => !featuredKinds.has(k),
  );

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link href="/">
          <ArrowLeft className="h-4 w-4" />
          Home
        </Link>
      </Button>

      <header className="rounded-xl border border-border/60 bg-sender-soft p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <Building2 className="h-4 w-4 text-[hsl(var(--sender))]" />
              <Badge variant="sender">campaign · sender</Badge>
            </div>
            <h1 className="mt-2 text-2xl font-semibold tracking-tight truncate">
              {prettyUrl(data.url)}
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              {data.company_id} · added {formatDate(data.created_at)}
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <Button variant="outline" size="sm" asChild>
              <a href={data.url} target="_blank" rel="noreferrer">
                <ExternalLink className="h-4 w-4" />
                Visit
              </a>
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={onDelete}
              disabled={deleting}
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </Button>
          </div>
        </div>

        <div className="mt-4 grid gap-3 grid-cols-2 sm:grid-cols-4">
          <Stat label="Pages" value={String(data.counts.pages)} />
          <Stat label="Sections" value={String(data.counts.sections)} />
          <Stat
            label="Observations"
            value={String(data.counts.observations)}
          />
          <Stat
            label="ICP fields"
            value={
              data.icp
                ? String(
                    countNonEmptyFields(data.icp),
                  )
                : "—"
            }
          />
        </div>
      </header>

      {/* --- Value Proposition(s) --- */}
      {vps.length > 0 && (
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">
              Value proposition{vps.length > 1 ? "s" : ""}
            </h2>
            <p className="text-xs text-muted-foreground">
              {vps.length > 1
                ? "Distinct business lines detected from sender evidence"
                : "Why customers buy"}
            </p>
          </div>
          {vps.map((vp, i) => (
            <Card key={vp.id || `vp-${i}`} className="accent-sender bg-sender-soft">
              <CardHeader className="pb-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <CardDescription>Value proposition</CardDescription>
                    <CardTitle className="text-base">
                      {vp.label || (vps.length > 1 ? `Offering ${i + 1}` : "Why customers buy")}
                    </CardTitle>
                  </div>
                  <Badge variant="sender">
                    conf {vp.confidence.toFixed(2)}
                  </Badge>
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <VPField label="Customer" value={vp.customer} />
                <VPField label="Pain" value={vp.pain} />
                <VPField label="Outcome" value={vp.outcome} />
                <VPField label="Mechanism" value={vp.mechanism} />
                <div className="pt-2">
                  <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Anchored evidence
                  </p>
                  <ClaimEvidence
                    claim="Composite VP statement above is grounded on these observations."
                    evidenceIds={vp.evidence_refs}
                    evidence={evidence}
                    tone="claim"
                  />
                </div>
              </CardContent>
            </Card>
          ))}
        </section>
      )}

      {/* --- ICP --- */}
      {data.icp && (
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">
              Ideal customer profile
            </h2>
            <p className="text-xs text-muted-foreground">
              Each field shows the inferred values, confidence, and the
              observations that support it.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {ICP_FIELDS.map((spec) => (
              <ICPCard
                key={spec.key}
                title={spec.label}
                description={spec.description}
                field={data.icp![spec.key]}
                evidence={evidence}
              />
            ))}
          </div>
        </section>
      )}

      {/* --- Featured observation kinds (pains / triggers / proof / differentiators) --- */}
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">
            Extracted signals
          </h2>
          <p className="text-xs text-muted-foreground">
            Atomic observations grouped by kind. Each one cites the section
            it came from and carries its own NLI validation status.
          </p>
        </div>
        <div className="grid gap-3 md:grid-cols-2">
          {FEATURED_KINDS.map((spec) => {
            const list = grouped.get(spec.kind) ?? [];
            if (list.length === 0) return null;
            return (
              <SignalCard
                key={spec.kind}
                title={spec.label}
                count={list.length}
                tone={spec.tone}
                items={list}
                evidence={evidence}
              />
            );
          })}
        </div>
      </section>

      {/* --- Targets panel (existing) --- */}
      <section className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">
            Targets in this campaign
          </h2>
          <p className="text-xs text-muted-foreground">
            Companies you're pursuing under this sender. Each target carries
            its own personas (recipients).
          </p>
        </div>
        <SenderTargetsPanel senderCompanyId={data.company_id} />
      </section>

      {/* --- Other observation kinds --- */}
      {otherGroups.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Other observations</CardTitle>
            <CardDescription>
              Everything else extracted by the pipeline, grouped by kind.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {observationsLoading && (
              <p className="text-sm text-muted-foreground">Loading…</p>
            )}
            <div className="space-y-2">
              {otherGroups.map(([kind, list]) => (
                <details
                  key={kind}
                  className="rounded-md border border-border/60 bg-card/40 px-3 py-2"
                >
                  <summary className="cursor-pointer text-sm font-medium flex items-center gap-2">
                    <Badge variant="outline" className="font-normal">
                      {kind}
                    </Badge>
                    <span className="text-muted-foreground">
                      {list.length}
                    </span>
                  </summary>
                  <ul className="mt-2 space-y-2">
                    {list.slice(0, 30).map((o) => (
                      <li key={o.observation_id}>
                        <ObservationEvidenceCard
                          row={o}
                          evidence={evidence.get(o.observation_id)}
                        />
                      </li>
                    ))}
                    {list.length > 30 && (
                      <li className="text-xs text-muted-foreground">
                        + {list.length - 30} more
                      </li>
                    )}
                  </ul>
                </details>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* --- Sources --- */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Sources</CardTitle>
          <CardDescription>
            Pages fetched, cleaned, and sectioned for this sender.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {sourcesLoading && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {sources.length === 0 && !sourcesLoading && (
            <p className="text-sm text-muted-foreground">
              No pages persisted.
            </p>
          )}
          <ul className="space-y-1.5">
            {sources.map((p) => (
              <li
                key={p.page_id}
                className="flex items-center gap-3 rounded border border-border/40 px-2.5 py-1.5"
              >
                <a
                  href={p.url}
                  target="_blank"
                  rel="noreferrer"
                  className="flex flex-1 min-w-0 items-center gap-2 text-sm hover:text-foreground"
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
        </CardContent>
      </Card>
    </div>
  );
}

function VPField({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/40 bg-background/40 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 text-sm leading-snug text-foreground/90">
        {value || <span className="text-muted-foreground">—</span>}
      </p>
    </div>
  );
}

function ICPCard({
  title,
  description,
  field,
  evidence,
}: {
  title: string;
  description: string;
  field: FieldWithEvidence;
  evidence: Map<string, import("@/lib/api").EvidenceRecord>;
}) {
  return (
    <Card className="accent-sender">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div>
            <CardTitle className="text-sm">{title}</CardTitle>
            <CardDescription className="text-xs">{description}</CardDescription>
          </div>
          <Badge variant="outline" className="font-mono">
            conf {field.confidence.toFixed(2)}
          </Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        {field.values.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No values inferred.
          </p>
        ) : (
          <ul className="flex flex-wrap gap-1.5">
            {field.values.map((v, i) => (
              <li key={i}>
                <Badge variant="secondary">{v}</Badge>
              </li>
            ))}
          </ul>
        )}
        {field.evidence_refs.length > 0 && (
          <ClaimEvidence
            claim={`Inferred from ${field.evidence_refs.length} observations`}
            evidenceIds={field.evidence_refs}
            evidence={evidence}
            tone="evidence"
          />
        )}
      </CardContent>
    </Card>
  );
}

function SignalCard({
  title,
  count,
  tone,
  items,
  evidence,
}: {
  title: string;
  count: number;
  tone: string;
  items: ObservationRow[];
  evidence: Map<string, import("@/lib/api").EvidenceRecord>;
}) {
  const toneClass = cn(
    tone === "warning" && "accent-target",
    tone === "success" && "accent-target",
    tone === "sender" && "accent-sender",
    !tone && "accent-claim",
  );
  return (
    <Card className={toneClass}>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">{title}</CardTitle>
          <Badge variant="outline" className="font-mono">
            {count}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <ul className="space-y-2">
          {items.slice(0, 6).map((o) => (
            <li key={o.observation_id}>
              <ObservationEvidenceCard
                row={o}
                evidence={evidence.get(o.observation_id)}
                compact
              />
            </li>
          ))}
          {items.length > 6 && (
            <li>
              <details>
                <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                  Show {items.length - 6} more
                </summary>
                <ul className="mt-2 space-y-2">
                  {items.slice(6).map((o) => (
                    <li key={o.observation_id}>
                      <ObservationEvidenceCard
                        row={o}
                        evidence={evidence.get(o.observation_id)}
                        compact
                      />
                    </li>
                  ))}
                </ul>
              </details>
            </li>
          )}
        </ul>
      </CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/60 bg-background/40 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <p className="mt-1 font-mono text-lg leading-tight">{value}</p>
    </div>
  );
}

function collectArtifactEvidenceIds(
  icp: ICP | null,
  vps: ValueProposition[],
): string[] {
  const ids: string[] = [];
  if (icp) {
    ICP_FIELDS.forEach((spec) => {
      const f = icp[spec.key];
      ids.push(...(f?.evidence_refs ?? []));
    });
  }
  vps.forEach((vp) => ids.push(...vp.evidence_refs));
  return ids;
}

function countNonEmptyFields(icp: ICP): number {
  return ICP_FIELDS.reduce(
    (acc, spec) => acc + (icp[spec.key]?.values?.length ? 1 : 0),
    0,
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

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString();
  } catch {
    return iso;
  }
}
