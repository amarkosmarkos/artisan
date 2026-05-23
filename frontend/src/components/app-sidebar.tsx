"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Building2,
  ChevronRight,
  Compass,
  Megaphone,
  Plus,
  Target as TargetIcon,
  UserRound,
} from "lucide-react";
import { cn } from "@/lib/utils";
import {
  listCompanies,
  listPersonas,
  listSenderTargets,
  type CompanyRow,
  type PersonaRow,
  type SenderTargetRow,
} from "@/lib/api";

interface NavItem {
  href: string;
  label: string;
  icon: React.ReactNode;
  matchPrefix: string;
}

const NAV: NavItem[] = [
  {
    href: "/",
    label: "Home",
    icon: <Compass className="h-4 w-4" />,
    matchPrefix: "/",
  },
  {
    href: "/metrics",
    label: "Metrics",
    icon: <Activity className="h-4 w-4" />,
    matchPrefix: "/metrics",
  },
];

export function AppSidebar() {
  const pathname = usePathname() ?? "/";
  const senders = useQuery({
    queryKey: ["sidebar-senders"],
    queryFn: () => listCompanies({ role: "sender", limit: 50 }),
    staleTime: 30_000,
  });

  return (
    <aside className="hidden md:flex md:w-64 md:shrink-0 md:flex-col md:border-r md:border-border/60 md:bg-card/40 md:backdrop-blur md:sticky md:top-0 md:h-screen">
      <div className="px-5 pt-6 pb-4">
        <Link href="/" className="flex items-center gap-2">
          <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-foreground text-background font-semibold">
            A
          </span>
          <span className="text-sm font-semibold tracking-tight">Artisan</span>
        </Link>
      </div>

      <Link
        href="/run"
        className="mx-3 mb-3 flex items-center gap-2 rounded-md border border-border/60 bg-background/60 px-3 py-2 text-sm font-medium hover:border-foreground/40 hover:bg-accent/60"
      >
        <Plus className="h-4 w-4" />
        New campaign
      </Link>

      <div className="px-2 mb-1">
        {NAV.map((item) => {
          const active =
            item.href === "/"
              ? pathname === "/"
              : pathname === item.href ||
                pathname.startsWith(`${item.matchPrefix}/`);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
              )}
            >
              {item.icon}
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>

      <div className="px-2 pt-2 pb-1">
        <div className="flex items-center gap-2 px-3 pb-1.5 pt-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground/80">
          <Megaphone className="h-3 w-3" />
          Campaigns
        </div>
        <nav className="flex-1 overflow-y-auto px-1">
          {senders.isLoading && (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              Loading…
            </p>
          )}
          {!senders.isLoading &&
            (senders.data?.companies ?? []).length === 0 && (
              <Link
                href="/run"
                className="block px-3 py-2 text-xs text-muted-foreground hover:text-foreground"
              >
                Start your first campaign →
              </Link>
            )}
          {(senders.data?.companies ?? []).map((s) => (
            <SenderNode key={s.company_id} sender={s} pathname={pathname} />
          ))}
        </nav>
      </div>

      <div className="mt-auto px-5 pb-5 pt-3 text-[10px] uppercase tracking-wide text-muted-foreground/70">
        Evidence-first outbound
      </div>
    </aside>
  );
}

function SenderNode({
  sender,
  pathname,
}: {
  sender: CompanyRow;
  pathname: string;
}) {
  const senderHref = `/senders/${sender.company_id}`;
  const isActive = pathname.startsWith(senderHref);
  const [open, setOpen] = React.useState(isActive);

  React.useEffect(() => {
    if (isActive) setOpen(true);
  }, [isActive]);

  const targets = useQuery({
    enabled: open,
    queryKey: ["sidebar-targets", sender.company_id],
    queryFn: () => listSenderTargets(sender.company_id),
    staleTime: 30_000,
  });

  return (
    <div className="mb-0.5">
      <div
        className={cn(
          "group flex items-center gap-1 rounded-md pr-1.5 text-sm",
          isActive && "bg-accent/60",
        )}
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:text-foreground"
          aria-label={open ? "Collapse" : "Expand"}
        >
          <ChevronRight
            className={cn(
              "h-3.5 w-3.5 transition-transform",
              open && "rotate-90",
            )}
          />
        </button>
        <Link
          href={senderHref}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-1.5 py-1.5 pr-2 text-sm",
            isActive
              ? "text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Building2 className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--sender))]" />
          <span className="truncate">{prettyUrl(sender.url)}</span>
        </Link>
      </div>
      {open && (
        <SenderChildren
          senderCompanyId={sender.company_id}
          pathname={pathname}
          targets={targets.data?.targets ?? []}
          isLoading={targets.isLoading}
        />
      )}
    </div>
  );
}

function SenderChildren({
  senderCompanyId: _senderCompanyId,
  pathname,
  targets,
  isLoading,
}: {
  senderCompanyId: string;
  pathname: string;
  targets: SenderTargetRow[];
  isLoading: boolean;
}) {
  if (isLoading) {
    return (
      <p className="ml-7 py-1 text-xs text-muted-foreground">Loading…</p>
    );
  }
  if (targets.length === 0) {
    return (
      <p className="ml-7 py-1 text-xs text-muted-foreground/70">No targets</p>
    );
  }
  return (
    <div className="ml-7 mt-0.5 space-y-0.5 border-l border-border/40 pl-2">
      {targets.map((t) => (
        <TargetNode key={t.company_id} target={t} pathname={pathname} />
      ))}
    </div>
  );
}

function TargetNode({
  target,
  pathname,
}: {
  target: SenderTargetRow;
  pathname: string;
}) {
  const targetHref = `/targets/${target.company_id}`;
  const isActive = pathname.startsWith(targetHref);
  const [open, setOpen] = React.useState(isActive);

  React.useEffect(() => {
    if (isActive) setOpen(true);
  }, [isActive]);

  const personas = useQuery({
    enabled: open,
    queryKey: ["sidebar-personas", target.company_id],
    queryFn: () => listPersonas(target.company_id),
    staleTime: 30_000,
  });

  return (
    <div>
      <div
        className={cn(
          "group flex items-center gap-1 rounded-md pr-1.5",
          isActive && "bg-accent/60",
        )}
      >
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="grid h-6 w-6 place-items-center rounded text-muted-foreground hover:text-foreground"
          aria-label={open ? "Collapse" : "Expand"}
        >
          <ChevronRight
            className={cn(
              "h-3 w-3 transition-transform",
              open && "rotate-90",
            )}
          />
        </button>
        <Link
          href={targetHref}
          className={cn(
            "flex min-w-0 flex-1 items-center gap-1.5 py-1 pr-1 text-sm",
            isActive
              ? "text-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <TargetIcon className="h-3.5 w-3.5 shrink-0 text-[hsl(var(--target))]" />
          <span className="truncate">{prettyUrl(target.url)}</span>
        </Link>
      </div>
      {open && (
        <PersonaChildren
          targetCompanyId={target.company_id}
          personas={personas.data?.personas ?? []}
          isLoading={personas.isLoading}
          pathname={pathname}
        />
      )}
    </div>
  );
}

function PersonaChildren({
  targetCompanyId,
  personas,
  isLoading,
  pathname,
}: {
  targetCompanyId: string;
  personas: PersonaRow[];
  isLoading: boolean;
  pathname: string;
}) {
  if (isLoading) {
    return (
      <p className="ml-7 py-1 text-xs text-muted-foreground">Loading…</p>
    );
  }
  if (personas.length === 0) {
    return (
      <p className="ml-7 py-1 text-xs text-muted-foreground/70">
        No personas yet
      </p>
    );
  }
  return (
    <div className="ml-7 mt-0.5 space-y-0.5 border-l border-border/40 pl-2">
      {personas.map((p) => {
        const href = `/targets/${targetCompanyId}?persona=${p.persona_id}`;
        const active = pathname.startsWith(`/targets/${targetCompanyId}`);
        return (
          <Link
            key={p.persona_id}
            href={href}
            className={cn(
              "flex items-center gap-1.5 rounded px-1 py-1 text-xs",
              active
                ? "text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <UserRound className="h-3 w-3 shrink-0 text-[hsl(var(--persona))]" />
            <span className="truncate">
              {p.name ? `${p.name} · ${p.role}` : p.role}
            </span>
          </Link>
        );
      })}
    </div>
  );
}

function prettyUrl(url: string): string {
  try {
    const u = new URL(url);
    return u.hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
