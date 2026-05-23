"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import {
  getSenderResult,
  getSenderStatus,
  getTargetResult,
  getTargetStatus,
  startSender,
  startTarget,
  streamProgress,
} from "@/lib/api";
import type {
  PersonaInput,
  ProgressEvent,
  SenderResponse,
  TargetResponse,
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
}

const VALID_VIEWS: View[] = ["sender", "icp", "outreach", "analytics"];

const ROUTE = "/run";

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

  const [view, setView] = React.useState<View>("sender");
  const [run, setRun] = React.useState<RunState | null>(null);
  const [sender, setSender] = React.useState<SenderResponse | null>(null);
  const [target, setTarget] = React.useState<TargetResponse | null>(null);
  const [pendingKind, setPendingKind] = React.useState<"sender" | "target" | null>(null);
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
              setTarget(result as TargetResponse);
              setView("outreach");
              syncUrl("outreach", { kind, run_id });
            }
            setRun((r) => (r ? { ...r, done: true } : r));
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
                  setTarget(result as TargetResponse);
                  setView("outreach");
                  syncUrl("outreach", { kind, run_id });
                }
                setRun((r) => (r ? { ...r, done: true } : r));
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
    [cleanup, syncUrl],
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
            setView(
              startView === "analytics" ? "analytics" : "outreach",
            );
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
      });
      setPendingKind(null);
    }
  };

  const goBackToIcp = () => {
    setView("icp");
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

  const showLiveStream = pendingKind !== null && run !== null;

  if (hydrating) {
    return (
      <div className="flex items-center justify-center py-24 text-sm text-muted-foreground">
        Loading...
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-10">
      <AnimatePresence mode="wait">
        {view === "sender" && (
          <StepSender
            onStart={onStartSender}
            running={pendingKind === "sender"}
          />
        )}
        {view === "icp" && sender && (
          <StepIcp
            sender={sender}
            onContinue={onStartTarget}
            running={pendingKind === "target"}
          />
        )}
        {view === "outreach" && target && (
          <StepOutreach
            result={target}
            onBack={goBackToIcp}
            onShowAnalytics={goAnalytics}
          />
        )}
        {view === "analytics" && target && (
          <StepAnalytics result={target} onBack={goBackOutreach} />
        )}
      </AnimatePresence>

      {showLiveStream && run && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
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
    </div>
  );
}
