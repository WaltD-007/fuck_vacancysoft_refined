"use client";

import type { Dispatch, SetStateAction } from "react";

import type { ScoredJob, Source } from "../types";
import SourceJobsDrawer from "./SourceJobsDrawer";

type Props = {
  src: Source;

  // Expansion / scrape / delete / diagnose state (all parent-owned)
  expandedSource: number | null;
  expandedCategory: string | null;
  addedSourceId: number | null;
  scrapingSourceId: number | null;
  scrapeQueue: number[];
  scrapeSourceResult: Record<number, string>;
  confirmDeleteId: number | null;
  diagnosing: Set<number>;
  diagnoseResult: Record<number, string>;

  // Filters affecting what the card shows
  filters: string[];
  subFilters: string[];
  countryFilter: string;

  // Shared caches
  sourceJobs: Record<string, ScoredJob[]>;
  hotlist: Set<string>;

  // Colour map
  categoryColors: Record<string, string>;

  // Derived helpers that close over parent state — passed as callables
  getCats: (s: Source) => Record<string, number>;
  getScored: (s: Source) => number;
  effCatCount: (s: Source, cat: string) => number;

  // Callbacks
  onToggleJobs: (sourceId: number, category?: string, companyName?: string) => void;
  onScrape: (sourceId: number) => void;
  onDiagnose: (sourceId: number) => void;
  onDelete: (sourceId: number) => void;
  onRequestDelete: (sourceId: number) => void;
  onCancelDelete: () => void;
  setHotlist: Dispatch<SetStateAction<Set<string>>>;

  // API base (for the hotlist POST inside the drawer)
  apiBase: string;
};

/**
 * One source row in the sources grid: header (employer + status /
 * scrape / diagnose / delete controls), metadata strip (adapter name +
 * base URL), category pill row with per-category counts, and the
 * expanded jobs drawer when the card is expanded.
 *
 * All state is parent-owned; the card reads it through props and
 * mutates only via the `on*` callbacks. Extracted verbatim from
 * `sources/page.tsx` during the Week 3 split.
 */
export default function SourceCard({
  src,
  expandedSource,
  expandedCategory,
  addedSourceId,
  scrapingSourceId,
  scrapeQueue,
  scrapeSourceResult,
  confirmDeleteId,
  diagnosing,
  diagnoseResult,
  filters,
  subFilters,
  countryFilter,
  sourceJobs,
  hotlist,
  categoryColors,
  getCats,
  getScored,
  effCatCount,
  onToggleJobs,
  onScrape,
  onDiagnose,
  onDelete,
  onRequestDelete,
  onCancelDelete,
  setHotlist,
  apiBase,
}: Props) {
  return (
    <div className="rounded-xl" style={{ background: "var(--bg-card)", border: expandedSource === src.id ? "1px solid var(--accent)" : src.id === addedSourceId ? "1px solid var(--green)" : (src.last_run_status === "FAIL" || src.last_run_status === "error") ? "1px solid var(--red)" : "1px solid var(--border-subtle)" }}>
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
                onClick={(e) => { e.stopPropagation(); onDelete(src.id); }}
                className="text-[10px] font-semibold px-1.5 py-0.5 rounded cursor-pointer"
                style={{ background: "var(--red-bg)", color: "var(--red)", border: "1px solid var(--red)" }}
              >
                Yes
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onCancelDelete(); }}
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
                    onClick={(e) => { e.stopPropagation(); onDiagnose(src.id); }}
                    className="text-[10px] font-medium px-2 py-0.5 rounded cursor-pointer"
                    style={{ background: "rgba(255,107,107,0.08)", color: "var(--red)", border: "1px solid rgba(255,107,107,0.2)" }}
                    title="Diagnose why this source has no leads"
                  >
                    &#9881; Diagnose
                  </button>
                )
              )}
              <button
                onClick={(e) => { e.stopPropagation(); onScrape(src.id); }}
                className="text-[10px] font-medium px-2 py-0.5 rounded cursor-pointer"
                style={{ background: "var(--bg-elevated)", color: "var(--text-muted)", border: "1px solid var(--border)" }}
                title="Re-scrape this board for new jobs"
              >
                &#8635; Update
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onRequestDelete(src.id); }}
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
              onClick={() => onToggleJobs(src.id, cat, src.employer_name)}
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
            <div className="cursor-pointer" onClick={() => onToggleJobs(src.id, undefined, src.employer_name)}>
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
                  onClick={() => onScrape(src.id)}
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
      {expandedSource === src.id && (
        <SourceJobsDrawer
          src={src}
          expandedCategory={expandedCategory}
          countryFilter={countryFilter}
          subFilters={subFilters}
          sourceJobs={sourceJobs}
          categoryColors={categoryColors}
          hotlist={hotlist}
          setHotlist={setHotlist}
          apiBase={apiBase}
        />
      )}
    </div>
  );
}
