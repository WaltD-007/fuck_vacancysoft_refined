"use client";

import { useState } from "react";
import type { Dispatch, SetStateAction } from "react";

import { AGGREGATOR_LABELS, type Source, type Stats, type SourceView } from "../types";

type Props = {
  stats: Stats | null;
  sources: Source[];

  // Precomputed bucket counts
  withLeadsCount: number;
  noJobsCount: number;
  notRelevantCount: number;
  brokenCount: number;

  // View selector
  sourceView: SourceView;
  onSelectView: (view: SourceView) => void;

  // Category + sub-specialism chip state
  filters: string[];
  setFilters: Dispatch<SetStateAction<string[]>>;
  /** Fired after any category-chip toggle; parent uses this to clear the
   *  "recently added" green highlight (legacy behaviour). */
  onFilterChipToggled: () => void;
  subFilters: string[];
  setSubFilters: Dispatch<SetStateAction<string[]>>;

  // Adapter + aggregator filter chips
  sortedAdapters: [string, number][];
  adapterFilter: string;
  setAdapterFilter: Dispatch<SetStateAction<string>>;
  sortedAggregators: [string, number][];
  aggregatorFilter: string;
  setAggregatorFilter: Dispatch<SetStateAction<string>>;
  aggregatorJobCounts: Record<string, number>;

  // Derived helpers that close over parent state
  effScored: (s: Source) => number;
  effCatCount: (s: Source, cat: string) => number;
  getCats: (s: Source) => Record<string, number>;
  // Country-aware sub-specialism blob lookup — mirrors getCats so
  // sub chip counts track the active country filter instead of summing
  // worldwide totals.
  getSubs: (s: Source) => Record<string, Record<string, number>>;

  // Colour map
  categoryColors: Record<string, string>;
};

/**
 * Header stats block for the sources page:
 *   - 5 clickable bucket tiles (With Leads, No Jobs Found, Not
 *     Relevant, Broken, All Sources) + a Qualified Leads readout
 *   - 7 category chips (Risk, Quant, Compliance, Audit, Cyber, Legal,
 *     Front Office) — multi-select OR, counts narrow with the current
 *     country / sub-specialism filter
 *   - conditional sub-specialism chip row — only shown when ≥1 category
 *     chip is active
 *   - adapter filter chips — only shown when there are ≥2 distinct
 *     adapters in the current view
 *   - aggregator filter chips with per-aggregator job counts
 *
 * All state is parent-owned; this component is pure-render over derived
 * props. Extracted verbatim from `sources/page.tsx` during the Week 3
 * split.
 */
export default function StatsSection({
  stats,
  sources,
  withLeadsCount,
  noJobsCount,
  notRelevantCount,
  brokenCount,
  sourceView,
  onSelectView,
  filters,
  setFilters,
  onFilterChipToggled,
  subFilters,
  setSubFilters,
  sortedAdapters,
  adapterFilter,
  setAdapterFilter,
  sortedAggregators,
  aggregatorFilter,
  setAggregatorFilter,
  aggregatorJobCounts,
  effScored,
  effCatCount,
  getCats,
  getSubs,
  categoryColors,
}: Props) {
  // Adapter + aggregator chips are collapsed by default — they're
  // directory-audit surfaces, noisy in normal use. Toggle reveals
  // both rows together; state lives in this component because the
  // toggle only controls its own rendering and nothing upstream.
  const [showAdapterChips, setShowAdapterChips] = useState(false);

  if (!stats) return null;
  return (
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
            onClick={() => onSelectView(v.key)}
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
                // Multi-select OR toggle. Clearing sub-filters on any category change
                // avoids stale sub chips that no longer belong to the selected set.
                setFilters((prev) => prev.includes(cat) ? prev.filter((c) => c !== cat) : [...prev, cat]);
                setSubFilters([]);
                onFilterChipToggled();
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
          mixed row, each chip coloured by its parent category. Multi-select OR. */}
      {filters.length > 0 && (() => {
        const options: { sub: string; cat: string; count: number }[] = [];
        const seen = new Set<string>();
        // Sub-chip pool follows the card grid's OR semantics: a source contributes
        // its subs if it has leads in ANY selected category.
        const poolSources = sources.filter((s) => filters.some((c) => (getCats(s)[c] || 0) > 0));
        for (const s of poolSources) {
          // getSubs narrows to the active country (or merges all non-N/A
          // countries when no country filter is active) so sub chip counts
          // track the filter instead of summing worldwide totals.
          const subs = getSubs(s);
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
      {/* Adapter + aggregator filter chips — collapsed by default to
          reduce visual noise. The chips are still useful when auditing
          the directory but most of the time the operator is filtering
          by category / sub-specialism / country, not by adapter or
          aggregator. */}
      {(sortedAdapters.length > 1 || sortedAggregators.length > 0) && (
        <div className="mt-3">
          <button
            onClick={() => setShowAdapterChips((v) => !v)}
            className="text-[10px] uppercase tracking-wider cursor-pointer hover:underline"
            style={{ color: "var(--text-muted)" }}
          >
            {showAdapterChips ? "▾ Hide" : "▸ Show"} adapter &amp; aggregator filters
          </button>
        </div>
      )}
      {showAdapterChips && sortedAdapters.length > 1 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
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
      {showAdapterChips && sortedAggregators.length > 0 && (
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
  );
}
