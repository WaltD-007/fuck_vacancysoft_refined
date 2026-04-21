// Shared fetcher + API prefix for SWR hooks.
//
// Keeping the fetcher module-level means SWR dedupes by the relative
// path string (e.g. "/queue") across every component in the app — the
// Sidebar and the Leads page both calling `useSWR('/queue', fetcher)`
// share a single in-flight request and a single cache entry.
//
// API base is a same-origin relative path. In dev and prod, Next.js
// rewrites `/api/*` to the FastAPI backend (see web/next.config.ts).
// Keeping it relative means:
//   1. The frontend works behind any tunnel / ingress URL without
//      a rebuild (e.g. sharing with a colleague via ngrok, Cloudflare
//      Tunnel, Tailscale — the colleague's browser never needs to know
//      about the API host).
//   2. No CORS config needed — fetches are same-origin.
//   3. Deploying to Azure Container Apps just needs the rewrite target
//      to point at the API container; no frontend code change.
//
// Override for a standalone frontend that talks to a remote API by
// setting NEXT_PUBLIC_API_BASE at build time.
export const API = process.env.NEXT_PUBLIC_API_BASE || "/api";

export const fetcher = async (path: string) => {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
};
