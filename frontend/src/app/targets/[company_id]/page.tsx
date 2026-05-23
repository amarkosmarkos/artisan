"use client";

import * as React from "react";
import Link from "next/link";
import {
  useParams,
  useRouter,
  useSearchParams,
} from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Suspense } from "react";
import {
  ArrowLeft,
  ExternalLink,
  Flame,
  Lightbulb,
  Loader2,
  Send,
  Sparkles,
  Target as TargetIcon,
  Trash2,
  TrendingUp,
  UserRound,
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
import { SelectedValuePropositionCard } from "@/components/selected-vp-card";
import {
  deleteCompany,
  deleteEmail,
  deleteStrategy,
  getCompanyDetail,
  getCompanyObservations,
  startTarget,
  type ObservationRow,
  type PersonaRunRow,
  type TargetDetail,
} from "@/lib/api";
import type {
  AngleType,
  Email,
  EmailClaim,
  FitLevel,
  Seniority,
} from "@/lib/types";
import { cn } from "@/lib/utils";

const ANGLE_META: Record<
  AngleType,
  { label: string; icon: React.ReactNode; tone: string }
> = {
  pain_led: {
    label: "Pain-led",
    icon: <Flame className="h-3.5 w-3.5" />,
    tone: "text-rose-400",
  },
  trigger_led: {
    label: "Trigger-led",
    icon: <TrendingUp className="h-3.5 w-3.5" />,
    tone: "text-amber-400",
  },
  outcome_led: {
    label: "Outcome-led",
    icon: <Sparkles className="h-3.5 w-3.5" />,
    tone: "text-emerald-400",
  },
};

const FIT_META: Record<
  FitLevel,
  { label: string; variant: "success" | "default" | "warning" | "destructive" }
> = {
  strong: { label: "Strong fit", variant: "success" },
  plausible: { label: "Plausible fit", variant: "default" },
  weak: { label: "Weak fit", variant: "warning" },
  none: { label: "No fit", variant: "destructive" },
};

export default function TargetDetailPage() {
  return (
    <Suspense
      fallback={
        <p className="text-sm text-muted-foreground">Loading…</p>
      }
    >
      <TargetDetailInner />
    </Suspense>
  );
}

function TargetDetailInner() {
  const params = useParams<{ company_id: string }>();
  const router = useRouter();
  const search = useSearchParams();
  const queryClient = useQueryClient();
  const companyId = params?.company_id;
  const personaParam = search.get("persona");

  const detail = useQuery({
    enabled: !!companyId,
    queryKey: ["company", companyId],
    queryFn: () => getCompanyDetail(companyId!),
  });

  const observations = useQuery({
    enabled: !!companyId,
    queryKey: ["company-observations", companyId],
    queryFn: () => getCompanyObservations(companyId!),
  });

  const removeCompany = useMutation({
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

  const data = detail.data as TargetDetail | undefined;
  if (!data) return null;
  if (data.role !== "target") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Not a target</CardTitle>
          <CardDescription>
            This company is registered as a {data.role}.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Button asChild>
            <Link href={`/senders/${data.company_id}`}>
              <ArrowLeft className="h-4 w-4" />
              Open as sender
            </Link>
          </Button>
        </CardContent>
      </Card>
    );
  }

  const personas: PersonaRunRow[] = data.personas ?? [];
  const activePersona =
    personas.find((p) => p.persona_id === personaParam) ?? personas[0] ?? null;

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" asChild>
        <Link href="/">
          <ArrowLeft className="h-4 w-4" />
          Home
        </Link>
      </Button>

      <header className="rounded-xl border border-border/60 bg-target-soft p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <TargetIcon className="h-4 w-4 text-[hsl(var(--target))]" />
              <Badge variant="target">target</Badge>
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
              onClick={() => {
                if (
                  confirm(
                    "Delete this target and all its evidence, strategies and emails? This cannot be undone.",
                  )
                ) {
                  removeCompany.mutate();
                }
              }}
              disabled={removeCompany.isPending}
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
          <Stat label="Personas" value={String(personas.length)} />
        </div>
      </header>

      {personas.length === 0 ? (
        <NoPersonasYet companyId={companyId} />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2 border-b border-border/60 pb-1">
            {personas.map((p) => {
              const isActive = p.persona_id === activePersona?.persona_id;
              return (
                <button
                  key={p.persona_id}
                  onClick={() => {
                    const sp = new URLSearchParams(search.toString());
                    sp.set("persona", p.persona_id);
                    router.replace(`?${sp.toString()}`, { scroll: false });
                  }}
                  className={cn(
                    "flex items-center gap-2 rounded-t-md border-b-2 px-3 py-2 text-sm transition-colors",
                    isActive
                      ? "border-[hsl(var(--persona))] text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground",
                  )}
                >
                  <UserRound className="h-3.5 w-3.5 text-[hsl(var(--persona))]" />
                  <span className="font-medium">
                    {p.name ? `${p.name}` : p.role}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {p.seniority}
                  </span>
                </button>
              );
            })}
          </div>

          {activePersona && (
            <PersonaPanel
              targetCompanyId={companyId}
              targetUrl={data.url}
              persona={activePersona}
              observations={observations.data?.observations ?? []}
            />
          )}
        </div>
      )}

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Observations</CardTitle>
          <CardDescription>
            Atomic, source-grounded signals extracted from this target's site.
            Used as the substrate for every claim above.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {observations.isLoading && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {observations.data && (
            <ObservationGrid observations={observations.data.observations} />
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function NoPersonasYet({ companyId }: { companyId: string }) {
  return (
    <Card className="bg-target-soft">
      <CardHeader>
        <CardTitle className="text-base">No outreach generated yet</CardTitle>
        <CardDescription>
          Add a persona to this target to evaluate fit and generate emails.
          Open the sender that owns this target — its detail page has the
          inline form to add personas.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Button variant="outline" size="sm" asChild>
          <Link href={`/run`}>
            <ArrowLeft className="h-4 w-4" />
            Start a quick run
          </Link>
        </Button>
        <p className="mt-2 text-xs text-muted-foreground">
          Target id: {companyId}
        </p>
      </CardContent>
    </Card>
  );
}

function PersonaPanel({
  targetCompanyId,
  targetUrl,
  persona,
  observations,
}: {
  targetCompanyId: string;
  targetUrl: string;
  persona: PersonaRunRow;
  observations: ObservationRow[];
}) {
  const queryClient = useQueryClient();
  const router = useRouter();

  const strategy = persona.strategy?.strategy;
  const fit = strategy?.fit_assessment;
  const angles = strategy?.strategy.angles ?? [];
  const align = strategy?.strategy.persona_alignment;
  const senderCompanyId = persona.strategy?.sender_company_id ?? null;
  const selectedVp = persona.strategy?.selected_value_proposition ?? null;
  const senderVps = persona.strategy?.sender_value_propositions ?? [];

  // Collect all observation IDs referenced from angles + email claims so the
  // evidence resolver fetches them in one batch.
  const refs = React.useMemo(() => {
    const ids: string[] = [];
    angles.forEach((a) => ids.push(...a.evidence_refs));
    persona.emails.forEach((e) =>
      e.claims.forEach((c) => ids.push(...c.evidence_refs)),
    );
    observations.forEach((o) => ids.push(o.observation_id));
    if (selectedVp) ids.push(...selectedVp.evidence_refs);
    return ids;
  }, [angles, persona.emails, observations, selectedVp]);

  const evidence = useEvidenceLookup(refs);

  const localObsMap = React.useMemo(() => {
    const m = new Map<string, ObservationRow>();
    observations.forEach((o) => m.set(o.observation_id, o));
    return m;
  }, [observations]);

  const generate = useMutation({
    mutationFn: async () => {
      if (!senderCompanyId) {
        throw new Error("Sender id missing — open via the sender page first.");
      }
      const { run_id } = await startTarget({
        sender_company_id: senderCompanyId,
        target_url: targetUrl,
        persona: {
          role: persona.role,
          seniority: persona.seniority as Seniority,
        },
        persona_id: persona.persona_id,
      });
      return run_id;
    },
    onSuccess: (run_id) => {
      router.push(`/run?kind=target&run=${run_id}&view=outreach`);
    },
  });

  const removeStrategy = useMutation({
    mutationFn: () =>
      deleteStrategy(targetCompanyId, persona.persona_id || undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["company", targetCompanyId],
      });
    },
  });

  return (
    <div className="space-y-6">
      <Card className="accent-persona">
        <CardHeader className="pb-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-base flex items-center gap-2">
                <UserRound className="h-4 w-4 text-[hsl(var(--persona))]" />
                {persona.name ? (
                  <>
                    {persona.name} ·{" "}
                    <span className="text-muted-foreground font-normal">
                      {persona.role}
                    </span>
                  </>
                ) : (
                  persona.role
                )}
                <Badge variant="persona" className="ml-1">
                  {persona.seniority}
                </Badge>
              </CardTitle>
              <CardDescription>
                {strategy
                  ? "Strategy generated for this persona."
                  : "Persona registered. No strategy generated yet."}
              </CardDescription>
            </div>
            <div className="flex shrink-0 gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => generate.mutate()}
                disabled={generate.isPending || !senderCompanyId}
              >
                {generate.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                {strategy ? "Re-run outreach" : "Generate outreach"}
              </Button>
              {strategy && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    if (
                      confirm(
                        "Delete this persona's strategy + emails (keeps the persona)?",
                      )
                    ) {
                      removeStrategy.mutate();
                    }
                  }}
                  disabled={removeStrategy.isPending}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          </div>
        </CardHeader>
      </Card>

      {!strategy && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            Click <span className="text-foreground">Generate outreach</span>{" "}
            above to evaluate fit and produce two emails for this persona.
          </CardContent>
        </Card>
      )}

      {strategy && (
        <SelectedValuePropositionCard
          strategy={strategy}
          selectedVp={selectedVp}
          senderVps={senderVps}
          evidenceById={evidence}
        />
      )}

      {strategy && fit && (
        <div className="grid gap-4 md:grid-cols-3">
          <Card className="md:col-span-1 bg-target-soft accent-target">
            <CardHeader className="pb-2">
              <CardDescription>Fit assessment</CardDescription>
              <div className="flex items-center gap-2 pt-1">
                <Badge variant={FIT_META[fit.level].variant}>
                  {FIT_META[fit.level].label}
                </Badge>
                <Badge variant="outline">
                  {strategy.strategy.contact_decision.replaceAll("_", " ")}
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <BulletGroup
                title="Reasons"
                tone="positive"
                items={fit.reasons}
              />
              <BulletGroup
                title="Risks"
                tone="warning"
                items={fit.risks}
              />
              <BulletGroup
                title="Missing evidence"
                tone="muted"
                items={fit.missing_evidence}
              />
            </CardContent>
          </Card>

          <Card className="md:col-span-2 bg-persona-soft accent-persona">
            <CardHeader className="pb-2">
              <CardDescription>Persona alignment</CardDescription>
              <CardTitle className="text-base">
                Why this persona is relevant
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              {align && (
                <>
                  <Field
                    label="Role relevance"
                    value={
                      <Badge
                        variant={
                          align.role_relevance === "high"
                            ? "success"
                            : align.role_relevance === "medium"
                              ? "default"
                              : "muted"
                        }
                      >
                        {align.role_relevance}
                      </Badge>
                    }
                    reason={align.role_relevance_reason}
                  />
                  <Field
                    label="Preferred framing"
                    value={
                      <span className="text-foreground/90 font-medium">
                        {align.preferred_framing}
                      </span>
                    }
                    reason={align.preferred_framing_reason}
                  />
                  <Field
                    label="Avoid"
                    value={
                      align.avoid.length === 0 ? (
                        <span className="text-muted-foreground">—</span>
                      ) : (
                        <div className="flex flex-wrap gap-1">
                          {align.avoid.map((a) => (
                            <Badge key={a} variant="outline">
                              {a}
                            </Badge>
                          ))}
                        </div>
                      )
                    }
                    reason={align.avoid_reason}
                  />
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}

      {angles.length > 0 && (
        <section className="space-y-2">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">
              Recommended outbound angles
            </h2>
            <p className="text-xs text-muted-foreground">
              Each angle is anchored on observation IDs you can expand to see
              the supporting snippet.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {angles.map((a, i) => {
              const meta = ANGLE_META[a.type];
              return (
                <Card key={i} className="accent-claim">
                  <CardHeader className="pb-2">
                    <div className="flex items-center gap-2">
                      <span className={meta.tone}>{meta.icon}</span>
                      <CardTitle className="text-sm">{meta.label}</CardTitle>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <ClaimEvidence
                      claim={a.hypothesis}
                      evidenceIds={a.evidence_refs}
                      evidence={evidence}
                      tone="claim"
                    />
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </section>
      )}

      {persona.emails.length > 0 && (
        <section className="space-y-3">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">
              Generated emails
            </h2>
            <p className="text-xs text-muted-foreground">
              Two emails with meaningfully different angles. Every factual
              claim is expandable to its grounding evidence.
            </p>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            {persona.emails.map((e) => (
              <EmailCard
                key={e.email_id}
                email={e}
                evidence={evidence}
                onDelete={() => {
                  if (confirm(`Delete this ${e.angle} email?`)) {
                    deleteEmail(e.email_id).then(() =>
                      queryClient.invalidateQueries({
                        queryKey: ["company", targetCompanyId],
                      }),
                    );
                  }
                }}
              />
            ))}
          </div>
        </section>
      )}

      {persona.claim_map.length > 0 && (
        <section className="space-y-2">
          <div>
            <h2 className="text-sm font-semibold tracking-tight">
              Claim map for this persona
            </h2>
            <p className="text-xs text-muted-foreground">
              Every factual claim across the two emails, with NLI status and
              the grounding evidence.
            </p>
          </div>
          <div className="space-y-2">
            {persona.claim_map.map((c) => (
              <ClaimMapRow
                key={c.claim_id}
                claim={c.text}
                status={c.status}
                score={c.nli_score}
                citations={c.citations}
                angle={c.angle}
              />
            ))}
          </div>
        </section>
      )}

      {/* Tiny supporting summary at the end so the user can sanity-check the
          observation universe used to generate the persona. */}
      <details className="rounded-md border border-border/60 bg-card/40 px-3 py-2 text-sm">
        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Local observations referenced in this persona ({localObsMap.size})
        </summary>
        <ul className="mt-2 space-y-2">
          {Array.from(localObsMap.values()).slice(0, 30).map((o) => (
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
    </div>
  );
}

function ClaimMapRow({
  claim,
  status,
  score,
  citations,
  angle,
}: {
  claim: string;
  status: import("@/lib/types").ClaimStatus;
  score: number | null;
  citations: { url: string; snippet: string }[];
  angle: string;
}) {
  const [open, setOpen] = React.useState(false);
  const meta = ANGLE_META[angle as AngleType];
  return (
    <div className="rounded-lg border border-border/60 bg-claim">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-start gap-2 px-3 py-2.5 text-left"
      >
        <span
          className={cn(
            "mt-0.5 h-2 w-2 shrink-0 rounded-full",
            status === "entailed" && "bg-[hsl(var(--success))]",
            status === "neutral" && "bg-muted-foreground",
            status === "contradicted" && "bg-destructive",
            status === "unsupported" && "bg-[hsl(var(--warning))]",
            status === "repaired" && "bg-foreground/60",
          )}
        />
        <div className="min-w-0 flex-1">
          <p className="text-sm leading-snug text-foreground/90">{claim}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {meta && (
              <Badge variant="outline" className="font-normal">
                <span className={meta.tone}>{meta.icon}</span>
                <span className="ml-1">{meta.label}</span>
              </Badge>
            )}
            <Badge
              variant={
                status === "entailed"
                  ? "success"
                  : status === "contradicted"
                    ? "destructive"
                    : status === "neutral"
                      ? "muted"
                      : "warning"
              }
            >
              {status}
              {score !== null && (
                <span className="ml-1 font-mono text-[10px] opacity-70">
                  {score.toFixed(2)}
                </span>
              )}
            </Badge>
            <Badge variant="outline" className="font-mono">
              {citations.length} citation
              {citations.length === 1 ? "" : "s"}
            </Badge>
          </div>
        </div>
      </button>
      {open && (
        <div className="space-y-2 border-t border-border/60 px-3 py-2.5">
          {citations.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No citations recorded.
            </p>
          )}
          {citations.map((c, i) => (
            <div
              key={i}
              className="rounded-md border border-border/40 bg-background/50 p-2.5"
            >
              <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground mb-1">
                Source on page
              </p>
              <p className="text-sm leading-snug text-foreground/90 whitespace-pre-wrap">
                {c.snippet}
              </p>
              {c.url && (
                <a
                  href={c.url}
                  target="_blank"
                  rel="noreferrer"
                  className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                >
                  <ExternalLink className="h-3 w-3" />
                  {prettyUrl(c.url)}
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EmailCard({
  email,
  evidence,
  onDelete,
}: {
  email: Email;
  evidence: Map<string, import("@/lib/api").EvidenceRecord>;
  onDelete: () => void;
}) {
  const meta = ANGLE_META[email.angle];
  return (
    <Card className="accent-claim">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className={meta.tone}>{meta.icon}</span>
            <CardTitle className="text-sm">{meta.label}</CardTitle>
          </div>
          <Button variant="ghost" size="sm" onClick={onDelete}>
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
        <CardDescription className="text-foreground font-medium">
          {email.subject}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-foreground/90">
          {email.body}
        </pre>
        <div className="space-y-2 pt-2">
          <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Claims ({email.claims.length})
          </p>
          {email.claims.length === 0 && (
            <p className="text-xs text-muted-foreground">
              No claims extracted from this draft.
            </p>
          )}
          {email.claims.map((c: EmailClaim) => (
            <ClaimEvidence
              key={c.claim_id}
              claim={c.text}
              status={c.status}
              score={c.nli_score}
              evidenceIds={c.evidence_refs}
              evidence={evidence}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function ObservationGrid({
  observations,
}: {
  observations: ObservationRow[];
}) {
  const evidenceIds = React.useMemo(
    () => observations.map((o) => o.observation_id),
    [observations],
  );
  const evidence = useEvidenceLookup(evidenceIds);

  const grouped = React.useMemo(() => {
    const m = new Map<string, ObservationRow[]>();
    observations.forEach((o) => {
      const arr = m.get(o.kind) ?? [];
      arr.push(o);
      m.set(o.kind, arr);
    });
    return Array.from(m.entries()).sort(
      (a, b) => b[1].length - a[1].length,
    );
  }, [observations]);

  if (observations.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No observations yet.</p>
    );
  }

  return (
    <div className="space-y-3">
      {grouped.map(([kind, list]) => (
        <details
          key={kind}
          className="rounded-md border border-border/60 bg-card/40 px-3 py-2"
          open={kind === grouped[0][0]}
        >
          <summary className="cursor-pointer text-sm font-medium flex items-center gap-2">
            <Badge variant="outline" className="font-normal">
              {kind}
            </Badge>
            <span className="text-muted-foreground">{list.length}</span>
            <span className="ml-auto text-[10px] uppercase tracking-wide text-muted-foreground">
              click to expand
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
  );
}

function BulletGroup({
  title,
  items,
  tone,
}: {
  title: string;
  items: string[];
  tone: "positive" | "warning" | "muted";
}) {
  if (!items || items.length === 0) return null;
  const dot =
    tone === "positive"
      ? "bg-[hsl(var(--success))]"
      : tone === "warning"
        ? "bg-[hsl(var(--warning))]"
        : "bg-muted-foreground/60";
  return (
    <div>
      <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <ul className="space-y-1.5">
        {items.map((s, i) => (
          <li key={i} className="flex gap-2">
            <span
              className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", dot)}
            />
            <span className="text-foreground/90 leading-snug">{s}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Field({
  label,
  value,
  reason,
}: {
  label: string;
  value: React.ReactNode;
  reason?: string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        {value}
      </div>
      {reason && (
        <p className="mt-1 text-xs text-muted-foreground leading-snug">
          {reason}
        </p>
      )}
    </div>
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

// suppress unused-import noise: <Lightbulb> reserved for future angle types
void Lightbulb;
