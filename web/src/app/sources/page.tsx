"use client";

import { useEffect, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import Sidebar from "../components/Sidebar";
import AddCompanyModal from "./components/AddCompanyModal";
import SourceCard from "./components/SourceCard";
import SourceFilters from "./components/SourceFilters";
import StatsSection from "./components/StatsSection";
import {
  CATEGORY_COLORS,
  isBroken,
  type ScoredJob,
  type Source,
  type Stats,
  type SourceView,
} from "./types";
import { API, fetcher } from "../lib/swr";

export default function SourcesPage() {
  // SWR drives initial + focus-triggered fetches. Mutation handlers still
  // refetch via raw fetch() + setSources/setStats for immediate feedback;
  // SWR's own focus-revalidate keeps the cache aligned with the server.
  const { data: sourcesSWR, isLoading: sourcesLoading } =
    useSWR<Source[]>("/sources", fetcher, { keepPreviousData: true });
  const { data: statsSWR } =
    useSWR<Stats>("/stats", fetcher, { keepPreviousData: true });
  const { data: countriesSWR } =
    useSWR<{ country: string; count: number }[]>("/countries", fetcher, { keepPreviousData: true });
  const { mutate: swrMutate } = useSWRConfig();

  // Revalidate /sources + /stats (+ /dashboard) after a drawer admin
  // action. The backend clears its own caches in the handler, so
  // the next fetch is authoritative.
  const refreshAfterAdminAction = () => {
    void swrMutate("/sources");
    void swrMutate("/stats");
    void swrMutate("/dashboard");
  };

  const [sources, setSources] = useState<Source[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<string[]>([]);      // multi-select OR: category chips
  const [subFilters, setSubFilters] = useState<string[]>([]); // multi-select OR: sub-specialism chips
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
  const PAGE_SIZE = 99;
  const [displayLimit, setDisplayLimit] = useState<number>(PAGE_SIZE);

  // Add Company (Coresignal taxonomy sweep) UI state. All internal
  // state of the wizard (name, phase, result, per-candidate busy flag,
  // error) now lives in <AddCompanyModal/>; the parent only tracks
  // whether the panel is open.
  const [showAddCompany, setShowAddCompany] = useState(false);
  // `highlightSourceId` pins a newly-added card to the top of the Sources list until the user interacts elsewhere.
  const [highlightSourceId, setHighlightSourceId] = useState<number | null>(null);
  const [sourceView, setSourceView] = useState<SourceView>("leads");
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);
  const [iframeTitle, setIframeTitle] = useState("");

  // Mirror SWR's data into the local state the rest of the page already
  // uses. Mutation handlers still call setSources/setStats for immediate
  // feedback; on the next SWR revalidation (focus, navigation, or explicit
  // mutate()) the server truth replaces it.
  useEffect(() => {
    if (countriesSWR) setCountries(countriesSWR);
  }, [countriesSWR]);
  useEffect(() => {
    if (sourcesSWR) setSources(sourcesSWR);
  }, [sourcesSWR]);
  useEffect(() => {
    if (statsSWR) setStats(statsSWR);
  }, [statsSWR]);
  useEffect(() => {
    setLoading(sourcesLoading);
  }, [sourcesLoading]);


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

  // Sub-chip changes invalidate the per-card job cache: the server now
  // filters by sub_specialism so rows cached under a different sub set
  // don't match any new selection. Also close any open drawer so the
  // user sees a fresh fetch when they re-expand.
  useEffect(() => {
    setSourceJobs({});
    setExpandedSource(null);
    setExpandedCategory(null);
  }, [subFilters]);

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
    if (expandedSource === sourceId && expandedCategory === (category || null)) {
      setExpandedSource(null);
      setExpandedCategory(null);
      return;
    }
    setExpandedSource(sourceId);
    setExpandedCategory(category || null);
    // Cache key includes every filter that changes the server's response
    // — category, country, and the sub-specialism chip set — so toggling
    // sub chips doesn't serve rows cached under a different selection.
    const subKey = subFilters.length > 0 ? [...subFilters].sort().join("|") : "";
    const jobKey = [String(sourceId), category || "", countryFilter, subKey].filter(Boolean).join("_");
    if (!sourceJobs[jobKey]) {
      const params = new URLSearchParams();
      if (category) params.set("category", category);
      if (countryFilter) params.set("country", countryFilter);
      // Server-side sub filter keeps the drawer row list in lockstep with
      // the card pill count — both computed against the same routing snapshot.
      for (const sub of subFilters) params.append("sub_specialism", sub);
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

  // `isBroken` lives in types.ts — a card is Broken only when direct source
  // errored AND no aggregator is finding jobs. Cards with aggregator
  // coverage stay in the normal flow; the direct-source failure is
  // captured by the separate backend health report, not surfaced here.

  // Get effective categories for a source based on country filter
  const getCats = (s: Source): Record<string, number> => {
    if (!countryFilter) return s.categories || {};
    return (s.categories_by_country || {})[countryFilter] || {};
  };
  const getScored = (s: Source): number => {
    const cats = getCats(s);
    return Object.values(cats).reduce((a, b) => a + b, 0);
  };

  // Country-scoped sub-specialism blob for a source. When a country filter is
  // active we return only that country's bucket. When no country filter is
  // active we merge every known country EXCEPT "N/A" so global sub totals
  // don't mix in unresolved-location rows.
  const getSubs = (s: Source): Record<string, Record<string, number>> => {
    const byCountry = s.sub_specialisms_by_country || {};
    if (countryFilter) return byCountry[countryFilter] || {};
    const merged: Record<string, Record<string, number>> = {};
    for (const [country, catMap] of Object.entries(byCountry)) {
      if (country === "N/A") continue;
      for (const [cat, subMap] of Object.entries(catMap)) {
        merged[cat] = merged[cat] || {};
        for (const [sub, n] of Object.entries(subMap)) {
          merged[cat][sub] = (merged[cat][sub] || 0) + (n as number);
        }
      }
    }
    return merged;
  };

  // Effective category count for a source, narrowed by any active sub-specialism
  // filter. When no sub chips are selected this is just the raw category count.
  // When sub chips ARE selected, we sum only the matching sub counts within that
  // category — so the number displayed always agrees with what the filter
  // would actually surface.
  const effCatCount = (s: Source, cat: string): number => {
    const base = getCats(s)[cat] || 0;
    if (subFilters.length === 0) return base;
    const subs = getSubs(s)[cat] || {};
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
  // With Leads = any card with at least one classified lead (direct or
  // aggregator). Wins precedence over No Jobs Found: if an aggregator
  // confirms jobs at this employer, the card lives here and not there.
  const withLeadsCount = sources.filter((s) => getScored(s) > 0).length;
  // No Jobs Found = a direct adapter ran and returned zero raw_jobs AND
  // no aggregator has any classified leads for this employer either.
  // Mutually exclusive with With Leads.
  const noJobsCount = sources.filter((s) => !isBroken(s) && s.jobs === 0 && s.adapter_name !== "aggregator" && getScored(s) === 0).length;
  const notRelevantCount = sources.filter((s) => getScored(s) === 0 && !isBroken(s) && (countryFilter ? globalScored(s) > 0 || s.jobs > 0 : s.jobs > 0)).length;
  const brokenCount = sources.filter((s) => isBroken(s)).length;

  // Compute adapter counts from current view (before adapter filter applied)
  const viewFiltered = sources.filter((s) => {
    const scored = getScored(s);
    const cats = getCats(s);
    if (searchLower) return s.employer_name.toLowerCase().includes(searchLower);
    // No Jobs Found: direct adapter ran, returned zero raw_jobs, AND no
    // aggregator has classified leads for this employer either. Once an
    // aggregator confirms even one relevant lead, the card promotes to
    // With Leads and is removed from here. Matches the noJobsCount calc.
    if (sourceView === "no_jobs") return !isBroken(s) && s.jobs === 0 && s.adapter_name !== "aggregator" && scored === 0;
    // Not Relevant: jobs were scraped but none classified into core markets.
    if (sourceView === "not_relevant") return scored === 0 && !isBroken(s) && s.jobs > 0;
    if (sourceView === "broken") return isBroken(s);
    if (sourceView === "all") return true;
    // Default ("With Leads"): any card with at least one classified lead,
    // regardless of whether it came from a direct adapter or an aggregator.
    if (filters.length === 0) return scored > 0;
    // OR across selected category chips: source qualifies if it has leads in ANY selected category.
    if (!filters.some((c) => (cats[c] || 0) > 0)) return false;
    // OR across selected sub-specialism chips: at least one (selected cat, selected sub)
    // pair must exist on this source WITHIN THE ACTIVE COUNTRY. getSubs narrows
    // the blob to the current country filter so a UK filter doesn't let through
    // cards whose only Credit Risk jobs are in the US.
    if (subFilters.length > 0) {
      const subs = getSubs(s);
      return filters.some((c) => subFilters.some((sub) => (subs[c]?.[sub] || 0) > 0));
    }
    return true;
  });
  const adapterCounts: Record<string, number> = {};
  viewFiltered.forEach((s) => { adapterCounts[s.adapter_name] = (adapterCounts[s.adapter_name] || 0) + 1; });
  const sortedAdapters = Object.entries(adapterCounts).sort((a, b) => b[1] - a[1]);

  // Aggregator chip counts — per-aggregator card count + job count across the current view
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

  // Special views ("No Jobs Found", "Broken", "Not Relevant") describe
  // cards by absence-of-leads or failure state. Applying the adapter /
  // aggregator / employment-type filters on top would always empty the
  // list — a no-jobs card has no employment_types or aggregator_hits
  // to match against. Skip those filters in those views so the rendered
  // list matches the chip count.
  const isSpecialView = sourceView === "no_jobs" || sourceView === "broken" || sourceView === "not_relevant";
  const filtered = isSpecialView
    ? viewFiltered
    : viewFiltered
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

  const categoryColors = CATEGORY_COLORS;

  return (
    <div className="min-h-screen" style={{ background: "var(--bg-primary)" }}>
      <Sidebar />

      {/* Main */}
      <main className="ml-60 h-screen flex flex-col overflow-hidden">
        <div className="flex items-center px-8 h-14 shrink-0" style={{ background: "rgba(10,10,15,0.8)", borderBottom: "1px solid var(--border-subtle)" }}>
          <div className="font-bold text-base">Companies</div>
        </div>

        <div className="px-7 pt-5 shrink-0" style={{ background: "var(--bg-primary)" }}>
          {/* Header */}
          <div className="flex justify-between items-center mb-5">
            <div>
              <div className="text-xl font-bold">Companies</div>
              <div className="text-sm mt-1" style={{ color: "var(--text-muted)" }}>{sources.length} companies with active leads</div>
            </div>
            <div className="flex items-center gap-3">
              <SourceFilters
                companySearch={companySearch}
                onCompanySearchChange={setCompanySearch}
                countryFilter={countryFilter}
                onCountryFilterChange={(value) => { setCountryFilter(value); setSourceJobs({}); setExpandedSource(null); }}
                countries={countries}
                employmentTypeFilter={employmentTypeFilter}
                onEmploymentTypeFilterChange={(value) => { setEmploymentTypeFilter(value); setSourceJobs({}); setExpandedSource(null); }}
              />
              {/* Add Company — Coresignal-backed taxonomy sweep. Modal mounts
                  fresh each time the user opens it, so all internal state
                  (name, phase, result, error) resets naturally — no need to
                  reset it here. */}
              <button
                onClick={() => setShowAddCompany((v) => !v)}
                className="px-4 py-2 rounded-lg text-sm font-semibold text-white cursor-pointer"
                style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)", boxShadow: "0 2px 12px rgba(108,92,231,0.3)" }}
              >
                + Add Company
              </button>
            </div>
          </div>

          {/* Add Company Panel — mounts fresh each time via the `open` guard
              so the wizard always starts from a clean state. */}
          {showAddCompany && (
            <AddCompanyModal
              onClose={() => setShowAddCompany(false)}
              apiBase={API}
              countryFilter={countryFilter}
              onCardAdded={(sourceId) => {
                setHighlightSourceId(sourceId);
                setSourceView("all");
                setAdapterFilter("");
                setAggregatorFilter("");
              }}
              onSourcesRefreshed={(s, st) => { setSources(s); setStats(st); }}
            />
          )}

          {/* Stats tiles + category / sub-specialism / adapter / aggregator chips */}
          <StatsSection
            stats={stats}
            sources={sources}
            withLeadsCount={withLeadsCount}
            noJobsCount={noJobsCount}
            notRelevantCount={notRelevantCount}
            brokenCount={brokenCount}
            sourceView={sourceView}
            onSelectView={(view) => {
              setSourceView(view);
              setAddedSourceId(null);
              setAdapterFilter("");
              setAggregatorFilter("");
              setHighlightSourceId(null);
            }}
            filters={filters}
            setFilters={setFilters}
            onFilterChipToggled={() => setAddedSourceId(null)}
            subFilters={subFilters}
            setSubFilters={setSubFilters}
            sortedAdapters={sortedAdapters}
            adapterFilter={adapterFilter}
            setAdapterFilter={setAdapterFilter}
            sortedAggregators={sortedAggregators}
            aggregatorFilter={aggregatorFilter}
            setAggregatorFilter={setAggregatorFilter}
            aggregatorJobCounts={aggregatorJobCounts}
            effScored={effScored}
            effCatCount={effCatCount}
            getCats={getCats}
            getSubs={getSubs}
            categoryColors={categoryColors}
          />

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
                ? `Showing companies with ${filters.join(" OR ")}${subFilters.length > 0 ? ` · ${subFilters.join(" OR ")}` : ""} leads`
                : "All companies"}
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
                <SourceCard
                  key={src.id}
                  src={src}
                  expandedSource={expandedSource}
                  expandedCategory={expandedCategory}
                  addedSourceId={addedSourceId}
                  scrapingSourceId={scrapingSourceId}
                  scrapeQueue={scrapeQueue}
                  scrapeSourceResult={scrapeSourceResult}
                  confirmDeleteId={confirmDeleteId}
                  diagnosing={diagnosing}
                  diagnoseResult={diagnoseResult}
                  filters={filters}
                  subFilters={subFilters}
                  countryFilter={countryFilter}
                  sourceJobs={sourceJobs}
                  setSourceJobs={setSourceJobs}
                  hotlist={hotlist}
                  categoryColors={categoryColors}
                  getCats={getCats}
                  getScored={getScored}
                  effCatCount={effCatCount}
                  onToggleJobs={handleToggleJobs}
                  onScrape={handleScrapeSource}
                  onDiagnose={handleDiagnose}
                  onDelete={handleDeleteSource}
                  onRequestDelete={setConfirmDeleteId}
                  onCancelDelete={() => setConfirmDeleteId(null)}
                  setHotlist={setHotlist}
                  onAdminAction={refreshAfterAdminAction}
                  apiBase={API}
                />
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
                Load More?
              </button>
              {orderedSources.length - displayLimit > PAGE_SIZE && (
                <button
                  onClick={() => setDisplayLimit(orderedSources.length)}
                  className="text-[11px] cursor-pointer underline"
                  style={{ color: "var(--text-muted)" }}
                >
                  Load all remaining
                </button>
              )}
            </div>
          )}
        </div>
      </main>

    </div>
  );
}
