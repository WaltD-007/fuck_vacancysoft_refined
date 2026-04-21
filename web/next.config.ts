import type { NextConfig } from "next";

// The frontend talks to FastAPI via same-origin `/api/*`. In dev the two
// services run on different ports (frontend :3000, API :8000), so we
// rewrite `/api/*` at the Next.js server to forward to the API. In prod
// (Azure Container Apps) the rewrite target is overridden via the
// `BACKEND_API_URL` env var so the rewrite can point at the internal
// DNS name of the API container.
//
// Why rewrite and not a browser-side absolute URL:
//   - Same-origin requests mean no CORS config and no leaked internal
//     hostnames into the browser.
//   - A tunnel URL (ngrok / Cloudflare / Tailscale) "just works" — the
//     colleague's browser only needs to reach the frontend port.
//   - Production deploys don't require rebuilding the frontend when
//     the API hostname changes.
const backendApiUrl = process.env.BACKEND_API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  devIndicators: false,
  reactStrictMode: false,
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendApiUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
