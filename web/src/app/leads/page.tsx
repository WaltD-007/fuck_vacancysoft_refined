"use client";
import React, { useEffect, useRef, useState } from "react";
import useSWR, { mutate as globalMutate } from "swr";
import Sidebar from "../components/Sidebar";
import { API, fetcher } from "../lib/swr";

const catColors: Record<string, string> = { Risk: "#a29bfe", Quant: "#4dabf7", Compliance: "#00d2a0", Audit: "#ffd93d", Cyber: "#ff6b6b", Legal: "#fd79a8", "Quant Risk": "#4dabf7", "Front Office": "#ffa500" };

type QueuedLead = {
  id: string;
  status: string;
  title: string;
  company: string;
  location: string | null;
  country: string | null;
  category: string | null;
  sub_specialism: string | null;
  url: string | null;
  score: number | null;
  board_url: string | null;
  created_at: string | null;
  // Inlined by /api/queue so the Leads page can render the intelligence
  // panel without issuing an N+1 /dossier request per row.
  dossier: Dossier | null;
};

type Dossier = {
  id: string;
  category: string;
  model: string;
  tokens: number;
  latency_ms: number;
  lead_score: number | null;
  lead_score_justification: string | null;
  company_context: string | null;
  core_problem: string | null;
  stated_vs_actual: Array<{ jd_asks_for: string; business_likely_needs: string }>;
  spec_risk: Array<{ risk: string; severity: string; explanation?: string }>;
  candidate_profiles: Array<{ label: string; background: string; fit_reason: string; outcomes?: string }>;
  search_booleans: Record<string, string>;
  hiring_managers: Array<{ name: string; title: string; confidence: string; reasoning?: string }>;
};

const statusStyles: Record<string, { bg: string; color: string; border: string; label: string }> = {
  pending: { bg: "rgba(108,92,231,0.15)", color: "#a29bfe", border: "rgba(108,92,231,0.2)", label: "\u25F7 Queued" },
  generating: { bg: "rgba(255,217,61,0.08)", color: "#ffd93d", border: "rgba(255,217,61,0.2)", label: "\u21BB Generating" },
  ready: { bg: "rgba(0,210,160,0.08)", color: "#00d2a0", border: "rgba(0,210,160,0.2)", label: "\u2713 Ready" },
  live: { bg: "rgba(77,171,247,0.08)", color: "#4dabf7", border: "rgba(77,171,247,0.2)", label: "Live" },
};

const riskBadgeStyles: Record<string, { bg: string; color: string; border: string }> = {
  high: { bg: "rgba(255,107,107,0.08)", color: "#ff6b6b", border: "rgba(255,107,107,0.2)" },
  medium: { bg: "rgba(255,217,61,0.08)", color: "#ffd93d", border: "rgba(255,217,61,0.2)" },
  med: { bg: "rgba(255,217,61,0.08)", color: "#ffd93d", border: "rgba(255,217,61,0.2)" },
  low: { bg: "rgba(0,210,160,0.08)", color: "#00d2a0", border: "rgba(0,210,160,0.2)" },
};

function timeAgo(iso: string | null): string {
  if (!iso) return "";
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function Linkify({ text }: { text: string }) {
  const urlRegex = /(https?:\/\/[^\s),\]]+)/g;
  const parts = text.split(urlRegex);
  return (
    <>
      {parts.map((part, i) =>
        urlRegex.test(part) ? (
          <a key={i} href={part} target="_blank" rel="noreferrer" style={{ color: "#a29bfe", textDecoration: "none" }} onMouseOver={(e) => (e.currentTarget.style.textDecoration = "underline")} onMouseOut={(e) => (e.currentTarget.style.textDecoration = "none")}>{part}</a>
        ) : (
          <React.Fragment key={i}>{part}</React.Fragment>
        )
      )}
    </>
  );
}

function DossierPanel({ dossier, onCreateCampaign, jobUrl, company, leadId }: { dossier: Dossier; onCreateCampaign: () => void; jobUrl?: string | null; company?: string; leadId: string }) {
  const scoreColor = (dossier.lead_score || 0) >= 4 ? "#00d2a0" : (dossier.lead_score || 0) >= 3 ? "#ffd93d" : "#ff6b6b";

  return (
    <div style={{ background: "#0a0a0f", border: "1px solid #2a2a3a", borderRadius: 8, margin: "8px 12px 12px", padding: 20, animation: "fadeIn 0.25s ease-out" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, paddingBottom: 12, borderBottom: "1px solid #1f1f2f" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{ fontSize: 15, fontWeight: 700 }}>Intelligence Report</div>
          <span style={{ fontSize: 10, fontWeight: 600, padding: "2px 8px", borderRadius: 4, background: "rgba(108,92,231,0.15)", color: "#a29bfe", border: "1px solid rgba(108,92,231,0.2)" }}>AI Generated</span>
          <span style={{ fontSize: 10, color: "#555570" }}>{dossier.model} | {dossier.tokens} tokens | {(dossier.latency_ms / 1000).toFixed(1)}s</span>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {jobUrl && <button onClick={() => window.open(jobUrl, "_blank")} style={{ padding: "6px 14px", fontSize: 11, fontWeight: 600, background: "rgba(77,171,247,0.08)", border: "1px solid rgba(77,171,247,0.2)", borderRadius: 6, color: "#4dabf7", cursor: "pointer", transition: "all 0.15s" }} onMouseOver={(e) => { e.currentTarget.style.background = "rgba(77,171,247,0.15)"; e.currentTarget.style.borderColor = "rgba(77,171,247,0.4)"; }} onMouseOut={(e) => { e.currentTarget.style.background = "rgba(77,171,247,0.08)"; e.currentTarget.style.borderColor = "rgba(77,171,247,0.2)"; }}>&#128209; View Advert</button>}
          <button style={{ padding: "6px 14px", fontSize: 11, fontWeight: 600, background: "rgba(0,210,160,0.08)", border: "1px solid rgba(0,210,160,0.2)", borderRadius: 6, color: "#00d2a0", cursor: "pointer", transition: "all 0.15s" }} onMouseOver={(e) => { e.currentTarget.style.background = "rgba(0,210,160,0.15)"; e.currentTarget.style.borderColor = "rgba(0,210,160,0.4)"; }} onMouseOut={(e) => { e.currentTarget.style.background = "rgba(0,210,160,0.08)"; e.currentTarget.style.borderColor = "rgba(0,210,160,0.2)"; }}>&#128196; Export PDF</button>
          <button onClick={onCreateCampaign} style={{ padding: "5px 12px", fontSize: 11, background: "linear-gradient(135deg, #6c5ce7, #8b7cf7)", border: "none", borderRadius: 6, color: "white", cursor: "pointer", fontWeight: 600 }}>&#9993; Create Campaign</button>
        </div>
      </div>

      {/* Top grid: Company Context + Stated vs Actual */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignItems: "start" }}>
        <div style={{ background: "#16161f", border: "1px solid #1f1f2f", borderRadius: 8, padding: 14, maxHeight: 550, overflowY: "auto" }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 8 }}>Company &amp; Market Context</div>
          <div style={{ fontSize: 12, lineHeight: 1.8, color: "#8888a0" }}>
            {(dossier.company_context || "").split(/\n+|(?<=\.)\s{2,}|(?<=\.)\s(?=[A-Z])/).filter(Boolean).map((para, i) => (
              <p key={i} style={{ marginBottom: 10 }}><Linkify text={para.trim()} /></p>
            ))}
          </div>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 8, marginTop: 16 }}>The Core Business Problem</div>
          <div style={{ fontSize: 12, lineHeight: 1.8, color: "#8888a0" }}>
            {(dossier.core_problem || "").split(/\n+|(?<=\.)\s{2,}|(?<=\.)\s(?=[A-Z])/).filter(Boolean).map((para, i) => (
              <p key={i} style={{ marginBottom: 10 }}><Linkify text={para.trim()} /></p>
            ))}
          </div>
        </div>

        <div style={{ background: "#16161f", border: "1px solid #1f1f2f", borderRadius: 8, padding: 14, maxHeight: 550, overflowY: "auto" }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 8 }}>Stated Need vs Actual Need</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "#1f1f2f", borderRadius: 6, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1 }}>
              <div style={{ background: "#1e1e2a", padding: "8px 10px", fontSize: 10, fontWeight: 600, color: "#555570", textTransform: "uppercase", letterSpacing: "0.5px" }}>What the JD Asks For</div>
              <div style={{ background: "#1e1e2a", padding: "8px 10px", fontSize: 10, fontWeight: 600, color: "#555570", textTransform: "uppercase", letterSpacing: "0.5px" }}>What They Likely Need</div>
            </div>
            {dossier.stated_vs_actual.map((row, i) => (
              <div key={i} style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 1 }}>
                <div style={{ background: "#0a0a0f", padding: "8px 10px", fontSize: 11, color: "#8888a0" }}><Linkify text={row.jd_asks_for} /></div>
                <div style={{ background: "#0a0a0f", padding: "8px 10px", fontSize: 11, color: "#8888a0" }}><Linkify text={row.business_likely_needs} /></div>
              </div>
            ))}
          </div>

          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 8, marginTop: 16 }}>Ideal Candidate Profiles</div>
          {dossier.candidate_profiles.map((p, i) => (
            <div key={i} style={{ fontSize: 12, lineHeight: 1.7, color: "#8888a0", marginBottom: 6 }}>
              <span style={{ color: i === 0 ? "#a29bfe" : "#4dabf7", fontWeight: 600 }}>{p.label}:</span> <Linkify text={`${p.background} ${p.fit_reason ? `\u2014 ${p.fit_reason}` : ""}`} />
            </div>
          ))}
        </div>
      </div>

      {/* Bottom grid: Spec Risk + Hiring Manager */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginTop: 12, alignItems: "stretch" }}>
        <div style={{ background: "#16161f", border: "1px solid #1f1f2f", borderRadius: 8, padding: 14, display: "flex", flexDirection: "column" }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 10 }}>Specification Risk</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 1, background: "#1f1f2f", borderRadius: 6, overflow: "hidden" }}>
            {dossier.spec_risk.map((r, i) => {
              const badge = riskBadgeStyles[r.severity?.toLowerCase()] || riskBadgeStyles.med;
              return (
                <div key={i} style={{ display: "flex", alignItems: "stretch", background: "#0a0a0f" }}>
                  <div style={{ width: 60, flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center", padding: "8px 4px", background: badge.bg, borderRight: `1px solid ${badge.border}` }}>
                    <span style={{ fontSize: 9, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.5px", color: badge.color }}>{r.severity}</span>
                  </div>
                  <div style={{ flex: 1, padding: "8px 10px" }}>
                    <div style={{ fontSize: 11, fontWeight: 600, color: "#e8e8f0", marginBottom: 2 }}>{r.risk}</div>
                    {r.explanation && <div style={{ fontSize: 11, color: "#8888a0", lineHeight: 1.6 }}><Linkify text={r.explanation} /></div>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div style={{ background: "#16161f", border: "1px solid #1f1f2f", borderRadius: 8, padding: 14, maxHeight: 550, overflowY: "auto" }}>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 10 }}>Likely Hiring Manager</div>
          {/*
            HM list is capped at ~200px so 3 cards fit flush and the
            4th peeks in to signal scrollability. Prompts allow up to
            6 candidates (bumped from 3 on 2026-04-20); more than 3
            scroll inside this inner container, leaving Lead Score
            and the HM Boolean button visible below without pushing
            the outer card taller.
          */}
          <div style={{ display: "flex", flexDirection: "column", gap: 8, maxHeight: 200, overflowY: "auto", paddingRight: 4 }}>
            {dossier.hiring_managers.map((hm, i) => {
              const linkedinUrl = (hm as Record<string, string>).linkedin_url || (hm as Record<string, string>).url;
              const searchUrl = linkedinUrl || `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(hm.name)}`;
              return (
                <div key={i} style={{ background: "#0a0a0f", border: "1px solid #1f1f2f", borderRadius: 6, padding: "10px 12px" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <a href={searchUrl} target="_blank" rel="noreferrer" style={{ fontWeight: 600, fontSize: 13, color: "#a29bfe", textDecoration: "none", cursor: "pointer" }} onMouseOver={(e) => (e.currentTarget.style.textDecoration = "underline")} onMouseOut={(e) => (e.currentTarget.style.textDecoration = "none")}>{hm.name}</a>
                      <div style={{ fontSize: 11, color: "#555570", marginTop: 2 }}>{hm.title}</div>
                    </div>
                    <span style={{ fontSize: 10, color: hm.confidence === "high" ? "#00d2a0" : hm.confidence === "medium" ? "#ffd93d" : "#555570" }}>{hm.confidence}</span>
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginBottom: 10, marginTop: 16 }}>Lead Score</div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <div style={{ fontSize: 28, fontWeight: 800, color: scoreColor }}>{dossier.lead_score}<span style={{ fontSize: 14, color: "#555570", fontWeight: 400 }}> / 5</span></div>
            <div style={{ fontSize: 11, color: "#555570", flex: 1, lineHeight: 1.6 }}><Linkify text={dossier.lead_score_justification || ""} /></div>
          </div>

          {dossier.search_booleans?.hiring_manager_boolean && (
            <>
              <div
                onClick={() => {
                  navigator.clipboard.writeText(dossier.search_booleans.hiring_manager_boolean || "");
                  const el = document.getElementById(`hm-bool-reveal-${leadId}`);
                  if (el) {
                    el.style.display = "block";
                    setTimeout(() => { el.style.display = "none"; }, 5000);
                  }
                  const label = document.getElementById(`hm-bool-label-${leadId}`);
                  if (label) { label.textContent = "Copied!"; setTimeout(() => { label.textContent = "Hiring Manager Search Boolean"; }, 1500); }
                }}
                id={`hm-bool-label-${leadId}`}
                style={{ fontSize: 11, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.8px", color: "#a29bfe", marginTop: 12, cursor: "pointer", textDecoration: "none" }}
                onMouseOver={(e) => (e.currentTarget.style.textDecoration = "underline")}
                onMouseOut={(e) => (e.currentTarget.style.textDecoration = "none")}
              >Hiring Manager Search Boolean</div>
              <div id={`hm-bool-reveal-${leadId}`} style={{ display: "none", marginTop: 6 }}>
                <div style={{ fontSize: 11, color: "#8888a0", fontFamily: "'JetBrains Mono', monospace", background: "#0a0a0f", padding: "6px 8px", borderRadius: 4, wordBreak: "break-all" }}>
                  {dossier.search_booleans.hiring_manager_boolean}
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default function LeadsPage() {
  // /api/queue now returns each lead with its latest dossier inlined
  // (see src/vacancysoft/api/routes/leads.py::list_queue). We poll every
  // 5s to pick up pending → generating → ready status transitions.
  const { data: leads = [], mutate: mutateLeads } = useSWR<QueuedLead[]>(
    "/queue",
    fetcher,
    { refreshInterval: 5000, keepPreviousData: true },
  );
  const [filter, setFilter] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  // Overrides for leads whose dossier had to be freshly generated
  // (unusual — server pre-generates on queue). Falls back to lead.dossier
  // otherwise.
  const [dossierOverrides, setDossierOverrides] = useState<Record<string, Dossier>>({});
  const [loadingDossier, setLoadingDossier] = useState<Set<string>>(new Set());
  const [iframeUrl, setIframeUrl] = useState<string | null>(null);

  // Paste-an-advert flow state. The operator pastes the full advert body
  // into a textarea; the backend LLM-extracts title/company/location and
  // runs the usual pipeline. Optional Source URL below the textarea is
  // kept for provenance + the "View original" link on the lead card.
  // LinkedIn URLs are rejected server-side AND client-side below.
  const PASTE_MIN_CHARS = 80;
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteSourceUrl, setPasteSourceUrl] = useState("");
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteError, setPasteError] = useState<string | null>(null);
  const [pasteStatus, setPasteStatus] = useState<string | null>(null);
  const pasteTextRef = useRef<HTMLTextAreaElement | null>(null);

  // Auto-focus the textarea the moment the panel opens so the operator can
  // paste immediately without a second click.
  useEffect(() => {
    if (pasteOpen) pasteTextRef.current?.focus();
  }, [pasteOpen]);

  // Client-side LinkedIn guard — the URL field rejects linkedin.com and
  // lnkd.in so the operator gets instant feedback. Server enforces the
  // same rule.
  const isLinkedInUrl = (u: string) => /(^|\/\/|\.)linkedin\.com|lnkd\.in/i.test(u);
  const sourceUrlLinkedInError =
    pasteSourceUrl.trim() && isLinkedInUrl(pasteSourceUrl.trim())
      ? "LinkedIn URLs aren't accepted — paste the advert text only, or use the 'Apply on company website' link."
      : null;

  const canSubmitPaste =
    pasteText.trim().length >= PASTE_MIN_CHARS &&
    !sourceUrlLinkedInError &&
    !pasteBusy;

  const closePaste = () => {
    setPasteOpen(false);
    setPasteText("");
    setPasteSourceUrl("");
    setPasteError(null);
  };

  const submitPaste = async () => {
    const text = pasteText.trim();
    const sourceUrl = pasteSourceUrl.trim();
    if (text.length < PASTE_MIN_CHARS || pasteBusy) return;
    if (sourceUrl && isLinkedInUrl(sourceUrl)) return;
    setPasteBusy(true);
    setPasteError(null);
    setPasteStatus(null);
    try {
      const res = await fetch(`${API}/leads/paste`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          advert_text: text,
          url: sourceUrl || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setPasteError(
          typeof body.detail === "string"
            ? body.detail
            : `Request failed (HTTP ${res.status})`,
        );
        return;
      }
      const data = await res.json();
      setPasteText("");
      setPasteSourceUrl("");
      setPasteStatus(
        data.status === "queued"
          ? "Lead queued — dossier generating"
          : data.status === "already_queued"
          ? "Already in the queue — generation in progress"
          : "Existing lead re-queued — dossier will update",
      );
      // Collapse the panel on success so the operator sees the freshly
      // queued row without the panel hovering above the table. The status
      // pill below Lead List shows the confirmation for a few seconds.
      setPasteOpen(false);
      // Surface the new row immediately instead of waiting for the 5s poll.
      mutateLeads();
      // Auto-clear the success banner after a few seconds.
      setTimeout(() => setPasteStatus(null), 4000);
    } catch (err) {
      setPasteError(
        err instanceof Error ? err.message : "Network error",
      );
    } finally {
      setPasteBusy(false);
    }
  };

  const getDossier = (lead: QueuedLead): Dossier | null =>
    dossierOverrides[lead.id] ?? lead.dossier ?? null;

  const toggleDossier = async (lead: QueuedLead) => {
    if (expandedId === lead.id) {
      setExpandedId(null);
      return;
    }
    setExpandedId(lead.id);

    // Inline dossier already present — render immediately, no request.
    if (getDossier(lead)) return;

    // Rare fallback: no dossier yet for this row. Try to fetch, and if
    // none exists, generate one on demand (same behaviour as before SWR).
    setLoadingDossier((prev) => new Set(prev).add(lead.id));
    try {
      const res = await fetch(`${API}/leads/${lead.id}/dossier`);
      if (res.ok) {
        const data = await res.json();
        setDossierOverrides((prev) => ({ ...prev, [lead.id]: data }));
      } else if (res.status === 404) {
        const genRes = await fetch(`${API}/leads/${lead.id}/dossier`, { method: "POST" });
        if (genRes.ok) {
          const data = await genRes.json();
          setDossierOverrides((prev) => ({ ...prev, [lead.id]: data }));
        }
      }
    } catch (err) {
      console.error("Failed to load dossier:", err);
    } finally {
      setLoadingDossier((prev) => { const n = new Set(prev); n.delete(lead.id); return n; });
    }
  };

  const createCampaign = async (id: string) => {
    try {
      const res = await fetch(`${API}/leads/${id}/campaign`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        alert(`Campaign created: ${data.emails?.length || 0} emails generated`);
      }
    } catch (err) {
      console.error("Failed to create campaign:", err);
    }
  };

  const filtered = filter ? leads.filter((l) => l.category === filter) : leads;

  const catCounts: Record<string, number> = {};
  leads.forEach((l) => { if (l.category) catCounts[l.category] = (catCounts[l.category] || 0) + 1; });

  return (
    <div className="min-h-screen" style={{ background: "#0a0a0f", color: "#e8e8f0", fontFamily: "'Inter', sans-serif" }}>
      <Sidebar />
      <main className="ml-60">
        <div className="flex items-center justify-between px-8 h-14" style={{ background: "rgba(10,10,15,0.8)", borderBottom: "1px solid #1f1f2f" }}>
          <div className="font-bold text-base">Lead List</div>
          <div className="flex items-center gap-3">
            {/* Paste-an-advert — the button stays in the topbar; when
                active it expands a full-width panel below (see after the
                topbar close-tag). The panel holds the textarea + optional
                URL field. Backend LLM-extracts title / company /
                location / posted date from the pasted text, runs
                enrichment → classification → scoring → queue. */}
            <button
              onClick={() => (pasteOpen ? closePaste() : setPasteOpen(true))}
              disabled={pasteBusy}
              className="px-3 py-1.5 rounded-lg text-sm font-semibold text-white cursor-pointer"
              style={{
                background: pasteOpen
                  ? "#2a2a3a"
                  : "linear-gradient(135deg, #6c5ce7, #8b7cf7)",
                cursor: pasteBusy ? "not-allowed" : "pointer",
              }}
            >
              {pasteOpen ? "Close" : "+ Add Lead"}
            </button>
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#555570", minWidth: 240 }}>
              <span style={{ fontSize: 14 }}>&#128269;</span>Search leads, sources, campaigns...
              <span className="ml-auto text-[10px] px-1.5 py-0.5 rounded" style={{ background: "#1e1e2a", border: "1px solid #2a2a3a" }}>&#8984;K</span>
            </div>
            <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#8888a0" }}>&#128276;</div>
            <div className="w-9 h-9 rounded-lg flex items-center justify-center" style={{ background: "#16161f", border: "1px solid #2a2a3a", color: "#8888a0" }}>&#9881;</div>
          </div>
        </div>

        {/* Paste-an-advert panel. Full-width below the topbar when open
            so the textarea has real estate; the optional URL field sits
            underneath it. Submits to /api/leads/paste with the text
            body; backend LLM-extracts structured fields. */}
        {pasteOpen && (
          <div
            className="px-8 pt-5 pb-6"
            style={{ background: "#0f0f17", borderBottom: "1px solid #1f1f2f" }}
          >
            <div className="mb-2 flex items-baseline justify-between">
              <div className="text-sm font-semibold">Paste job advert</div>
              <div className="text-[11px]" style={{ color: "#555570" }}>
                Works for LinkedIn, aggregators, and sites behind login walls.
              </div>
            </div>
            <textarea
              ref={pasteTextRef}
              value={pasteText}
              onChange={(e) => {
                setPasteText(e.target.value);
                if (pasteError) setPasteError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  e.preventDefault();
                  if (!pasteBusy) closePaste();
                } else if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
                  e.preventDefault();
                  if (canSubmitPaste) submitPaste();
                }
              }}
              placeholder="Paste the full job advert here — title, company, location, and description will be extracted automatically. Cmd/Ctrl+Enter to submit."
              disabled={pasteBusy}
              rows={10}
              className="w-full px-3 py-2.5 rounded-lg text-sm outline-none resize-y"
              style={{
                background: "#16161f",
                border: "1px solid #2a2a3a",
                color: "#e8e8f0",
                fontFamily: "'Inter', sans-serif",
                lineHeight: 1.5,
                minHeight: 200,
                opacity: pasteBusy ? 0.6 : 1,
              }}
            />
            <div className="mt-1 flex items-center justify-between text-[11px]">
              <span style={{ color: pasteText.trim().length >= PASTE_MIN_CHARS ? "#555570" : "#8888a0" }}>
                {pasteText.length} chars — minimum {PASTE_MIN_CHARS}
              </span>
            </div>

            <div className="mt-4">
              <label
                className="text-[11px] font-medium uppercase tracking-wider mb-1.5 block"
                style={{ color: "#555570", letterSpacing: "0.8px" }}
              >
                Source URL (optional)
              </label>
              <input
                type="url"
                value={pasteSourceUrl}
                onChange={(e) => {
                  setPasteSourceUrl(e.target.value);
                  if (pasteError) setPasteError(null);
                }}
                placeholder="https://… — stored for reference; leave blank for LinkedIn pastes"
                disabled={pasteBusy}
                className="w-full px-3 py-2 rounded-lg text-sm outline-none"
                style={{
                  background: "#16161f",
                  border: `1px solid ${sourceUrlLinkedInError ? "#ff6b6b" : "#2a2a3a"}`,
                  color: "#e8e8f0",
                  opacity: pasteBusy ? 0.6 : 1,
                }}
              />
              {sourceUrlLinkedInError && (
                <div className="mt-1 text-[11px]" style={{ color: "#ff6b6b" }}>
                  {sourceUrlLinkedInError}
                </div>
              )}
            </div>

            <div className="mt-4 flex items-center gap-2">
              <button
                onClick={submitPaste}
                disabled={!canSubmitPaste}
                className="px-4 py-2 rounded-lg text-sm font-semibold text-white flex items-center gap-2"
                style={{
                  background: canSubmitPaste
                    ? "linear-gradient(135deg, #6c5ce7, #8b7cf7)"
                    : "#2a2a3a",
                  cursor: canSubmitPaste ? "pointer" : "not-allowed",
                }}
              >
                {pasteBusy && (
                  <span
                    className="inline-block w-3 h-3 rounded-full"
                    style={{
                      border: "2px solid rgba(255,255,255,0.3)",
                      borderTopColor: "#fff",
                      animation: "spin 0.8s linear infinite",
                    }}
                  />
                )}
                {pasteBusy ? "Extracting…" : "Add Lead"}
              </button>
              <button
                onClick={closePaste}
                disabled={pasteBusy}
                className="px-3 py-2 rounded-lg text-sm"
                style={{
                  background: "#16161f",
                  border: "1px solid #2a2a3a",
                  color: "#8888a0",
                  cursor: pasteBusy ? "not-allowed" : "pointer",
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="p-7">
          {/* Paste status / error — shown briefly beneath the header so the
              operator sees the outcome without the input staying open. */}
          {(pasteError || pasteStatus) && (
            <div
              className="text-xs px-3 py-2 rounded-md mb-4"
              style={
                pasteError
                  ? {
                      background: "rgba(255,107,107,0.08)",
                      color: "#ff6b6b",
                      border: "1px solid rgba(255,107,107,0.2)",
                    }
                  : {
                      background: "rgba(0,210,160,0.08)",
                      color: "#00d2a0",
                      border: "1px solid rgba(0,210,160,0.2)",
                    }
              }
            >
              {pasteError || pasteStatus}
            </div>
          )}

          <div className="rounded-xl overflow-hidden" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
            {/* Filter bar */}
            <div className="flex items-center justify-between px-5 py-3.5" style={{ borderBottom: "1px solid #1f1f2f" }}>
              <div className="flex gap-1.5 flex-wrap">
                <span
                  onClick={() => setFilter("")}
                  className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer"
                  style={{ background: !filter ? "rgba(108,92,231,0.15)" : "#1e1e2a", color: !filter ? "#a29bfe" : "#8888a0", border: `1px solid ${!filter ? "rgba(108,92,231,0.3)" : "#2a2a3a"}` }}
                >All ({leads.length})</span>
                {["Risk", "Quant", "Compliance", "Audit", "Cyber", "Legal", "Front Office"].map((cat) => (
                  <span
                    key={cat}
                    onClick={() => setFilter(filter === cat ? "" : cat)}
                    className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer"
                    style={{ background: filter === cat ? "rgba(108,92,231,0.15)" : "#1e1e2a", color: filter === cat ? "#a29bfe" : "#8888a0", border: `1px solid ${filter === cat ? "rgba(108,92,231,0.3)" : "#2a2a3a"}` }}
                  >{cat} ({catCounts[cat] || 0})</span>
                ))}
              </div>
              <button className="px-3 py-1.5 rounded-lg text-xs font-semibold" style={{ background: "transparent", color: "#8888a0", border: "1px solid #2a2a3a" }}>&darr; Export</button>
            </div>

            {/* Table */}
            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #1f1f2f" }}>
                  {["TITLE", "COMPANY", "LOCATION", "CATEGORY", "SCORE", "QUEUED", "STATUS", ""].map((h) => (
                    <th key={h} className="px-5 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider" style={{ color: "#555570", background: "#12121a", letterSpacing: "0.8px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="px-5 py-12 text-center text-sm" style={{ color: "#555570" }}>
                      No leads queued yet. Use the Dashboard live feed to queue campaigns.
                    </td>
                  </tr>
                ) : (
                  filtered.map((l) => {
                    const st = statusStyles[l.status] || statusStyles.pending;
                    const isExpanded = expandedId === l.id;
                    const dossier = getDossier(l);
                    const isLoading = loadingDossier.has(l.id);

                    return (
                      <React.Fragment key={l.id}>
                        <tr className="cursor-pointer group" style={{ borderBottom: isExpanded ? "none" : "1px solid #1f1f2f", background: isExpanded ? "#1a1a25" : undefined }}
                          onMouseOver={(e) => { if (!isExpanded) e.currentTarget.style.background = "#1a1a25"; const btn = e.currentTarget.querySelector('.remove-btn') as HTMLElement; if (btn) btn.style.opacity = "1"; }}
                          onMouseOut={(e) => { if (!isExpanded) e.currentTarget.style.background = "transparent"; const btn = e.currentTarget.querySelector('.remove-btn') as HTMLElement; if (btn) btn.style.opacity = "0"; }}
                        >
                          <td className="px-5 py-3.5 text-[13px] font-semibold">
                            <span
                              onClick={(e) => { e.stopPropagation(); toggleDossier(l); }}
                              className="hover:underline cursor-pointer"
                              style={{ color: isExpanded ? "#a29bfe" : "#e8e8f0" }}
                            >{l.title}</span>
                          </td>
                          <td className="px-5 py-3.5 text-[13px]" style={{ color: "#8888a0" }}>{l.company}</td>
                          <td className="px-5 py-3.5 text-xs" style={{ color: "#8888a0" }}>{[l.location, l.country].filter(Boolean).join(", ")}</td>
                          <td className="px-5 py-3.5" style={{ minWidth: 140 }}>
                            {l.category && (
                              <span className="text-[10px] font-semibold px-2 py-0.5 rounded uppercase tracking-wide whitespace-nowrap" style={{
                                background: `${catColors[l.category] || "#a29bfe"}15`,
                                color: catColors[l.category] || "#a29bfe",
                              }}>{l.sub_specialism || l.category}</span>
                            )}
                          </td>
                          <td className="px-5 py-3.5 text-sm font-bold" style={{
                            color: (l.score || 0) >= 8 ? "#00d2a0" : (l.score || 0) >= 6 ? "#ffd93d" : "#555570",
                            fontFamily: "'JetBrains Mono', monospace",
                          }}>{l.score?.toFixed(1) || ""}</td>
                          <td className="px-5 py-3.5 text-xs" style={{ color: "#555570" }}>{timeAgo(l.created_at)}</td>
                          <td className="px-3 py-3.5">
                            {l.status === "generating" ? (
                              <span className="text-[10px] font-semibold px-2.5 py-1 rounded-md inline-flex items-center gap-1.5" style={{ background: "rgba(255,217,61,0.08)", color: "#ffd93d", border: "1px solid rgba(255,217,61,0.2)" }}>
                                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ border: "2px solid rgba(255,217,61,0.2)", borderTopColor: "#ffd93d", animation: "spin 0.8s linear infinite" }} />
                                Generating...
                              </span>
                            ) : l.status === "ready" ? (
                              <span className="text-[10px] font-semibold px-2.5 py-1 rounded-md" style={{ background: "rgba(0,210,160,0.08)", color: "#00d2a0", border: "1px solid rgba(0,210,160,0.2)" }}>&#10003; Ready</span>
                            ) : l.status === "pending" ? (
                              <span className="text-[10px] font-semibold px-2.5 py-1 rounded-md inline-flex items-center gap-1.5" style={{ background: "rgba(108,92,231,0.15)", color: "#a29bfe", border: "1px solid rgba(108,92,231,0.2)" }}>
                                <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ border: "2px solid rgba(108,92,231,0.2)", borderTopColor: "#a29bfe", animation: "spin 0.8s linear infinite" }} />
                                Queued
                              </span>
                            ) : (
                              <span className="text-[10px]" style={{ color: "#555570" }}>&mdash;</span>
                            )}
                          </td>
                          <td className="px-2 py-3.5">
                            <button
                              className="remove-btn w-5 h-5 rounded flex items-center justify-center text-xs cursor-pointer"
                              style={{ color: "#555570", background: "#1e1e2a", opacity: 0, transition: "opacity 0.15s" }}
                              onClick={async (e) => {
                                e.stopPropagation();
                                await fetch(`${API}/queue/${l.id}`, { method: "DELETE" });
                                // Optimistic update: drop the row locally, then let
                                // SWR revalidate to confirm. Dedupes across Sidebar too.
                                mutateLeads((prev) => (prev ?? []).filter((x) => x.id !== l.id), false);
                                globalMutate("/queue");
                              }}
                              title="Remove from Lead List"
                            >&#10005;</button>
                          </td>
                        </tr>

                        {/* Dossier panel */}
                        {isExpanded && (
                          <tr style={{ background: "transparent" }}>
                            <td colSpan={9} style={{ padding: 0, border: "none", borderBottom: "1px solid #1f1f2f" }}>
                              {isLoading ? (
                                <div style={{ padding: "40px 0", textAlign: "center" }}>
                                  <span className="inline-block w-5 h-5 rounded-full" style={{ border: "2px solid rgba(108,92,231,0.2)", borderTopColor: "#a29bfe", animation: "spin 0.8s linear infinite" }} />
                                  <div style={{ fontSize: 12, color: "#555570", marginTop: 8 }}>Generating intelligence dossier...</div>
                                </div>
                              ) : dossier ? (
                                <DossierPanel dossier={dossier} onCreateCampaign={() => createCampaign(l.id)} jobUrl={l.url} company={l.company} leadId={l.id} />
                              ) : (
                                <div style={{ padding: "20px 0", textAlign: "center", fontSize: 12, color: "#555570" }}>
                                  No dossier available. The job may not have been enriched yet.
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      {/* LinkedIn preview panel */}
      {iframeUrl && (
        <div style={{ position: "fixed", top: 0, right: 0, bottom: 0, width: 480, zIndex: 1000, background: "#0a0a0f", borderLeft: "1px solid #2a2a3a", display: "flex", flexDirection: "column", animation: "slideIn 0.2s ease-out" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px 14px", borderBottom: "1px solid #1f1f2f" }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: "#8888a0" }}>LinkedIn Lookup</span>
            <div style={{ display: "flex", gap: 6 }}>
              <button onClick={() => window.open(iframeUrl, "_blank")} style={{ fontSize: 10, padding: "3px 8px", background: "transparent", border: "1px solid #2a2a3a", borderRadius: 4, color: "#8888a0", cursor: "pointer" }}>Open in tab</button>
              <button onClick={() => setIframeUrl(null)} style={{ fontSize: 14, width: 24, height: 24, display: "flex", alignItems: "center", justifyContent: "center", background: "transparent", border: "1px solid #2a2a3a", borderRadius: 4, color: "#8888a0", cursor: "pointer" }}>&#10005;</button>
            </div>
          </div>
          <iframe src={iframeUrl} style={{ flex: 1, border: "none", background: "white" }} sandbox="allow-same-origin allow-scripts allow-popups allow-forms" />
        </div>
      )}
      </main>

      <style jsx global>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes slideIn { from { transform: translateX(100%); } to { transform: translateX(0); } }
      `}</style>
    </div>
  );
}
