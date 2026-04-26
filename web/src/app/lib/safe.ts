// Defensive helpers for rendering data that originates outside the trust
// boundary (LLM output, scraped pages, third-party APIs).

// Reject URLs whose scheme isn't http(s) before placing them into an `href`.
// React 19 does NOT block `javascript:` URLs in href — it only emits a
// dev-mode warning. So any URL that came from an LLM, a scraper, or a
// third-party API must be scheme-checked before render. Returns `fallback`
// when the URL is missing, malformed, or not http(s).
//
// Origin scenarios the call sites guard against:
//   - LLM dossier output (e.g. hiring_managers[i].linkedin_url) — prompt
//     injection via a hostile job description can produce a `javascript:`
//     URL.
//   - Scraped lead URLs (lead.url, job.url) — most adapters extract from
//     <a href="..."> tags which browsers normalise, but it's not guaranteed
//     across every adapter / fallback path.
//   - CoreSignal API responses (cand.sample_url, lead.url in update flows)
//     — third-party data we trust but don't own.
export function safeHref(url: string | null | undefined, fallback: string): string {
  if (typeof url !== "string") return fallback;
  return /^https?:\/\//i.test(url.trim()) ? url : fallback;
}
