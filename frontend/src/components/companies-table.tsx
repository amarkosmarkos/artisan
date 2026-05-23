"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  deleteCompany,
  listCompanies,
  type CompanyRow,
} from "@/lib/api";

interface Props {
  role: "sender" | "target";
  title: string;
  description: string;
  detailHrefBase: string; // "/senders" or "/targets"
}

export function CompaniesTable({ role, title, description, detailHrefBase }: Props) {
  const [q, setQ] = React.useState("");
  const queryClient = useQueryClient();
  const list = useQuery({
    queryKey: ["companies", role, q],
    queryFn: () =>
      listCompanies({ role, q: q.trim() || undefined, limit: 200 }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteCompany(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
    },
  });

  const rows: CompanyRow[] = list.data?.companies ?? [];

  return (
    <div className="space-y-6">
      <header className="flex items-end justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          <p className="text-sm text-muted-foreground">{description}</p>
        </div>
        <div className="w-72">
          <Input
            placeholder="Filter by URL…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        </div>
      </header>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">
            {rows.length} result{rows.length === 1 ? "" : "s"}
          </CardTitle>
          <CardDescription>
            {role === "sender"
              ? "Each row is a sender website you've researched."
              : "Each row is a target evaluation."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {list.isLoading && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {list.isError && (
            <p className="text-sm text-destructive">
              Failed to load: {String(list.error)}
            </p>
          )}
          {!list.isLoading && rows.length === 0 && (
            <p className="text-sm text-muted-foreground">
              No {role}s yet.
            </p>
          )}
          {rows.length > 0 && (
            <ul className="divide-y divide-border/60">
              {rows.map((r) => (
                <li
                  key={r.company_id}
                  className="flex items-center justify-between gap-3 py-3"
                >
                  <Link
                    href={`${detailHrefBase}/${r.company_id}`}
                    className="flex-1 min-w-0"
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground hover:underline">
                        {prettyUrl(r.url)}
                      </span>
                      <Badge variant="outline" className="capitalize">
                        {r.role}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {r.company_id} · {formatDate(r.created_at)}
                    </p>
                  </Link>
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-muted-foreground hover:text-foreground"
                    title="Open original URL"
                  >
                    <ExternalLink className="h-4 w-4" />
                  </a>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (
                        confirm(
                          `Delete ${prettyUrl(r.url)} and all its derived artifacts?`,
                        )
                      ) {
                        remove.mutate(r.company_id);
                      }
                    }}
                    disabled={remove.isPending}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
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
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
