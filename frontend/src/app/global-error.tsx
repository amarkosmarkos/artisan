"use client";

import * as React from "react";

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  React.useEffect(() => {
    // Make sure the real cause shows up in the browser console and in any
    // forwarded log collector, instead of the opaque Next.js shell.
    console.error("[GlobalError]", error, error?.stack, error?.digest);
  }, [error]);

  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, sans-serif",
          padding: "2rem",
          color: "#0a0a0a",
          background: "#fafafa",
        }}
      >
        <h1 style={{ fontSize: 18, marginBottom: 8 }}>
          Client-side error
        </h1>
        <p style={{ color: "#666", marginBottom: 16, fontSize: 13 }}>
          The app crashed while rendering. Full stack below; the same
          information is in the browser console as <code>[GlobalError]</code>.
        </p>
        <pre
          style={{
            background: "#fff",
            border: "1px solid #ddd",
            borderRadius: 6,
            padding: 12,
            fontSize: 12,
            whiteSpace: "pre-wrap",
            overflow: "auto",
            maxHeight: 480,
          }}
        >
          {error?.name}: {error?.message}
          {error?.digest ? `\ndigest: ${error.digest}` : ""}
          {error?.stack ? `\n\n${error.stack}` : ""}
        </pre>
        <button
          onClick={reset}
          style={{
            marginTop: 16,
            padding: "6px 12px",
            border: "1px solid #888",
            borderRadius: 6,
            background: "#fff",
            cursor: "pointer",
            fontSize: 13,
          }}
        >
          Try again
        </button>
      </body>
    </html>
  );
}
