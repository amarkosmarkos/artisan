"use client";

/** Discrete ambient background — soft tint, not flat white/black. */
export function AppBackground() {
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-0 -z-10"
    >
      <div className="ambient-mesh" />
      <div className="ambient-grid" />
    </div>
  );
}
