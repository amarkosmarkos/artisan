"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Globe, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { listCompanies, startSender, type CompanyRow } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [url, setUrl] = React.useState("");

  const recent = useQuery({
    queryKey: ["companies", "sender", "recent"],
    queryFn: () => listCompanies({ role: "sender", limit: 6 }),
  });

  const start = useMutation({
    mutationFn: (senderUrl: string) => startSender(senderUrl),
    onSuccess: ({ run_id }) => {
      router.push(`/run?kind=sender&run=${run_id}`);
    },
  });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const v = url.trim();
    if (!v || start.isPending) return;
    start.mutate(v);
  };

  const senders: CompanyRow[] = recent.data?.companies ?? [];

  return (
    <div className="flex min-h-[calc(100vh-5rem)] flex-col items-center justify-center gap-16 py-16">
      <section className="w-full max-w-2xl text-center">
        <p className="text-xs uppercase tracking-[0.2em] text-muted-foreground">
          Evidence-first outbound
        </p>
        <h1 className="mt-3 text-4xl md:text-5xl font-semibold tracking-tight text-balance">
          Research a sender.
          <br />
          <span className="text-muted-foreground">
            Get an auditable strategy.
          </span>
        </h1>
        <p className="mx-auto mt-4 max-w-md text-sm text-muted-foreground leading-relaxed">
          Paste a company website. We crawl it, extract observations, validate
          them with NLI, and synthesize an ICP and value proposition you can
          stand behind.
        </p>

        <form onSubmit={submit} className="mt-10 w-full max-w-xl mx-auto">
          <label className="sr-only" htmlFor="sender-url">
            Sender URL
          </label>
          <div className="relative">
            <Globe className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              id="sender-url"
              autoFocus
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={start.isPending}
              placeholder="https://yourcompany.com"
              className="h-14 pl-11 pr-36 text-base rounded-full bg-card/40 border-border/60 shadow-sm focus-visible:ring-2 focus-visible:ring-foreground/20"
            />
            <Button
              type="submit"
              disabled={!url.trim() || start.isPending}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 h-11 rounded-full px-5"
            >
              {start.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Starting
                </>
              ) : (
                <>
                  Start research
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </Button>
          </div>
          {start.isError && (
            <p className="mt-3 text-xs text-destructive">
              {String(start.error)}
            </p>
          )}
        </form>
      </section>

      {senders.length > 0 && (
        <section className="w-full max-w-2xl">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
              Recent senders
            </p>
            <Link
              href="/senders"
              className="text-xs text-muted-foreground hover:text-foreground"
            >
              See all
            </Link>
          </div>
          <ul className="divide-y divide-border/60 rounded-md border border-border/60 bg-card/40 backdrop-blur">
            {senders.map((s) => (
              <li key={s.company_id}>
                <Link
                  href={`/senders/${s.company_id}`}
                  className="flex items-center justify-between gap-3 px-4 py-3 text-sm hover:bg-accent/30"
                >
                  <span className="truncate font-medium text-foreground">
                    {prettyUrl(s.url)}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {formatDate(s.created_at)}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function prettyUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname);
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
