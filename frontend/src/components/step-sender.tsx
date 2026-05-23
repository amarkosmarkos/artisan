"use client";

import * as React from "react";
import { motion } from "framer-motion";
import { ArrowRight, Globe, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Props {
  onStart: (url: string) => void;
  running: boolean;
}

export function StepSender({ onStart, running }: Props) {
  const [url, setUrl] = React.useState("");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = url.trim();
    if (!v || running) return;
    onStart(v);
  };

  return (
    <motion.div
      key="sender"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -16 }}
      transition={{ duration: 0.4, ease: [0.2, 0.8, 0.2, 1] }}
      className="mx-auto flex w-full max-w-2xl flex-col items-center pt-28 md:pt-40"
    >
      <form onSubmit={submit} className="flex w-full flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Globe className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="https://yourcompany.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={running}
            autoFocus
            className="pl-9 h-12 text-base"
          />
        </div>
        <Button
          type="submit"
          size="lg"
          disabled={running || !url.trim()}
          className="h-12 px-6"
        >
          {running ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Researching
            </>
          ) : (
            <>
              Start
              <ArrowRight className="h-4 w-4" />
            </>
          )}
        </Button>
      </form>
    </motion.div>
  );
}
