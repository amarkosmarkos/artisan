"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, Globe, Loader2 } from "lucide-react";
import { MarkosLogo } from "@/components/markos-logo";
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
    <div className="flex min-h-[calc(100vh-5rem)] flex-col items-center justify-center gap-14 py-12">
      <section className="w-full max-w-xl text-center">
        <div className="mb-6 flex justify-center">
          <MarkosLogo href={null} size="md" />
        </div>

        <h1 className="text-3xl font-semibold tracking-tight text-balance md:text-4xl">
          Research a sender.
          <span className="block text-muted-foreground">
            Get an auditable strategy.
          </span>
        </h1>

        <p className="mx-auto mt-4 max-w-md text-sm leading-relaxed text-muted-foreground">
          Paste a company website. We crawl it, extract observations, and
          synthesize an ICP and value proposition you can stand behind.
        </p>

        <form onSubmit={submit} className="mt-9 w-full">
          <label className="sr-only" htmlFor="sender-url">
            Sender URL
          </label>
          <div className="home-search">
            <Globe className="home-search-icon" aria-hidden />
            <Input
              id="sender-url"
              autoFocus
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={start.isPending}
              placeholder="https://yourcompany.com"
              className="home-search-input"
            />
            <Button
              type="submit"
              disabled={!url.trim() || start.isPending}
              className="home-search-button"
            >
              {start.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Starting
                </>
              ) : (
                <>
                  Start
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
        <section className="w-full max-w-xl">
          <div className="mb-3 flex items-center justify-between">
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
          <ul className="divide-y divide-border/50 overflow-hidden rounded-xl border border-border/50 bg-card/50 backdrop-blur-sm">
            {senders.map((s) => (
              <li key={s.company_id}>
                <Link
                  href={`/senders/${s.company_id}`}
                  className="flex items-center justify-between gap-3 px-4 py-3 text-sm transition-colors hover:bg-accent/40"
                >
                  <span className="truncate font-medium">{prettyUrl(s.url)}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
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
