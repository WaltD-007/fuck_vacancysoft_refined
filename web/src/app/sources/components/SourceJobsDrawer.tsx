"use client";

import type { Dispatch, SetStateAction } from "react";

import type { ScoredJob, Source } from "../types";

type Props = {
  src: Source;
  expandedCategory: string | null;
  countryFilter: string;
  // Active sub-specialism chips from the page header. Each row's
  // sub_specialism (already computed server-side) is matched against
  // this list so the drawer agrees with the card's pill count.
  subFilters: string[];
  sourceJobs: Record<string, ScoredJob[]>;
  categoryColors: Record<string, string>;
  hotlist: Set<string>;
  setHotlist: Dispatch<SetStateAction<Set<string>>>;
  apiBase: string;
};

/**
 * The expanded per-card job list. Rendered by the parent when the card
 * for `src.id` is expanded. Reads its job rows out of the shared
 * `sourceJobs` cache using the same key rule the parent uses to
 * populate it:
 *
 *   `${src.id}` or `${src.id}_${category}`, then optionally
 *   `_${countryFilter}` when a country filter is active.
 *
 * Extracted verbatim from `sources/page.tsx` during the Week 3 split.
 */
export default function SourceJobsDrawer({
  src,
  expandedCategory,
  countryFilter,
  subFilters,
  sourceJobs,
  categoryColors,
  hotlist,
  setHotlist,
  apiBase,
}: Props) {
  const baseKey = expandedCategory ? `${src.id}_${expandedCategory}` : `${src.id}`;
  const jobKey = countryFilter ? `${baseKey}_${countryFilter}` : baseKey;
  const rows = sourceJobs[jobKey];
  // Client-side sub-specialism narrowing so the drawer agrees with the
  // card pill count (which already narrows via effCatCount on the page).
  // Per-card job lists are small (<100 rows) — filtering in JS costs
  // nothing and keeps the server endpoint + cache key unchanged.
  const visibleJobs = rows
    ? (subFilters.length === 0
        ? rows
        : rows.filter((j) => subFilters.includes(j.sub_specialism ?? "")))
    : undefined;

  return (
    <div style={{ borderTop: "1px solid var(--border-subtle)", animation: "fadeIn 0.2s ease-out" }}>
      {expandedCategory && <div className="px-4 pt-2 text-xs font-semibold" style={{ color: categoryColors[expandedCategory] || "var(--accent-light)" }}>{expandedCategory} leads</div>}
      {visibleJobs === undefined ? (
        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>Loading...</div>
      ) : rows!.length === 0 ? (
        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>No jobs found</div>
      ) : visibleJobs.length === 0 ? (
        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>
          No jobs matching {subFilters.join(", ")}
        </div>
      ) : (
        <div className="max-h-64 overflow-y-auto">
          {visibleJobs.map((job, i) => (
            <div
              key={i}
              className="px-4 py-2 flex items-center gap-3"
              style={{ borderBottom: i < visibleJobs.length - 1 ? "1px solid var(--border-subtle)" : "none" }}
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
                    await fetch(`${apiBase}/queue`, {
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
}
