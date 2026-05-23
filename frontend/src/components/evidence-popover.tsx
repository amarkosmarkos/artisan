"use client";

import * as React from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  EvidenceCard,
  useEvidenceLookup,
} from "@/components/claim-evidence";
import { cn } from "@/lib/utils";
import type { EvidenceRecord } from "@/lib/api";

interface Props {
  evidenceRefs: string[];
  /** Pre-fetched map; if omitted, resolves via `/evidence/resolve`. */
  evidenceById?: Map<string, EvidenceRecord>;
  inline?: boolean;
}

export function EvidenceList({
  evidenceRefs,
  evidenceById: externalEvidence,
  inline,
}: Props) {
  const resolved = useEvidenceLookup(
    externalEvidence ? [] : evidenceRefs,
  );
  const evidenceById = externalEvidence ?? resolved;

  const items = evidenceRefs
    .map((id) => evidenceById.get(id))
    .filter((r): r is EvidenceRecord => Boolean(r));

  if (evidenceRefs.length === 0) {
    return (
      <p className="text-xs text-muted-foreground italic">
        No supporting observations
      </p>
    );
  }

  if (items.length === 0) {
    return (
      <p className="text-xs text-muted-foreground italic">
        Loading evidence…
      </p>
    );
  }

  return (
    <ul
      className={cn("flex flex-col gap-2", inline ? "text-xs" : "text-sm")}
    >
      {items.map((ev) => (
        <li key={ev.observation_id}>
          <EvidenceCard ev={ev} compact={inline} />
        </li>
      ))}
    </ul>
  );
}

export function ExpandableEvidence({
  count,
  children,
}: {
  count: number;
  children: React.ReactNode;
}) {
  const [open, setOpen] = React.useState(false);
  if (count === 0) {
    return (
      <span className="text-[11px] uppercase tracking-wide text-muted-foreground">
        no evidence
      </span>
    );
  }
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[11px] uppercase tracking-wide text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? "hide" : "show"} {count} evidence
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="pt-2">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
}
