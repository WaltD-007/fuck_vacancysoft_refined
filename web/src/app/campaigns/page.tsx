"use client";
// Campaigns tracker — PR P8.
//
// Shows one row per launched campaign with stage / status / opens /
// clicks / replies. Filters by status chip + sender-user dropdown.
// Click a row → slide-over with the per-step timeline + reply log +
// Stop button. URL state drives the slide-over (`?focus=<id>`) so the
// Builder's post-launch redirect can deep-link in.

import { Suspense, useMemo } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import useSWR from "swr";
import Sidebar from "../components/Sidebar";
import { API, fetcher } from "../lib/swr";
import { FEATURES } from "../lib/features";
import CampaignDetailPanel from "./CampaignDetailPanel";

const STATUS_FILTERS = [
  { key: "", label: "All" },
  { key: "sent", label: "In Sequence" },
  { key: "opened", label: "Opened" },
  { key: "replied", label: "Replied" },
  { key: "no_response", label: "No Response" },
  { key: "cancelled", label: "Cancelled" },
  { key: "failed", label: "Failed" },
] as const;

type CampaignListItem = {
  campaign_output_id: string;
  title: string | null;
  company: string | null;
  location_city: string | null;
  location_country: string | null;
  category: string | null;
  hiring_manager: { email: string | null; name: string | null };
  sender: { sender_user_id: string; display_name: string | null; email: string | null };
  stage: { sent: number; pending: number; cancelled: number; failed: number; total: number };
  status: string;
  counts: { opens: number; clicks: number; replies: number };
  last_activity: string | null;
  launched_at: string | null;
  archived_at: string | null;
};

type CampaignListResponse = {
  items: CampaignListItem[];
  total: number;
  limit: number;
  offset: number;
};

type Launcher = {
  sender_user_id: string;
  display_name: string | null;
  email: string | null;
  campaign_count: number;
};

const CAT_COLOR: Record<string, string> = {
  Risk: "#a29bfe",
  Quant: "#4dabf7",
  Compliance: "#00d2a0",
  Audit: "#ffd93d",
  Cyber: "#ff6b6b",
  Legal: "#fd79a8",
  "Quant Risk": "#4dabf7",
  "Front Office": "#ffa500",
};

const STATUS_COLOR: Record<string, string> = {
  replied: "#4dabf7",
  opened: "#00d2a0",
  sent: "#00d2a0",
  pending: "#8888a0",
  cancelled: "#ff6b6b",
  failed: "#ff6b6b",
};

const STATUS_LABEL: Record<string, string> = {
  replied: "✉ Replied",
  opened: "Opened",
  sent: "Sent",
  pending: "Pending",
  cancelled: "Cancelled",
  failed: "Failed",
};

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60_000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

function locationText(item: CampaignListItem): string {
  const parts = [item.location_city, item.location_country].filter(Boolean);
  return parts.join(", ") || "—";
}

export default function CampaignsPage() {
  return (
    <Suspense
      fallback={
        <div style={{ minHeight: "100vh", background: "#0a0a0f", color: "#8888a0", padding: 40 }}>
          Loading Campaigns…
        </div>
      }
    >
      <CampaignsPageInner />
    </Suspense>
  );
}

function CampaignsPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const statusFilter = searchParams.get("status") || "";
  const ownerFilter = searchParams.get("owner") || "";
  const focusId = searchParams.get("focus") || "";
  // Archive view: `false` (default — hide archived), `true` (only archived),
  // `all` (both). Three-state to match the backend; UI surfaces it as a
  // toggle button "Show archived" / "Showing archived" / "Showing all".
  const archivedView = searchParams.get("archived") || "false";

  // List endpoint — re-fetches when filters change. SWR key includes the
  // filter values so cache is per-filter-combo.
  const listKey = `/campaigns?${new URLSearchParams({
    ...(statusFilter ? { status: statusFilter } : {}),
    ...(ownerFilter ? { owner: ownerFilter } : {}),
    archived: archivedView,
    limit: "50",
  }).toString()}`;
  const { data: list, error: listError, isLoading } = useSWR<CampaignListResponse>(
    FEATURES.campaignsManager ? listKey : null,
    fetcher,
    { keepPreviousData: true, refreshInterval: 30_000 },
  );

  // Launchers — for the dropdown. Static-ish, refresh every 5 min.
  const { data: launchersData } = useSWR<{ launchers: Launcher[] }>(
    FEATURES.campaignsManager ? "/campaigns/launchers" : null,
    fetcher,
    { refreshInterval: 300_000 },
  );
  const launchers = launchersData?.launchers ?? [];

  const items = list?.items ?? [];

  // URL-state helpers
  const setParam = (key: string, value: string) => {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(key, value);
    else params.delete(key);
    router.replace(`/campaigns${params.toString() ? `?${params.toString()}` : ""}`);
  };

  const onRowClick = (id: string) => setParam("focus", id);
  const onClosePanel = () => setParam("focus", "");

  return (
    <div className="min-h-screen" style={{ background: "#0a0a0f", color: "#e8e8f0", fontFamily: "'Inter', sans-serif" }}>
      <Sidebar />
      <main className="ml-60">
        <div className="flex items-center justify-between px-8 h-14" style={{ background: "rgba(10,10,15,0.8)", borderBottom: "1px solid #1f1f2f" }}>
          <div className="font-bold text-base">Campaigns</div>
        </div>

        <div className="p-7">
          {/* Header */}
          <div className="flex justify-between items-center mb-6">
            <div>
              <div className="text-xl font-bold">Campaigns</div>
              <div className="text-sm mt-1" style={{ color: "#555570" }}>
                {list ? `${list.total} campaign${list.total === 1 ? "" : "s"}` : "—"}
              </div>
            </div>
          </div>

          {/* Stats — placeholder per decision 5; live numbers come later */}
          <div className="grid grid-cols-5 gap-4 mb-6">
            {[
              { label: "EMAILS SENT" },
              { label: "OPEN RATE" },
              { label: "REPLY RATE" },
              { label: "MEETINGS BOOKED" },
              { label: "BOUNCE RATE" },
            ].map((s) => (
              <div key={s.label} className="p-5 rounded-xl" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
                <div className="text-[11px] font-medium uppercase tracking-wider mb-2" style={{ color: "#555570", letterSpacing: "0.8px" }}>{s.label}</div>
                <div className="text-[28px] font-extrabold tracking-tight" style={{ color: "#3a3a4a" }}>—</div>
                <div className="text-xs mt-1.5" style={{ color: "#3a3a4a" }}>coming soon</div>
              </div>
            ))}
          </div>

          {/* Table */}
          <div className="rounded-xl overflow-hidden" style={{ background: "#16161f", border: "1px solid #1f1f2f" }}>
            {/* Toolbar: status chips + owner dropdown */}
            <div className="flex items-center gap-3 px-5 py-3 flex-wrap" style={{ borderBottom: "1px solid #1f1f2f" }}>
              <div className="flex gap-1.5 flex-wrap">
                {STATUS_FILTERS.map((f) => {
                  const active = (statusFilter || "") === f.key;
                  return (
                    <button
                      key={f.key || "all"}
                      onClick={() => setParam("status", f.key)}
                      className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer"
                      style={{
                        background: active ? "rgba(108,92,231,0.15)" : "#1e1e2a",
                        color: active ? "#a29bfe" : "#8888a0",
                        border: `1px solid ${active ? "rgba(108,92,231,0.3)" : "#2a2a3a"}`,
                      }}
                    >{f.label}</button>
                  );
                })}
              </div>
              <div className="ml-auto flex items-center gap-2">
                <label className="text-[11px] uppercase tracking-wider" style={{ color: "#555570", letterSpacing: "0.8px" }}>Operator</label>
                <select
                  value={ownerFilter}
                  onChange={(e) => setParam("owner", e.target.value)}
                  className="px-3 py-1.5 rounded-lg text-xs outline-none cursor-pointer"
                  style={{ background: "#1e1e2a", border: "1px solid #2a2a3a", color: "#e8e8f0", minWidth: 180 }}
                >
                  <option value="">All operators</option>
                  {launchers.map((l) => (
                    <option key={l.sender_user_id} value={l.sender_user_id}>
                      {(l.display_name || l.email || l.sender_user_id) + ` (${l.campaign_count})`}
                    </option>
                  ))}
                </select>
                <select
                  value={archivedView}
                  onChange={(e) => setParam("archived", e.target.value === "false" ? "" : e.target.value)}
                  className="px-3 py-1.5 rounded-lg text-xs outline-none cursor-pointer"
                  style={{
                    background: archivedView !== "false" ? "rgba(255,217,61,0.08)" : "#1e1e2a",
                    border: `1px solid ${archivedView !== "false" ? "rgba(255,217,61,0.25)" : "#2a2a3a"}`,
                    color: archivedView !== "false" ? "#ffd93d" : "#e8e8f0",
                  }}
                  title="Archive view"
                >
                  <option value="false">Active only</option>
                  <option value="true">Archived only</option>
                  <option value="all">Active + archived</option>
                </select>
              </div>
            </div>

            {/* Loading / error */}
            {isLoading && (
              <div className="px-5 py-3 text-xs" style={{ color: "#8888a0" }}>Loading…</div>
            )}
            {listError && (
              <div className="px-5 py-3 text-xs" style={{ color: "#ff6b6b" }}>
                Failed to load campaigns: {(listError as Error).message}
              </div>
            )}

            <table className="w-full">
              <thead>
                <tr style={{ borderBottom: "1px solid #1f1f2f" }}>
                  {[
                    "JOB TITLE",
                    "COMPANY",
                    "LOCATION",
                    "CATEGORY",
                    "HIRING MANAGER",
                    "OPERATOR",
                    "STAGE",
                    "STATUS",
                    "OPENS",
                    "CLICKS",
                    "REPLIES",
                    "LAST ACTIVITY",
                  ].map((h) => (
                    <th key={h} className="px-4 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider" style={{ color: "#555570", background: "#12121a", letterSpacing: "0.8px" }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {items.map((item) => {
                  const cat = item.category || "";
                  const stageLabel = item.stage.total > 0
                    ? `${item.stage.sent} of ${item.stage.total}`
                    : "—";
                  const stageWarn = item.stage.total > 0 && item.stage.sent === item.stage.total;
                  const isArchived = item.archived_at !== null;
                  return (
                    <tr
                      key={item.campaign_output_id}
                      className="cursor-pointer"
                      style={{
                        borderBottom: "1px solid #1f1f2f",
                        opacity: isArchived ? 0.55 : 1,
                      }}
                      onClick={() => onRowClick(item.campaign_output_id)}
                      onMouseOver={(e) => (e.currentTarget.style.background = "#1a1a25")}
                      onMouseOut={(e) => (e.currentTarget.style.background = "transparent")}
                    >
                      <td className="px-4 py-3 text-[13px] font-semibold">
                        {item.title || "—"}
                        {isArchived && (
                          <span className="ml-2 text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded" style={{
                            background: "rgba(255,217,61,0.08)",
                            color: "#ffd93d",
                            border: "1px solid rgba(255,217,61,0.25)",
                          }}>archived</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-[13px] font-semibold">{item.company || "—"}</td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#8888a0" }}>{locationText(item)}</td>
                      <td className="px-4 py-3">
                        {cat ? (
                          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded uppercase tracking-wide" style={{
                            background: `${CAT_COLOR[cat] || "#a29bfe"}15`,
                            color: CAT_COLOR[cat] || "#a29bfe",
                          }}>{cat}</span>
                        ) : <span className="text-[11px]" style={{ color: "#555570" }}>—</span>}
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-xs font-medium">{item.hiring_manager.name || "—"}</div>
                        <div className="text-[11px]" style={{ color: "#555570" }}>{item.hiring_manager.email || ""}</div>
                      </td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#8888a0" }}>
                        {item.sender.display_name || item.sender.email || item.sender.sender_user_id || "—"}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-[11px] px-2 py-0.5 rounded" style={{
                          background: stageWarn ? "rgba(255,217,61,0.08)" : "#1e1e2a",
                          color: stageWarn ? "#ffd93d" : "#8888a0",
                        }}>{stageLabel}</span>
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold" style={{ color: STATUS_COLOR[item.status] || "#8888a0" }}>
                        {STATUS_LABEL[item.status] || item.status}
                      </td>
                      <td className="px-4 py-3 text-xs font-bold" style={{
                        color: item.counts.opens > 0 ? "#00d2a0" : "#555570",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>{item.counts.opens}</td>
                      <td className="px-4 py-3 text-xs font-bold" style={{
                        color: item.counts.clicks > 0 ? "#00d2a0" : "#555570",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>{item.counts.clicks}</td>
                      <td className="px-4 py-3 text-xs font-bold" style={{
                        color: item.counts.replies > 0 ? "#4dabf7" : "#555570",
                        fontFamily: "'JetBrains Mono', monospace",
                      }}>{item.counts.replies}</td>
                      <td className="px-4 py-3 text-xs" style={{ color: "#555570" }}>{relativeTime(item.last_activity)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </main>

      {/* Slide-over detail panel — driven by ?focus=<id> in URL */}
      {focusId && (
        <CampaignDetailPanel
          campaignId={focusId}
          onClose={onClosePanel}
        />
      )}
    </div>
  );
}
