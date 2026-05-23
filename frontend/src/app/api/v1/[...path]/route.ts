// Catch-all proxy from /api/v1/* to the backend. Streams SSE natively.
//
// We deliberately do NOT use next.config.mjs `rewrites()` because that proxy
// buffers responses and breaks Server-Sent Events: Chrome rejects them with
// ERR_INCOMPLETE_CHUNKED_ENCODING. This route handler forwards requests
// with explicit streaming control.

import type { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const fetchCache = "force-no-store";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

async function proxy(
  req: NextRequest,
  path: string[],
): Promise<Response> {
  const search = req.nextUrl.search ?? "";
  const target = `${BACKEND_URL}/api/v1/${path.join("/")}${search}`;
  const isStream = path[path.length - 1] === "stream";

  // Only forward a minimal, safe set of headers. In particular we MUST NOT
  // forward Accept-Encoding (the upstream might gzip an SSE stream, which
  // breaks streaming), Host, or Connection.
  const forwardHeaders = new Headers();
  const contentType = req.headers.get("content-type");
  if (contentType) forwardHeaders.set("content-type", contentType);
  const accept = req.headers.get("accept");
  if (accept) forwardHeaders.set("accept", accept);

  const init: RequestInit = {
    method: req.method,
    headers: forwardHeaders,
  };

  if (req.method !== "GET" && req.method !== "HEAD") {
    // Buffer small JSON bodies; our requests are tiny.
    const bodyText = await req.text();
    if (bodyText) init.body = bodyText;
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch (err) {
    return new Response(
      JSON.stringify({ error: "upstream_unreachable", detail: String(err) }),
      { status: 502, headers: { "content-type": "application/json" } },
    );
  }

  if (isStream) {
    // Pass the upstream ReadableStream straight through. Headers force
    // any intermediate buffering off (compression middleware, browsers).
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") || "text/event-stream",
        "cache-control": "no-cache, no-transform",
        "connection": "keep-alive",
        "x-accel-buffering": "no",
      },
    });
  }

  // For regular JSON, fully buffer and forward. This avoids any subtle
  // streaming/encoding issues for short responses.
  const bodyBuf = await upstream.arrayBuffer();
  const respHeaders = new Headers();
  const upstreamCt = upstream.headers.get("content-type");
  if (upstreamCt) respHeaders.set("content-type", upstreamCt);
  return new Response(bodyBuf, {
    status: upstream.status,
    headers: respHeaders,
  });
}

export async function GET(
  req: NextRequest,
  ctx: { params: { path: string[] } },
) {
  return proxy(req, ctx.params.path);
}
export async function POST(
  req: NextRequest,
  ctx: { params: { path: string[] } },
) {
  return proxy(req, ctx.params.path);
}
export async function PUT(
  req: NextRequest,
  ctx: { params: { path: string[] } },
) {
  return proxy(req, ctx.params.path);
}
export async function PATCH(
  req: NextRequest,
  ctx: { params: { path: string[] } },
) {
  return proxy(req, ctx.params.path);
}
export async function DELETE(
  req: NextRequest,
  ctx: { params: { path: string[] } },
) {
  return proxy(req, ctx.params.path);
}
export async function OPTIONS(
  req: NextRequest,
  ctx: { params: { path: string[] } },
) {
  return proxy(req, ctx.params.path);
}
