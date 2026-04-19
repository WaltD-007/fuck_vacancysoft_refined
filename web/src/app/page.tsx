"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import Sidebar from "./components/Sidebar";
import { API, fetcher } from "./lib/swr";

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
  categories: Record<string, number>;
  recent_leads: Array<{
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
  // SWR caches the last response in the browser. On navigation back to `/`
  // it paints instantly with cached data then revalidates in the background.
  // Focus revalidation gives us fresh data when the user returns to the tab.
  const { data } = useSWR<Dashboard>("/dashboard", fetcher, {
    revalidateOnFocus: true,
    dedupingInterval: 2000,
    keepPreviousData: true,
  });
  const [feedCategory, setFeedCategory] = useState("");
  const [feedCountry, setFeedCountry] = useState("");
  const [feedSubSpec, setFeedSubSpec] = useState("");
  const [feedEmploymentType, setFeedEmploymentType] = useState("Permanent");
  const [queued, setQueued] = useState<Set<string>>(new Set());
  const [excludedCompanies, setExcludedCompanies] = useState<Set<string>>(new Set());
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

  // Real data only — no fallbacks that pretend to be live numbers.
  const totalLeads = data?.total_jobs ?? 0;
  const scored = data?.total_scored ?? 0;
  const activeSources = data?.active_sources ?? 0;
  const brokenSources = data?.broken_sources ?? 0;
  const cats = data?.categories ?? {};
  const maxCat = Math.max(...Object.values(cats), 1);
  const leads = data?.recent_leads ?? [];
  const health = data?.source_health ?? [];
  const dailyLeads = data?.daily_leads ?? [];
  const maxDaily = Math.max(...dailyLeads, 1);
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

          {/* Main content: Left (chart + feed) | Right (category + health) */}
          <div className="grid gap-4" style={{ gridTemplateColumns: "1fr 1fr" }}>
            {/* Left column */}
            <div className="flex flex-col gap-4">
              {/* Chart */}
              <div className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="flex justify-between items-center mb-4">
                  <div>
                    <div className="text-sm font-semibold">Leads Discovered</div>
                    <div className="text-xs mt-0.5" style={{ color: "#555570" }}>Daily discovery volume across all sources</div>
                  </div>
                  <div className="flex gap-1">
                    {["7D", "30D", "90D"].map((p) => (
                      <span key={p} className="px-2.5 py-1 text-[11px] font-medium rounded-md cursor-pointer"
                        style={p === "30D" ? { background: "rgba(108,92,231,0.15)", color: "#a29bfe", border: "1px solid rgba(108,92,231,0.2)" } : { color: "#555570" }}>
                        {p}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex items-end gap-[3px]" style={{ height: 160, padding: "0 4px" }}>
                  {dailyLeads.length > 0 ? (
                    dailyLeads.map((n, i) => {
                      const pct = maxDaily > 0 ? (n / maxDaily) * 100 : 0;
                      return (
                        <div
                          key={i}
                          className="flex-1 rounded-t-sm cursor-pointer"
                          title={`${n.toLocaleString()} leads`}
                          style={{
                            height: `${Math.max(pct, 1)}%`,
                            background: "linear-gradient(to top, #6c5ce7, #a29bfe)",
                            opacity: 0.6 + (i / Math.max(dailyLeads.length, 1)) * 0.4,
                            minWidth: 4,
                          }}
                        />
                      );
                    })
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-xs" style={{ color: "#555570" }}>
                      No lead activity in the last 30 days
                    </div>
                  )}
                </div>
              </div>
            {/* Live Feed */}
            <div className="rounded-xl overflow-hidden" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
              <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid #1f1f2f" }}>
                <div className="text-sm font-semibold flex items-center gap-2">
                  <span className="w-[7px] h-[7px] rounded-full" style={{ background: "#00d2a0", animation: "pulse 2s infinite" }} />
                  Live Feed
                </div>
                <div className="flex gap-1.5 flex-nowrap">
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
                    No leads in the last 7 days.
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
                                <a href={lead.url || "#"} target="_blank" rel="noreferrer" className="px-2.5 py-1 rounded text-[10px] font-semibold text-white cursor-pointer inline-block" style={{ background: "#00d2a0", textDecoration: "none" }}>&#128196; View Advert</a>
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
                                <button
                                  className="ml-auto px-2 py-1 rounded text-[10px] font-medium cursor-pointer"
                                  style={{ background: "transparent", color: "#555570", border: "1px solid #2a2a3a" }}
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
              {/* Category breakdown */}
              <div className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="text-sm font-semibold mb-1">By Category</div>
                <div className="text-xs mb-4" style={{ color: "#555570" }}>Qualified leads breakdown</div>
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

              {/* Source Health */}
              <div className="rounded-xl overflow-hidden" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid #1f1f2f" }}>
                  <div className="text-sm font-semibold">Source Health</div>
                  <div className="text-xs px-2.5 py-1 rounded-md cursor-pointer" style={{ color: "#555570", border: "1px solid #2a2a3a" }}>Show Failing &#9662;</div>
                </div>
                <div style={{ maxHeight: 360, overflowY: "auto" }}>
                  {health.length === 0 ? (
                    <div className="px-5 py-8 text-center text-xs" style={{ color: "#555570" }}>
                      No recent scrape activity.
                    </div>
                  ) : (
                    health.map((h, i) => (
                      <div key={i} className="px-5 py-3 flex items-center gap-3" style={{ borderBottom: "1px solid #1f1f2f" }}>
                        <div className="w-2 h-2 rounded-full shrink-0" style={{ background: h.status === "success" ? "#00d2a0" : "#ff6b6b" }} />
                        <div className="text-[13px] font-medium flex-1">{h.company}</div>
                        <div className="text-[11px] px-2 py-0.5 rounded" style={{ background: "#0a0a0f", color: "#555570", fontFamily: "'JetBrains Mono', monospace" }}>{h.adapter}</div>
                        <div className="text-xs font-medium min-w-[50px] text-right" style={{ fontFamily: "'JetBrains Mono', monospace" }}>{(h.jobs ?? 0).toLocaleString()}</div>
                        <div className="text-[11px] min-w-[35px] text-right" style={{ color: "#555570" }}>{((h.duration_ms ?? 0) / 1000).toFixed(1)}s</div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>{/* end right column */}
          </div>
        </div>
      </main>
    </div>
  );
}
