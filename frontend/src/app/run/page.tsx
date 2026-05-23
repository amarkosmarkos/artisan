"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import {
  getCompanyDetail,
  getSenderResult,
  getSenderStatus,
  getTargetResult,
  getTargetStatus,
  startSender,
  startTarget,
  streamProgress,
  type SenderDetail,
} from "@/lib/api";
import type {
  PersonaInput,
  ProgressEvent,
  SenderResponse,
  TargetResponse,
  ValueProposition,
} from "@/lib/types";
import { StepSender } from "@/components/step-sender";
import { StepIcp } from "@/components/step-icp";
import { StepOutreach } from "@/components/step-outreach";
import { StepAnalytics } from "@/components/step-analytics";
import { ProgressStream } from "@/components/progress-stream";

type View = "sender" | "icp" | "outreach" | "analytics";

interface RunState {
  kind: "sender" | "target";
  run_id: string | null;
  events: ProgressEvent[];
  done: boolean;
  error: string | null;
  reconnected?: boolean;
  // The URL we are analyzing in this run. Captured at start so the UI can
  // replace the input area with "Analyzing acme.com" without waiting for
  // the result payload.
  url?: string;
}

const VALID_VIEWS: View[] = ["sender", "icp", "outreach", "analytics"];

const ROUTE = "/run";

// Keys the AppSidebar reads. We invalidate these any time the workflow
// produces a new artifact so the sidebar stays in sync with the same
// source of truth as the main view.
const SIDEBAR_KEYS = ["sidebar-senders", "sidebar-targets", "sidebar-personas"];

export default function RunPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center py-24 text-sm text-muted-foreground">
          Loading...
        </div>
      }
    >
      <RunPageInner />
    </Suspense>
  );
}

function RunPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();

  const [view, setView] = React.useState<View>("sender");
  const [run, setRun] = React.useState<RunState | null>(null);
  const [sender, setSender] = React.useState<SenderResponse | null>(null);
  const [target, setTarget] = React.useState<TargetResponse | null>(null);
  const [pendingKind, setPendingKind] = React.useState<"sender" | "target" | null>(
    null,
  );
  const [hydrating, setHydrating] = React.useState(true);
  const unsubRef = React.useRef<(() => void) | null>(null);
  const hydratedRef = React.useRef(false);

  const cleanup = React.useCallback(() => {
    if (unsubRef.current) {
      unsubRef.current();
      unsubRef.current = null;
    }
  }, []);

  React.useEffect(() => cleanup, [cleanup]);

  const invalidateSidebar = React.useCallback(() => {
    SIDEBAR_KEYS.forEach((k) =>
      queryClient.invalidateQueries({ queryKey: [k] }),
    );
    // Also invalidate the keys used by the sender-targets-panel and per-target
    // detail pages so any open page picks up the new artifact immediately.
    queryClient.invalidateQueries({ queryKey: ["companies"] });
    queryClient.invalidateQueries({ queryKey: ["sender-targets"] });
    queryClient.invalidateQueries({ queryKey: ["personas"] });
    queryClient.invalidateQueries({ queryKey: ["company"] });
  }, [queryClient]);

  const syncUrl = React.useCallback(
    (
      next: View,
      activeRun: { kind: "sender" | "target"; run_id: string } | null,
    ) => {
      const params = new URLSearchParams();
      if (next !== "sender") params.set("view", next);
      if (activeRun) {
        params.set("kind", activeRun.kind);
        params.set("run", activeRun.run_id);
      }
      const qs = params.toString();
      router.replace(qs ? `${ROUTE}?${qs}` : ROUTE, { scroll: false });
    },
    [router],
  );

  // Hydrate sender state from a persisted target. After a target run, if the
  // user reloads the page the run state only points at the target run_id, so
  // we have to re-fetch the sender artifact from the dashboard endpoint to
  // restore the continuous workflow.
  const hydrateSenderFromCompanyId = React.useCallback(
    async (senderCompanyId: string, fallbackUrl: string) => {
      try {
        const detail = (await getCompanyDetail(senderCompanyId)) as SenderDetail;
        const vps: ValueProposition[] =
          detail.value_propositions?.length
            ? detail.value_propositions
            : detail.value_proposition
              ? [detail.value_proposition]
              : [];
        const primary =
          detail.value_proposition ?? vps[0] ?? {
            customer: "",
            pain: "",
            outcome: "",
            mechanism: "",
            confidence: 0,
            evidence_refs: [],
          };
        const reconstructed: SenderResponse = {
          company_id: detail.company_id,
          sender_url: detail.url || fallbackUrl,
          icp: detail.icp ?? {
            target_industries: { values: [], confidence: 0, evidence_refs: [] },
            size_bands: { values: [], confidence: 0, evidence_refs: [] },
            likely_buyers: { values: [], confidence: 0, evidence_refs: [] },
            common_triggers: { values: [], confidence: 0, evidence_refs: [] },
            negative_icp: { values: [], confidence: 0, evidence_refs: [] },
          },
          value_proposition: primary,
          value_propositions: vps,
          observations: [],
          metrics: {} as SenderResponse["metrics"],
        };
        setSender(reconstructed);
      } catch (e) {
        console.warn("hydrateSender: failed to load sender", e);
      }
    },
    [],
  );

  const attachStream = React.useCallback(
    (
      kind: "sender" | "target",
      run_id: string,
      isReconnect: boolean,
    ) => {
      cleanup();
      unsubRef.current = streamProgress(
        kind,
        run_id,
        (ev) =>
          setRun((r) => (r ? { ...r, events: [...r.events, ev] } : r)),
        async () => {
          try {
            const result =
              kind === "sender"
                ? await getSenderResult(run_id)
                : await getTargetResult(run_id);
            if (kind === "sender") {
              setSender(result as SenderResponse);
              setView("icp");
              syncUrl("icp", { kind, run_id });
            } else {
              const t = result as TargetResponse;
              setTarget(t);
              // Make sure sender state is still available after a target run.
              // If it isn't (e.g. user reloaded mid-run), hydrate it from the
              // sender_company_id the target run carries.
              if (!sender && t.sender_company_id) {
                await hydrateSenderFromCompanyId(t.sender_company_id, "");
              }
              setView("outreach");
              syncUrl("outreach", { kind, run_id });
            }
            setRun((r) => (r ? { ...r, done: true } : r));
            invalidateSidebar();
          } catch (e) {
            setRun((r) =>
              r ? { ...r, done: true, error: String(e) } : r,
            );
          } finally {
            setPendingKind(null);
          }
        },
        (msg) => {
          if (isReconnect) {
            (async () => {
              try {
                const result =
                  kind === "sender"
                    ? await getSenderResult(run_id)
                    : await getTargetResult(run_id);
                if (kind === "sender") {
                  setSender(result as SenderResponse);
                  setView("icp");
                  syncUrl("icp", { kind, run_id });
                } else {
                  const t = result as TargetResponse;
                  setTarget(t);
                  if (!sender && t.sender_company_id) {
                    await hydrateSenderFromCompanyId(t.sender_company_id, "");
                  }
                  setView("outreach");
                  syncUrl("outreach", { kind, run_id });
                }
                setRun((r) => (r ? { ...r, done: true } : r));
                invalidateSidebar();
              } catch (e) {
                setRun((r) =>
                  r ? { ...r, done: true, error: String(e) } : r,
                );
              } finally {
                setPendingKind(null);
              }
            })();
            return;
          }
          setRun((r) => (r ? { ...r, done: true, error: msg } : r));
          setPendingKind(null);
        },
      );
    },
    [cleanup, syncUrl, invalidateSidebar, sender, hydrateSenderFromCompanyId],
  );

  React.useEffect(() => {
    if (hydratedRef.current) return;
    hydratedRef.current = true;

    const urlView = searchParams.get("view") as View | null;
    const urlKind = searchParams.get("kind") as "sender" | "target" | null;
    const urlRun = searchParams.get("run");

    const startView: View =
      urlView && VALID_VIEWS.includes(urlView) ? urlView : "sender";

    if (!urlKind || !urlRun) {
      setView(startView);
      setHydrating(false);
      return;
    }

    (async () => {
      try {
        const status =
          urlKind === "sender"
            ? await getSenderStatus(urlRun)
            : await getTargetStatus(urlRun);

        if (status.state === "done") {
          if (urlKind === "sender") {
            const result = await getSenderResult(urlRun);
            setSender(result);
            setView(startView !== "sender" ? startView : "icp");
          } else {
            const result = await getTargetResult(urlRun);
            setTarget(result);
            // Critical: also restore the sender artifact so the workflow does
            // not get stuck on the outreach view with no way back to ICP.
            if (result.sender_company_id) {
              await hydrateSenderFromCompanyId(
                result.sender_company_id,
                "",
              );
            }
            // Honor whichever view the URL asked for; default to outreach
            // when no explicit view was set. This preserves "back to ICP"
            // across reloads.
            if (startView === "icp" || startView === "analytics") {
              setView(startView);
            } else {
              setView("outreach");
            }
          }
        } else if (status.state === "running") {
          setPendingKind(urlKind);
          setRun({
            kind: urlKind,
            run_id: urlRun,
            events: [],
            done: false,
            error: null,
            reconnected: true,
          });
          attachStream(urlKind, urlRun, true);
          setView(startView);
        } else {
          syncUrl("sender", null);
          setView("sender");
        }
      } catch {
        syncUrl("sender", null);
        setView("sender");
      } finally {
        setHydrating(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onStartSender = async (url: string) => {
    setPendingKind("sender");
    setSender(null);
    setTarget(null);
    setRun({
      kind: "sender",
      run_id: null,
      events: [],
      done: false,
      error: null,
      url,
    });
    try {
      const { run_id } = await startSender(url);
      setRun((r) => (r ? { ...r, run_id } : r));
      syncUrl("sender", { kind: "sender", run_id });
      attachStream("sender", run_id, false);
    } catch (e) {
      setRun({
        kind: "sender",
        run_id: null,
        events: [],
        done: true,
        error: String(e),
        url,
      });
      setPendingKind(null);
    }
  };

  const onStartTarget = async (input: {
    target_url: string;
    persona: PersonaInput;
  }) => {
    if (!sender) return;
    setPendingKind("target");
    setTarget(null);
    setRun({
      kind: "target",
      run_id: null,
      events: [],
      done: false,
      error: null,
      url: input.target_url,
    });
    try {
      const { run_id } = await startTarget({
        sender_company_id: sender.company_id,
        target_url: input.target_url,
        persona: input.persona,
      });
      setRun((r) => (r ? { ...r, run_id } : r));
      syncUrl("icp", { kind: "target", run_id });
      attachStream("target", run_id, false);
    } catch (e) {
      setRun({
        kind: "target",
        run_id: null,
        events: [],
        done: true,
        error: String(e),
        url: input.target_url,
      });
      setPendingKind(null);
    }
  };

  // In the continuous layout the ICP/VP section stays mounted above the
  // outreach card, so "Back to ICP" just scrolls the user up to it. The
  // run pointer stays in the URL so a reload still re-hydrates the target.
  const goBackToIcp = () => {
    setView("icp");
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
    if (run?.kind === "target" && run.run_id) {
      syncUrl("icp", { kind: "target", run_id: run.run_id });
    } else {
      syncUrl("icp", null);
    }
  };

  const goAnalytics = () => {
    setView("analytics");
    if (run?.kind === "target" && run.run_id) {
      syncUrl("analytics", { kind: "target", run_id: run.run_id });
    } else {
      syncUrl("analytics", null);
    }
  };

  const goBackOutreach = () => {
    setView("outreach");
    if (run?.kind === "target" && run.run_id) {
      syncUrl("outreach", { kind: "target", run_id: run.run_id });
    } else {
      syncUrl("outreach", null);
    }
  };

  const onNewTarget = () => {
    // Drop the previous target result entirely so the user gets a clean
    // "ready for the next target" surface and the URL no longer references
    // the old run. The sender stays in state.
    setTarget(null);
    setRun(null);
    setView("icp");
    syncUrl("icp", null);
    if (typeof window !== "undefined") {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  // Continuous flow: sections render based on STATE, not on a single
  // active view. As the user progresses (sender -> ICP/VP -> target ->
  // outreach), each new section stacks below the previous one instead of
  // unmounting it. The only mode that swaps sections is analytics, which
  // intentionally replaces the outreach card.
  const showSenderRunningBanner = pendingKind === "sender" && run !== null;
  const showLiveStream = pendingKind !== null && run !== null;
  const showAnalyticsInsteadOfOutreach = view === "analytics" && target !== null;

  if (hydrating) {
    return (
      <div className="flex items-center justify-center py-24 text-sm text-muted-foreground">
        Loading...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-10">
      {/* SECTION 1: Sender. Initial input form OR "Analyzing acme.com" banner.
          Once a sender flow has completed, the sender header is part of
          StepIcp below (it owns the H2 with the hostname), so we hide the
          input form here to avoid duplication and to make sure the user
          cannot accidentally kick off a second sender run mid-flow. */}
      {!sender && !showSenderRunningBanner && (
        <StepSender onStart={onStartSender} running={false} />
      )}

      {showSenderRunningBanner && (
        <motion.div
          key="sender-running"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="mx-auto flex w-full max-w-2xl flex-col items-center pt-20 md:pt-32"
        >
          <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
            Analyzing
          </p>
          <h1 className="mt-2 break-all text-center text-3xl font-semibold tracking-tight md:text-4xl">
            {prettyHost(run?.url ?? "")}
          </h1>
          <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <span>Crawl → extract → validate → synthesize</span>
          </div>
        </motion.div>
      )}

      {/* SECTION 2: Sender artifacts + target launcher. Stays mounted across
          the whole rest of the workflow so the user always has the ICP/VP
          context visible above any generated target. */}
      {sender && (
        <StepIcp
          sender={sender}
          onContinue={onStartTarget}
          running={pendingKind === "target"}
        />
      )}

      {/* SECTION 3: Live progress stream while any flow is in flight. */}
      {showLiveStream && run && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
          className="mx-auto w-full max-w-2xl"
        >
          {run.reconnected && run.events.length === 0 && (
            <p className="mb-3 text-center text-xs text-muted-foreground">
              Reconnecting to in-flight run. Earlier progress was emitted before
              you reloaded.
            </p>
          )}
          <ProgressStream
            events={run.events}
            kind={run.kind}
            done={run.done}
          />
          {run.error && (
            <p className="mt-3 text-sm text-destructive text-center">
              {run.error}
            </p>
          )}
        </motion.div>
      )}

      {/* SECTION 4: Target outreach (fit, persona, selected VP, emails). Sits
          BELOW the sender section so the user keeps ICP/VP visible at the
          same time. */}
      {target && !showAnalyticsInsteadOfOutreach && (
        <StepOutreach
          result={target}
          onBack={goBackToIcp}
          onShowAnalytics={goAnalytics}
          onNewTarget={onNewTarget}
        />
      )}

      {/* SECTION 5: Analytics. Toggled by the outreach "Analytics" button.
          Replaces the outreach card on demand; the sender section above
          stays visible. */}
      {showAnalyticsInsteadOfOutreach && target && (
        <StepAnalytics result={target} onBack={goBackOutreach} />
      )}
    </div>
  );
}

function prettyHost(url: string): string {
  if (!url) return "";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
