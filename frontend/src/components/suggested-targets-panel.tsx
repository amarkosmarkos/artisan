"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Building2,
  Check,
  ExternalLink,
  Globe,
  Loader2,
  Plus,
  RefreshCw,
  Sparkles,
  User,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";

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
import { addSenderTarget, discoverSenderTargets } from "@/lib/api";
import type {
  DiscoveryConfidence,
  PersonaInput,
  Seniority,
  SuggestedPersona,
  SuggestedTarget,
  SuggestedTargetsResponse,
} from "@/lib/types";

interface Props {
  senderCompanyId: string;
  /** Pre-loaded discovery from the sender run (auto-triggered server-side). */
  initialDiscovery?: SuggestedTargetsResponse | null;
  /** Same handler as the manual "Evaluate a target" form — starts target analysis + outreach. */
  onGenerateOutreach: (input: {
    target_url: string;
    persona: PersonaInput;
  }) => void;
  /** Prefill the manual evaluate form (e.g. after saving a target for later). */
  onPrefillEvaluate?: (input: {
    target_url: string;
    persona: PersonaInput;
  }) => void;
  running: boolean;
  /** When false, user must click Discover (e.g. sender detail page). Default: true if no initialDiscovery. */
  autoDiscover?: boolean;
  className?: string;
}

type CardState = "pending" | "saving" | "saved" | "error" | "ignored";

interface LocalSuggestionState {
  state: CardState;
  error?: string;
}

const SIDEBAR_KEYS = [
  "sidebar-senders",
  "sidebar-targets",
  "sidebar-personas",
];

const SENIORITIES: { value: Seniority; label: string }[] = [
  { value: "ic", label: "IC" },
  { value: "manager", label: "Manager" },
  { value: "director", label: "Director" },
  { value: "vp", label: "VP" },
  { value: "c_level", label: "C-level" },
  { value: "founder", label: "Founder" },
];

export function SuggestedTargetsPanel({
  senderCompanyId,
  initialDiscovery = null,
  onGenerateOutreach,
  onPrefillEvaluate,
  running,
  autoDiscover = !initialDiscovery,
  className,
}: Props) {
  const queryClient = useQueryClient();
  const [data, setData] = React.useState<SuggestedTargetsResponse | null>(
    initialDiscovery,
  );
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [hasRun, setHasRun] = React.useState(Boolean(initialDiscovery));
  const [cardState, setCardState] = React.useState<
    Record<string, LocalSuggestionState>
  >(() => {
    if (!initialDiscovery) return {};
    const next: Record<string, LocalSuggestionState> = {};
    initialDiscovery.suggestions.forEach((s) => {
      next[s.domain] = { state: "pending" };
    });
    return next;
  });

  React.useEffect(() => {
    if (!initialDiscovery) return;
    setData(initialDiscovery);
    setHasRun(true);
    const next: Record<string, LocalSuggestionState> = {};
    initialDiscovery.suggestions.forEach((s) => {
      next[s.domain] = { state: "pending" };
    });
    setCardState(next);
  }, [initialDiscovery]);

  const invalidateAfterAdd = React.useCallback(() => {
    SIDEBAR_KEYS.forEach((k) =>
      queryClient.invalidateQueries({ queryKey: [k] }),
    );
    queryClient.invalidateQueries({
      queryKey: ["sender-targets", senderCompanyId],
    });
  }, [queryClient, senderCompanyId]);

  const runDiscovery = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    setHasRun(true);
    try {
      const resp = await discoverSenderTargets(senderCompanyId);
      setData(resp);
      const next: Record<string, LocalSuggestionState> = {};
      resp.suggestions.forEach((s) => {
        next[s.domain] = { state: "pending" };
      });
      setCardState(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [senderCompanyId]);

  React.useEffect(() => {
    if (!autoDiscover || initialDiscovery || hasRun || loading) return;
    void runDiscovery();
  }, [autoDiscover, initialDiscovery, hasRun, loading, runDiscovery]);

  const saveCustomTarget = useMutation({
    mutationFn: (target_url: string) =>
      addSenderTarget(senderCompanyId, target_url),
    onSuccess: invalidateAfterAdd,
  });

  const saveTargetOnly = useMutation({
    mutationFn: async (target: SuggestedTarget) => {
      return addSenderTarget(senderCompanyId, target.homepage_url);
    },
    onMutate: (target) => {
      setCardState((s) => ({
        ...s,
        [target.domain]: { state: "saving" },
      }));
    },
    onSuccess: (_result, target) => {
      setCardState((s) => ({
        ...s,
        [target.domain]: { state: "saved" },
      }));
      invalidateAfterAdd();
    },
    onError: (e, target) => {
      setCardState((s) => ({
        ...s,
        [target.domain]: {
          state: "error",
          error: e instanceof Error ? e.message : String(e),
        },
      }));
    },
  });

  const onIgnore = (target: SuggestedTarget) => {
    setCardState((s) => ({
      ...s,
      [target.domain]: { state: "ignored" },
    }));
  };

  const visibleSuggestions = (data?.suggestions ?? []).filter(
    (s) => cardState[s.domain]?.state !== "ignored",
  );

  const isHealthy = data?.status === "ok" && visibleSuggestions.length > 0;

  return (
    <section
      aria-label="Suggested targets"
      className={cn(
        "rounded-xl border border-border bg-card/40 p-6",
        className ?? "mt-10",
      )}
    >
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="mt-0.5 rounded-md bg-[hsl(var(--target))]/15 p-1.5 text-target">
            <Sparkles className="h-4 w-4" />
          </div>
          <div>
            <h3 className="text-lg font-medium">Suggested targets</h3>
            <p className="text-sm text-muted-foreground">
              Discover ICP-fit companies, add your own target URL, pick one
              recipient, and generate outreach — or save targets for later.
            </p>
          </div>
        </div>
        <Button
          variant={hasRun ? "outline" : "default"}
          size="sm"
          onClick={runDiscovery}
          disabled={loading || running}
        >
          {loading ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Searching
            </>
          ) : hasRun ? (
            <>
              <RefreshCw className="h-3.5 w-3.5" />
              Re-run discovery
            </>
          ) : (
            <>
              <Sparkles className="h-3.5 w-3.5" />
              Discover targets
            </>
          )}
        </Button>
      </header>

      <div className="mt-6">
        <CustomTargetBlock
          running={running}
          saving={saveCustomTarget.isPending}
          onGenerateOutreach={onGenerateOutreach}
          onSaveOnly={(input) => {
            saveCustomTarget.mutate(input.target_url, {
              onSuccess: () => onPrefillEvaluate?.(input),
            });
          }}
        />

        {autoDiscover && !hasRun && !loading && <IdleState />}

        {!autoDiscover && !hasRun && !loading && !data && !error && (
          <DiscoverPrompt onDiscover={runDiscovery} />
        )}

        {loading && <LoadingState />}

        {!loading && data && data.status !== "ok" && (
          <EmptyState
            status={data.status}
            message={data.message}
            onRetry={runDiscovery}
          />
        )}

        {!loading && data?.status === "ok" && visibleSuggestions.length === 0 && (
          <EmptyState
            status="weak"
            message="All suggestions were ignored. Re-run to fetch new ones."
            onRetry={runDiscovery}
          />
        )}

        {!loading && error && (
          <EmptyState status="error" message={error} onRetry={runDiscovery} />
        )}

        {!loading && isHealthy && (
          <ul className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            <AnimatePresence initial={false}>
              {visibleSuggestions.map((s) => (
                <motion.li
                  key={s.domain}
                  layout
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, scale: 0.96 }}
                  transition={{ duration: 0.25 }}
                >
                  <SuggestionCard
                    suggestion={s}
                    state={cardState[s.domain] ?? { state: "pending" }}
                    running={running}
                    onGenerateOutreach={(persona) =>
                      onGenerateOutreach({
                        target_url: s.homepage_url,
                        persona,
                      })
                    }
                    onSaveOnly={(persona) => {
                      saveTargetOnly.mutate(s, {
                        onSuccess: () => onPrefillEvaluate?.({
                          target_url: s.homepage_url,
                          persona,
                        }),
                      });
                    }}
                    onIgnore={() => onIgnore(s)}
                    saving={saveTargetOnly.isPending}
                  />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        )}
      </div>
    </section>
  );
}

// ---------- Suggestion card ----------

type PersonaChoice = `suggested-${number}` | "custom";

function SuggestionCard({
  suggestion,
  state,
  running,
  onGenerateOutreach,
  onSaveOnly,
  onIgnore,
  saving,
}: {
  suggestion: SuggestedTarget;
  state: LocalSuggestionState;
  running: boolean;
  onGenerateOutreach: (persona: PersonaInput) => void;
  onSaveOnly: (persona: PersonaInput) => void;
  onIgnore: () => void;
  saving: boolean;
}) {
  const defaultChoice: PersonaChoice =
    suggestion.personas.length > 0 ? "suggested-0" : "custom";
  const [choice, setChoice] = React.useState<PersonaChoice>(defaultChoice);
  const [customRole, setCustomRole] = React.useState("VP of Sales");
  const [customSeniority, setCustomSeniority] =
    React.useState<Seniority>("vp");

  const saved = state.state === "saved";
  const busy = state.state === "saving" || saving;
  const errored = state.state === "error";

  const resolvedPersona = React.useMemo(
    () =>
      resolvePersona(choice, suggestion.personas, customRole, customSeniority),
    [choice, suggestion.personas, customRole, customSeniority],
  );

  const canSubmit = Boolean(resolvedPersona.role.trim()) && !running && !busy;

  const handleGenerate = () => {
    if (!canSubmit) return;
    onGenerateOutreach(resolvedPersona);
  };

  const handleSaveOnly = () => {
    if (!canSubmit) return;
    onSaveOnly(resolvedPersona);
  };

  return (
    <Card className="flex h-full flex-col accent-target">
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="truncate text-base">
              {suggestion.company_name}
            </CardTitle>
            <CardDescription className="mt-0.5 flex items-center gap-1.5">
              <Globe className="h-3 w-3" />
              <a
                href={suggestion.homepage_url}
                target="_blank"
                rel="noreferrer"
                className="truncate text-xs underline-offset-4 hover:underline"
              >
                {suggestion.domain}
              </a>
              <ExternalLink className="h-3 w-3 text-muted-foreground" />
            </CardDescription>
          </div>
          <ConfidencePill confidence={suggestion.confidence} />
        </div>
      </CardHeader>

      <CardContent className="flex flex-1 flex-col gap-4 text-sm">
        {suggestion.fit_rationale && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Why it fits
            </p>
            <p className="mt-1 leading-relaxed">{suggestion.fit_rationale}</p>
          </div>
        )}

        {suggestion.matched_value_proposition_label && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Matched value proposition
            </p>
            <Badge variant="sender" className="mt-1.5 font-normal">
              {suggestion.matched_value_proposition_label}
            </Badge>
          </div>
        )}

        {suggestion.evidence.length > 0 && (
          <div>
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Sources
            </p>
            <ul className="mt-1.5 space-y-1.5">
              {suggestion.evidence.slice(0, 2).map((e, i) => (
                <li
                  key={`${e.url}-${i}`}
                  className="rounded-md bg-evidence px-2.5 py-1.5"
                >
                  <a
                    href={e.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1.5 text-xs font-medium text-foreground/90 hover:underline"
                  >
                    <span className="truncate">
                      {e.title || hostnameOf(e.url)}
                    </span>
                    <ExternalLink className="h-3 w-3 shrink-0 text-muted-foreground" />
                  </a>
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Recipient picker — one persona per outreach run */}
        <div className="rounded-lg border border-border/80 bg-muted/20 p-3">
          <p className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">
            <User className="h-3.5 w-3.5" />
            Recipient for outreach
          </p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            One person per run. Outreach is generated for this role only.
          </p>

          <div className="mt-3 space-y-2" role="radiogroup" aria-label="Recipient">
            {suggestion.personas.map((p, i) => (
              <label
                key={`${p.title}-${i}`}
                className={`flex cursor-pointer items-start gap-2.5 rounded-md border px-3 py-2.5 transition-colors ${
                  choice === `suggested-${i}`
                    ? "border-[hsl(var(--persona))]/50 bg-persona-soft"
                    : "border-border/60 hover:bg-muted/40"
                }`}
              >
                <input
                  type="radio"
                  name={`persona-${suggestion.domain}`}
                  className="mt-1"
                  checked={choice === `suggested-${i}`}
                  onChange={() => setChoice(`suggested-${i}`)}
                  disabled={running || busy}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-sm font-medium">{p.title}</span>
                    <Badge variant="muted" className="font-normal">
                      {p.seniority}
                    </Badge>
                    {p.name && (
                      <Badge variant="persona" className="font-normal">
                        {p.name}
                      </Badge>
                    )}
                  </div>
                  {p.rationale && (
                    <p className="mt-1 text-xs text-muted-foreground line-clamp-2">
                      {p.rationale}
                    </p>
                  )}
                </div>
              </label>
            ))}

            <label
              className={`flex cursor-pointer items-start gap-2.5 rounded-md border px-3 py-2.5 transition-colors ${
                choice === "custom"
                  ? "border-[hsl(var(--persona))]/50 bg-persona-soft"
                  : "border-border/60 hover:bg-muted/40"
              }`}
            >
              <input
                type="radio"
                name={`persona-${suggestion.domain}`}
                className="mt-1"
                checked={choice === "custom"}
                onChange={() => setChoice("custom")}
                disabled={running || busy}
              />
              <div className="min-w-0 flex-1 space-y-2">
                <span className="text-sm font-medium">Custom role</span>
                {choice === "custom" && (
                  <div className="grid gap-2 sm:grid-cols-[1fr,120px]">
                    <Input
                      placeholder="e.g. VP of Engineering"
                      value={customRole}
                      onChange={(e) => setCustomRole(e.target.value)}
                      disabled={running || busy}
                      className="h-8 text-sm"
                    />
                    <Select
                      value={customSeniority}
                      onValueChange={(v) =>
                        setCustomSeniority(v as Seniority)
                      }
                      disabled={running || busy}
                    >
                      <SelectTrigger className="h-8 text-sm">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {SENIORITIES.map((s) => (
                          <SelectItem key={s.value} value={s.value}>
                            {s.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>
            </label>
          </div>
        </div>

        <div className="mt-auto space-y-2 pt-1">
          {saved && (
            <Badge variant="success" className="font-normal">
              <Check className="h-3 w-3" />
              Saved to campaign
            </Badge>
          )}
          {errored && (
            <p className="text-xs text-destructive">{state.error}</p>
          )}

          <Button
            className="w-full"
            size="sm"
            onClick={handleGenerate}
            disabled={!canSubmit}
          >
            {running ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Generating outreach…
              </>
            ) : (
              <>
                Generate outreach
                <ArrowRight className="h-3.5 w-3.5" />
              </>
            )}
          </Button>

          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              className="flex-1"
              onClick={handleSaveOnly}
              disabled={!canSubmit}
            >
              {busy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                "Save target only"
              )}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={onIgnore}
              disabled={running || busy}
            >
              <X className="h-3.5 w-3.5" />
              Ignore
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function resolvePersona(
  choice: PersonaChoice,
  suggested: SuggestedPersona[],
  customRole: string,
  customSeniority: Seniority,
): PersonaInput {
  if (choice === "custom") {
    return { role: customRole.trim(), seniority: customSeniority };
  }
  const idx = parseInt(choice.replace("suggested-", ""), 10);
  const p = suggested[idx];
  if (!p) {
    return { role: customRole.trim(), seniority: customSeniority };
  }
  return {
    role: p.title.trim(),
    seniority: p.seniority ?? "vp",
    ...(p.name ? { name: p.name } : {}),
  };
}

// ---------- Custom target (any URL) ----------

function CustomTargetBlock({
  running,
  saving,
  onGenerateOutreach,
  onSaveOnly,
}: {
  running: boolean;
  saving: boolean;
  onGenerateOutreach: (input: {
    target_url: string;
    persona: PersonaInput;
  }) => void;
  onSaveOnly: (input: { target_url: string; persona: PersonaInput }) => void;
}) {
  const [targetUrl, setTargetUrl] = React.useState("");
  const [name, setName] = React.useState("");
  const [role, setRole] = React.useState("VP of Sales");
  const [seniority, setSeniority] = React.useState<Seniority>("vp");
  const [saved, setSaved] = React.useState(false);

  const persona = React.useMemo((): PersonaInput => {
    const trimmedName = name.trim();
    return {
      role: role.trim(),
      seniority,
      ...(trimmedName ? { name: trimmedName } : {}),
    };
  }, [name, role, seniority]);

  const canSubmit =
    Boolean(targetUrl.trim()) && Boolean(role.trim()) && !running && !saving;

  const handleGenerate = () => {
    if (!canSubmit) return;
    onGenerateOutreach({ target_url: targetUrl.trim(), persona });
  };

  const handleSave = () => {
    if (!canSubmit) return;
    onSaveOnly({ target_url: targetUrl.trim(), persona });
    setSaved(true);
  };

  return (
    <div className="mb-6 rounded-lg border border-dashed border-[hsl(var(--target))]/40 bg-target-soft p-4">
      <div className="flex items-start gap-2">
        <Building2 className="mt-0.5 h-4 w-4 text-target" />
        <div className="flex-1">
          <p className="text-sm font-medium">Add custom target</p>
          <p className="text-xs text-muted-foreground">
            Any company URL — not from suggestions. Add the company first,
            then one recipient for outreach.
          </p>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Input
          placeholder="https://target-company.com"
          value={targetUrl}
          onChange={(e) => {
            setTargetUrl(e.target.value);
            setSaved(false);
          }}
          disabled={running || saving}
          className="sm:col-span-2 lg:col-span-4"
        />
        <Input
          placeholder="Recipient name (optional)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={running || saving}
        />
        <Input
          placeholder="Recipient role"
          value={role}
          onChange={(e) => setRole(e.target.value)}
          disabled={running || saving}
        />
        <Select
          value={seniority}
          onValueChange={(v) => setSeniority(v as Seniority)}
          disabled={running || saving}
        >
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SENIORITIES.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={handleGenerate} disabled={!canSubmit}>
          {running ? (
            <>
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              Generating…
            </>
          ) : (
            <>
              Generate outreach
              <ArrowRight className="h-3.5 w-3.5" />
            </>
          )}
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={handleSave}
          disabled={!canSubmit}
        >
          {saving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <>
              <Plus className="h-3.5 w-3.5" />
              Save target only
            </>
          )}
        </Button>
        {saved && (
          <Badge variant="success" className="font-normal">
            <Check className="h-3 w-3" />
            Saved to campaign
          </Badge>
        )}
      </div>
    </div>
  );
}

function DiscoverPrompt({ onDiscover }: { onDiscover: () => void }) {
  return (
    <div className="rounded-lg border border-dashed border-border/80 bg-background/40 px-6 py-8 text-center">
      <Sparkles className="mx-auto h-5 w-5 text-muted-foreground" />
      <p className="mt-3 text-sm font-medium">Find ICP-fit companies</p>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
        Search the web for prospects matching this sender&apos;s profile.
      </p>
      <Button variant="outline" size="sm" className="mt-4" onClick={onDiscover}>
        <Sparkles className="h-3.5 w-3.5" />
        Discover targets
      </Button>
    </div>
  );
}

// ---------- Empty / status states ----------

function IdleState() {
  return (
    <div className="rounded-lg border border-dashed border-border/80 bg-background/40 px-6 py-10 text-center">
      <Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" />
      <p className="mt-3 text-sm font-medium">Discovering target companies…</p>
      <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
        This runs automatically after sender research. Suggestions will appear
        here shortly.
      </p>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
      {[0, 1, 2].map((i) => (
        <Card key={i} className="overflow-hidden">
          <CardHeader className="pb-3">
            <div className="skeleton h-4 w-2/3 rounded" />
            <div className="skeleton mt-2 h-3 w-1/3 rounded" />
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="skeleton h-3 w-full rounded" />
            <div className="skeleton h-16 w-full rounded" />
            <div className="skeleton h-9 w-full rounded" />
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function EmptyState({
  status,
  message,
  onRetry,
}: {
  status: "weak" | "unavailable" | "error";
  message: string;
  onRetry: () => void;
}) {
  const title =
    status === "unavailable"
      ? "Discovery unavailable"
      : status === "error"
        ? "Something went wrong"
        : "No strong matches found";

  return (
    <div className="rounded-lg border border-dashed border-border/80 bg-background/40 px-6 py-10 text-center">
      <AlertTriangle className="mx-auto h-5 w-5 text-muted-foreground" />
      <p className="mt-3 text-sm font-medium">{title}</p>
      {message && (
        <p className="mx-auto mt-1 max-w-md text-xs text-muted-foreground">
          {message}
        </p>
      )}
      <div className="mt-4 flex justify-center">
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCw className="h-3.5 w-3.5" />
          Try again
        </Button>
      </div>
    </div>
  );
}

function ConfidencePill({
  confidence,
}: {
  confidence: DiscoveryConfidence;
}) {
  const variant: "success" | "warning" | "muted" =
    confidence === "high"
      ? "success"
      : confidence === "medium"
        ? "warning"
        : "muted";
  return (
    <Badge variant={variant} className="shrink-0 font-normal capitalize">
      {confidence}
    </Badge>
  );
}

function hostnameOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
