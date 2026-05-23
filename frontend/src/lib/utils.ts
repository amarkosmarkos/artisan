import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatNumber(n: number | undefined | null, opts?: { digits?: number }) {
  if (n === undefined || n === null || Number.isNaN(n)) return "—";
  const digits = opts?.digits ?? (Number.isInteger(n) ? 0 : 2);
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}

export function formatCost(usd: number | undefined | null) {
  if (usd === undefined || usd === null) return "—";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatDuration(ms: number | undefined | null) {
  if (ms === undefined || ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${(ms / 60_000).toFixed(1)} m`;
}
