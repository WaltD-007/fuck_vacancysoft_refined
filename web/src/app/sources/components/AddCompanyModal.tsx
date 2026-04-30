"use client";

import { useState } from "react";

import { safeHref } from "../../lib/safe";
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
  // Set of selected lead keys. Commit only persists the ticked leads, so the
  // user can skip irrelevant adverts and avoid churning downstream tokens.
  const [selectedLeadKeys, setSelectedLeadKeys] = useState<Set<string>>(new Set());
  // Aggregator failure list from the multi-source preview (PR #...). Names
  // of aggregators that errored on this preview run; rendered as a small
  // status note above the lead list.
  const [aggregatorsErrored, setAggregatorsErrored] = useState<string[]>([]);
  const [aggregatorsAttempted, setAggregatorsAttempted] = useState(0);
  // Last commit's inserted raw_job_ids — powers the post-commit Undo
  // banner. Cleared when the modal opens or the operator dismisses.
  const [lastCommitRawJobIds, setLastCommitRawJobIds] = useState<string[]>([]);
  const [rollbackPending, setRollbackPending] = useState(false);

  const leadKey = (lead: AddCompanyUpdateLead) =>
    lead.external_id || `${lead.title}|${lead.url ?? ""}`;

  const resetUpdateFlow = () => {
    setUpdateState("idle");
    setUpdateLeads([]);
    setUpdateMessage("");
    setUpdateError("");
    setSelectedLeadKeys(new Set());
    setAggregatorsErrored([]);
    setAggregatorsAttempted(0);
    setLastCommitRawJobIds([]);
    setRollbackPending(false);
  };

  const toggleLead = (key: string) => {
    setSelectedLeadKeys((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
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
        const leads: AddCompanyUpdateLead[] = data.leads || [];
        setUpdateLeads(leads);
        // Default: nothing ticked. Operator opt-in to each lead they
        // want to persist. (Was the inverse pre-2026-04-30 — too easy
        // to commit a batch you only meant to skim.) "Select all" /
        // "Clear" controls below the count let users still bulk-tick
        // when that's the intent.
        setSelectedLeadKeys(new Set());
        setAggregatorsErrored(data.aggregators_errored || []);
        setAggregatorsAttempted(data.aggregators_attempted || 0);
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

  // Phase 2: commit — selective. Sends only the leads the user ticked; the
  // backend persists the pre-fetched preview payloads directly, so this costs
  // zero additional CoreSignal credits regardless of how many are selected.
  const handleUpdateCommitSelected = async () => {
    const sourceId = addCompanyResult?.source_id;
    if (!sourceId) return;
    const chosen = updateLeads.filter((lead) => selectedLeadKeys.has(leadKey(lead)));
    if (chosen.length === 0) return;
    setUpdateError("");
    setUpdateState("committing");
    try {
      const res = await fetch(`${apiBase}/sources/add-company/update-commit-selected`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_id: sourceId, leads: chosen }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        setUpdateError(err.detail || `Server returned ${res.status}`);
        setUpdateState("ready"); // stay on the leads list so the user can retry
        return;
      }
      const data = await res.json();
      setUpdateMessage(data.message || "");
      // Capture the inserted RawJob ids so the post-commit banner can
      // offer a one-click Undo via /add-company/rollback.
      setLastCommitRawJobIds(Array.isArray(data.raw_job_ids) ? data.raw_job_ids : []);
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

  // Rollback handler — fires the /add-company/rollback endpoint with
  // the list of RawJob ids the last commit returned. On success we
  // clear the ids (banner disappears) and refetch /sources so counts
  // reflect the deletion.
  const handleRollback = async () => {
    if (rollbackPending || lastCommitRawJobIds.length === 0) return;
    setRollbackPending(true);
    try {
      const res = await fetch(`${apiBase}/sources/add-company/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_job_ids: lastCommitRawJobIds }),
      });
      if (!res.ok) {
        setUpdateError(`Rollback failed: ${res.status}`);
        return;
      }
      const data = await res.json();
      setUpdateMessage(`Undo: ${data.message || "rolled back"}`);
      setLastCommitRawJobIds([]);
      const params = countryFilter ? `?country=${encodeURIComponent(countryFilter)}` : "";
      const [s, st] = await Promise.all([
        fetch(`${apiBase}/sources${params}`).then((r) => r.json()),
        fetch(`${apiBase}/stats${params}`).then((r) => r.json()),
      ]);
      onSourcesRefreshed(s, st);
    } catch (e) {
      setUpdateError(`Rollback failed: ${(e as Error).message}`);
    } finally {
      setRollbackPending(false);
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

        {/* Phase 1 result — `exists`: skip the "Already exists" banner entirely and
            drop straight into the auto-fired preview flow. Container kept for layout. */}
        {addCompanyState === "done" && addCompanyResult && addCompanyResult.status === "exists" && (
          <div className="text-sm">
            {addCompanyResult.can_update && updateState === "idle" && !updateError && (
              <button
                onClick={() => handleUpdatePreview()}
                className="mt-3 px-4 py-1.5 rounded-lg text-xs font-semibold text-white cursor-pointer"
                style={{ background: "linear-gradient(135deg, var(--accent), #8b7cf7)" }}
              >
                Update via CoreSignal
              </button>
            )}

            {updateState === "previewing" && (
              <div className="mt-3 flex items-center gap-2 text-xs" style={{ color: "var(--text-secondary)" }}>
                <span className="inline-block w-3 h-3 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "var(--accent)", animation: "spin 0.8s linear infinite" }} />
                Searching CoreSignal · Adzuna · eFC · Google Jobs in parallel…
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
                <div className="flex items-baseline justify-between mb-1">
                  <div className="font-semibold text-[13px]" style={{ color: "var(--accent-light)" }}>
                    {selectedLeadKeys.size} of {updateLeads.length} selected
                  </div>
                  <div className="flex gap-2 text-[11px]">
                    <button
                      onClick={() => setSelectedLeadKeys(new Set(updateLeads.map(leadKey)))}
                      disabled={updateState === "committing"}
                      className="underline cursor-pointer"
                      style={{ color: "var(--text-muted)", background: "transparent", border: "none" }}
                    >
                      all
                    </button>
                    <span style={{ color: "var(--text-muted)" }}>·</span>
                    <button
                      onClick={() => setSelectedLeadKeys(new Set())}
                      disabled={updateState === "committing"}
                      className="underline cursor-pointer"
                      style={{ color: "var(--text-muted)", background: "transparent", border: "none" }}
                    >
                      none
                    </button>
                  </div>
                </div>
                <div className="text-xs mb-2" style={{ color: "var(--text-muted)" }}>
                  {updateMessage || "These are new — not already captured by this card. Each ticked lead fetches its full record (URL, JD, location, etc.) on commit — costs 1 credit per ticked lead."}
                </div>
                {/* Aggregator status line — visible only when at least
                    one source errored. Shows a small inline note above
                    the leads list so the operator knows whether
                    "few results" means "no jobs found" or "Y of N
                    aggregators couldn't be reached". */}
                {aggregatorsErrored.length > 0 && (
                  <div className="text-[11px] mb-2" style={{ color: "var(--amber)" }}>
                    Searched {aggregatorsAttempted} aggregator(s); {aggregatorsErrored.join(", ")} unreachable. Other sources still populated the list.
                  </div>
                )}
                <div className="flex flex-col gap-1.5 max-h-72 overflow-y-auto">
                  {updateLeads.map((lead) => {
                    const key = leadKey(lead);
                    const checked = selectedLeadKeys.has(key);
                    return (
                      <label
                        key={key}
                        className="flex items-start gap-3 px-3 py-2 rounded-md cursor-pointer"
                        style={{
                          background: "var(--bg-primary)",
                          border: `1px solid ${checked ? "var(--accent)" : "var(--border-subtle)"}`,
                          opacity: checked ? 1 : 0.55,
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleLead(key)}
                          disabled={updateState === "committing"}
                          className="mt-1 cursor-pointer"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-semibold truncate">
                            {lead.url ? (
                              <a
                                href={safeHref(lead.url, "#")}
                                target="_blank"
                                rel="noreferrer"
                                onClick={(e) => e.stopPropagation()}
                                className="hover:underline"
                                style={{ color: "var(--accent-light)" }}
                                title={lead.url}
                              >
                                {lead.title}
                                <span className="text-[11px] ml-1" aria-hidden>↗</span>
                              </a>
                            ) : (
                              <span style={{ color: "var(--text-primary)" }}>{lead.title}</span>
                            )}
                          </div>
                          <div className="text-[11px] truncate flex items-center gap-1.5 flex-wrap" style={{ color: "var(--text-muted)" }}>
                            <span>{[lead.company, lead.location, lead.posted_at].filter(Boolean).join(" — ")}</span>
                            {lead.source_adapter && (
                              <span
                                className="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-[1px] rounded"
                                style={{
                                  background: lead.source_adapter === "coresignal" ? "rgba(108,92,231,0.12)"
                                    : lead.source_adapter === "adzuna" ? "rgba(0,210,160,0.10)"
                                    : lead.source_adapter === "google_jobs" ? "rgba(255,179,64,0.10)"
                                    : lead.source_adapter === "efinancialcareers" ? "rgba(77,171,247,0.10)"
                                    : "rgba(85,85,112,0.10)",
                                  color: lead.source_adapter === "coresignal" ? "#a29bfe"
                                    : lead.source_adapter === "adzuna" ? "#00d2a0"
                                    : lead.source_adapter === "google_jobs" ? "#ffd93d"
                                    : lead.source_adapter === "efinancialcareers" ? "#4dabf7"
                                    : "var(--text-muted)",
                                  letterSpacing: "0.5px",
                                }}
                              >
                                {lead.source_adapter === "google_jobs" ? "Google" : lead.source_adapter === "efinancialcareers" ? "eFC" : lead.source_adapter}
                              </span>
                            )}
                          </div>
                          {lead.summary && (
                            <div className="text-[11px] mt-1 line-clamp-2" style={{ color: "var(--text-muted)" }}>
                              {lead.summary}
                            </div>
                          )}
                        </div>
                      </label>
                    );
                  })}
                </div>
                <div className="flex gap-2 mt-3">
                  <button
                    onClick={handleUpdateCommitSelected}
                    disabled={updateState === "committing" || selectedLeadKeys.size === 0}
                    className="px-4 py-1.5 rounded-lg text-xs font-semibold text-white cursor-pointer flex items-center gap-1.5"
                    style={{
                      background: "linear-gradient(135deg, var(--accent), #8b7cf7)",
                      opacity: (updateState === "committing" || selectedLeadKeys.size === 0) ? 0.5 : 1,
                    }}
                  >
                    {updateState === "committing" && <span className="inline-block w-3 h-3 rounded-full" style={{ border: "2px solid var(--border)", borderTopColor: "white", animation: "spin 0.8s linear infinite" }} />}
                    {updateState === "committing"
                      ? "Adding…"
                      : `Add ${selectedLeadKeys.size} selected to ${addCompanyResult.company}`}
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
              <div className="mt-3 p-2 rounded-md text-xs flex items-center gap-3" style={{ background: "var(--bg-card)", border: "1px solid var(--green-border)", color: "var(--green)" }}>
                <span className="flex-1">{updateMessage || "Update complete."}</span>
                {/* Rollback button — visible only when this commit
                    produced inserted RawJobs. Persists until the
                    operator clicks Undo or dismisses the modal; no
                    auto-dismiss timer. Hits /add-company/rollback
                    which deletes the rows + dependents. */}
                {lastCommitRawJobIds.length > 0 && (
                  <button
                    onClick={handleRollback}
                    disabled={rollbackPending}
                    className="text-[11px] font-semibold underline cursor-pointer disabled:cursor-not-allowed disabled:opacity-50"
                    style={{ color: "var(--accent-light)", background: "transparent", border: "none" }}
                    title={`Undo: deletes the ${lastCommitRawJobIds.length} just-imported lead(s) and their dependents`}
                  >
                    {rollbackPending ? "Undoing…" : `Undo (${lastCommitRawJobIds.length})`}
                  </button>
                )}
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
                          {cand.sample_title && cand.sample_url ? (
                            <a
                              href={safeHref(cand.sample_url, "#")}
                              target="_blank"
                              rel="noreferrer"
                              className="hover:underline"
                              style={{ color: "var(--accent-light)" }}
                              title={cand.sample_url}
                            >
                              {cand.sample_title}
                              <span className="ml-1" aria-hidden>↗</span>
                            </a>
                          ) : (
                            cand.sample_title
                          )}
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
