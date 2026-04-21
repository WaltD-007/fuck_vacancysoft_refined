"use client";

import type { Dispatch, SetStateAction } from "react";

import type { ScoredJob } from "../types";

type Props = {
  expandedCategory: string | null;
  // Cache key for this card's current job set. Computed in the parent
  // so key construction stays in one place (it includes country + the
  // active sub-specialism chips, both of which the server filters on).
  jobKey: string;
  sourceJobs: Record<string, ScoredJob[]>;
  // Setter the parent wires up so drawer rows can evict themselves
  // from the cache after a successful admin action. Keeps cache
  // ownership in the parent (so a fresh /api/sources/{id}/jobs fetch
  // still replaces the whole list cleanly).
  setSourceJobs: Dispatch<SetStateAction<Record<string, ScoredJob[]>>>;
  categoryColors: Record<string, string>;
  hotlist: Set<string>;
  setHotlist: Dispatch<SetStateAction<Set<string>>>;
  apiBase: string;
  // Fired after any admin action completes so the parent can refresh
  // the ledger / source card counts. Optional — if omitted, counts
  // refresh on the next poll instead.
  onAdminAction?: () => void;
};

/**
 * The expanded per-card job list. Rendered by the parent when the card
 * for `src.id` is expanded. Reads its job rows out of the shared
 * `sourceJobs` cache using the key computed by the parent
 * (`handleToggleJobs` in sources/page.tsx). Sub-specialism filtering
 * is done server-side now — this component is pure render.
 *
 * Admin buttons per row (added 2026-04-21):
 *   * Agy job        — POST /api/agency. Blocklists the employer and
 *                      cascades all their leads; the whole card will
 *                      usually vanish after the parent refreshes.
 *   * Dead job       — DELETE /api/leads/{enriched_id}. Removes this
 *                      single job and flags its RawJob so it doesn't
 *                      come back on the next scrape.
 *   * Wrong location — POST /api/leads/{enriched_id}/flag-location.
 *                      Queues a manual review without destroying data.
 *
 * All three optimistically evict the affected row(s) from the local
 * cache so the operator sees immediate feedback, then call
 * `onAdminAction()` so the parent refreshes counts from the server.
 */
export default function SourceJobsDrawer({
  expandedCategory,
  jobKey,
  sourceJobs,
  setSourceJobs,
  categoryColors,
  hotlist,
  setHotlist,
  apiBase,
  onAdminAction,
}: Props) {
  const rows = sourceJobs[jobKey];

  // Evict one row from every cache entry (not just `jobKey`) — the same
  // job can appear under several keys if the operator has toggled
  // sub-specialism chips on and off, and all of them should drop that
  // row in lockstep. Matches by enriched_job_id, which is stable.
  const evictRowById = (id: string) => {
    setSourceJobs((prev) => {
      const next: Record<string, ScoredJob[]> = {};
      for (const [k, v] of Object.entries(prev)) {
        next[k] = v.filter((r) => r.id !== id);
      }
      return next;
    });
  };

  // Evict every row for a given employer name. Used by the Agy job
  // button so the whole company disappears from the drawer on click
  // instead of one row at a time.
  const evictRowsByCompany = (company: string) => {
    const norm = company.trim().toLowerCase();
    setSourceJobs((prev) => {
      const next: Record<string, ScoredJob[]> = {};
      for (const [k, v] of Object.entries(prev)) {
        next[k] = v.filter((r) => (r.company || "").trim().toLowerCase() !== norm);
      }
      return next;
    });
  };

  const handleAgyJob = async (job: ScoredJob) => {
    if (!job.company) return;
    const confirmed = window.confirm(
      `Mark "${job.company}" as a recruitment agency? This removes ALL their current leads and blocklists them from future scrapes.`,
    );
    if (!confirmed) return;
    evictRowsByCompany(job.company);
    try {
      await fetch(`${apiBase}/agency`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ company: job.company }),
      });
    } finally {
      onAdminAction?.();
    }
  };

  const handleDeadJob = async (job: ScoredJob) => {
    const confirmed = window.confirm(
      `Delete "${job.title}"? The job is removed from the DB and won't re-enrich on the next scrape.`,
    );
    if (!confirmed) return;
    evictRowById(job.id);
    try {
      await fetch(`${apiBase}/leads/${encodeURIComponent(job.id)}`, {
        method: "DELETE",
      });
    } finally {
      onAdminAction?.();
    }
  };

  const handleWrongLocation = async (job: ScoredJob) => {
    const note = window.prompt(
      `Correct location for "${job.title}" (currently ${job.location ?? "—"}).\n\n` +
        `If you type a real location (e.g. "Buffalo, NY, USA" or "London, UK") it will be applied immediately. ` +
        `Leave blank or type free text to just flag for manual review.`,
      "",
    );
    if (note === null) return;   // operator cancelled
    try {
      const res = await fetch(`${apiBase}/leads/${encodeURIComponent(job.id)}/flag-location`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      if (res.ok) {
        const body = await res.json().catch(() => null);
        if (body?.status === "applied") {
          // Optimistic local patch so the operator sees the fix land
          // before the server's next refresh pushes counts through.
          setSourceJobs((prev) => {
            const next: Record<string, ScoredJob[]> = {};
            for (const [k, v] of Object.entries(prev)) {
              next[k] = v.map((r) =>
                r.id === job.id
                  ? { ...r, location: body.city, country: body.country }
                  : r,
              );
            }
            return next;
          });
          window.alert(`Location updated to ${body.city}, ${body.country}.`);
        } else if (body?.status === "queued") {
          window.alert("Flagged for manual review — location was left unchanged.");
        }
      }
    } finally {
      onAdminAction?.();
    }
  };

  return (
    <div style={{ borderTop: "1px solid var(--border-subtle)", animation: "fadeIn 0.2s ease-out" }}>
      {expandedCategory && <div className="px-4 pt-2 text-xs font-semibold" style={{ color: categoryColors[expandedCategory] || "var(--accent-light)" }}>{expandedCategory} leads</div>}
      {rows === undefined ? (
        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>Loading...</div>
      ) : rows.length === 0 ? (
        <div className="p-3 text-center text-xs" style={{ color: "var(--text-muted)" }}>No jobs found</div>
      ) : (
        <div className="max-h-80 overflow-y-auto">
          {rows.map((job, i) => {
            const hotlistKey = job.url || `${job.title}-${job.company}`;
            const isQueued = hotlist.has(hotlistKey);
            return (
              <div
                key={job.id || i}
                className="px-4 py-2 flex items-center gap-3"
                style={{ borderBottom: i < rows.length - 1 ? "1px solid var(--border-subtle)" : "none" }}
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
                    if (!isQueued) {
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
                      if (next.has(hotlistKey)) next.delete(hotlistKey); else next.add(hotlistKey);
                      return next;
                    });
                  }}
                  className="text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0 cursor-pointer"
                  // Green in both states to match the "positive action"
                  // colour of the other coloured admin buttons (red Agy,
                  // blue Dead, amber Wrong loc). The ★ / ☆ glyph + the
                  // label swap ("Queued" vs "Hotlist") already signal
                  // which state the row is in; the colour doesn't need
                  // to double up on that.
                  style={isQueued
                    ? { background: "rgba(0,210,160,0.18)", color: "var(--green)", border: "1px solid rgba(0,210,160,0.4)" }
                    : { background: "rgba(0,210,160,0.08)", color: "var(--green)", border: "1px solid rgba(0,210,160,0.25)" }
                  }
                  title={isQueued ? "Added to Lead List" : "Add to Lead List"}
                >
                  {isQueued ? "★ Queued" : "☆ Hotlist"}
                </button>
                {/* Admin actions — kept visually subdued so they don't
                    crowd the row. Each one confirms before firing. */}
                <button
                  onClick={(e) => { e.stopPropagation(); void handleAgyJob(job); }}
                  className="text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0 cursor-pointer"
                  style={{ background: "rgba(255,107,107,0.08)", color: "var(--red)", border: "1px solid rgba(255,107,107,0.25)" }}
                  title={`Mark ${job.company || "this employer"} as a recruitment agency`}
                >
                  Agy job
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); void handleDeadJob(job); }}
                  disabled={!job.id}
                  className="text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                  // Blue — matches the other coloured admin buttons
                  // (red Agy, amber Wrong loc, green Hotlist) so all
                  // four actions on the row read as deliberate /
                  // clickable rather than greyed-out. Blue is picked
                  // for "destructive but scoped" (removing one job)
                  // vs red's "destructive + company-wide" (Agy).
                  style={{ background: "rgba(77,171,247,0.08)", color: "var(--blue)", border: "1px solid rgba(77,171,247,0.25)" }}
                  title="Delete this job and stop it from re-enriching"
                >
                  Dead job
                </button>
                <button
                  onClick={(e) => { e.stopPropagation(); void handleWrongLocation(job); }}
                  disabled={!job.id}
                  className="text-[9px] font-semibold px-1.5 py-0.5 rounded shrink-0 cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                  style={{ background: "rgba(255,179,64,0.08)", color: "var(--amber)", border: "1px solid rgba(255,179,64,0.25)" }}
                  title="Flag this location as wrong for manual review"
                >
                  Wrong loc
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
