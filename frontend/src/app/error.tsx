"use client";

import * as React from "react";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    console.error("[RouteError]", error, error?.stack, error?.digest);
  }, [error]);

  return (
    <div className="space-y-3 p-4">
      <h1 className="text-base font-semibold">Page error</h1>
      <p className="text-xs text-muted-foreground">
        Full stack below; the same information is in the browser console as{" "}
        <code>[RouteError]</code>.
      </p>
      <pre className="max-h-[480px] overflow-auto rounded-md border border-border/60 bg-card/40 p-3 text-[11px] leading-snug">
        {error?.name}: {error?.message}
        {error?.digest ? `\ndigest: ${error.digest}` : ""}
        {error?.stack ? `\n\n${error.stack}` : ""}
      </pre>
      <button
        onClick={reset}
        className="rounded-md border border-border/60 bg-card px-3 py-1.5 text-xs hover:bg-accent/30"
      >
        Try again
      </button>
    </div>
  );
}
