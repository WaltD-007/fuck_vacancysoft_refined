"use client";

import { useEffect, useState } from "react";
import Sidebar from "../components/Sidebar";

const API = "http://localhost:8000/api";

type Source = {
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
  aggregator_hits?: Record<string, number>;
  employment_types?: Record<string, number>;
  last_run_status: string | null;
  last_run_error: string | null;
};

type Stats = {
  total_sources: number;
  active_sources: number;
  total_jobs: number;
  total_enriched: number;
  total_scored: number;
  adapters: Record<string, number>;
  categories: Record<string, number>;
};

type ScoredJob = {
  title: string;
  company: string;
  location: string | null;
  country: string | null;
  category: string | null;
  sub_specialism: string | null;
  score: number | null;
  url: string | null;
};

type DetectResult = {
  adapter: string;
  slug: string | null;
  url: string;
  company_guess: string;
  reachable: boolean;
  job_count: number | null;
  error: string | null;
};

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [addUrl, setAddUrl] = useState("");
  const [detectResult, setDetectResult] = useState<DetectResult | null>(null);
  const [companyName, setCompanyName] = useState("");
  const [addState, setAddState] = useState<"idle" | "detecting" | "detected" | "error" | "added">("idle");
  const [addError, setAddError] = useState("");
  const [filters, setFilters] = useState<string[]>([]);      // multi-select AND: category chips
  const [subFilters, setSubFilters] = useState<string[]>([]); // multi-select AND: sub-specialism chips
  const [addedSourceId, setAddedSourceId] = useState<number | null>(null);
  const [expandedSource, setExpandedSource] = useState<number | null>(null);
  const [expandedCategory, setExpandedCategory] = useState<string | null>(null);
  const [sourceJobs, setSourceJobs] = useState<Record<string, ScoredJob[]>>({});
  const [scrapeState, setScrapeState] = useState<"idle" | "scraping" | "done">("idle");
  const [scrapeResult, setScrapeResult] = useState<string>("");
  const [scrapingSourceId, setScrapingSourceId] = useState<number | null>(null);
  const [scrapeSourceResult, setScrapeSourceResult] = useState<Record<number, string>>({});
  const [diagnosing, setDiagnosing] = useState<Set<number>>(new Set());
  const [diagnoseResult, setDiagnoseResult] = useState<Record<number, string>>({});
  const [scrapeQueue, setScrapeQueue] = useState<number[]>([]);
  const [isQueueRunning, setIsQueueRunning] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);
  const [hotlist, setHotlist] = useState<Set<string>>(new Set());
  const [countries, setCountries] = useState<{ country: string; count: number }[]>([]);
  const [countryFilter, setCountryFilter] = useState("");
  const [employmentTypeFilter, setEmploymentTypeFilter] = useState("Permanent");
  const [companySearch, setCompanySearch] = useState("");
  const [adapterFilter, setAdapterFilter] = useState("");
  const [aggregatorFilter, setAggregatorFilter] = useState("");
  // Pagination — render the first PAGE_SIZE cards, then a Load More
  // button reveals the next batch. Resets on any view / filter change
  // so the operator always starts from the top of a fresh list.
  const PAGE_SIZE = 100;
  const [displayLimit, setDisplayLimit] = useState<number>(PAGE_SIZE);

  // Add Company (Coresignal taxonomy sweep) UI state
  const [showAddCompany, setShowAddCompany] = useState(false);
  const [addCompanyName, setAddCompanyName] = useState("");
  const [addCompanyState, setAddCompanyState] = useState<"idle" | "searching" | "confirming" | "scraping" | "done" | "error">("idle");
  type AddCompanyCandidate = {
    employer_name: string;
    jobs_count: number;
    sample_title: string | null;
    sample_location: string | null;
    already_in_db: boolean;
  };
  const [addCompanyResult, setAddCompanyResult] = useState<{
    status: string;
    jobs_found: number;
    company: string;
    source_id: number | null;
    message: string;
    candidates?: AddCompanyCandidate[];
  } | null>(null);
  // Which candidate is currently being scraped (null when idle)
  const [addCompanyConfirmingFor, setAddCompanyConfirmingFor] = useState<string | null>(null);
  const [addCompanyError, setAddCompanyError] = useState("");
  // `highlightSourceId` pins a newly-added card to the top of the Sources list until the user interacts elsewhere.
  const [highlightSourceId, setHighlightSourceId] = useState<number | null>(null);
  const [sourceView, setSourceView] = useState<"leads" | "no_jobs" | "not_relevant" | "broken" | "all">("leads");
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [iframeTitle, setIframeTitle] = useState("");

  useEffect(() => {
    fetch(`${API}/countries`).then((r) => r.json()).then(setCountries).catch(() => {});
    // Load all sources once — country filtering happens client-side on the jobs
    setLoading(true);
    Promise.all([
      fetch(`${API}/sources`).then((r) => r.json()),
      fetch(`${API}/stats`).then((r) => r.json()),
    ]).then(([s, st]) => {
      setSources(s);
      setStats(st);
      setLoading(false);
    }).catch(() => setLoading(false));
  }, []);

  const handleDetect = async () => {
    if (!addUrl.trim()) return;
    setAddState("detecting");
    setDetectResult(null);
    try {
      const res = await fetch(`${API}/sources/detect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: addUrl.trim() }),
      });
      const data: DetectResult = await res.json();
      setDetectResult(data);
      setCompanyName(data.company_guess || "");
      setAddState(data.error && !data.reachable ? "error" : "detected");
      if (data.error && !data.reachable) setAddError(data.error);
    } catch {
      setAddError("Failed to connect to API");
      setAddState("error");
    }
  };

  const [isAdding, setIsAdding] = useState(false);

  // Phase 1: Coresignal count-only search. No DB writes.
  const handleAddCompanySearch = async () => {
    const company = addCompanyName.trim();
    if (!company) return;
    setAddCompanyError("");
    setAddCompanyResult(null);
    setAddCompanyState("searching");
    try {
      const res = await fetch(`${API}/sources/add-company/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company, days_back: 30 }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setAddCompanyError(err.detail || `Server returned ${res.status}`);
        setAddCompanyState("error");
        return;
      }
      const data = await res.json();
      setAddCompanyResult(data);
      // status is one of: "ready" (needs confirm) | "no_jobs" | "exists"
      setAddCompanyState(data.status === "ready" ? "confirming" : "done");
    } catch (e) {
      setAddCompanyError((e as Error).message || "Failed to reach API");
      setAddCompanyState("error");
    }
  };

  // Phase 2: user picked one candidate — create the card and run the capped scrape.
  // `employerExact` is the canonical Coresignal company_name from the candidate row.
  const handleAddCompanyConfirm = async (employerExact: string) => {
    const typed = addCompanyName.trim();
    const exact = employerExact.trim();
    if (!exact) return;
    setAddCompanyError("");
    setAddCompanyConfirmingFor(exact);
    setAddCompanyState("scraping");
    try {
      const res = await fetch(`${API}/sources/add-company/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company: typed, employer_exact: exact, days_back: 30 }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setAddCompanyError(err.detail || `Server returned ${res.status}`);
        setAddCompanyState("error");
        setAddCompanyConfirmingFor(null);
        return;
      }
      const data = await res.json();
      // Keep the candidate list visible but flip the just-added candidate to
      // already_in_db=true so the user can add another match from the same search.
      setAddCompanyResult((prev) => {
        if (!prev) return data;
        const updated = (prev.candidates ?? []).map((c) =>
          c.employer_name === exact ? { ...c, already_in_db: true } : c
        );
        return { ...prev, candidates: updated };
      });
      setAddCompanyState("confirming");
      setAddCompanyConfirmingFor(null);
      // Pin the new card to the top of the Sources list
      if (data.source_id) {
        setHighlightSourceId(data.source_id);
        setSourceView("all");
        setAdapterFilter("");
        setAggregatorFilter("");
      }
      // Refresh sources so the new card appears
      const params = countryFilter ? `?country=${encodeURIComponent(countryFilter)}` : "";
      const [s, st] = await Promise.all([
        fetch(`${API}/sources${params}`).then((r) => r.json()),
        fetch(`${API}/stats${params}`).then((r) => r.json()),
      ]);
      setSources(s);
      setStats(st);
    } catch (e) {
      setAddCompanyError((e as Error).message || "Failed to reach API");
      setAddCompanyState("error");
      setAddCompanyConfirmingFor(null);
    }
  };

  const handleAdd = async () => {
    if (!detectResult || !companyName.trim()) return;
    setIsAdding(true);
    try {
      const res = await fetch(`${API}/sources`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: detectResult.url || addUrl.trim(), company: companyName.trim() }),
      });
      if (res.ok) {
        const data = await res.json();
        setAddedSourceId(data.id);
        setScrapeState("idle");
        setScrapeResult("");
        setAddState("added");
        const [s, st] = await Promise.all([
          fetch(`${API}/sources`).then((r) => r.json()),
          fetch(`${API}/stats`).then((r) => r.json()),
        ]);
        setSources(s);
        setStats(st);
      } else if (res.status === 409) {
        // Already exists — find it and highlight it
        const err = await res.json();
        const [s, st] = await Promise.all([
          fetch(`${API}/sources`).then((r) => r.json()),
          fetch(`${API}/stats`).then((r) => r.json()),
        ]);
        setSources(s);
        setStats(st);
        if (err.id) {
          setAddedSourceId(err.id);
        } else {
          const existing = s.find((src: Source) => src.employer_name.toLowerCase() === companyName.trim().toLowerCase());
          if (existing) setAddedSourceId(existing.id);
        }
        setAddState("added");
      } else {
        const err = await res.json();
        setAddError(err.detail || "Failed to add");
        setAddState("error");
      }
    } catch {
      setAddError("Failed to connect to API");
      setAddState("error");
    } finally {
      setIsAdding(false);
    }
  };

  const handleScrape = async (sourceId?: number) => {
    const id = sourceId ?? addedSourceId;
    if (!id) return;
    setScrapeState("scraping");
    setScrapingSourceId(id);
    try {
      const res = await fetch(`${API}/sources/${id}/scrape`, { method: "POST" });
      const data = await res.json();
      setScrapeState("done");
      setScrapingSourceId(null);
      if (data.status === "ok") {
        setScrapeResult(`${data.jobs_found} jobs found`);
      } else if (data.status === "queued") {
        setScrapeResult("Scrape queued — processing in background");
      } else if (data.removed) {
        setScrapeResult(`${data.status}`);
      } else {
        setScrapeResult(`Failed: ${data.status || "unknown error"}`);
      }
      // Refresh sources (source may have been removed)
      const [s, st] = await Promise.all([
        fetch(`${API}/sources`).then((r) => r.json()),
        fetch(`${API}/stats`).then((r) => r.json()),
      ]);
      setSources(s);
      setStats(st);
      setSourceJobs({}); // Clear cached job lists so they re-fetch after scrape
    } catch {
      setScrapeState("done");
      setScrapingSourceId(null);
      setScrapeResult("Failed to connect to API");
    }
  };

  const queueScrape = (sourceId: number) => {
    setScrapeQueue((prev) => prev.includes(sourceId) ? prev : [...prev, sourceId]);
  };

  const runScrapeQueue = async (queue: number[]) => {
    if (queue.length === 0) return;
    setIsQueueRunning(true);

    for (const sourceId of queue) {
      setScrapingSourceId(sourceId);
      setScrapeSourceResult((prev) => ({ ...prev, [sourceId]: "scraping" }));
      try {
        const res = await fetch(`${API}/sources/${sourceId}/scrape`, { method: "POST" });
        const data = await res.json();
        if (data.status === "ok") {
          setScrapeSourceResult((prev) => ({ ...prev, [sourceId]: `${data.jobs_found} jobs found` }));
        } else if (data.status === "queued") {
          setScrapeSourceResult((prev) => ({ ...prev, [sourceId]: "Queued" }));
        } else {
          setScrapeSourceResult((prev) => ({ ...prev, [sourceId]: data.status }));
        }
      } catch {
        setScrapeSourceResult((prev) => ({ ...prev, [sourceId]: "Failed to connect" }));
      }
      // Remove from queue
      setScrapeQueue((prev) => prev.filter((id) => id !== sourceId));
    }

    setScrapingSourceId(null);
    setIsQueueRunning(false);

    // Refresh once at the end
    const params = countryFilter ? `?country=${encodeURIComponent(countryFilter)}` : "";
    const [s, st] = await Promise.all([
      fetch(`${API}/sources${params}`).then((r) => r.json()),
      fetch(`${API}/stats${params}`).then((r) => r.json()),
    ]);
    setSources(s);
    setStats(st);
    setSourceJobs({}); // Clear cached job lists so they re-fetch after scrape
    // Clear results after 5s
    setTimeout(() => setScrapeSourceResult({}), 5000);
  };

  const handleDiagnose = async (sourceId: number) => {
    setDiagnosing((prev) => new Set(prev).add(sourceId));
    setDiagnoseResult((prev) => ({ ...prev, [sourceId]: "" }));
    try {
      const res = await fetch(`${API}/sources/${sourceId}/diagnose`, { method: "POST" });
      const data = await res.json();
      const parts: string[] = [];
      if (data.issues?.length) parts.push(data.issues.join(". "));
      if (data.actions_taken?.length) parts.push("Fix: " + data.actions_taken.join(", "));
      if (data.detected_adapter && data.detected_adapter !== data.current_adapter) parts.push(`Platform: ${data.current_adapter} → ${data.detected_adapter}`);
      setDiagnoseResult((prev) => ({ ...prev, [sourceId]: parts.join(" | ") || "No issues found" }));
      // Refresh sources after fix
      if (data.actions_taken?.length) {
        const params = countryFilter ? `?country=${encodeURIComponent(countryFilter)}` : "";
        const [s, st] = await Promise.all([
          fetch(`${API}/sources${params}`).then((r) => r.json()),
          fetch(`${API}/stats${params}`).then((r) => r.json()),
        ]);
        setSources(s);
        setStats(st);
        setSourceJobs({});
      }
      setTimeout(() => setDiagnoseResult((prev) => { const n = { ...prev }; delete n[sourceId]; return n; }), 10000);
    } catch {
      setDiagnoseResult((prev) => ({ ...prev, [sourceId]: "Failed to connect" }));
    } finally {
      setDiagnosing((prev) => { const n = new Set(prev); n.delete(sourceId); return n; });
    }
  };

  // Auto-start queue when items are added
  useEffect(() => {
    if (scrapeQueue.length > 0 && !isQueueRunning) {
      runScrapeQueue([...scrapeQueue]);
    }
  }, [scrapeQueue, isQueueRunning]);

  // Reset pagination back to PAGE_SIZE whenever the user changes the
  // view, search, or any filter — so a fresh list always starts from
  // the top instead of inheriting the previous Load More clicks.
  useEffect(() => {
    setDisplayLimit(PAGE_SIZE);
  }, [sourceView, companySearch, countryFilter, employmentTypeFilter, adapterFilter, aggregatorFilter, filters, subFilters]);

  const handleScrapeSource = (sourceId: number) => {
    queueScrape(sourceId);
  };

  const handleDeleteSource = async (sourceId: number) => {
    try {
      await fetch(`${API}/sources/${sourceId}`, { method: "DELETE" });
      setConfirmDeleteId(null);
      // Remove from local state immediately
      setSources((prev) => prev.filter((s) => s.id !== sourceId));
    } catch {
      setConfirmDeleteId(null);
    }
  };

  const handleToggleJobs = async (sourceId: number, category?: string, companyName?: string) => {
    const key = category ? `${sourceId}_${category}` : `${sourceId}`;
    if (expandedSource === sourceId && expandedCategory === (category || null)) {
      setExpandedSource(null);
      setExpandedCategory(null);
      return;
    }
    setExpandedSource(sourceId);
    setExpandedCategory(category || null);
    const jobKey = countryFilter ? `${key}_${countryFilter}` : key;
    if (!sourceJobs[jobKey]) {
      const params = new URLSearchParams();
      if (category) params.set("category", category);
      if (countryFilter) params.set("country", countryFilter);
      // For aggregator cards (negative ID), search by company name
      if (sourceId < 0 && companyName) params.set("company", companyName);
      const qs = params.toString();
      const endpoint = sourceId < 0 ? `${API}/sources/0/jobs` : `${API}/sources/${sourceId}/jobs`;
      const url = `${endpoint}${qs ? `?${qs}` : ""}`;
      try {
        const res = await fetch(url);
        const data = await res.json();
        setSourceJobs((prev) => ({ ...prev, [jobKey]: data }));
      } catch {
        setSourceJobs((prev) => ({ ...prev, [jobKey]: [] }));
      }
    }
  };

  const recentlyAdded = addedSourceId ? sources.find((s) => s.id === addedSourceId) : null;
  const searchLower = companySearch.toLowerCase().trim();

  const isBroken = (s: Source) => s.last_run_status === "FAIL" || s.last_run_status === "error";

  // Get effective categories for a source based on country filter
  const getCats = (s: Source): Record<string, number> => {
    if (!countryFilter) return s.categories || {};
    return (s.categories_by_country || {})[countryFilter] || {};
  };
  const getScored = (s: Source): number => {
    const cats = getCats(s);
    return Object.values(cats).reduce((a, b) => a + b, 0);
  };

  // Effective category count for a source, narrowed by any active sub-specialism
  // filter. When no sub chips are selected this is just the raw category count.
  // When sub chips ARE selected, we sum only the matching sub counts within that
  // category — so the number displayed always agrees with what the AND filter
  // would actually surface.
  const effCatCount = (s: Source, cat: string): number => {
    const base = getCats(s)[cat] || 0;
    if (subFilters.length === 0) return base;
    const subs = (s.sub_specialisms || {})[cat] || {};
    const narrowed = subFilters.reduce((n, sub) => n + (subs[sub] || 0), 0);
    // Never exceed the raw cat count (matters if a country filter is active and
    // narrows the raw cat count while sub counts remain country-agnostic).
    return Math.min(base, narrowed);
  };

  // Effective total leads on a source under the current cat+sub filter.
  // When no chips are selected, this is the full scored total.
  const effScored = (s: Source): number => {
    if (filters.length === 0) return getScored(s);
    return filters.reduce((n, c) => n + effCatCount(s, c), 0);
  };

  const globalScored = (s: Source): number => Object.values(s.categories || {}).reduce((a, b) => a + b, 0);
  const withLeadsCount = sources.filter((s) => getScored(s) > 0).length;
  const noJobsCount = sources.filter((s) => !isBroken(s) && s.jobs === 0).length;
  const notRelevantCount = sources.filter((s) => getScored(s) === 0 && !isBroken(s) && (countryFilter ? globalScored(s) > 0 || s.jobs > 0 : s.jobs > 0)).length;
  const brokenCount = sources.filter((s) => isBroken(s)).length;

  // Compute adapter counts from current view (before adapter filter applied)
  const viewFiltered = sources.filter((s) => {
    const scored = getScored(s);
    const cats = getCats(s);
    if (searchLower) return s.employer_name.toLowerCase().includes(searchLower);
    // No Jobs Found: any source whose direct scrape produced zero raw_jobs.
    // Includes cards that have aggregator-contributed leads (scored > 0) —
    // those are the prime triage candidates: another path proves the employer
    // exists but our direct scraper is silent. The Update button on each card
    // re-runs the direct scrape so the operator can manually verify.
    // Matches the noJobsCount calc above so count and rendered list agree.
    if (sourceView === "no_jobs") return !isBroken(s) && s.jobs === 0;
    if (sourceView === "not_relevant") return scored === 0 && !isBroken(s) && s.jobs > 0;
    if (sourceView === "broken") return isBroken(s);
    if (sourceView === "all") return true;
    if (filters.length === 0) return scored > 0;
    // AND across selected category chips: source must have leads in every selected category
    if (!filters.every((c) => (cats[c] || 0) > 0)) return false;
    // AND across selected sub-specialism chips: each selected sub must exist in at least one of
    // the selected categories on this source.
    if (subFilters.length > 0) {
      const subs = s.sub_specialisms || {};
      return subFilters.every((sub) => filters.some((c) => (subs[c]?.[sub] || 0) > 0));
    }
    return true;
  });
  const adapterCounts: Record<string, number> = {};
  viewFiltered.forEach((s) => { adapterCounts[s.adapter_name] = (adapterCounts[s.adapter_name] || 0) + 1; });
  const sortedAdapters = Object.entries(adapterCounts).sort((a, b) => b[1] - a[1]);

  // Aggregator chip counts — per-aggregator card count + job count across the current view
  const AGGREGATOR_LABELS: Record<string, string> = {
    adzuna: "Adzuna",
    reed: "Reed",
    efinancialcareers: "eFinancialCareers",
    google_jobs: "Google Jobs",
    coresignal: "Coresignal",
  };
  const aggregatorCardCounts: Record<string, number> = {};
  const aggregatorJobCounts: Record<string, number> = {};
  viewFiltered.forEach((s) => {
    if (s.aggregator_hits) {
      Object.entries(s.aggregator_hits).forEach(([agg, n]) => {
        if (n > 0) {
          aggregatorCardCounts[agg] = (aggregatorCardCounts[agg] || 0) + 1;
          aggregatorJobCounts[agg] = (aggregatorJobCounts[agg] || 0) + n;
        }
      });
    }
  });
  const sortedAggregators = Object.entries(aggregatorCardCounts).sort((a, b) => b[1] - a[1]);

  const filtered = viewFiltered
    .filter((s) => !adapterFilter || s.adapter_name === adapterFilter)
    .filter((s) => !aggregatorFilter || (s.aggregator_hits?.[aggregatorFilter] ?? 0) > 0)
    .filter((s) => !employmentTypeFilter || (s.employment_types?.[employmentTypeFilter] ?? 0) > 0);
  // Put recently added source first — `highlightSourceId` (from Add Company) takes
  // priority over `addedSourceId` (from legacy Add Source flow).
  const pinned = highlightSourceId ? sources.find((s) => s.id === highlightSourceId) : null;
  const pinnedOrRecent = pinned ?? recentlyAdded;
  const orderedSources = pinnedOrRecent && !filtered.find((s) => s.id === pinnedOrRecent.id)
    ? [pinnedOrRecent, ...filtered]
    : pinnedOrRecent
      ? [pinnedOrRecent, ...filtered.filter((s) => s.id !== pinnedOrRecent.id)]
      : filtered;

  const categoryColors: Record<string, string> = {
    Risk: "var(--accent-light)",
    Quant: "var(--blue)",
    Compliance: "var(--green)",
    Audit: "var(--amber)",
    Cyber: "var(--red)",
    Legal: "#fd79a8",
    "Front Office": "#ffa500",
  };

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-primary)" }}>
      <Sidebar />

      {/* Main */}
      <main className="ml-60 h-screen flex flex-col overflow-hidden">
        <div className="flex items-center px-8 h-14 shrink-0" style={{ background: "rgba(10,10,15,0.8)", borderBottom: "1px solid var(--border-subtle)" }}>
          <div className="font-bold text-base">Sources</div>
        </div>

        <div className="px-7 pt-5 shrink-0" style={{ background: "var(--bg-primary)" }}>
          {/* Header */}
          <div className="flex justify-between items-center mb-5">
            <div>
              <div className="text-xl font-bold">Sources</div>
              <div className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>{sources.length} companies with active leads</div>
            </div>
            <div className="flex items-center gap-3">
              <input
                type="text"
                value={companySearch}
                onChange={(e) => setCompanySearch(e.target.value)}
                placeholder="Search companies..."
                className="px-3 py-2 rounded-lg text-sm outline-none w-48"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              />
              <select
                value={countryFilter}
                onChange={(e) => { setCountryFilter(e.target.value); setSourceJobs({}); setExpandedSource(null); }}
                className="px-3 py-2 rounded-lg text-sm cursor-pointer outline-none"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                <option value="">All Countries</option>
                {countries.map((c) => (
                  <option key={c.country} value={c.country}>{c.country} ({c.count})</option>
                ))}
              </select>
              <select
                value={employmentTypeFilter}
                onChange={(e) => { setEmploymentTypeFilter(e.target.value); setSourceJobs({}); setExpandedSource(null); }}
                className="px-3 py-2 rounded-lg text-sm cursor-pointer outline-none"
                style={{ background: "var(--bg-card)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
              >
                <option value="Permanent">Permanent</option>
                <option value="Contract">Contract</option>
              </select>
              {/* Add Source temporarily disconnected — keep entire flow intact for reinstatement. */}
              {false && (
                <button onClick={() => { setShowAdd(!showAdd); setAddState("idle"); setDetectResult(null); setAddUrl(""); }} className="px-4 py-2 rounded-lg text-sm font-semibold text-white cursor-pointer" style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)", boxShadow: "0 2px 12px rgba(108,92,231,0.3)" }}>+ Add Source</button>
              )}
              {/* Add Company — Coresignal-backed taxonomy sweep */}
              <button
                onClick={() => { setShowAddCompany(!showAddCompany); setAddCompanyState("idle"); setAddCompanyName(""); setAddCompanyResult(null); }}
                className="px-4 py-2 rounded-lg text-sm font-semibold text-white cursor-pointer"
                style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)", boxShadow: "0 2px 12px rgba(108,92,231,0.3)" }}
              >
                + Add Company
              </button>
            </div>
          </div>

          {/* Add Company Panel (Coresignal taxonomy sweep) */}
          {showAddCompany && (
            <div className="mb-5" style={{ animation: "fadeIn 0.3s ease-out" }}>
              <div className="relative p-6 rounded-xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                <button onClick={() => { setShowAddCompany(false); setAddCompanyState("idle"); setAddCompanyResult(null); setAddCompanyError(""); setAddCompanyName(""); }} className="absolute top-3 right-4 text-lg cursor-pointer" style={{ color: "var(--text-muted)" }}>&times;</button>
                <div className="font-bold text-[15px] mb-1">Add a Company</div>
                <div className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
                  Search Coresignal for this company&apos;s jobs across the full taxonomy (last 30 days). If jobs are found, a card will be created.
                </div>

                <div className="flex gap-2.5 mb-3">
                  <input
                    type="text"
                    value={addCompanyName}
                    onChange={(e) => { setAddCompanyName(e.target.value); if (addCompanyState === "confirming" || addCompanyState === "done" || addCompanyState === "error") { setAddCompanyState("idle"); setAddCompanyResult(null); setAddCompanyError(""); } }}
                    onPaste={(e) => { const text = e.clipboardData.getData("text"); if (text) { e.preventDefault(); setAddCompanyName(text.trim()); } }}
                    onKeyDown={(e) => e.key === "Enter" && (addCompanyState === "idle" || addCompanyState === "done" || addCompanyState === "error") && handleAddCompanySearch()}
                    placeholder="e.g. Goldman Sachs"
                    disabled={addCompanyState === "searching" || addCompanyState === "scraping"}
                    className="flex-1 px-4 py-2.5 rounded-lg text-sm outline-none"
                    style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
                  />
                  <button
                    onClick={handleAddCompanySearch}
                    disabled={addCompanyState === "searching" || addCompanyState === "scraping" || addCompanyState === "confirming" || !addCompanyName.trim()}
                    className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white cursor-pointer whitespace-nowrap flex items-center gap-2"
                    style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)", opacity: (addCompanyState === "searching" || addCompanyState === "scraping" || addCompanyState === "confirming" || !addCompanyName.trim()) ? 0.7 : 1 }}
                  >
                    {addCompanyState === "searching" && <span className="inline-block w-3.5 h-3.5 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "white", animation: "spin 0.8s linear infinite" }} />}
                    {addCompanyState === "searching" ? "Searching..." : "Search"}
                  </button>
                </div>

                {/* Error banner */}
                {addCompanyState === "error" && (
                  <div className="p-3 rounded-lg text-sm" style={{ background: "var(--bg-primary)", border: "1px solid var(--red-border)", color: "var(--red)" }}>
                    Error: {addCompanyError}
                  </div>
                )}

                {/* Phase 1 result — `no_jobs` | `exists` (both terminal, no confirm) */}
                {addCompanyState === "done" && addCompanyResult && (addCompanyResult.status === "no_jobs" || addCompanyResult.status === "exists") && (
                  <div className="p-3 rounded-lg text-sm" style={{
                    background: "var(--bg-primary)",
                    border: `1px solid ${addCompanyResult.status === "no_jobs" ? "var(--amber-border)" : "var(--border)"}`,
                    color: addCompanyResult.status === "no_jobs" ? "var(--amber)" : "var(--text-secondary)",
                  }}>
                    <div className="font-semibold">
                      {addCompanyResult.status === "no_jobs" && "No jobs found"}
                      {addCompanyResult.status === "exists" && `Already exists${addCompanyResult.source_id ? ` (id=${addCompanyResult.source_id})` : ""}`}
                    </div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{addCompanyResult.message}</div>
                  </div>
                )}

                {/* Phase 1 result — `ready`: list all matching employers, one Add button each */}
                {(addCompanyState === "confirming" || addCompanyState === "scraping") && addCompanyResult && addCompanyResult.candidates && addCompanyResult.candidates.length > 0 && (
                  <div className="p-4 rounded-lg" style={{ background: "var(--bg-primary)", border: "1px solid var(--accent)" }}>
                    <div className="font-semibold text-[14px] mb-1" style={{ color: "var(--accent-light)" }}>
                      Found {addCompanyResult.jobs_found} jobs across {addCompanyResult.candidates.length} employer{addCompanyResult.candidates.length === 1 ? "" : "s"}
                    </div>
                    <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
                      Pick which one to add. Only the exact employer name you click will be scraped.
                    </div>
                    <div className="flex flex-col gap-1.5 max-h-80 overflow-y-auto">
                      {addCompanyResult.candidates.map((cand) => {
                        const isBusy = addCompanyConfirmingFor === cand.employer_name;
                        const anyBusy = !!addCompanyConfirmingFor;
                        const disabled = cand.already_in_db || anyBusy;
                        return (
                          <div
                            key={cand.employer_name}
                            className="flex items-center gap-3 px-3 py-2 rounded-md"
                            style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}
                          >
                            <div className="flex-1 min-w-0">
                              <div className="text-sm font-semibold truncate" style={{ color: "var(--text-primary)" }}>
                                {cand.employer_name}
                                {cand.already_in_db && (
                                  <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)" }}>already in DB</span>
                                )}
                              </div>
                              {(cand.sample_title || cand.sample_location) && (
                                <div className="text-[11px] truncate" style={{ color: "var(--text-muted)" }}>
                                  {cand.sample_title}
                                  {cand.sample_title && cand.sample_location ? " — " : ""}
                                  {cand.sample_location}
                                </div>
                              )}
                            </div>
                            <span className="text-xs font-semibold whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                              {cand.jobs_count} job{cand.jobs_count === 1 ? "" : "s"}
                            </span>
                            <button
                              onClick={() => handleAddCompanyConfirm(cand.employer_name)}
                              disabled={disabled}
                              className="px-3 py-1 rounded-md text-xs font-semibold text-white cursor-pointer whitespace-nowrap flex items-center gap-1.5"
                              style={{
                                background: disabled ? "var(--bg-elevated)" : "linear-gradient(135deg, var(--accent), #8b7cf7)",
                                opacity: disabled ? 0.5 : 1,
                                color: disabled ? "var(--text-muted)" : "white",
                              }}
                            >
                              {isBusy && <span className="inline-block w-3 h-3 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "white", animation: "spin 0.8s linear infinite" }} />}
                              {isBusy ? "Adding…" : cand.already_in_db ? "Added" : "Add"}
                            </button>
                          </div>
                        );
                      })}
                    </div>
                    <div className="flex mt-3">
                      <button
                        onClick={() => { setAddCompanyState("idle"); setAddCompanyResult(null); setAddCompanyConfirmingFor(null); }}
                        disabled={!!addCompanyConfirmingFor}
                        className="px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer"
                        style={{ background: "transparent", color: "var(--text-secondary)", border: "1px solid var(--border)", opacity: addCompanyConfirmingFor ? 0.5 : 1 }}
                      >
                        Close
                      </button>
                    </div>
                  </div>
                )}

                {/* Phase 2 feedback — the candidates list handles per-row "Added" state inline,
                    so no extra success banner needed. This block is kept for safety if the response
                    arrives with `status="ok"` outside the normal flow. */}
                {addCompanyState === "done" && addCompanyResult && addCompanyResult.status === "ok" && (
                  <div className="p-3 rounded-lg text-sm" style={{ background: "var(--bg-primary)", border: "1px solid var(--green-border)", color: "var(--green)" }}>
                    <div className="font-semibold">Card created for {addCompanyResult.company}</div>
                    <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{addCompanyResult.message}</div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Add Source Panel */}
          {showAdd && (
            <div className="mb-5" style={{ animation: "fadeIn 0.3s ease-out" }}>
              <div className="relative p-6 rounded-xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                <button onClick={() => setShowAdd(false)} className="absolute top-3 right-4 text-lg cursor-pointer" style={{ color: "var(--text-muted)" }}>&times;</button>
                <div className="font-bold text-[15px] mb-1">Add a New Source</div>
                <div className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>Paste any careers page URL — we&apos;ll auto-detect the platform and validate it</div>

                <div className="flex gap-2.5 mb-4">
                  <input type="text" value={addUrl} onChange={(e) => setAddUrl(e.target.value)} onPaste={(e) => { const text = e.clipboardData.getData("text"); if (text) { e.preventDefault(); setAddUrl(text.trim()); } }} onKeyDown={(e) => e.key === "Enter" && handleDetect()} placeholder="https://boards.greenhouse.io/robinhood" className="flex-1 px-4 py-2.5 rounded-lg text-sm outline-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                  <button onClick={handleDetect} disabled={addState === "detecting"} className="px-5 py-2.5 rounded-lg text-sm font-semibold text-white cursor-pointer whitespace-nowrap" style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)" }}>
                    {addState === "detecting" ? "Detecting..." : "Detect & Validate"}
                  </button>
                </div>

                {addState === "detecting" && (
                  <div className="py-5 text-center text-sm" style={{ color: "var(--text-secondary)" }}>
                    <span className="inline-block w-4 h-4 rounded-full mr-2 align-middle" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
                    Analysing URL...
                  </div>
                )}

                {addState === "detected" && detectResult && (
                  <div className="p-4 rounded-lg" style={{ background: "var(--bg-primary)", border: "1px solid var(--green-border)" }}>
                    <div className="flex items-start gap-4">
                      <div className="w-10 h-10 rounded-lg flex items-center justify-center text-lg shrink-0" style={{ background: "var(--green-bg)", border: "1px solid var(--green-border)" }}>&#10003;</div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2 mb-1.5">
                          <span className="font-bold text-[15px]">{detectResult.company_guess || "Unknown"}</span>
                          <span className="text-[10px] font-semibold px-2 py-0.5 rounded uppercase tracking-wider" style={{ background: "var(--accent-glow)", color: "var(--accent-light)", border: "1px solid rgba(108,92,231,0.2)" }}>{detectResult.adapter}</span>
                        </div>
                        <div className="text-xs mb-3" style={{ color: "var(--text-muted)" }}>
                          {detectResult.job_count !== null ? `${detectResult.job_count} jobs found · Board is active` : "URL is reachable · Will use browser scraper"}
                        </div>
                        <div className="grid grid-cols-2 gap-2.5">
                          <div>
                            <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>Company Name</div>
                            <input type="text" value={companyName} onChange={(e) => setCompanyName(e.target.value)} className="w-full px-2.5 py-1.5 rounded text-sm outline-none" style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", color: "var(--text-primary)" }} />
                          </div>
                          <div>
                            <div className="text-[10px] uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>Platform</div>
                            <input type="text" value={detectResult.adapter} disabled className="w-full px-2.5 py-1.5 rounded text-sm" style={{ background: "var(--bg-secondary)", border: "1px solid var(--border-subtle)", color: "var(--text-muted)" }} />
                          </div>
                        </div>
                        <div className="flex gap-2 mt-3.5">
                          <button onClick={handleAdd} disabled={isAdding} className="px-4 py-2 rounded-lg text-sm font-semibold text-white cursor-pointer flex items-center gap-2" style={{ background: isAdding ? "var(--bg-elevated)" : "linear-gradient(135deg, var(--accent), #8b7cf7)", opacity: isAdding ? 0.7 : 1 }}>
                            {isAdding && <span className="inline-block w-3.5 h-3.5 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />}
                            {isAdding ? "Adding..." : "Add to Sources"}
                          </button>
                          <button onClick={() => setShowAdd(false)} className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer" style={{ background: "transparent", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {addState === "error" && (
                  <div className="p-4 rounded-lg" style={{ background: "var(--bg-primary)", border: "1px solid var(--red-border)" }}>
                    <div className="font-semibold text-sm" style={{ color: "var(--red)" }}>{addError}</div>
                    <button onClick={() => setAddState("idle")} className="mt-2 px-3 py-1.5 rounded text-xs font-semibold cursor-pointer" style={{ background: "transparent", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Try Again</button>
                  </div>
                )}

                {addState === "added" && (
                  <div className="p-5 rounded-lg text-center" style={{ background: "var(--bg-primary)", border: "1px solid var(--green-border)" }}>
                    <div className="text-2xl mb-2">&#10003;</div>
                    <div className="font-bold text-[15px]" style={{ color: "var(--green)" }}>Source Added</div>
                    <div className="text-xs mt-1 mb-3" style={{ color: "var(--text-muted)" }}>{companyName} will be included in the next pipeline run</div>
                    <div className="flex gap-2 justify-center">
                      {scrapeState === "idle" && (
                        <button onClick={() => handleScrape()} className="px-4 py-2 rounded-lg text-sm font-semibold text-white cursor-pointer" style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)" }}>
                          Scrape Now
                        </button>
                      )}
                      {scrapeState === "scraping" && (
                        <div className="flex items-center gap-2 text-sm" style={{ color: "var(--accent-light)" }}>
                          <span className="inline-block w-4 h-4 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
                          Scraping {companyName}...
                        </div>
                      )}
                      {scrapeState === "done" && (
                        <div className="text-sm font-semibold" style={{ color: scrapeResult.startsWith("Failed") ? "var(--red)" : "var(--green)" }}>
                          {scrapeResult}
                        </div>
                      )}
                      <button onClick={() => { setShowAdd(false); setAddState("idle"); setAddUrl(""); setDetectResult(null); setScrapeState("idle"); }} className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer" style={{ background: "transparent", color: "var(--text-secondary)", border: "1px solid var(--border)" }}>
                        Close
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Stats */}
          {stats && (
            <div className="mb-5">
              <div className="grid grid-cols-6 gap-2 mb-4">
                {[
                  { key: "leads" as const, label: "With Leads", count: withLeadsCount, color: "var(--green)" },
                  { key: "no_jobs" as const, label: "No Jobs Found", count: noJobsCount, color: "var(--amber)" },
                  { key: "not_relevant" as const, label: "Not Relevant", count: notRelevantCount, color: "var(--text-secondary)" },
                  { key: "broken" as const, label: "Broken", count: brokenCount, color: "var(--red)" },
                  { key: "all" as const, label: "All Sources", count: withLeadsCount + noJobsCount + notRelevantCount + brokenCount, color: "var(--text-primary)" },
                ].map((v) => (
                  <div
                    key={v.key}
                    className="px-3 py-3 rounded-lg cursor-pointer"
                    onClick={() => { setSourceView(v.key); setAddedSourceId(null); setAdapterFilter(""); setAggregatorFilter(""); setHighlightSourceId(null); }}
                    style={{ background: sourceView === v.key ? "var(--accent-glow)" : "var(--bg-card)", border: `1px solid ${sourceView === v.key ? "rgba(108,92,231,0.3)" : "var(--border-subtle)"}` }}
                  >
                    <div className="text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>{v.label}</div>
                    <div className="text-xl font-extrabold tracking-tight" style={{ color: v.color }}>{v.count}</div>
                  </div>
                ))}
                <div className="px-3 py-3 rounded-lg" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
                  <div className="text-[10px] font-medium uppercase tracking-wider mb-1" style={{ color: "var(--text-muted)" }}>Qualified Leads</div>
                  <div className="text-xl font-extrabold tracking-tight" style={{ color: "var(--accent-light)" }}>{sources.reduce((sum, s) => sum + effScored(s), 0).toLocaleString()}</div>
                </div>
              </div>
              <div className="grid grid-cols-7 gap-2">
                {["Risk", "Quant", "Compliance", "Audit", "Cyber", "Legal", "Front Office"].map((cat) => {
                  const isSelected = filters.includes(cat);
                  return (
                    <div
                      key={cat}
                      className="p-3 rounded-lg text-center cursor-pointer"
                      onClick={() => {
                        // Multi-select AND toggle. Clearing sub-filters on any category change
                        // avoids stale sub chips that no longer belong to the selected set.
                        setFilters((prev) => prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]);
                        setSubFilters([]);
                        setAddedSourceId(null);
                      }}
                      style={{
                        background: isSelected ? "var(--accent-glow)" : "var(--bg-card)",
                        border: `1px solid ${isSelected ? "rgba(108,92,231,0.3)" : "var(--border-subtle)"}`,
                      }}
                    >
                      <div className="text-xl font-bold" style={{ color: categoryColors[cat] || "var(--text-primary)" }}>
                        {sources.reduce((sum, s) => sum + effCatCount(s, cat), 0).toLocaleString()}
                      </div>
                      <div className="text-[10px] font-medium uppercase tracking-wider mt-1" style={{ color: "var(--text-muted)" }}>{cat}</div>
                    </div>
                  );
                })}
              </div>
              {/* Sub-specialism chips: shown when at least one category chip is selected. Flat
                  mixed row, each chip coloured by its parent category. Multi-select AND. */}
              {filters.length > 0 && (() => {
                const options: { sub: string; cat: string; count: number }[] = [];
                const seen = new Set<string>();
                const poolSources = sources.filter((s) => filters.every((c) => (getCats(s)[c] || 0) > 0));
                for (const s of poolSources) {
                  const subs = s.sub_specialisms || {};
                  for (const cat of filters) {
                    const bucket = subs[cat];
                    if (!bucket) continue;
                    for (const [sub, count] of Object.entries(bucket)) {
                      const key = `${cat}::${sub}`;
                      if (seen.has(key)) {
                        const existing = options.find((o) => o.cat === cat && o.sub === sub);
                        if (existing) existing.count += count as number;
                      } else {
                        seen.add(key);
                        options.push({ sub, cat, count: count as number });
                      }
                    }
                  }
                }
                if (options.length === 0) return null;
                const sorted = options.sort((a, b) => b.count - a.count);
                return (
                  <div className="flex flex-wrap gap-1.5 mt-3">
                    {sorted.map(({ sub, cat, count }) => {
                      const isSel = subFilters.includes(sub);
                      const color = categoryColors[cat] || "var(--text-primary)";
                      return (
                        <div
                          key={`${cat}::${sub}`}
                          className="text-[11px] px-2 py-1 rounded-md cursor-pointer"
                          onClick={() =>
                            setSubFilters((prev) => prev.includes(sub) ? prev.filter((x) => x !== sub) : [...prev, sub])
                          }
                          title={`${cat} · ${sub}`}
                          style={{
                            background: isSel ? "var(--accent-glow)" : "var(--bg-elevated)",
                            border: `1px solid ${isSel ? color : "var(--border-subtle)"}`,
                            color,
                          }}
                        >
                          {sub} <span style={{ opacity: 0.6 }}>{count}</span>
                        </div>
                      );
                    })}
                  </div>
                );
              })()}
              {/* Adapter filter chips */}
              {sortedAdapters.length > 1 && (
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {sortedAdapters.map(([adapter, count]) => (
                    <button
                      key={adapter}
                      onClick={() => setAdapterFilter(adapterFilter === adapter ? "" : adapter)}
                      className="px-2.5 py-1 rounded-md text-[10px] font-semibold cursor-pointer"
                      style={{
                        background: adapterFilter === adapter ? "var(--accent-glow)" : "var(--bg-elevated)",
                        border: `1px solid ${adapterFilter === adapter ? "rgba(108,92,231,0.3)" : "var(--border-subtle)"}`,
                        color: adapterFilter === adapter ? "var(--accent-light)" : "var(--text-muted)",
                      }}
                    >
                      {adapter} ({count})
                    </button>
                  ))}
                </div>
              )}
              {/* Aggregator filter chips (audit which cards were contributed by each aggregator) */}
              {sortedAggregators.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2 items-center">
                  <span className="text-[10px] font-semibold uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>
                    Aggregators:
                  </span>
                  {sortedAggregators.map(([agg, cardCount]) => (
                    <button
                      key={agg}
                      onClick={() => setAggregatorFilter(aggregatorFilter === agg ? "" : agg)}
                      className="px-2.5 py-1 rounded-md text-[10px] font-semibold cursor-pointer"
                      style={{
                        background: aggregatorFilter === agg ? "var(--accent-glow)" : "var(--bg-elevated)",
                        border: `1px solid ${aggregatorFilter === agg ? "rgba(108,92,231,0.3)" : "var(--border-subtle)"}`,
                        color: aggregatorFilter === agg ? "var(--accent-light)" : "var(--text-secondary)",
                      }}
                      title={`${aggregatorJobCounts[agg] || 0} jobs across ${cardCount} cards`}
                    >
                      {AGGREGATOR_LABELS[agg] || agg} · {cardCount} cards · {aggregatorJobCounts[agg] || 0} jobs
                    </button>
                  ))}
                  {aggregatorFilter && (
                    <button
                      onClick={() => setAggregatorFilter("")}
                      className="px-2 py-1 rounded-md text-[10px] cursor-pointer"
                      style={{ background: "transparent", color: "var(--text-muted)" }}
                    >
                      clear ×
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Scrape queue status */}
          {(isQueueRunning || scrapeQueue.length > 0) && (
            <div className="flex items-center gap-3 mb-4 px-4 py-2.5 rounded-lg" style={{ background: "var(--accent-glow)", border: "1px solid rgba(108,92,231,0.2)" }}>
              <span className="inline-block w-3 h-3 rounded-full shrink-0" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
              <span className="text-xs font-semibold" style={{ color: "var(--accent-light)" }}>
                Scraping {scrapingSourceId ? sources.find(s => s.id === scrapingSourceId)?.employer_name || "" : ""}
                {scrapeQueue.length > 0 && ` — ${scrapeQueue.length} more in queue`}
              </span>
            </div>
          )}

          {/* Filter label */}
          <div className="flex items-center gap-2 mb-4">
            <div className="text-sm font-medium" style={{ color: "var(--text-muted)" }}>
              {filters.length > 0
                ? `Showing sources with ${filters.join(" AND ")}${subFilters.length > 0 ? ` · ${subFilters.join(" AND ")}` : ""} leads`
                : "All sources"}
            </div>
            {(filters.length > 0 || subFilters.length > 0) && (
              <button onClick={() => { setFilters([]); setSubFilters([]); }} className="text-xs px-2 py-0.5 rounded cursor-pointer" style={{ background: "var(--bg-elevated)", color: "var(--accent-light)", border: "1px solid var(--border)" }}>
                Clear filter
              </button>
            )}
          </div>

        </div>

        <div className="flex-1 overflow-y-auto px-7 pb-7">
          {/* Source cards */}
          {loading ? (
            <div className="text-center py-20 text-sm" style={{ color: "var(--text-muted)" }}>Loading sources...</div>
          ) : (
            <div className="grid grid-cols-3 gap-3">
              {orderedSources.slice(0, displayLimit).map((src) => (
                <div key={src.id} className="rounded-xl" style={{ background: "var(--bg-card)", border: expandedSource === src.id ? "1px solid var(--accent)" : src.id === addedSourceId ? "1px solid var(--green)" : (src.last_run_status === "FAIL" || src.last_run_status === "error") ? "1px solid var(--red)" : "1px solid var(--border-subtle)" }}>
                  <div className="p-4">
                    <div className="flex justify-between items-start mb-3">
                      <div className="font-semibold text-sm">{src.employer_name}</div>
                      {scrapingSourceId === src.id ? (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded" style={{ background: "var(--accent-glow)", color: "var(--accent-light)" }}>
                          <span className="inline-block w-2.5 h-2.5 rounded-full mr-1 align-middle" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
                          Scraping...
                        </span>
                      ) : scrapeQueue.includes(src.id) ? (
                        <span className="text-[10px] font-semibold px-2 py-0.5 rounded" style={{ background: "var(--amber-bg)", color: "var(--amber)" }}>
                          Queued ({scrapeQueue.indexOf(src.id) + 1})
                        </span>
                      ) : confirmDeleteId === src.id ? (
                        <div className="flex items-center gap-1">
                          <span className="text-[10px]" style={{ color: "var(--red)" }}>Remove?</span>
                          <button
                            onClick={(e) => { e.stopPropagation(); handleDeleteSource(src.id); }}
                            className="text-[10px] font-semibold px-1.5 py-0.5 rounded cursor-pointer"
                            style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red)" }}
                          >
                            Yes
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(null); }}
                            className="text-[10px] font-medium px-1.5 py-0.5 rounded cursor-pointer"
                            style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", border: "1px solid var(--border)" }}
                          >
                            No
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-1">
                          {getScored(src) === 0 && (
                            diagnosing.has(src.id) ? (
                              <span className="text-[10px] font-semibold px-2 py-0.5 rounded" style={{ background: "var(--amber-bg)", color: "var(--amber)" }}>
                                <span className="inline-block w-2 h-2 rounded-full mr-1 align-middle" style={{ border: "2px solid var(--border)", borderTopColor: "var(--amber)", animation: "spin 0.8s linear infinite" }} />
                                Diagnosing...
                              </span>
                            ) : diagnoseResult[src.id] ? (
                              <span className="text-[10px] px-2 py-0.5 rounded" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: "inline-block" }} title={diagnoseResult[src.id]}>
                                {diagnoseResult[src.id]}
                              </span>
                            ) : (
                              <button
                                onClick={(e) => { e.stopPropagation(); handleDiagnose(src.id); }}
                                className="text-[10px] font-medium px-2 py-0.5 rounded cursor-pointer"
                                style={{ background: "rgba(255,107,107,0.08)", color: "var(--red)", border: "1px solid rgba(255,107,107,0.2)" }}
                                title="Diagnose why this source has no leads"
                              >
                                &#9881; Diagnose
                              </button>
                            )
                          )}
                          <button
                            onClick={(e) => { e.stopPropagation(); handleScrapeSource(src.id); }}
                            className="text-[10px] font-medium px-2 py-0.5 rounded cursor-pointer"
                            style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", border: "1px solid var(--border)" }}
                            title="Re-scrape this board for new jobs"
                          >
                            &#8635; Update
                          </button>
                          <button
                            onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(src.id); }}
                            className="text-[10px] font-medium px-1.5 py-0.5 rounded cursor-pointer"
                            style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", border: "1px solid var(--border)" }}
                            title="Remove this source"
                          >
                            &times;
                          </button>
                        </div>
                      )}
                    </div>
                    <div className="mb-2">
                      <span className="text-[9px] font-medium px-1.5 py-0.5 rounded uppercase mr-1.5" style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>{src.adapter_name}</span>
                      <a href={src.base_url} target="_blank" rel="noreferrer" className="text-[9px] hover:underline" style={{ color: "var(--text-muted)" }}>{src.base_url.length > 55 ? src.base_url.slice(0, 55) + "..." : src.base_url}</a>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {getCats(src) && Object.entries(getCats(src))
                        .filter(([cat]) => filters.length === 0 || filters.includes(cat))
                        // Narrow count to active sub-specialism chips (if any) before sort/render so
                        // the count shown on the card agrees with what the chip filter surfaces.
                        .map(([cat]) => [cat, effCatCount(src, cat)] as [string, number])
                        // Hide categories that have zero effective leads under the current sub filter.
                        .filter(([, count]) => count > 0 || subFilters.length === 0)
                        .sort(([,a],[,b]) => b - a)
                        .map(([cat, count]) => (
                        <div
                          key={cat}
                          className="cursor-pointer px-2 py-1 rounded"
                          onClick={() => handleToggleJobs(src.id, cat, src.employer_name)}
                          title={`Click to view ${cat} jobs`}
                          style={{
                            background: expandedSource === src.id && expandedCategory === cat ? "var(--accent-glow)" : "transparent",
                            border: expandedSource === src.id && expandedCategory === cat ? "1px solid rgba(108,92,231,0.3)" : "1px solid transparent",
                          }}
                        >
                          <div className="text-sm font-bold" style={{ color: categoryColors[cat] || "var(--text-primary)" }}>{count}</div>
                          <div className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>{cat}</div>
                        </div>
                      ))}
                      {(src.last_run_status === "FAIL" || src.last_run_status === "error") && getScored(src) === 0 && (
                        <div className="flex-1">
                          <div className="text-xs font-semibold mb-1" style={{ color: "var(--red)" }}>Failed</div>
                          <div className="text-[10px] truncate" style={{ color: "var(--text-muted)" }} title={src.last_run_error || ""}>{src.last_run_error?.slice(0, 60) || "Unknown error"}</div>
                        </div>
                      )}
                      {(!getCats(src) || Object.keys(getCats(src)).length === 0) && getScored(src) > 0 && (
                        <div className="cursor-pointer" onClick={() => handleToggleJobs(src.id, undefined, src.employer_name)}>
                          <div className="text-sm font-bold" style={{ color: "var(--accent-light)" }}>{getScored(src)}</div>
                          <div className="text-[9px] uppercase tracking-wider" style={{ color: "var(--text-muted)" }}>Leads</div>
                        </div>
                      )}
                      {(!getCats(src) || Object.keys(getCats(src)).length === 0) && getScored(src) === 0 && !(src.last_run_status === "FAIL" || src.last_run_status === "error") && (
                        <div className="flex items-center gap-2">
                          {scrapingSourceId === src.id ? (
                            <div className="flex items-center gap-2 text-xs" style={{ color: "var(--accent-light)" }}>
                              <span className="inline-block w-3 h-3 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
                              Scraping...
                            </div>
                          ) : scrapeSourceResult[src.id] ? (
                            <div className="text-xs font-semibold" style={{ color: scrapeSourceResult[src.id].includes("found") ? "var(--green)" : "var(--text-muted)" }}>
                              {scrapeSourceResult[src.id]}
                            </div>
                          ) : src.jobs > 0 ? (
                            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                              {src.jobs} jobs found, none in your categories
                            </div>
                          ) : src.last_run_status === "success" ? (
                            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                              Scraped — 0 jobs found
                            </div>
                          ) : (
                            <button
                              onClick={() => handleScrapeSource(src.id)}
                              className="px-3 py-1 rounded text-xs font-semibold cursor-pointer"
                              style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)", color: "white" }}
                            >
                              Scrape Now
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                  {expandedSource === src.id && (() => {
                    const baseKey = expandedCategory ? `${src.id}_${expandedCategory}` : `${src.id}`;
                    const jobKey = countryFilter ? `${baseKey}_${countryFilter}` : baseKey;
                    return (
                    <div style={{ borderTop: "1px solid var(--border-subtle)", animation: "fadeIn 0.2s ease-out" }}>
                      {expandedCategory && <div className="px-4 pt-2 text-xs font-semibold" style={{ color: categoryColors[expandedCategory] || "var(--accent-light)" }}>{expandedCategory} leads</div>}
                      {sourceJobs[jobKey] === undefined ? (
                        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>Loading...</div>
                      ) : sourceJobs[jobKey].length === 0 ? (
                        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>No jobs found</div>
                      ) : (
                        <div className="max-h-64 overflow-y-auto">
                          {sourceJobs[jobKey].map((job, i) => (
                            <div
                              key={i}
                              className="px-4 py-2 flex items-center gap-3"
                              style={{ borderBottom: i < sourceJobs[jobKey].length - 1 ? "1px solid var(--border-subtle)" : "none" }}
                            >
                              <div className="flex-1 min-w-0">
                                <div className="text-xs font-semibold truncate">
                                  {job.url ? (
                                    <a href={job.url} target="_blank" rel="noreferrer" style={{ color: "var(--text-primary)" }} className="hover:underline">{job.title}</a>
                                  ) : job.title}
                                </div>
                                <div className="text-[10px] truncate" style={{ color: "var(--text-muted)" }}>
                                  {[job.location, job.country].filter(Boolean).join(", ")}
                                </div>
                              </div>
                              {job.category && (
                                <span className="text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0" style={{ background: "var(--accent-glow)", color: "var(--accent-light)" }}>
                                  {job.sub_specialism || job.category}
                                </span>
                              )}
                              {job.score !== null && (
                                <span className="text-xs font-bold shrink-0" style={{ color: job.score >= 8 ? "var(--green)" : job.score >= 6 ? "var(--amber)" : "var(--text-muted)", fontFamily: "'JetBrains Mono', monospace" }}>
                                  {job.score.toFixed(1)}
                                </span>
                              )}
                              <button
                                onClick={async (e) => {
                                  e.stopPropagation();
                                  const key = job.url || `${job.title}-${job.company}`;
                                  if (!hotlist.has(key)) {
                                    await fetch(`${API}/queue`, {
                                      method: "POST",
                                      headers: { "Content-Type": "application/json" },
                                      body: JSON.stringify({
                                        title: job.title, company: job.company,
                                        location: job.location, country: job.country,
                                        category: job.category, sub_specialism: job.sub_specialism,
                                        url: job.url, score: job.score,
                                      }),
                                    });
                                  }
                                  setHotlist((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(key)) next.delete(key); else next.add(key);
                                    return next;
                                  });
                                }}
                                className="text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0 cursor-pointer"
                                style={hotlist.has(job.url || `${job.title}-${job.company}`)
                                  ? { background: "rgba(0,210,160,0.08)", color: "var(--green)", border: "1px solid rgba(0,210,160,0.2)" }
                                  : { background: "var(--bg-elevated)", color: "var(--text-muted)", border: "1px solid var(--border)" }
                                }
                                title={hotlist.has(job.url || `${job.title}-${job.company}`) ? "Added to Lead List" : "Add to Lead List"}
                              >
                                {hotlist.has(job.url || `${job.title}-${job.company}`) ? "★ Queued" : "☆ Hotlist"}
                              </button>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                    );
                  })()}
                </div>
              ))}
            </div>
          )}
          {/* Load more — only renders when there are still cards beyond
              the current displayLimit. Each click reveals another PAGE_SIZE
              batch. The 'X of Y shown' text gives the operator a sense of
              how many remain. */}
          {!loading && orderedSources.length > displayLimit && (
            <div className="flex flex-col items-center gap-2 mt-6 mb-2">
              <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                Showing {Math.min(displayLimit, orderedSources.length).toLocaleString()} of {orderedSources.length.toLocaleString()} sources
              </div>
              <button
                onClick={() => setDisplayLimit((n) => n + PAGE_SIZE)}
                className="px-5 py-2 rounded-lg text-sm font-semibold cursor-pointer"
                style={{ background: "var(--accent-glow)", color: "var(--accent-light)", border: "1px solid rgba(108,92,231,0.3)" }}
              >
                Load {Math.min(PAGE_SIZE, orderedSources.length - displayLimit).toLocaleString()} more
              </button>
              {orderedSources.length - displayLimit > PAGE_SIZE && (
                <button
                  onClick={() => setDisplayLimit(orderedSources.length)}
                  className="text-[11px] cursor-pointer underline"
                  style={{ color: "var(--text-muted)" }}
                >
                  Load all {(orderedSources.length - displayLimit).toLocaleString()} remaining
                </button>
              )}
            </div>
          )}
        </div>
      </main>

    </div>
  );
}
