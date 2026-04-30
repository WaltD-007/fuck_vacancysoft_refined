// Shared types and constants for the sources page and its components.
// Originally inlined in page.tsx; extracted during the Week 3 refactor so
// each extracted component can import from one canonical location.

export type Source = {
  id: number;
  employer_name: string;
  adapter_name: string;
  base_url: string;
  active: boolean;
  seed_type: string;
  ats_family: string | null;
  jobs: number;
  enriched: number;
  scored: number;
  categories: Record<string, number>;
  categories_by_country: Record<string, Record<string, number>>;
  sub_specialisms?: Record<string, Record<string, number>>;
  // {country: {category: {sub: count}}} — country-partitioned sub totals
  // so card readouts can narrow to the active country filter. Populated
  // by the ledger; missing when the backend is older than Apr 2026.
  sub_specialisms_by_country?: Record<string, Record<string, Record<string, number>>>;
  aggregator_hits?: Record<string, number>;
  employment_types?: Record<string, number>;
  last_run_status: string | null;
  last_run_error: string | null;
  // Preferred Supplier List flag — operator-curated for BD targeting.
  // Optional in the type because backends older than the PSL migration
  // won't include it; missing/false is treated as "not on PSL".
  is_psl?: boolean;
};


// ── Broken-card helper ─────────────────────────────────────────────────
// A card counts as "Broken" for the user-facing UI only when BOTH:
//   1. The direct source's latest SourceRun errored, AND
//   2. No aggregator is currently finding jobs for this employer.
//
// Pre-2026-04-23 the rule was just (1) — which surfaced ~89 cards as
// Broken even when Adzuna / Google Jobs / eFC / Coresignal were happily
// covering them. Operator preference: direct-source failures where
// aggregators compensate belong on the backend health report (hidden
// from typical users), not the user-facing "Broken" bucket. A card is
// Broken TO THE USER only when the lead flow is truly dark.
//
// Callers: sources/page.tsx (brokenCount filter, noJobsCount exclusion,
// notRelevantCount exclusion) + sources/components/SourceCard.tsx (red
// card border + status-row messaging).
export function isBroken(src: Source): boolean {
  const directFailed =
    src.last_run_status === "FAIL" || src.last_run_status === "error";
  if (!directFailed) return false;
  const aggregatorLeads = Object.values(src.aggregator_hits || {}).reduce(
    (sum, n) => sum + (n || 0),
    0,
  );
  if (aggregatorLeads > 0) return false;
  // last_run_status reflects the WORST status across all sister sources
  // for this employer. If any sister direct source has produced classified
  // leads (categories), the card has live coverage and isn't broken — the
  // failing sister is just one of several scrape paths to the same firm.
  const directLeads = Object.values(src.categories || {}).reduce(
    (sum, n) => sum + (n || 0),
    0,
  );
  if (directLeads > 0) return false;
  return true;
}

export type Stats = {
  total_sources: number;
  active_sources: number;
  total_jobs: number;
  total_enriched: number;
  total_scored: number;
  adapters: Record<string, number>;
  categories: Record<string, number>;
};

export type ScoredJob = {
  // Enriched-job id — needed by the per-row admin buttons (Dead job,
  // Wrong location) in the card drawer to target the correct row
  // server-side. Backend populates this on the /api/sources/{id}/jobs
  // response since 2026-04-21.
  id: string;
  title: string;
  company: string;
  location: string | null;
  country: string | null;
  category: string | null;
  sub_specialism: string | null;
  score: number | null;
  url: string | null;
};

export type AddCompanyCandidate = {
  employer_name: string;
  jobs_count: number;
  sample_title: string | null;
  sample_location: string | null;
  // URL of the representative advert captured by the CoreSignal preview row —
  // null when the preview carried no http(s) URL field. UI renders sample_title
  // as a link when present so the user can peek at the actual advert.
  sample_url: string | null;
  already_in_db: boolean;
};

// One lead surfaced by POST /sources/add-company/update-preview —
// not yet persisted. UI shows these in a table before committing.
export type AddCompanyUpdateLead = {
  external_id: string;
  title: string;
  company: string | null;
  location: string | null;
  url: string | null;
  posted_at: string | null;
  summary: string | null;
  // Which aggregator surfaced this row. One of:
  // 'coresignal' / 'adzuna' / 'efinancialcareers' / 'google_jobs'.
  // Optional for compatibility with pre-multi-aggregator API responses.
  source_adapter?: string | null;
};

export type SourceView = "leads" | "psl" | "no_jobs" | "not_relevant" | "broken" | "all";

export const AGGREGATOR_LABELS: Record<string, string> = {
  adzuna: "Adzuna",
  reed: "Reed",
  efinancialcareers: "eFinancialCareers",
  google_jobs: "Google Jobs",
  coresignal: "Coresignal",
};

export const CATEGORY_COLORS: Record<string, string> = {
  Risk: "var(--accent-light)",
  Quant: "var(--blue)",
  Compliance: "var(--green)",
  Audit: "var(--amber)",
  Cyber: "var(--red)",
  Legal: "#fd79a8",
  "Front Office": "#ffa500",
};
