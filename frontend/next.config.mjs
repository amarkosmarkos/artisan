/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // Note: we intentionally do NOT use `rewrites()` to proxy /api/v1/* to the
  // backend. Next.js's rewrite proxy buffers responses, which breaks SSE in
  // Chrome (ERR_INCOMPLETE_CHUNKED_ENCODING). The proxy lives in
  // src/app/api/v1/[...path]/route.ts and streams responses natively.
};

export default nextConfig;
