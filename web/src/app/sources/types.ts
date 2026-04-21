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
};

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

export type DetectResult = {
  adapter: string;
  slug: string | null;
  url: string;
  company_guess: string;
  reachable: boolean;
  job_count: number | null;
  error: string | null;
};

export type AddCompanyCandidate = {
  employer_name: string;
  jobs_count: number;
  sample_title: string | null;
  sample_location: string | null;
  already_in_db: boolean;
};

export type SourceView = "leads" | "no_jobs" | "not_relevant" | "broken" | "all";

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
