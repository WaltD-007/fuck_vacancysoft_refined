"use client";

import { useEffect, useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import Sidebar from "./components/Sidebar";
import { API, fetcher } from "./lib/swr";
import { safeHref } from "./lib/safe";
import { useCurrentUser } from "./lib/useCurrentUser";

type Dashboard = {
  total_scored: number;
  total_jobs: number;
  active_sources: number;
  broken_sources: number;
  leads_today: number;
  leads_yesterday: number;
  avg_score: number;
  avg_score_prev_week: number | null;
  campaigns_active: number;
  dossiers_active: number;
  daily_leads: number[];
  daily_categories: Record<string, number>[];
  categories: Record<string, number>;
  recent_leads: Array<{
    // enriched_job_id — used by the row-level admin buttons (Dead
    // job, Wrong location) to reference the DB row server-side.
    // Null-tolerated defensively; the join filters in
    // get_dashboard guarantee a populated value in practice.
    id: string | null;
    title: string; company: string; location: string | null;
    country: string | null; category: string; sub_specialism: string;
    url: string | null; discovered: string | null;
    score: number | null;
    employment_type: string | null;
  }>;
  source_health: Array<{
    company: string; adapter: string; status: string;
    jobs: number; duration_ms: number;
  }>;
};

// Format delta like "▲ 312 today" | "▼ 42 today" | "— no change" (neutral if 0)
function formatDelta(today: number, yesterday: number): { text: string; color: string } {
  if (today === 0 && yesterday === 0) return { text: "—", color: "#555570" };
  const diff = today - yesterday;
  if (diff === 0) return { text: "no change", color: "#555570" };
  if (diff > 0) return { text: `▲ ${diff.toLocaleString()} vs yesterday`, color: "#00d2a0" };
  return { text: `▼ ${Math.abs(diff).toLocaleString()} vs yesterday`, color: "#ff6b6b" };
}

function formatScoreDelta(now: number, prev: number | null): { text: string; color: string } {
  if (prev === null || prev === 0) return { text: "no baseline", color: "#555570" };
  const diff = +(now - prev).toFixed(1);
  if (diff === 0) return { text: "flat vs last week", color: "#555570" };
  if (diff > 0) return { text: `▲ ${diff.toFixed(1)} vs last week`, color: "#00d2a0" };
  return { text: `▼ ${Math.abs(diff).toFixed(1)} vs last week`, color: "#ff6b6b" };
}

// Simple relative timestamp: "12m ago", "3h ago", "2d ago"
function relativeTime(iso: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (isNaN(then)) return "";
  const diffMs = Date.now() - then;
  const m = Math.max(0, Math.floor(diffMs / 60000));
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

const catColors: Record<string, string> = {
  Risk: "#a29bfe", Quant: "#4dabf7", Compliance: "#00d2a0",
  Audit: "#ffd93d", Cyber: "#ff6b6b", Legal: "#fd79a8", "Front Office": "#ffa500",
};

export default function DashboardPage() {
  // Live-feed filter state. Initial values come from the current
  // user's saved preferences (if any); changes push back to the
  // backend on a 500ms debounce. When the user hook resolves async,
  // a `useEffect` below overwrites the local state once so the
  // operator sees their last selections restore on reload.
  //
  // Falls back to hardcoded defaults when no user exists (backend
  // 401) — so the Dashboard still works on a stack without the
  // users backend deployed, or before `prospero user add` has been
  // run, just without persistence.
  const { user, preferences, updatePreferences } = useCurrentUser();
  const savedFeed = preferences.dashboard_feed ?? {};
  const [feedCategory, setFeedCategoryRaw] = useState<string>(
    savedFeed.category ?? "",
  );
  const [feedCountry, setFeedCountryRaw] = useState<string>(
    savedFeed.country ?? "",
  );
  const [feedSubSpec, setFeedSubSpecRaw] = useState<string>(
    savedFeed.sub_specialism ?? "",
  );
  const [feedEmploymentType, setFeedEmploymentTypeRaw] = useState<string>(
    savedFeed.employment_type ?? "Permanent",
  );
  const [feedRange, setFeedRangeRaw] = useState<string>(
    savedFeed.range ?? "7d",
  );

  // SWR caches the last response in the browser. On navigation back to `/`
  // it paints instantly with cached data then revalidates in the background.
  // Focus revalidation gives us fresh data when the user returns to the tab.
  // Key includes the feed range so 24h / 7d / 30d / all each get their own
  // cache entry — switching ranges paints from cache then revalidates.
  const { data } = useSWR<Dashboard>(
    `/dashboard?recent_window=${feedRange}`,
    fetcher,
    {
      revalidateOnFocus: true,
      dedupingInterval: 2000,
      keepPreviousData: true,
    },
  );

  // When the user resolves async (SWR fetch completes), overwrite the
  // four filter states from their saved preferences. Keyed on
  // `user?.id` so this runs exactly once per user-identity change;
  // subsequent preference updates come via `updatePreferences` and
  // don't need to re-sync local state (we're the source of truth
  // for them during this session).
  useEffect(() => {
    if (!user) return;
    const d = user.preferences.dashboard_feed ?? {};
    if (d.category !== undefined) setFeedCategoryRaw(d.category);
    if (d.country !== undefined) setFeedCountryRaw(d.country);
    if (d.sub_specialism !== undefined) setFeedSubSpecRaw(d.sub_specialism);
    if (d.employment_type !== undefined) setFeedEmploymentTypeRaw(d.employment_type);
    if (d.range !== undefined) setFeedRangeRaw(d.range);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.id]);

  // Every setter wraps its `setXRaw` sibling and fires an
  // `updatePreferences` with the full `dashboard_feed` blob.
  // We rebuild the whole section each time because the backend does
  // a shallow top-level merge — sending partial keys would wipe the
  // others.
  function pushFeedPrefs(next: Partial<{
    category: string;
    country: string;
    sub_specialism: string;
    employment_type: string;
    range: string;
  }>) {
    updatePreferences({
      dashboard_feed: {
        category: next.category ?? feedCategory,
        country: next.country ?? feedCountry,
        sub_specialism: next.sub_specialism ?? feedSubSpec,
        employment_type: next.employment_type ?? feedEmploymentType,
        range: next.range ?? feedRange,
      },
    });
  }

  const setFeedCategory = (v: string) => {
    setFeedCategoryRaw(v);
    pushFeedPrefs({ category: v });
  };
  const setFeedCountry = (v: string) => {
    setFeedCountryRaw(v);
    pushFeedPrefs({ country: v });
  };
  const setFeedSubSpec = (v: string) => {
    setFeedSubSpecRaw(v);
    pushFeedPrefs({ sub_specialism: v });
  };
  const setFeedEmploymentType = (v: string) => {
    setFeedEmploymentTypeRaw(v);
    pushFeedPrefs({ employment_type: v });
  };
  const setFeedRange = (v: string) => {
    setFeedRangeRaw(v);
    pushFeedPrefs({ range: v });
  };
  const [queued, setQueued] = useState<Set<string>>(new Set());
  const [excludedCompanies, setExcludedCompanies] = useState<Set<string>>(new Set());
  // Clicking a bar in "Leads Discovered" selects that day and filters the
  // "By Category" block below to that day's breakdown. null = all-time totals.
  const [selectedDay, setSelectedDay] = useState<number | null>(null);
  // Chart range toggle. Slices the 90-day series returned by the API.
  const [period, setPeriod] = useState<"7D" | "30D" | "90D">("30D");
  // company -> end timestamp ms; non-empty rows are greyed out and
  // become live until the timer expires, at which point we POST.
  const [pendingUndo, setPendingUndo] = useState<Record<string, number>>({});
  const [nowTick, setNowTick] = useState<number>(Date.now());

  // Tick every 200ms while there is at least one pending undo so the
  // countdown digits update. No-op when nothing is pending.
  useEffect(() => {
    if (Object.keys(pendingUndo).length === 0) return;
    const id = setInterval(() => setNowTick(Date.now()), 200);
    return () => clearInterval(id);
  }, [pendingUndo]);

  // Fire expired undos. We compute expired off the latest tick rather
  // than relying on per-key setTimeouts so the cancel path is just
  // "remove the key from pendingUndo".
  useEffect(() => {
    const expired = Object.entries(pendingUndo).filter(([, end]) => nowTick >= end);
    if (expired.length === 0) return;
    expired.forEach(async ([company]) => {
      try {
        const res = await fetch(`${API}/agency`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ company }),
        });
        if (res.ok) {
          setExcludedCompanies((prev) => new Set(prev).add(company));
        }
      } catch {
        // Network error: just drop the pending state, row reappears.
      } finally {
        setPendingUndo((prev) => {
          const next = { ...prev };
          delete next[company];
          return next;
        });
      }
    });
  }, [nowTick, pendingUndo]);

  // Dashboard row-level admin actions (mirrors the Sources drawer's
  // Dead job / Wrong location buttons — same endpoints, same confirm
  // prompts). Both require the lead's enriched_job_id, which the
  // dashboard payload now carries as `lead.id` (backend change
  // 2026-04-21).
  //
  // The pending-delete set greys out a row between the operator's
  // confirm and the server's response, so rapid clicks can't fire
  // the DELETE twice. SWR mutate("/dashboard") revalidates the feed
  // so deleted rows vanish and the "wrong location" auto-apply path
  // reflects the new city/country the next render.
  const { mutate: swrMutate } = useSWRConfig();
  const [deadPending, setDeadPending] = useState<Set<string>>(new Set());

  const handleDeadJob = async (leadId: string | null, title: string) => {
    if (!leadId) return;
    const confirmed = window.confirm(
      `Delete "${title}"? The job is removed from the DB and won't re-enrich on the next scrape.`,
    );
    if (!confirmed) return;
    setDeadPending((prev) => new Set(prev).add(leadId));
    try {
      await fetch(`${API}/leads/${encodeURIComponent(leadId)}`, {
        method: "DELETE",
      });
    } finally {
      // Pull fresh dashboard data so the deleted row leaves the feed.
      void swrMutate((key) => typeof key === "string" && key.startsWith("/dashboard"));
      setDeadPending((prev) => {
        const next = new Set(prev);
        next.delete(leadId);
        return next;
      });
    }
  };

  const handleWrongLocation = async (leadId: string | null, title: string, currentLocation: string | null) => {
    if (!leadId) return;
    const note = window.prompt(
      `Correct location for "${title}" (currently ${currentLocation ?? "—"}).\n\n` +
        `If you type a real location (e.g. "Buffalo, NY, USA" or "London, UK") it will be applied immediately. ` +
        `Leave blank or type free text to just flag for manual review.`,
      "",
    );
    if (note === null) return; // operator cancelled
    try {
      const res = await fetch(
        `${API}/leads/${encodeURIComponent(leadId)}/flag-location`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        },
      );
      if (res.ok) {
        const body = await res.json().catch(() => null);
        if (body?.status === "applied") {
          window.alert(`Location updated to ${body.city}, ${body.country}.`);
        } else if (body?.status === "queued") {
          window.alert("Flagged for manual review — location was left unchanged.");
        }
      }
    } finally {
      // Re-fetch so the new city/country shows on the next render
      // in the "applied" case, or so a re-flag isn't shown as
      // stale in the queued case.
      void swrMutate((key) => typeof key === "string" && key.startsWith("/dashboard"));
    }
  };

  // Real data only — no fallbacks that pretend to be live numbers.
  const totalLeads = data?.total_jobs ?? 0;
  const scored = data?.total_scored ?? 0;
  const activeSources = data?.active_sources ?? 0;
  const brokenSources = data?.broken_sources ?? 0;
  const allTimeCats = data?.categories ?? {};
  const leads = data?.recent_leads ?? [];
  // Full 90-day series from the API; slice client-side based on the chart toggle.
  const fullDailyLeads = data?.daily_leads ?? [];
  const fullDailyCategories = data?.daily_categories ?? [];
  const periodDays = period === "7D" ? 7 : period === "30D" ? 30 : 90;
  const dailyLeads = fullDailyLeads.slice(-periodDays);
  const dailyCategories = fullDailyCategories.slice(-periodDays);
  const maxDaily = Math.max(...dailyLeads, 1);
  // Displayed category breakdown — full-period totals by default, or a single
  // day's counts when the user clicks a bar in "Leads Discovered".
  const cats: Record<string, number> =
    selectedDay !== null && dailyCategories[selectedDay]
      ? dailyCategories[selectedDay]
      : allTimeCats;
  const maxCat = Math.max(...Object.values(cats), 1);
  const leadsTodayDelta = data ? formatDelta(data.leads_today, data.leads_yesterday) : { text: "—", color: "#555570" };
  const avgScore = data?.avg_score ?? 0;
  const avgScoreDelta = data ? formatScoreDelta(data.avg_score, data.avg_score_prev_week) : { text: "—", color: "#555570" };
  const campaignsActive = data?.campaigns_active ?? 0;
  const dossiersActive = data?.dossiers_active ?? 0;
  const leadsToday = data?.leads_today ?? 0;

  return (
    <div className="min-h-screen" style={{ background: "#0a0a0f", color: "#e8e8f0", fontFamily: "'Inter', -apple-system, sans-serif" }}>
      <Sidebar />
      <main className="ml-60">
        {/* Topbar */}
        <div className="flex items-center justify-between px-8 h-14" style={{ background: "rgba(10,10,15,0.8)", backdropFilter: "blur(20px)", borderBottom: "1px solid #1f1f2f" }}>
          <div className="font-bold text-base">Dashboard</div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#555570", minWidth: 240 }}>
              <span style={{ fontSize: 14 }}>&#128269;</span>
              Search leads, sources, campaigns...
              <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded" style={{ background: "#1e1e2a", border: "1px solid #2a2a3a", color: "#555570" }}>&#8984;K</span>
            </div>
            <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#8888a0" }}>&#128276;</div>
            <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#8888a0" }}>&#9881;</div>
          </div>
        </div>

        <div className="p-7">
          {/* Stat cards */}
          <div className="grid grid-cols-5 gap-4 mb-6">
            {[
              { label: "TOTAL LEADS", value: totalLeads.toLocaleString(), color: "#e8e8f0", sub: `${leadsToday.toLocaleString()} added last 24h`, subColor: leadsToday > 0 ? "#00d2a0" : "#555570" },
              { label: "SCORED & QUALIFIED", value: scored.toLocaleString(), color: "#a29bfe", sub: leadsTodayDelta.text, subColor: leadsTodayDelta.color },
              { label: "SOURCES ACTIVE", value: activeSources.toLocaleString(), color: "#00d2a0", sub: `${brokenSources.toLocaleString()} failing`, subColor: brokenSources > 0 ? "#ff6b6b" : "#555570" },
              { label: "CAMPAIGNS ACTIVE", value: campaignsActive.toLocaleString(), color: "#ffd93d", sub: `${dossiersActive.toLocaleString()} dossier${dossiersActive === 1 ? "" : "s"}`, subColor: "#555570" },
              { label: "AVG SCORE", value: avgScore.toFixed(1), color: "#e8e8f0", sub: avgScoreDelta.text, subColor: avgScoreDelta.color },
            ].map((s) => (
              <div key={s.label} className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="text-[11px] font-medium uppercase tracking-wider mb-2" style={{ color: "#555570", letterSpacing: "0.8px" }}>{s.label}</div>
                <div className="text-[28px] font-extrabold tracking-tight" style={{ color: s.color }}>{s.value}</div>
                <div className="text-xs mt-1.5" style={{ color: s.subColor }}>{s.sub}</div>
              </div>
            ))}
          </div>

          {/* Main content: Left (feed only) | Right (chart + category) */}
          <div className="grid gap-4" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {/* Left column */}
            <div className="flex flex-col gap-4">
            {/* Live Feed */}
            <div className="rounded-xl overflow-hidden" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
              <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid #1f1f2f" }}>
                <div className="text-sm font-semibold flex items-center gap-2">
                  <span className="w-[7px] h-[7px] rounded-full" style={{ background: "#00d2a0", animation: "pulse 2s infinite" }} />
                  Live Feed
                </div>
                <div className="flex gap-1.5 flex-nowrap">
                  <select value={feedRange} onChange={(e) => setFeedRange(e.target.value)} className="text-[11px] px-1.5 py-1 rounded-md cursor-pointer outline-none" style={{ background: "#16161f", color: "#e8e8f0", border: "1px solid #2a2a3a" }}>
                    <option value="24h">Last 24h</option>
                    <option value="7d">Last 7 days</option>
                    <option value="30d">Last 30 days</option>
                    <option value="all">All time</option>
                  </select>
                  <select value={feedCountry} onChange={(e) => setFeedCountry(e.target.value)} className="text-[11px] px-1.5 py-1 rounded-md cursor-pointer outline-none max-w-[110px]" style={{ background: "#16161f", color: feedCountry ? "#e8e8f0" : "#555570", border: "1px solid #2a2a3a" }}>
                    <option value="">Locations</option>
                    {(() => { const c = new Set<string>(); (data?.recent_leads || []).forEach(l => { if (l.country) c.add(l.country); }); return Array.from(c).sort().map(co => <option key={co} value={co}>{co}</option>); })()}
                  </select>
                  <select value={feedEmploymentType} onChange={(e) => setFeedEmploymentType(e.target.value)} className="text-[11px] px-1.5 py-1 rounded-md cursor-pointer outline-none" style={{ background: "#16161f", color: "#e8e8f0", border: "1px solid #2a2a3a" }}>
                    <option value="Permanent">Permanent</option>
                    <option value="Contract">Contract</option>
                  </select>
                  <select value={feedCategory} onChange={(e) => { setFeedCategory(e.target.value); setFeedSubSpec(""); }} className="text-[11px] px-1.5 py-1 rounded-md cursor-pointer outline-none max-w-[110px]" style={{ background: "#16161f", color: feedCategory ? "#e8e8f0" : "#555570", border: "1px solid #2a2a3a" }}>
                    <option value="">Categories</option>
                    {["Risk", "Quant", "Compliance", "Audit", "Cyber", "Legal", "Front Office"].map(c => <option key={c} value={c}>{c}</option>)}
                  </select>
                  <select value={feedSubSpec} onChange={(e) => setFeedSubSpec(e.target.value)} className="text-[11px] px-1.5 py-1 rounded-md cursor-pointer outline-none max-w-[130px]" style={{ background: "#16161f", color: feedSubSpec ? "#e8e8f0" : "#555570", border: "1px solid #2a2a3a" }}>
                    <option value="">Specialisms</option>
                    {(() => { const subs = new Set<string>(); (data?.recent_leads || []).filter(l => !feedCategory || l.category === feedCategory).forEach(l => { if (l.sub_specialism) subs.add(l.sub_specialism); }); return Array.from(subs).sort().map(s => <option key={s} value={s}>{s}</option>); })()}
                  </select>
                </div>
              </div>
              <div style={{ maxHeight: "calc(100vh - 200px)", overflowY: "auto" }}>
                {leads.length === 0 ? (
                  <div className="px-5 py-8 text-center text-xs" style={{ color: "#555570" }}>
                    No leads {feedRange === "24h" ? "in the last 24 hours" : feedRange === "30d" ? "in the last 30 days" : feedRange === "all" ? "yet" : "in the last 7 days"}.
                  </div>
                ) : (
                  leads
                    .filter((lead) => !feedCategory || lead.category === feedCategory)
                    .filter((lead) => !feedSubSpec || lead.sub_specialism === feedSubSpec)
                    .filter((lead) => !feedCountry || lead.country === feedCountry)
                    .filter((lead) => !feedEmploymentType || lead.employment_type === feedEmploymentType)
                    .filter((lead) => !excludedCompanies.has(lead.company))
                    .map((lead, i) => {
                      const score = lead.score;
                      const scoreColor = score === null ? "#555570" : score >= 8 ? "#00d2a0" : score >= 6 ? "#ffd93d" : "#ff6b6b";
                      const scoreBg = score === null ? "rgba(85,85,112,0.08)" : score >= 8 ? "rgba(0,210,160,0.08)" : score >= 6 ? "rgba(255,217,61,0.08)" : "rgba(255,107,107,0.08)";
                      const scoreBorder = score === null ? "rgba(85,85,112,0.2)" : score >= 8 ? "rgba(0,210,160,0.2)" : score >= 6 ? "rgba(255,217,61,0.2)" : "rgba(255,107,107,0.2)";
                      const undoEnd = pendingUndo[lead.company];
                      const isPending = undoEnd !== undefined;
                      const remainingSec = isPending ? Math.max(0, Math.ceil((undoEnd - nowTick) / 1000)) : 0;
                      return (
                        <div key={`${lead.url || lead.title}-${i}`} className="px-5 py-3.5" style={{ borderBottom: "1px solid #1f1f2f", opacity: isPending ? 0.45 : 1, transition: "opacity 150ms" }}>
                          <div className="flex justify-between items-start mb-1">
                            <div className="text-[13.5px] font-semibold">{lead.title} — {lead.company}</div>
                            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-md shrink-0 ml-3" style={{
                              background: scoreBg,
                              color: scoreColor,
                              border: `1px solid ${scoreBorder}`,
                              fontFamily: "'JetBrains Mono', monospace",
                            }}>{score === null ? "—" : score.toFixed(1)}</span>
                          </div>
                          <div className="flex items-center gap-3 text-xs" style={{ color: "#555570" }}>
                            <span>{lead.company}</span>
                            {(lead.location || lead.country) && <>
                              <span>&middot;</span>
                              <span>{[lead.location, lead.country].filter(Boolean).join(", ")}</span>
                            </>}
                            <span>&middot;</span>
                            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase tracking-wide" style={{
                              background: `${catColors[lead.category] || "#a29bfe"}15`,
                              color: catColors[lead.category] || "#a29bfe",
                            }}>{lead.category}</span>
                            <span className="ml-auto">{relativeTime(lead.discovered)}</span>
                          </div>
                          <div className="flex gap-2 mt-2 items-center">
                            {isPending ? (
                              <>
                                <span className="text-[11px]" style={{ color: "#ff9f6b" }}>Marked as agency</span>
                                <button
                                  className="px-2.5 py-1 rounded text-[10px] font-semibold cursor-pointer"
                                  style={{ background: "rgba(255,159,107,0.1)", color: "#ff9f6b", border: "1px solid rgba(255,159,107,0.3)" }}
                                  onClick={() => {
                                    setPendingUndo((prev) => {
                                      const next = { ...prev };
                                      delete next[lead.company];
                                      return next;
                                    });
                                  }}
                                >undo ({remainingSec}s)</button>
                              </>
                            ) : (
                              <>
                                <a href={safeHref(lead.url, "#")} target="_blank" rel="noreferrer" className="px-2.5 py-1 rounded text-[10px] font-semibold text-white cursor-pointer inline-block" style={{ background: "#00d2a0", textDecoration: "none" }}>&#128196; View Advert</a>
                                {queued.has(lead.url || lead.title) ? (
                                  <span className="px-2.5 py-1 rounded text-[10px] font-semibold" style={{ background: "rgba(0,210,160,0.08)", color: "#00d2a0", border: "1px solid rgba(0,210,160,0.2)" }}>&#10003; Added</span>
                                ) : (
                                  <button
                                    className="px-2.5 py-1 rounded text-[10px] font-semibold text-white cursor-pointer"
                                    style={{ background: "linear-gradient(135deg, #6c5ce7, #8b7cf7)" }}
                                    onClick={async () => {
                                      const key = lead.url || lead.title;
                                      await fetch(`${API}/queue`, {
                                        method: "POST",
                                        headers: { "Content-Type": "application/json" },
                                        body: JSON.stringify({
                                          title: lead.title, company: lead.company,
                                          location: lead.location, country: lead.country,
                                          category: lead.category, sub_specialism: lead.sub_specialism,
                                          url: lead.url, score: lead.score,
                                        }),
                                      });
                                      setQueued((prev) => new Set(prev).add(key));
                                    }}
                                  >+ Add Lead</button>
                                )}
                                {/* Row-level admin cluster, floated
                                    right via `ml-auto` on the first
                                    button. Three coloured buttons:
                                    Dead job (blue, one-job scope),
                                    Wrong loc (amber, flag-or-apply),
                                    agy job (red, company-scope —
                                    matches the Sources drawer's red
                                    Agy styling so the most destructive
                                    action reads as such. 5-sec undo
                                    flow stays as the safety net. */}
                                <button
                                  className="ml-auto px-2 py-1 rounded text-[10px] font-semibold cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                                  style={{ background: "rgba(77,171,247,0.08)", color: "#4dabf7", border: "1px solid rgba(77,171,247,0.25)" }}
                                  title="Delete this job and stop it from re-enriching"
                                  disabled={!lead.id || (lead.id != null && deadPending.has(lead.id))}
                                  onClick={() => void handleDeadJob(lead.id, lead.title)}
                                >Dead job</button>
                                <button
                                  className="px-2 py-1 rounded text-[10px] font-semibold cursor-pointer disabled:cursor-not-allowed disabled:opacity-40"
                                  style={{ background: "rgba(255,179,64,0.08)", color: "#ffd93d", border: "1px solid rgba(255,179,64,0.25)" }}
                                  title="Flag this location as wrong — type the correct one to auto-apply"
                                  disabled={!lead.id}
                                  onClick={() => void handleWrongLocation(lead.id, lead.title, lead.location)}
                                >Wrong loc</button>
                                <button
                                  className="px-2 py-1 rounded text-[10px] font-semibold cursor-pointer"
                                  style={{ background: "rgba(255,107,107,0.08)", color: "#ff6b6b", border: "1px solid rgba(255,107,107,0.25)" }}
                                  title="Mark this company as a recruitment agency"
                                  onClick={() => {
                                    setPendingUndo((prev) => ({ ...prev, [lead.company]: Date.now() + 5000 }));
                                  }}
                                >agy job</button>
                              </>
                            )}
                          </div>
                        </div>
                      );
                    })
                )}
              </div>
            </div>
            </div>{/* end left column */}

            {/* Right column */}
            <div className="flex flex-col gap-4">
              {/* Chart */}
              <div className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="flex justify-between items-center mb-4">
                  <div>
                    <div className="text-sm font-semibold">Leads Discovered</div>
                    <div className="text-xs mt-0.5" style={{ color: "#555570" }}>Daily discovery volume across all sources</div>
                  </div>
                  <div className="flex gap-1">
                    {(["7D", "30D", "90D"] as const).map((p) => (
                      <button
                        key={p}
                        className="px-2.5 py-1 text-[11px] font-medium rounded-md cursor-pointer"
                        onClick={() => { setPeriod(p); setSelectedDay(null); }}
                        style={p === period
                          ? { background: "rgba(108,92,231,0.15)", color: "#a29bfe", border: "1px solid rgba(108,92,231,0.2)" }
                          : { color: "#555570", background: "transparent", border: "1px solid transparent" }}
                      >
                        {p}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="relative" style={{ height: 160, padding: "4px 6px 0" }}>
                  {dailyLeads.length > 0 ? (
                    (() => {
                      const n = dailyLeads.length;
                      // SVG viewBox is (n-1) wide × 100 tall so each data point sits at
                      // integer x-coords. Stroke stays uniform via non-scaling-stroke.
                      const vbWidth = Math.max(n - 1, 1);
                      const pointsArr = dailyLeads.map((v, i) => {
                        const x = n === 1 ? vbWidth / 2 : i;
                        const y = 100 - (maxDaily > 0 ? (v / maxDaily) * 95 : 0) - 2; // 2px breathing room
                        return { x, y };
                      });
                      const linePath = pointsArr
                        .map((p, i) => `${i === 0 ? "M" : "L"}${p.x.toFixed(3)},${p.y.toFixed(3)}`)
                        .join(" ");
                      const areaPath = `${linePath} L${pointsArr[pointsArr.length - 1].x.toFixed(3)},100 L${pointsArr[0].x.toFixed(3)},100 Z`;
                      return (
                        <>
                          <svg
                            viewBox={`0 0 ${vbWidth} 100`}
                            preserveAspectRatio="none"
                            className="absolute inset-0 w-full h-full"
                            style={{ pointerEvents: "none" }}
                          >
                            <defs>
                              <linearGradient id="leadsAreaFill" x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor="#00d2a0" stopOpacity="0.35" />
                                <stop offset="100%" stopColor="#00d2a0" stopOpacity="0" />
                              </linearGradient>
                            </defs>
                            <path d={areaPath} fill="url(#leadsAreaFill)" />
                            <path
                              d={linePath}
                              fill="none"
                              stroke="#00d2a0"
                              strokeWidth={2}
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              vectorEffect="non-scaling-stroke"
                            />
                          </svg>
                          {/* Click targets — absolutely positioned circles over each data point */}
                          {dailyLeads.map((v, i) => {
                            const isSelected = selectedDay === i;
                            const daysAgo = n - 1 - i;
                            const dateLabel = daysAgo === 0 ? "today" : daysAgo === 1 ? "yesterday" : `${daysAgo}d ago`;
                            const leftPct = n === 1 ? 50 : (i / (n - 1)) * 100;
                            const topPct = 100 - (maxDaily > 0 ? (v / maxDaily) * 95 : 0) - 2;
                            return (
                              <button
                                key={i}
                                type="button"
                                title={`${v.toLocaleString()} leads — ${dateLabel}${isSelected ? " (selected)" : ""}`}
                                onClick={() => setSelectedDay(isSelected ? null : i)}
                                className="absolute cursor-pointer"
                                style={{
                                  left: `calc(${leftPct}% - 7px)`,
                                  top: `calc(${topPct}% - 7px)`,
                                  width: 14,
                                  height: 14,
                                  borderRadius: "50%",
                                  background: isSelected ? "#00d2a0" : "transparent",
                                  border: isSelected ? "2px solid #5dfaca" : "2px solid transparent",
                                  transition: "background 120ms, border 120ms, transform 120ms",
                                  padding: 0,
                                  transform: isSelected ? "scale(1.15)" : "scale(1)",
                                }}
                                onMouseEnter={(e) => {
                                  if (!isSelected) {
                                    e.currentTarget.style.background = "rgba(0,210,160,0.35)";
                                    e.currentTarget.style.border = "2px solid rgba(0,210,160,0.6)";
                                  }
                                }}
                                onMouseLeave={(e) => {
                                  if (!isSelected) {
                                    e.currentTarget.style.background = "transparent";
                                    e.currentTarget.style.border = "2px solid transparent";
                                  }
                                }}
                              />
                            );
                          })}
                        </>
                      );
                    })()
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-xs" style={{ color: "#555570" }}>
                      No lead activity in the last 30 days
                    </div>
                  )}
                </div>
              </div>

              {/* Category breakdown */}
              <div className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="flex items-center justify-between mb-1">
                  <div className="text-sm font-semibold">By Category</div>
                  {selectedDay !== null && (
                    <button
                      className="text-[11px] px-2 py-0.5 rounded cursor-pointer"
                      style={{ background: "rgba(0,210,160,0.08)", color: "#00d2a0", border: "1px solid rgba(0,210,160,0.25)" }}
                      onClick={() => setSelectedDay(null)}
                    >Clear day filter</button>
                  )}
                </div>
                <div className="text-xs mb-4" style={{ color: "#555570" }}>
                  {(() => {
                    if (selectedDay === null) return "Qualified leads breakdown — all time";
                    const daysAgo = dailyLeads.length - 1 - selectedDay;
                    const dateLabel = daysAgo === 0 ? "today" : daysAgo === 1 ? "yesterday" : `${daysAgo} days ago`;
                    const dayTotal = dailyLeads[selectedDay] || 0;
                    return `${dayTotal.toLocaleString()} leads ${dateLabel} — click the bar again to clear`;
                  })()}
                </div>
                <div className="flex flex-col gap-2.5">
                  {["Risk", "Quant", "Compliance", "Audit", "Cyber", "Legal", "Front Office"].map((cat) => {
                    const count = cats[cat] || 0;
                    const pct = maxCat > 0 ? (count / maxCat) * 100 : 0;
                    return (
                      <div key={cat} className="flex items-center gap-3 px-3 py-2.5 rounded-lg" style={{ background: "#0a0a0f", border: "1px solid #1f1f2f" }}>
                        <div className="w-2.5 h-2.5 rounded-sm shrink-0" style={{ background: catColors[cat] }} />
                        <div className="text-[13px] font-medium flex-1">{cat}</div>
                        <div className="text-[13px] font-medium mr-2" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{count.toLocaleString()}</div>
                        <div className="w-20 h-1 rounded-full" style={{ background: "#1e1e2a" }}>
                          <div className="h-full rounded-full" style={{ width: `${pct}%`, background: catColors[cat] }} />
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

            </div>{/* end right column */}
          </div>

          {/* Recently deleted panel — operators can spot-check the auto-sweep
              and undo false positives. Default-collapsed so it doesn't dominate
              the dashboard; collapsed state survives a refresh via localStorage. */}
          <RecentlyDeletedPanel />
        </div>
      </main>
    </div>
  );
}


// ── Recently deleted Dashboard panel ────────────────────────────────
//
// Lists RawJobs marked dead by the auto-sweep within the last 7 days.
// Powers the "spot-check + undo" loop for the auto-mark-dead feature.
//
// Default-collapsed; expands to show up to 200 most-recent deletions
// with an Undelete button per row. Each undelete optimistically
// removes the row from the panel and triggers a /dashboard revalidate
// so counts pick the lead back up within the 30s server cache window.

type RecentlyDeletedItem = {
  raw_job_id: string;
  enriched_job_id: string | null;
  title: string;
  employer: string;
  discovered_url: string;
  deleted_at_source_at: string | null;
  source_id: number;
  source_run_id: string;
  adapter_name: string;
};

type RecentlyDeletedResponse = {
  items: RecentlyDeletedItem[];
  days: number;
  count: number;
};

function RecentlyDeletedPanel() {
  // Collapsed by default; remember user's preference across refreshes.
  const [expanded, setExpanded] = useState<boolean>(false);
  useEffect(() => {
    try {
      const v = localStorage.getItem("recently_deleted_expanded");
      if (v === "true") setExpanded(true);
    } catch {
      // localStorage may be unavailable (private browsing, SSR-like
      // environments). Fail closed — stay collapsed.
    }
  }, []);
  const toggle = () => {
    const next = !expanded;
    setExpanded(next);
    try {
      localStorage.setItem("recently_deleted_expanded", String(next));
    } catch {
      // ignore storage errors
    }
  };

  const { data, mutate: mutateRecent } = useSWR<RecentlyDeletedResponse>(
    "/leads/recently-deleted?days=7&limit=200",
    fetcher,
    { revalidateOnFocus: true, dedupingInterval: 5000, keepPreviousData: true }
  );
  const { mutate: globalMutate } = useSWRConfig();

  const items = data?.items || [];
  const count = data?.count ?? 0;

  // Track per-row undelete state so the button shows feedback while
  // the request is in flight (and locks out double-clicks).
  const [pending, setPending] = useState<Set<string>>(new Set());

  async function handleUndelete(item: RecentlyDeletedItem) {
    if (!item.enriched_job_id) {
      // Recently-deleted RawJobs without an EnrichedJob can't be
      // undeleted via the API endpoint (which is keyed on
      // enriched_job_id). These are rare — usually pre-enrichment
      // dead-marks. CLI fallback: `prospero db undelete-job <id>`.
      alert(
        "This row has no enriched_job_id (rare). Use:\n  prospero db undelete-job " +
          item.raw_job_id
      );
      return;
    }
    setPending((p) => new Set(p).add(item.raw_job_id));
    try {
      const res = await fetch(
        `${API}/leads/${encodeURIComponent(item.enriched_job_id)}/undelete`,
        { method: "POST" }
      );
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      }
      // Optimistic removal from panel; SWR will reconcile on next fetch.
      await mutateRecent(
        (current) =>
          current
            ? {
                ...current,
                items: current.items.filter((it) => it.raw_job_id !== item.raw_job_id),
                count: Math.max(0, current.count - 1),
              }
            : current,
        { revalidate: false }
      );
      // Drop dashboard cache so counts reflect the restoration.
      globalMutate((key) => typeof key === "string" && key.startsWith("/dashboard"));
      globalMutate("/sources");
    } catch (e) {
      alert(`Undelete failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPending((p) => {
        const next = new Set(p);
        next.delete(item.raw_job_id);
        return next;
      });
    }
  }

  return (
    <div
      className="mt-6 rounded-lg"
      style={{ background: "#11111c", border: "1px solid #1f1f2f" }}
    >
      <button
        type="button"
        onClick={toggle}
        className="w-full flex items-center gap-3 px-4 py-3 text-left"
        style={{ background: "transparent", color: "#e5e7eb" }}
      >
        <span style={{ fontSize: 13, opacity: 0.7 }}>{expanded ? "▾" : "▸"}</span>
        <span className="text-[13px] font-medium">Recently deleted</span>
        <span className="text-[11px]" style={{ color: "#7a7a8a" }}>
          (last 7 days)
        </span>
        <span
          className="ml-auto text-[12px] px-2 py-0.5 rounded"
          style={{
            background: count > 0 ? "#2a1f1f" : "#1a1a26",
            color: count > 0 ? "#ff6b6b" : "#7a7a8a",
            fontFamily: "'JetBrains Mono', monospace",
          }}
        >
          {count}
        </span>
      </button>
      {expanded && (
        <div className="px-4 pb-4">
          {count === 0 ? (
            <div
              className="text-[12px] py-3"
              style={{ color: "#7a7a8a" }}
            >
              No jobs marked dead in the last 7 days.
            </div>
          ) : (
            <div className="flex flex-col gap-2 max-h-[420px] overflow-y-auto">
              {items.map((item) => {
                const isPending = pending.has(item.raw_job_id);
                return (
                  <div
                    key={item.raw_job_id}
                    className="flex items-center gap-3 px-3 py-2.5 rounded"
                    style={{ background: "#0a0a0f", border: "1px solid #1f1f2f" }}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-[13px] font-medium truncate">
                        {item.employer || "(unknown employer)"} •{" "}
                        <span style={{ color: "#aaa" }}>{item.title || "(no title)"}</span>
                      </div>
                      <div
                        className="text-[11px] mt-0.5"
                        style={{ color: "#7a7a8a" }}
                      >
                        Marked dead {relativeTime(item.deleted_at_source_at)} via{" "}
                        {item.adapter_name || "?"} scrape
                        {item.discovered_url && (
                          <>
                            {" · "}
                            <a
                              href={safeHref(item.discovered_url) ?? "#"}
                              target="_blank"
                              rel="noreferrer"
                              style={{ color: "#7a7a8a", textDecoration: "underline" }}
                            >
                              link
                            </a>
                          </>
                        )}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleUndelete(item)}
                      disabled={isPending}
                      className="text-[12px] px-3 py-1 rounded"
                      style={{
                        background: isPending ? "#1a1a26" : "#1f1f2f",
                        color: isPending ? "#7a7a8a" : "#e5e7eb",
                        border: "1px solid #2a2a3a",
                        cursor: isPending ? "default" : "pointer",
                      }}
                      title="Restore this job (clears is_deleted_at_source on the underlying RawJob)"
                    >
                      {isPending ? "…" : "↺ Undelete"}
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
