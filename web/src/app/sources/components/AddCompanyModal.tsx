"use client";

import { useState } from "react";

import type { AddCompanyCandidate, AddCompanyUpdateLead, Source, Stats } from "../types";

type AddCompanyResult = {
  status: string;
  jobs_found: number;
  company: string;
  source_id: number | null;
  message: string;
  candidates?: AddCompanyCandidate[];
  // Returned by /search when status="exists": true if the existing direct card
  // can be refreshed via a one-off CoreSignal sweep (the "Update" flow below).
  can_update?: boolean;
};

type AddCompanyState = "idle" | "searching" | "confirming" | "scraping" | "done" | "error";

// Sub-state machine for the "Update existing card via CoreSignal" flow — only
// exercised when status="exists" + can_update.
type UpdateState = "idle" | "previewing" | "ready" | "committing" | "done";

type Props = {
  onClose: () => void;
  apiBase: string;
  countryFilter: string;
  /**
   * Fired after a successful candidate add. Parent uses this to pin the
   * new card to the top of the Sources list and clear any adapter /
   * aggregator filters so the new card is visible.
   */
  onCardAdded: (sourceId: number) => void;
  /** Fired after the modal re-fetches /api/sources and /api/stats. */
  onSourcesRefreshed: (sources: Source[], stats: Stats | null) => void;
};

/**
 * The "Add a Company" wizard — Coresignal-backed taxonomy sweep. Two
 * phases:
 *
 *   1. Search — user types a company name; POST /sources/add-company/search
 *      returns either `no_jobs`, `exists` (both terminal), or `ready`
 *      with a candidates list.
 *   2. Confirm — user picks one candidate; POST
 *      /sources/add-company/confirm creates the card and runs a capped
 *      scrape. The chosen row flips to `already_in_db=true` so the user
 *      can add another employer from the same search.
 *
 * All state is local — the modal mounts fresh each time the parent
 * opens it (via `{open && <AddCompanyModal ... />}`), so there is
 * nothing to reset on close. Extracted verbatim from `sources/page.tsx`
 * during the Week 3 split.
 */
export default function AddCompanyModal({
  onClose,
  apiBase,
  countryFilter,
  onCardAdded,
  onSourcesRefreshed,
}: Props) {
  const [addCompanyName, setAddCompanyName] = useState("");
  const [addCompanyState, setAddCompanyState] = useState<AddCompanyState>("idle");
  const [addCompanyResult, setAddCompanyResult] = useState<AddCompanyResult | null>(null);
  const [addCompanyConfirmingFor, setAddCompanyConfirmingFor] = useState<string | null>(null);
  const [addCompanyError, setAddCompanyError] = useState("");

  // Update-existing-card flow (only active when the search returns status="exists")
  const [updateState, setUpdateState] = useState<UpdateState>("idle");
  const [updateLeads, setUpdateLeads] = useState<AddCompanyUpdateLead[]>([]);
  const [updateMessage, setUpdateMessage] = useState("");
  const [updateError, setUpdateError] = useState("");

  const resetUpdateFlow = () => {
    setUpdateState("idle");
    setUpdateLeads([]);
    setUpdateMessage("");
    setUpdateError("");
  };

  const handleAddCompanySearch = async () => {
    const company = addCompanyName.trim();
    if (!company) return;
    setAddCompanyError("");
    setAddCompanyResult(null);
    resetUpdateFlow();
    setAddCompanyState("searching");
    try {
      const res = await fetch(`${apiBase}/sources/add-company/search`, {
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
      // When the company already has a direct card, skip the extra click —
      // auto-kick the update preview so the user immediately sees what CoreSignal
      // can add. The "Update via CoreSignal" button is only shown as a fallback
      // if this auto-fire doesn't happen (e.g. can_update missing from response).
      if (data.status === "exists" && data.can_update && data.source_id) {
        handleUpdatePreview(data.source_id);
      }
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
      const res = await fetch(`${apiBase}/sources/add-company/confirm`, {
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
        onCardAdded(data.source_id);
      }
      // Refresh sources so the new card appears
      const params = countryFilter ? `?country=${encodeURIComponent(countryFilter)}` : "";
      const [s, st] = await Promise.all([
        fetch(`${apiBase}/sources${params}`).then((r) => r.json()),
        fetch(`${apiBase}/stats${params}`).then((r) => r.json()),
      ]);
      onSourcesRefreshed(s, st);
    } catch (e) {
      setAddCompanyError((e as Error).message || "Failed to reach API");
      setAddCompanyState("error");
      setAddCompanyConfirmingFor(null);
    }
  };

  // ── Update-existing-card flow ──
  // Phase 1: preview the CoreSignal sweep without persisting.
  // `sourceIdOverride` lets us kick the preview off immediately from
  // handleAddCompanySearch (before React state catches up with setAddCompanyResult).
  const handleUpdatePreview = async (sourceIdOverride?: number) => {
    const sourceId = sourceIdOverride ?? addCompanyResult?.source_id;
    if (!sourceId) return;
    setUpdateError("");
    setUpdateLeads([]);
    setUpdateMessage("");
    setUpdateState("previewing");
    try {
      const res = await fetch(`${apiBase}/sources/add-company/update-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_id: sourceId, days_back: 30 }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setUpdateError(err.detail || `Server returned ${res.status}`);
        setUpdateState("idle");
        return;
      }
      const data = await res.json();
      setUpdateMessage(data.message || "");
      if (data.status === "ready") {
        setUpdateLeads(data.leads || []);
        setUpdateState("ready");
      } else {
        // no_jobs / not_found / error — terminal
        setUpdateState("done");
      }
    } catch (e) {
      setUpdateError((e as Error).message || "Failed to reach API");
      setUpdateState("idle");
    }
  };

  // Phase 2: commit — backend creates/reuses a CoreSignal source and runs the
  // full scrape pipeline. Ledger merge surfaces the new leads on the direct card.
  const handleUpdateCommit = async () => {
    const sourceId = addCompanyResult?.source_id;
    if (!sourceId) return;
    setUpdateError("");
    setUpdateState("committing");
    try {
      const res = await fetch(`${apiBase}/sources/add-company/update-commit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_id: sourceId, days_back: 30 }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setUpdateError(err.detail || `Server returned ${res.status}`);
        setUpdateState("ready"); // stay on the leads list so the user can retry
        return;
      }
      const data = await res.json();
      setUpdateMessage(data.message || "");
      setUpdateState("done");
      onCardAdded(sourceId); // pin the updated card to the top
      const params = countryFilter ? `?country=${encodeURIComponent(countryFilter)}` : "";
      const [s, st] = await Promise.all([
        fetch(`${apiBase}/sources${params}`).then((r) => r.json()),
        fetch(`${apiBase}/stats${params}`).then((r) => r.json()),
      ]);
      onSourcesRefreshed(s, st);
    } catch (e) {
      setUpdateError((e as Error).message || "Failed to reach API");
      setUpdateState("ready");
    }
  };

  return (
    <div className="mb-5" style={{ animation: "fadeIn 0.3s ease-out" }}>
      <div className="relative p-6 rounded-xl" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)" }}>
        <button onClick={onClose} className="absolute top-3 right-4 text-lg cursor-pointer" style={{ color: "var(--text-muted)" }}>&times;</button>
        <div className="font-bold text-[15px] mb-1">Add a Company</div>
        <div className="text-xs mb-4" style={{ color: "var(--text-muted)" }}>
          Search Coresignal for this company&apos;s jobs across the full taxonomy (last 30 days). If jobs are found, a card will be created.
        </div>

        <div className="flex gap-2.5 mb-3">
          <input
            type="text"
            value={addCompanyName}
            onChange={(e) => { setAddCompanyName(e.target.value); if (addCompanyState === "confirming" || addCompanyState === "done" || addCompanyState === "error") { setAddCompanyState("idle"); setAddCompanyResult(null); setAddCompanyError(""); resetUpdateFlow(); } }}
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

        {/* Phase 1 result — `no_jobs` (terminal) */}
        {addCompanyState === "done" && addCompanyResult && addCompanyResult.status === "no_jobs" && (
          <div className="p-3 rounded-lg text-sm" style={{
            background: "var(--bg-primary)",
            border: "1px solid var(--amber-border)",
            color: "var(--amber)",
          }}>
            <div className="font-semibold">No jobs found</div>
            <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{addCompanyResult.message}</div>
          </div>
        )}

        {/* Phase 1 result — `exists`: offer an update sweep rather than dead-ending */}
        {addCompanyState === "done" && addCompanyResult && addCompanyResult.status === "exists" && (
          <div className="p-3 rounded-lg text-sm" style={{ background: "var(--bg-primary)", border: "1px solid var(--border)", color: "var(--text-secondary)" }}>
            <div className="font-semibold" style={{ color: "var(--text-primary)" }}>
              Already exists{addCompanyResult.source_id ? ` (id=${addCompanyResult.source_id})` : ""}
            </div>
            <div className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>{addCompanyResult.message}</div>

            {addCompanyResult.can_update && updateState === "idle" && !updateError && (
              <button
                onClick={handleUpdatePreview}
                className="mt-3 px-4 py-1.5 rounded-lg text-xs font-semibold text-white cursor-pointer"
                style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)" }}
              >
                Update via CoreSignal
              </button>
            )}

            {updateState === "previewing" && (
              <div className="mt-3 flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                <span className="inline-block w-3 h-3 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
                Running CoreSignal preview sweep…
              </div>
            )}

            {updateError && (
              <div className="mt-3 p-2 rounded-md text-xs" style={{ background: "var(--bg-card)", border: "1px solid var(--red-border)", color: "var(--red)" }}>
                {updateError}
              </div>
            )}

            {/* Preview results — leads list + Add all button */}
            {(updateState === "ready" || updateState === "committing") && updateLeads.length > 0 && (
              <div className="mt-3 p-3 rounded-lg" style={{ background: "var(--bg-card)", border: "1px solid var(--accent)" }}>
                <div className="font-semibold text-[13px] mb-1" style={{ color: "var(--accent-light)" }}>
                  {updateLeads.length} new lead{updateLeads.length === 1 ? "" : "s"} to add
                </div>
                <div className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>
                  {updateMessage || "These are new to CoreSignal — not already captured by this card."}
                </div>
                <div className="flex flex-col gap-1.5 max-h-72 overflow-y-auto">
                  {updateLeads.map((lead) => (
                    <div
                      key={lead.external_id || `${lead.title}-${lead.url}`}
                      className="flex items-start gap-3 px-3 py-2 rounded-md"
                      style={{ background: "var(--bg-primary)", border: "1px solid var(--border-subtle)" }}
                    >
                      <div className="flex-1 min-w-0">
                        <div className="text-sm font-semibold truncate" style={{ color: "var(--text-primary)" }}>
                          {lead.url ? (
                            <a href={lead.url} target="_blank" rel="noreferrer" style={{ color: "var(--text-primary)" }}>
                              {lead.title}
                            </a>
                          ) : (
                            lead.title
                          )}
                        </div>
                        <div className="text-[11px] truncate" style={{ color: "var(--text-muted)" }}>
                          {[lead.company, lead.location, lead.posted_at].filter(Boolean).join(" — ")}
                        </div>
                        {lead.summary && (
                          <div className="text-[11px] mt-1 line-clamp-2" style={{ color: "var(--text-muted)" }}>
                            {lead.summary}
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleUpdateCommit}
                    disabled={updateState === "committing"}
                    className="px-4 py-1.5 rounded-lg text-xs font-semibold text-white cursor-pointer flex items-center gap-1.5"
                    style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)", opacity: updateState === "committing" ? 0.7 : 1 }}
                  >
                    {updateState === "committing" && <span className="inline-block w-3 h-3 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "white", animation: "spin 0.8s linear infinite" }} />}
                    {updateState === "committing" ? "Adding…" : `Add all ${updateLeads.length} to ${addCompanyResult.company}`}
                  </button>
                  <button
                    onClick={() => { resetUpdateFlow(); }}
                    disabled={updateState === "committing"}
                    className="px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer"
                    style={{ background: "transparent", color: "var(--text-secondary)", border: "1px solid var(--border)", opacity: updateState === "committing" ? 0.5 : 1 }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Terminal update states — success or no new leads */}
            {updateState === "done" && (
              <div className="mt-3 p-2 rounded-md text-xs" style={{ background: "var(--bg-card)", border: "1px solid var(--green-border)", color: "var(--green)" }}>
                {updateMessage || "Update complete."}
              </div>
            )}
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
  );
}
