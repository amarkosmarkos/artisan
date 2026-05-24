"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  ExternalLink,
  Loader2,
  Plus,
  Send,
  Trash2,
} from "lucide-react";
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  addSenderTarget,
  createPersona,
  deletePersona,
  listPersonas,
  listSenderTargets,
  removeSenderTarget,
  startTarget,
  type PersonaRow,
  type SenderTargetRow,
} from "@/lib/api";
import type { Seniority } from "@/lib/types";

const SENIORITIES: { value: Seniority; label: string }[] = [
  { value: "ic", label: "IC" },
  { value: "manager", label: "Manager" },
  { value: "director", label: "Director" },
  { value: "vp", label: "VP" },
  { value: "c_level", label: "C-level" },
  { value: "founder", label: "Founder" },
];

export interface TargetsPrefill {
  target_url: string;
  role: string;
  seniority: Seniority;
  name?: string;
}

interface Props {
  senderCompanyId: string;
  /** Prefill the add-target / persona forms (e.g. from suggested targets panel). */
  prefill?: TargetsPrefill | null;
}

export function SenderTargetsPanel({
  senderCompanyId,
  prefill = null,
}: Props) {
  const queryClient = useQueryClient();
  const targets = useQuery({
    queryKey: ["sender-targets", senderCompanyId],
    queryFn: () => listSenderTargets(senderCompanyId),
  });

  const invalidateAll = React.useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["sender-targets", senderCompanyId],
    });
    // Sidebar reads a different key; invalidate it too so it stays in sync.
    queryClient.invalidateQueries({
      queryKey: ["sidebar-targets", senderCompanyId],
    });
    queryClient.invalidateQueries({ queryKey: ["sidebar-senders"] });
  }, [queryClient, senderCompanyId]);

  const addTarget = useMutation({
    mutationFn: (target_url: string) =>
      addSenderTarget(senderCompanyId, target_url),
    onSuccess: invalidateAll,
  });

  const removeTarget = useMutation({
    mutationFn: (targetCompanyId: string) =>
      removeSenderTarget(senderCompanyId, targetCompanyId),
    onSuccess: invalidateAll,
  });

  const [targetUrl, setTargetUrl] = React.useState("");

  React.useEffect(() => {
    if (!prefill?.target_url) return;
    setTargetUrl(prefill.target_url);
  }, [prefill]);

  const onAddTarget = (e: React.FormEvent) => {
    e.preventDefault();
    if (!targetUrl.trim()) return;
    addTarget.mutate(targetUrl.trim(), {
      onSuccess: () => setTargetUrl(""),
    });
  };

  const rows: SenderTargetRow[] = targets.data?.targets ?? [];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Add target</CardTitle>
          <CardDescription>
            Register a target URL under this sender. You can attach personas
            below and generate outreach for each one.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form
            onSubmit={onAddTarget}
            className="flex flex-wrap items-end gap-3"
          >
            <div className="flex-1 min-w-[260px]">
              <Input
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                placeholder="https://target-company.com"
              />
            </div>
            <Button
              type="submit"
              disabled={!targetUrl.trim() || addTarget.isPending}
            >
              <Plus className="h-4 w-4" />
              Add target
            </Button>
          </form>
          {addTarget.isError && (
            <p className="mt-2 text-xs text-destructive">
              {String(addTarget.error)}
            </p>
          )}
        </CardContent>
      </Card>

      {targets.isLoading && (
        <p className="text-sm text-muted-foreground">Loading targets…</p>
      )}

      {!targets.isLoading && rows.length === 0 && (
        <Card>
          <CardContent className="py-6 text-center text-sm text-muted-foreground">
            No targets yet. Add one above to start.
          </CardContent>
        </Card>
      )}

      {rows.map((t) => (
        <SenderTargetCard
          key={t.company_id}
          senderCompanyId={senderCompanyId}
          target={t}
          personaPrefill={
            prefill?.target_url && urlsMatch(prefill.target_url, t.url)
              ? prefill
              : null
          }
          onRemove={() => {
            if (
              confirm(
                `Remove ${prettyUrl(t.url)} from this sender?\n\nThe target's evidence and emails are kept; only the association is dropped.`,
              )
            ) {
              removeTarget.mutate(t.company_id);
            }
          }}
          removing={removeTarget.isPending}
        />
      ))}
    </div>
  );
}

function SenderTargetCard({
  senderCompanyId,
  target,
  onRemove,
  removing,
  personaPrefill,
}: {
  senderCompanyId: string;
  target: SenderTargetRow;
  onRemove: () => void;
  removing: boolean;
  personaPrefill?: TargetsPrefill | null;
}) {
  const [open, setOpen] = React.useState(true);
  const queryClient = useQueryClient();
  const router = useRouter();

  const personas = useQuery({
    enabled: open,
    queryKey: ["personas", target.company_id],
    queryFn: () => listPersonas(target.company_id),
  });

  const invalidatePersonas = React.useCallback(() => {
    queryClient.invalidateQueries({
      queryKey: ["personas", target.company_id],
    });
    queryClient.invalidateQueries({
      queryKey: ["sidebar-personas", target.company_id],
    });
  }, [queryClient, target.company_id]);

  const create = useMutation({
    mutationFn: (input: {
      role: string;
      seniority: Seniority;
      name?: string;
    }) => createPersona(target.company_id, input),
    onSuccess: invalidatePersonas,
  });

  const remove = useMutation({
    mutationFn: (personaId: string) => deletePersona(personaId),
    onSuccess: invalidatePersonas,
  });

  const generate = useMutation({
    mutationFn: async (persona: PersonaRow) => {
      const { run_id } = await startTarget({
        sender_company_id: senderCompanyId,
        target_url: target.url,
        persona: {
          role: persona.role,
          seniority: persona.seniority as Seniority,
        },
        persona_id: persona.persona_id,
      });
      return run_id;
    },
    onSuccess: (run_id) => {
      router.push(`/run?kind=target&run=${run_id}&view=icp`);
    },
  });

  const [role, setRole] = React.useState("VP of Sales");
  const [seniority, setSeniority] = React.useState<Seniority>("vp");
  const [name, setName] = React.useState("");

  React.useEffect(() => {
    if (!personaPrefill) return;
    setRole(personaPrefill.role);
    setSeniority(personaPrefill.seniority);
    setName(personaPrefill.name ?? "");
    setOpen(true);
  }, [personaPrefill]);

  const onCreate = (e: React.FormEvent) => {
    e.preventDefault();
    if (!role.trim()) return;
    create.mutate(
      { role: role.trim(), seniority, name: name.trim() || undefined },
      {
        onSuccess: () => setName(""),
      },
    );
  };

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0 pb-3">
        <button
          type="button"
          className="flex flex-1 items-start gap-2 text-left"
          onClick={() => setOpen((o) => !o)}
        >
          {open ? (
            <ChevronDown className="h-4 w-4 mt-0.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="h-4 w-4 mt-0.5 text-muted-foreground" />
          )}
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <CardTitle className="text-base truncate">
                {prettyUrl(target.url)}
              </CardTitle>
              <Badge variant="outline">target</Badge>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5">
              {target.company_id} · added {formatDate(target.added_at)}
            </p>
          </div>
        </button>
        <div className="flex shrink-0 gap-1">
          <Button variant="ghost" size="sm" asChild>
            <a href={target.url} target="_blank" rel="noreferrer">
              <ExternalLink className="h-4 w-4" />
            </a>
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <Link href={`/targets/${target.company_id}`}>
              View detail
            </Link>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onRemove}
            disabled={removing}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>

      {open && (
        <CardContent className="space-y-4">
          <form
            onSubmit={onCreate}
            className="flex flex-wrap items-end gap-3"
          >
            <div className="min-w-[200px] flex-1">
              <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Persona role
              </label>
              <Input
                value={role}
                onChange={(e) => setRole(e.target.value)}
                placeholder="VP of Sales"
              />
            </div>
            <div className="w-44">
              <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Seniority
              </label>
              <Select
                value={seniority}
                onValueChange={(v) => setSeniority(v as Seniority)}
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
            <div className="w-48">
              <label className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Name (optional)
              </label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Jane Doe"
              />
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={!role.trim() || create.isPending}
            >
              <Plus className="h-4 w-4" />
              Add persona
            </Button>
          </form>

          {personas.isLoading && (
            <p className="text-xs text-muted-foreground">Loading personas…</p>
          )}
          {personas.data?.personas?.length ? (
            <ul className="divide-y divide-border/60">
              {personas.data.personas.map((p) => (
                <li
                  key={p.persona_id}
                  className="flex items-center justify-between gap-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{p.name || p.role}</p>
                    <p className="text-xs text-muted-foreground">
                      {p.role} · {p.seniority}
                    </p>
                  </div>
                  <Button
                    size="sm"
                    onClick={() => generate.mutate(p)}
                    disabled={generate.isPending}
                  >
                    {generate.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Send className="h-3.5 w-3.5" />
                    )}
                    Generate outreach
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => {
                      if (confirm(`Delete persona "${p.role}"?`)) {
                        remove.mutate(p.persona_id);
                      }
                    }}
                    disabled={remove.isPending}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">
              No personas yet for this target.
            </p>
          )}
        </CardContent>
      )}
    </Card>
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

function urlsMatch(a: string, b: string): boolean {
  try {
    const na = new URL(a.includes("://") ? a : `https://${a}`);
    const nb = new URL(b.includes("://") ? b : `https://${b}`);
    return (
      na.hostname.replace(/^www\./, "") === nb.hostname.replace(/^www\./, "")
    );
  } catch {
    return a.trim().toLowerCase() === b.trim().toLowerCase();
  }
}
