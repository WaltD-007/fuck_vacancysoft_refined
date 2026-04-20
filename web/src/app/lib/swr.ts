// Shared fetcher + API prefix for SWR hooks.
//
// Keeping the fetcher module-level means SWR dedupes by the relative
// path string (e.g. "/queue") across every component in the app — the
// Sidebar and the Leads page both calling `useSWR('/queue', fetcher)`
// share a single in-flight request and a single cache entry.

export const API = "http://localhost:8000/api";

export const fetcher = async (path: string) => {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.json();
};
